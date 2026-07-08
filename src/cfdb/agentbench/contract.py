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

DEFAULT_WEIGHTS: dict[str, float] = {"qoi_error": -1.0}
"""Default public scoring weights (negative = lower metric is better).

``wall_time_sec`` is deliberately absent: it is a self-reported value and
must never drive the ranking by default. It can be added back explicitly
via ``init_contract(..., weights=...)``, which logs a loud warning."""

DEFAULT_VALIDITY_GATES: list[str] = ["qoi_complete", "within_budget"]
"""Default validity gates a submission must pass to receive a score."""


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

    contract_version: Literal["1"] = "1"
    """Contract schema version."""

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


def _collect_frozen_files(case: CaseSpec, case_dir: Path) -> list[str]:
    """Enumerate the case files that must be frozen.

    Includes ``case.yaml``, every file under ``<case_dir>/reference/``
    (recursive), and every file listed in ``case.reference.files``.

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
            if path.is_file():
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
    final_weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    final_gates = list(DEFAULT_VALIDITY_GATES if validity_gates is None else validity_gates)
    if "wall_time_sec" in final_weights:
        logger.warning(
            "wall_time_sec is self-reported: weighting it lets submissions "
            "influence their own ranking"
        )

    frozen: dict[str, str] = {}
    for rel in _collect_frozen_files(case, case_dir):
        frozen[rel] = sha256_file(case_dir / rel)
    frozen[WEIGHTS_KEY] = canonical_digest(final_weights)
    frozen[VALIDITY_GATES_KEY] = canonical_digest(final_gates)

    logger.info("initialized scoring contract for '%s' (%d frozen items)", case_id, len(frozen))
    return ScoringContract(
        case_id=case_id,
        frozen=frozen,
        weights=final_weights,
        validity_gates=final_gates,
    )


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
        else:
            path = case_dir / key
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
        pydantic.ValidationError: If the payload is not a valid contract.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ScoringContract.model_validate(raw)
