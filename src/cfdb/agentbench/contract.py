"""Frozen scoring contract for agent submissions (P4-E).

A :class:`ScoringContract` freezes the "ruler" used to score agent
submissions against a case: the case definition (``case.yaml``), every
reference data file, and the scoring weights themselves. Each frozen item is
anchored by its sha256 digest at contract-creation time.

Before any scoring, every frozen path is re-hashed. Any drift — a changed
byte in a reference file, an edited ``case.yaml``, or modified weights —
means the ruler changed, and scoring is refused by raising
:class:`FrozenDriftError` (the CLI layer maps it to exit code
:data:`EXIT_FROZEN_DRIFT`). This is fail-closed by design: a drifted ruler
can never silently keep scoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cfdb.registry import CaseRegistry
from cfdb.schema import CaseSpec

logger = logging.getLogger(__name__)

EXIT_FROZEN_DRIFT = 3
"""Process exit code the CLI layer must use when frozen material drifted."""

WEIGHTS_KEY = "__weights__"
"""Frozen-map key anchoring the sha256 of the contract's own weights."""

VALIDITY_GATES_KEY = "__validity_gates__"
"""Frozen-map key anchoring the sha256 of the contract's validity gates."""

HELD_OUT_PREFIX = "held_out:"

NORMALIZE_SOURCE_KEY = "__normalize_source__"
"""Frozen-map key anchoring the sha256 of cfdb/agentbench/normalize.py for
agentic contracts. Normalization rules are judging material (Architecture
v5.0 §0.7): editing normalize.py must drift every existing agentic contract
(exit 3 at scoring) — a version string alone would not catch a code change
that forgets to bump it (Codex R0 P2)."""
"""Frozen-map key prefix for held-out reference files (v5.0 Wave D2).

Held-out files live under the case directory like any other reference file,
but are kept out of the public case surface handed to agents. They get a
distinct key namespace (``held_out:<relpath>``) so a leaked/renamed public
reference can never be silently reinterpreted as a held-out anchor."""

FILE_MANIFEST_KEY = "__file_manifest__"
"""Frozen-map key anchoring the canonical digest of the sorted inventory of
every file under ``reference/`` and ``visible/`` (Codex R1 P1). Per-file
hashes alone only pin files that existed at init time: *adding* a file to a
judged tree (e.g. ``visible/messy/new.txt`` in an agentic case, which shifts
the checker-derived expected layout) would otherwise verify clean. The
manifest makes any addition, deletion, or rename in those trees drift the
ruler."""

JUDGE_SOURCE_PREFIX = "judge_source:"
"""Frozen-map key prefix anchoring the sha256 of a domain judge module's
source (Codex R1 P1). The judge's pass/fail policy (skipped-test rejection,
exit-code whitelist, bootstrap isolation, checker verdict reduction) is
judging material: hardening it must change the ruler lineage, so old and
new verdicts never share a leaderboard. Key form:
``judge_source:<module-shortname>``."""

_JUDGE_SOURCE_MODULES: dict[str, str] = {
    "sandbox_scorer": "cfdb.agentbench.sandbox_scorer",
    "checker_scorer": "cfdb.agentbench.checker_scorer",
    "scorer": "cfdb.agentbench.scorer",
}
"""Whitelist of anchorable judge modules; an unknown shortname in a contract
fails closed as drift, never crashes verification."""

_DOMAIN_JUDGE_MODULES: dict[str, tuple[str, ...]] = {
    "cfd": ("scorer",),
    "coding": ("sandbox_scorer", "scorer"),
    "agentic": ("checker_scorer", "scorer"),
}
"""Which judge modules each domain anchors. scorer.py is anchored for every
domain (Codex R2 P1): gate evaluation, verdict-to-score assembly, and the
cfd QoI recomputation all live there, so a policy edit anywhere in it must
drift every contract — the cost that unrelated scorer.py refactors force a
re-anchor is accepted as correct noise (one CLI command per case)."""

_MANIFEST_TREES: tuple[str, ...] = ("reference", "visible")
"""Case-dir subtrees whose file inventory the manifest anchor covers."""

REQUIRED_UNIVERSAL_ANCHORS: tuple[str, ...] = (
    "case.yaml",
    WEIGHTS_KEY,
    VALIDITY_GATES_KEY,
    FILE_MANIFEST_KEY,
)
"""Anchors every v2 contract must carry regardless of domain (Codex R2 P2):
the version label alone is not proof of migration — a payload claiming v2
without these anchors is refused at load."""

DEFAULT_WEIGHTS: dict[str, float] = {"qoi_error": -1.0}
"""Default public scoring weights (negative = lower metric is better).

``wall_time_sec`` is deliberately absent: it is a self-reported value and
must never drive the ranking by default. It can be added back explicitly
via ``init_contract(..., weights=...)``, which logs a loud warning."""

DEFAULT_VALIDITY_GATES: list[str] = ["qoi_complete", "within_budget"]
"""Default validity gates a submission must pass to receive a score."""

DOMAIN_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "cfd": DEFAULT_WEIGHTS,
    "coding": {"pass_rate": 1.0},
    "agentic": {"checker_success": 1.0},
}
"""Per-domain default weights (v5.0). The cfd entry aliases the historic
defaults so pre-v5 contracts and call sites are byte-identical."""

DOMAIN_DEFAULT_VALIDITY_GATES: dict[str, list[str]] = {
    "cfd": DEFAULT_VALIDITY_GATES,
    "coding": ["tests_all_pass", "sandbox_used"],
    "agentic": ["checker_ok"],
}
"""Per-domain default validity gates (v5.0). Names must match what the
domain scorer actually emits (sandbox_scorer / the agentic branch of
score_submission) — an unmatched gate name fails closed at scoring time."""


class FrozenDriftError(Exception):
    """Raised when any frozen path no longer matches its anchored sha256.

    Attributes:
        drifted: Frozen keys (relative paths or special keys) that drifted.
    """

    def __init__(self, drifted: list[str]) -> None:
        """Initialize with the list of drifted frozen keys.

        Args:
            drifted: Frozen keys whose current hash mismatches the anchor
                (or whose backing file is missing).
        """
        self.drifted: list[str] = list(drifted)
        joined = ", ".join(self.drifted)
        super().__init__(f"frozen material drifted, refusing to score: {joined}")


class ScoringContract(BaseModel):
    """Frozen ruler for scoring agent submissions on one case."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2"] = "2"
    """Contract schema version. v2 (Codex R1) adds the judging-material
    anchors :data:`FILE_MANIFEST_KEY` and ``judge_source:*`` (plus
    :data:`NORMALIZE_SOURCE_KEY` for agentic, introduced in the same
    hardening batch); v1 contracts predate them and are refused at load
    with a re-anchor instruction — a legacy contract must never silently
    verify without the hardened anchors."""

    case_id: str
    """Case this contract scores submissions for."""

    frozen: dict[str, str]
    """Frozen path -> sha256 map. Keys are case-dir-relative POSIX paths
    (``case.yaml`` and every reference file) plus the special keys
    :data:`WEIGHTS_KEY` and :data:`VALIDITY_GATES_KEY` anchoring the
    contract's own weights and gates."""

    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    """Public scoring weights: metric name -> weight. score = sum(w * metric)."""

    validity_gates: list[str] = Field(default_factory=lambda: list(DEFAULT_VALIDITY_GATES))
    """Gates a submission must pass to be scored (fail any = invalid sample)."""

    @model_validator(mode="after")
    def _validate_frozen_nonempty(self) -> ScoringContract:
        """Reject contracts that freeze nothing (an empty ruler gates nothing)."""
        if len(self.frozen) == 0:
            raise ValueError("frozen map must not be empty (fail-closed)")
        return self

    @model_validator(mode="after")
    def _validate_weights_finite(self) -> ScoringContract:
        """Reject non-finite weights (a NaN/inf ruler can never score)."""
        bad = sorted(m for m, w in self.weights.items() if not math.isfinite(w))
        if len(bad) > 0:
            raise ValueError(f"non-finite weights are not allowed (fail-closed): {bad}")
        return self


def sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file's bytes.

    Args:
        path: File to hash.

    Returns:
        64-char lowercase hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(obj: object) -> str:
    """Compute the sha256 of an object's canonical JSON serialization.

    Canonical form uses sorted keys and compact separators so the digest is
    stable regardless of insertion order.

    Args:
        obj: JSON-serializable object (weights dict, gates list, ...).

    Returns:
        64-char lowercase hex digest.
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_source_digest() -> str:
    """sha256 of the live ``cfdb/agentbench/normalize.py`` source file.

    Used both when anchoring (:data:`NORMALIZE_SOURCE_KEY` at init) and when
    re-verifying — so editing the normalization rules drifts every existing
    agentic contract instead of silently changing quasi-exact-match
    semantics under a frozen ruler.
    """
    from cfdb.agentbench import normalize

    return sha256_file(Path(normalize.__file__))


def _judge_source_digest(shortname: str) -> str:
    """sha256 of a whitelisted judge module's live source file.

    Args:
        shortname: Key into :data:`_JUDGE_SOURCE_MODULES`.

    Returns:
        64-char lowercase hex digest of the module source.

    Raises:
        KeyError: If ``shortname`` is not a whitelisted judge module.
    """
    import importlib

    module = importlib.import_module(_JUDGE_SOURCE_MODULES[shortname])
    return sha256_file(Path(module.__file__))  # type: ignore[arg-type]


def _is_cache_artifact(path: Path) -> bool:
    """True for Python bytecode-cache artifacts (``__pycache__``/``*.pyc``).

    These are interpreter side effects, not judging material: a checker
    legitimately importing a sibling helper module must never drift its own
    ruler via a generated cache file (Codex R2 P2), and a preexisting cache
    must never be anchored.
    """
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def _tree_manifest(case_dir: Path) -> list[str]:
    """Sorted inventory of every file under the judged case subtrees.

    Enumerates :data:`_MANIFEST_TREES` (``reference/`` and ``visible/``)
    recursively, returning case-dir-relative POSIX paths. Used identically
    at anchor time and at verify time, so any file added to, removed from,
    or renamed within a judged tree changes the manifest digest. Bytecode
    caches are excluded (see :func:`_is_cache_artifact`).

    Args:
        case_dir: Directory containing ``case.yaml``.

    Returns:
        Sorted relative POSIX paths of all files currently present.
    """
    paths: list[str] = []
    for tree in _MANIFEST_TREES:
        tree_dir = case_dir / tree
        if tree_dir.is_dir():
            for path in tree_dir.rglob("*"):
                if path.is_file() and not _is_cache_artifact(path):
                    paths.append(path.relative_to(case_dir).as_posix())
    return sorted(paths)


def _collect_frozen_files(case: CaseSpec, case_dir: Path) -> list[str]:
    """Enumerate the case files that must be frozen.

    Includes ``case.yaml``, every file under ``<case_dir>/reference/``
    (recursive), every file under ``<case_dir>/visible/`` (recursive), and
    every file listed in ``case.reference.files``. visible/ was initially
    agentic-only (Codex R0 P1: a state-based checker derives its expected
    verdict from the visible inputs) and is frozen for every domain since
    the R1 batch: the visible tree is the task surface handed to agents,
    and editing it (e.g. a coding case's starting ``solution.py``) changes
    what is being measured — scores over different task surfaces must not
    share a ruler lineage.

    Args:
        case: Loaded case spec.
        case_dir: Directory containing ``case.yaml``.

    Returns:
        Sorted, de-duplicated case-dir-relative POSIX paths.

    Raises:
        FileNotFoundError: If ``case.yaml`` or a declared reference file is
            missing (fail-closed: cannot freeze what does not exist).
    """
    rel_paths: set[str] = set()

    case_yaml = case_dir / "case.yaml"
    if not case_yaml.is_file():
        raise FileNotFoundError(f"case.yaml not found in {case_dir}")
    rel_paths.add("case.yaml")

    reference_dir = case_dir / "reference"
    if reference_dir.is_dir():
        for path in sorted(reference_dir.rglob("*")):
            if path.is_file() and not _is_cache_artifact(path):
                rel_paths.add(path.relative_to(case_dir).as_posix())

    visible_dir = case_dir / "visible"
    if visible_dir.is_dir():
        for path in sorted(visible_dir.rglob("*")):
            if path.is_file() and not _is_cache_artifact(path):
                rel_paths.add(path.relative_to(case_dir).as_posix())

    if case.reference is not None:
        for rel in case.reference.files.values():
            ref_path = case_dir / rel
            if not ref_path.is_file():
                raise FileNotFoundError(
                    f"declared reference file missing, cannot freeze: {ref_path}"
                )
            rel_paths.add(Path(rel).as_posix())

    return sorted(rel_paths)


def _collect_held_out_files(case: CaseSpec, case_dir: Path) -> dict[str, str]:
    """Enumerate held-out reference files, keyed by their frozen key.

    Args:
        case: Loaded case spec.
        case_dir: Directory containing ``case.yaml``.

    Returns:
        Mapping of frozen key (``held_out:<relpath>``) to the case-dir-relative
        POSIX path it anchors.

    Raises:
        FileNotFoundError: If a declared held-out file is missing
            (fail-closed: cannot freeze what does not exist).
    """
    result: dict[str, str] = {}
    if case.reference is None:
        return result
    for rel in case.reference.held_out_files.values():
        held_path = case_dir / rel
        if not held_path.is_file():
            raise FileNotFoundError(
                f"declared held-out reference file missing, cannot freeze: {held_path}"
            )
        result[f"{HELD_OUT_PREFIX}{Path(rel).as_posix()}"] = Path(rel).as_posix()
    return result


def init_contract(
    case_id: str,
    registry: CaseRegistry,
    *,
    weights: dict[str, float] | None = None,
    validity_gates: list[str] | None = None,
) -> ScoringContract:
    """Create a scoring contract by hashing the case's frozen material now.

    Args:
        case_id: Case to freeze.
        registry: Case registry used to resolve the case spec and directory.
        weights: Public scoring weights; defaults to :data:`DEFAULT_WEIGHTS`.
        validity_gates: Gates a submission must pass; defaults to
            :data:`DEFAULT_VALIDITY_GATES`.

    Returns:
        A contract whose ``frozen`` map anchors every frozen path's sha256,
        including the weights and gates themselves.

    Raises:
        KeyError: If ``case_id`` is unknown to the registry.
        FileNotFoundError: If a file that must be frozen is missing.
    """
    case = registry.load(case_id)
    case_dir = registry.get_case_dir(case_id)
    domain_weights = DOMAIN_DEFAULT_WEIGHTS.get(case.domain, DEFAULT_WEIGHTS)
    domain_gates = DOMAIN_DEFAULT_VALIDITY_GATES.get(case.domain, DEFAULT_VALIDITY_GATES)
    final_weights = dict(domain_weights if weights is None else weights)
    final_gates = list(domain_gates if validity_gates is None else validity_gates)
    if "wall_time_sec" in final_weights:
        logger.warning(
            "wall_time_sec is self-reported: weighting it lets submissions "
            "influence their own ranking"
        )

    if case.domain == "agentic":
        # Admission gate (Codex R0 P2): refuse to freeze a contract over a
        # checker that imports denied modules — validate_checker is supply
        # -chain hygiene, and init is its mandatory production call site.
        from cfdb.agentbench.checker_scorer import validate_checker

        checker_path = case_dir / "reference" / "checker.py"
        if not checker_path.is_file():
            raise FileNotFoundError(
                f"agentic case '{case_id}' has no reference/checker.py — "
                "cannot freeze a contract without its judging primitive"
            )
        admission = validate_checker(checker_path)
        if admission.ok is not True:
            raise ValueError(
                f"agentic checker {checker_path} failed admission: "
                + "; ".join(admission.violations)
            )

    frozen: dict[str, str] = {}
    for rel in _collect_frozen_files(case, case_dir):
        frozen[rel] = sha256_file(case_dir / rel)
    for held_out_key, rel in _collect_held_out_files(case, case_dir).items():
        frozen[held_out_key] = sha256_file(case_dir / rel)
    frozen[WEIGHTS_KEY] = canonical_digest(final_weights)
    frozen[VALIDITY_GATES_KEY] = canonical_digest(final_gates)
    frozen[FILE_MANIFEST_KEY] = canonical_digest(_tree_manifest(case_dir))
    for judge_module in _DOMAIN_JUDGE_MODULES.get(case.domain, ()):
        frozen[f"{JUDGE_SOURCE_PREFIX}{judge_module}"] = _judge_source_digest(judge_module)
    if case.domain == "agentic":
        frozen[NORMALIZE_SOURCE_KEY] = _normalize_source_digest()

    logger.info("initialized scoring contract for '%s' (%d frozen items)", case_id, len(frozen))
    return ScoringContract(
        case_id=case_id,
        frozen=frozen,
        weights=final_weights,
        validity_gates=final_gates,
    )


def required_domain_anchors(domain: str) -> tuple[str, ...]:
    """Anchors a v2 contract must carry for a given case domain.

    Args:
        domain: Case domain (``cfd`` / ``coding`` / ``agentic``).

    Returns:
        The mandatory domain-specific frozen keys (judge-source anchors,
        plus the normalize-source anchor for agentic).
    """
    keys = [f"{JUDGE_SOURCE_PREFIX}{module}" for module in _DOMAIN_JUDGE_MODULES.get(domain, ())]
    if domain == "agentic":
        keys.append(NORMALIZE_SOURCE_KEY)
    return tuple(keys)


def missing_required_anchors(contract: ScoringContract, domain: str) -> list[str]:
    """Mandatory anchors absent from a contract's frozen map (Codex R2 P2).

    ``verify_frozen`` can only re-check keys that exist; a contract stripped
    of an anchor would otherwise verify clean. The scoring path refuses to
    score when this is non-empty (an incomplete ruler is a drifted ruler).

    Args:
        contract: Contract to inspect.
        domain: Domain of the case being scored.

    Returns:
        Sorted missing mandatory keys (universal + domain-specific).
    """
    required = tuple(REQUIRED_UNIVERSAL_ANCHORS) + required_domain_anchors(domain)
    return sorted(key for key in required if key not in contract.frozen)


def verify_frozen(contract: ScoringContract, case_dir: Path) -> list[str]:
    """Re-hash every frozen item and report drifted keys.

    File entries are re-hashed from disk (a missing file counts as drift).
    The special keys re-derive the digest from the contract's current
    ``weights`` / ``validity_gates``, so editing the weights in a saved
    contract without re-anchoring is caught here (changing the ruler).

    Args:
        contract: Contract to verify.
        case_dir: Case directory the frozen relative paths resolve against.

    Returns:
        Drifted frozen keys, empty when everything still matches.
    """
    drifted: list[str] = []
    for key, expected in sorted(contract.frozen.items()):
        if key == WEIGHTS_KEY:
            actual = canonical_digest(contract.weights)
        elif key == VALIDITY_GATES_KEY:
            actual = canonical_digest(contract.validity_gates)
        elif key == NORMALIZE_SOURCE_KEY:
            actual = _normalize_source_digest()
        elif key == FILE_MANIFEST_KEY:
            actual = canonical_digest(_tree_manifest(case_dir))
        elif key.startswith(JUDGE_SOURCE_PREFIX):
            shortname = key[len(JUDGE_SOURCE_PREFIX) :]
            if shortname not in _JUDGE_SOURCE_MODULES:
                logger.error("unknown judge-source anchor '%s' (fail-closed: drift)", key)
                drifted.append(key)
                continue
            actual = _judge_source_digest(shortname)
        else:
            rel = key[len(HELD_OUT_PREFIX) :] if key.startswith(HELD_OUT_PREFIX) else key
            path = case_dir / rel
            if not path.is_file():
                logger.error("frozen file missing: %s", path)
                drifted.append(key)
                continue
            actual = sha256_file(path)
        if actual != expected:
            logger.error("frozen drift detected for '%s'", key)
            drifted.append(key)
    return drifted


def save_contract(contract: ScoringContract, path: Path, *, force: bool = False) -> None:
    """Write a contract to disk as pretty-printed JSON.

    Overwriting an existing contract changes the ruler mid-game, so it is
    refused by default: re-initialization must be loud and deliberate.

    Args:
        contract: Contract to persist.
        path: Destination file (parent directories are created).
        force: Allow overwriting an existing contract file.

    Raises:
        FileExistsError: If ``path`` already exists and ``force`` is False.
    """
    if force is False and path.exists():
        raise FileExistsError(
            f"scoring contract already exists at {path}: refusing to overwrite "
            "the frozen ruler (pass force=True to re-anchor deliberately)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contract.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_contract(path: Path) -> ScoringContract:
    """Load and validate a contract from a JSON file.

    Args:
        path: Contract JSON file.

    Returns:
        The validated contract.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the contract predates schema v2 (Codex R1 P2): a
            legacy contract carries none of the hardened judging-material
            anchors (file manifest, judge source, normalize source), so
            letting it verify clean would silently exempt it from the
            hardening. Rejected loudly with a re-anchor instruction.
        pydantic.ValidationError: If the payload is not a valid contract.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    version = raw.get("contract_version") if isinstance(raw, dict) else None
    case_hint = raw.get("case_id", "<case>") if isinstance(raw, dict) else "<case>"
    if version != "2":
        raise ValueError(
            f"legacy scoring contract (version {version!r}) at {path}: it "
            "predates the v2 judging-material anchors (file manifest, judge "
            "source) and cannot be trusted to detect ruler drift. Re-anchor "
            f"deliberately with 'cfdb agent-eval init --case {case_hint} "
            "--force' — scores in the existing ledger keep their old "
            "ruler_id and will not rank against new scores."
        )
    contract = ScoringContract.model_validate(raw)
    # The version label is not proof of migration (Codex R2 P2): a payload
    # relabeled or truncated to look like v2 must still carry every
    # universal anchor. Domain-specific anchors are enforced at the scoring
    # seam (score_submission), where the case domain is known.
    missing = sorted(key for key in REQUIRED_UNIVERSAL_ANCHORS if key not in contract.frozen)
    if len(missing) > 0:
        raise ValueError(
            f"scoring contract at {path} claims version 2 but is missing "
            f"mandatory anchors {missing} — refusing to trust an incomplete "
            f"ruler. Re-anchor with 'cfdb agent-eval init --case {case_hint} "
            "--force'."
        )
    return contract
