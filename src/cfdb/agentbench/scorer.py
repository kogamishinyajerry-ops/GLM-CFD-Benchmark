"""Agent submission scoring against a frozen contract (P4-E).

Scoring pipeline (fail-closed at every step):

1. Re-hash all frozen material; any drift raises
   :class:`~cfdb.agentbench.contract.FrozenDriftError` (no score is ever
   produced against a drifted ruler).
2. Recompute all validity gates from the submission artifacts; a submission
   failing any gate is invalid: ``score=None``, never ranked.
3. ``qoi_error`` is recomputed against the case reference data — any
   self-reported error or score fields inside the submission are ignored.
4. Every score is appended to an append-only JSONL ledger.

The only submission-supplied number consumed as-is is ``wall_time_sec`` from
``manifest.json`` (wall time is not recomputable after the fact); it feeds
the ``within_budget`` gate only, its absence fails closed, and it is marked
``self_reported`` in every ledger record. By default it carries no scoring
weight — weighting a self-reported value must be an explicit, warned choice.

Non-finite numbers (NaN/inf) are rejected everywhere: a submission with a
non-finite expected QoI fails ``qoi_complete``, and a score is never NaN/inf.

ANCHORING BOUNDARY: this module is orchestration and ledger IO — it wires
the anchored judging primitives together and copies their outputs into the
record. Everything that decides the (submission -> gates/validity/score)
mapping lives in the anchored modules :mod:`cfdb.agentbench.judge_policy`
(shared policy, ``judge_source:judge_policy`` in every contract),
``sandbox_scorer`` (coding) and ``checker_scorer`` (agentic). This file is
deliberately NOT anchored, so ledger/ranking improvements do not drift
every contract; its integrity is protected by the test suite and git — the
same trust root that protects the verification machinery itself (an
anchor-checker cannot anchor itself without regress).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cfdb.agentbench.contract import (
    FrozenDriftError,
    ScoringContract,
    missing_required_anchors,
    verify_frozen,
)
from cfdb.agentbench.judge_policy import (
    assemble_agentic,
    assemble_cfd,
    assemble_score,
    load_wall_time,
)
from cfdb.schema import CaseSpec

if TYPE_CHECKING:
    from cfdb.agentbench.sandbox_scorer import BackendFactory

logger = logging.getLogger(__name__)


class WallTimeRecord(BaseModel):
    """Wall time as recorded in the ledger, explicitly marked self-reported."""

    model_config = ConfigDict(extra="forbid")

    value_sec: float | None = None
    """Self-reported wall time in seconds; None when unavailable."""

    self_reported: bool = True
    """Always True: wall time comes from the submission, never recomputed."""


class SubmissionScore(BaseModel):
    """Scoring outcome for one agent submission."""

    model_config = ConfigDict(extra="forbid")

    submission_id: str
    """Submission identifier (the submission directory name)."""

    valid: bool = False
    """True only if every validity gate passed. Invalid samples never rank."""

    score: float | None = None
    """Weighted score; None for invalid or unscorable submissions
    (a None score is never fabricated into a number)."""

    breakdown: dict[str, float] = Field(default_factory=dict)
    """Per-metric weighted contributions (weight * recomputed value)."""

    gates: dict[str, bool] = Field(default_factory=dict)
    """Recomputed validity gate results."""

    scored_at: str = ""
    """UTC ISO 8601 timestamp of scoring."""

    notes: list[str] = Field(default_factory=list)
    """Human-readable audit notes (ignored fields, gate failures, ...)."""

    wall_time: WallTimeRecord | None = None
    """Self-reported wall time record (None for pre-existing ledger lines)."""

    ruler_id: str | None = None
    """First 8 hex chars of the contract.json sha256 that produced this
    score. Rows from an older (re-anchored) ruler are excluded from
    like-with-like ranking; None marks legacy rows of unknown lineage,
    which never rank once a ruler filter is applied (fail-closed)."""

    attempt_id: str | None = None
    """Content identity of the scored submission: sha256 over the sorted
    (relative path, file sha256) pairs of the submission tree, stamped by
    :func:`score_submission` (Codex R6 P1). ``submission_id`` is just the
    directory basename — caller-controlled and non-unique (``/a/run`` and
    ``/b/run`` collide; one candidate copied under fresh names multiplies).
    pass@k groups samples by this content identity; None marks legacy rows,
    which are never counted as samples (fail-closed)."""

    chain: str | None = None
    """Hash-chain link (R7 backlog): sha256 over the previous row's chain
    (genesis = 64 zeros for the first chained row) and this row's canonical
    JSON payload (every field except ``chain`` itself, sorted keys, compact
    separators). Stamped by :func:`append_ledger`; None marks rows ledgered
    before chaining existed, tolerated only as a contiguous file prefix
    (see :func:`verify_ledger_chain`). Honest boundary: the chain makes
    in-file edits, insertions and mid-file deletions tamper-evident; a
    writer with file access can rewrite the whole chain consistently, and
    pure tail truncation leaves a valid shorter chain — the committed
    ledger in git is the external trust root for those."""


def score_submission(
    contract: ScoringContract,
    case: CaseSpec,
    case_dir: Path,
    submission_dir: Path,
    ledger_path: Path | None = None,
    ruler_id: str | None = None,
    backend_factory: BackendFactory | None = None,
) -> SubmissionScore:
    """Score one agent submission against a frozen contract.

    Dispatches by ``case.domain`` (v5.0): ``cfd`` recomputes QoI/curve error
    against the (possibly held-out, see
    :func:`cfdb.agentbench.judge_policy.load_reference_qoi`) reference.
    ``coding`` delegates to
    :func:`cfdb.agentbench.sandbox_scorer.score_coding`, which runs the
    frozen hidden-test suite inside an execution backend. ``agentic``
    delegates to ``cfdb.agentbench.checker_scorer.score_agentic`` — a sibling
    module owned elsewhere, imported lazily so its absence fails closed with
    a clear ``ImportError`` instead of silently skipping the score.

    Args:
        contract: Frozen scoring contract for the case.
        case: Case spec (must match ``contract.case_id``).
        case_dir: Case directory (frozen paths and reference resolve here).
        submission_dir: Directory holding the submission artifacts
            (``qoi.json`` + optional ``manifest.json`` for cfd; the
            submission source tree for coding).
        ledger_path: When given, the score is appended to this JSONL ledger.
        ruler_id: Contract lineage tag recorded on the score (see
            :attr:`SubmissionScore.ruler_id`).
        backend_factory: Coding domain only. Overrides sandbox execution
            backend construction — unit tests inject a stub here; production
            callers leave this None to get the real sandboxed backend.
            Ignored for cfd/agentic domains.

    Returns:
        The submission score. Invalid or unscorable submissions carry
        ``score=None`` and are excluded from :func:`ranked`.

    Raises:
        FrozenDriftError: If any frozen path drifted (ruler changed —
            scoring is refused before anything else happens), or — coding
            domain only — drifted during the scoring window itself.
        ValueError: If ``case.id`` does not match the contract.
        ImportError: If ``case.domain == "agentic"`` and the sibling
            ``checker_scorer`` module is unavailable.
    """
    if case.id != contract.case_id:
        raise ValueError(f"case '{case.id}' does not match contract case '{contract.case_id}'")

    # verify_frozen runs first: it names drifted content and vanished
    # frozen files by their precise key. But an anchor that is absent
    # cannot drift (only existing keys are re-checked), so a contract
    # stripped of a mandatory anchor would verify clean (Codex R2 P2) —
    # the second check re-derives the full expected key set from the case
    # (Codex R3 P2: judged files and held-out keys included, not just the
    # special keys) and refuses an incomplete ruler exactly like a drifted
    # one: exit 3, zero ledger.
    drifted = verify_frozen(contract, case_dir)
    if len(drifted) > 0:
        raise FrozenDriftError(drifted)
    missing = missing_required_anchors(contract, case, case_dir)
    if len(missing) > 0:
        raise FrozenDriftError([f"{key} (mandatory anchor missing)" for key in missing])

    # Evaluator-owned output must never live inside the judged tree (Codex
    # R6-R2 P1): the post-scoring ledger append would mutate the submission
    # snapshot, so every rescore of otherwise unchanged content would mint
    # a fresh attempt_id and pad the pass@k sample population.
    if ledger_path is not None and ledger_path.resolve().is_relative_to(submission_dir.resolve()):
        raise ValueError(
            f"ledger path '{ledger_path}' lies inside the submission tree "
            f"'{submission_dir}': evaluator output would mutate the submission's "
            "content identity — refusing to score"
        )

    # Platform guard (R8 backlog: defensive limits on judged input): an
    # oversized submission tree is refused BEFORE hashing or judging — the
    # content digest, the checker and the sandbox mounts all walk this
    # tree, and an unbounded one is a denial-of-service on the judge host.
    # Entry COUNT is bounded in the same walk (Codex R8 P2): a million
    # empty files stay at zero bytes but still cost unbounded CPU/memory
    # in every tree walk downstream.
    total_bytes = 0
    for index, p in enumerate(submission_dir.rglob("*"), start=1):
        if index > MAX_SUBMISSION_ENTRIES:
            raise ValueError(
                f"submission tree has more than {MAX_SUBMISSION_ENTRIES} entries "
                "— refusing to score"
            )
        if p.is_file() or p.is_symlink():
            total_bytes += p.lstat().st_size
    if total_bytes > MAX_SUBMISSION_BYTES:
        raise ValueError(
            f"submission tree is {total_bytes} bytes, exceeding the platform cap "
            f"of {MAX_SUBMISSION_BYTES} bytes — refusing to score"
        )

    # Content identity stamped BEFORE judging (Codex R6-R1 P1): the attempt
    # is what was handed in, hashed up front — a submission that mutates
    # itself during scoring cannot retroactively pick its identity.
    attempt_id = _submission_digest(submission_dir)

    if case.domain == "coding":
        from cfdb.agentbench.sandbox_scorer import score_coding

        result = score_coding(
            case,
            case_dir,
            submission_dir,
            contract,
            backend_factory=backend_factory,
            ruler_id=ruler_id,
        )
    elif case.domain == "agentic":
        try:
            from cfdb.agentbench.checker_scorer import score_agentic
        except ImportError as e:
            raise ImportError(
                "agentic domain scoring requires cfdb.agentbench.checker_scorer, "
                "which is not available in this build (fail-closed: refusing to "
                "silently fabricate a score for an agentic submission)"
            ) from e
        # checker_scorer.score_agentic is the state-based checker execution
        # primitive (Architecture v5.0 §4): it runs case_dir/reference/checker.py
        # against submission_dir and reduces stdout to a CheckerVerdict. The
        # verdict-to-gates/validity/metrics conversion is judging policy and
        # lives in the anchored judge_policy module; this branch only wires
        # the pieces and keeps the ledger's single append point.
        agentic_notes: list[str] = []
        verdict = score_agentic(case_dir, submission_dir)

        # Post-run ruler re-verification (Codex R0 P2, mirrors the coding
        # branch): a host-side race or an accidental checker write during
        # execution must abort with exit 3, never ledger a score taken with
        # a disturbed ruler.
        post_drift = verify_frozen(contract, case_dir)
        if len(post_drift) > 0:
            raise FrozenDriftError(post_drift)

        agentic_gates, agentic_valid, agentic_metric_values = assemble_agentic(
            verdict, contract.validity_gates, agentic_notes
        )
        agentic_score, agentic_breakdown = assemble_score(
            contract, agentic_valid, agentic_metric_values, agentic_notes
        )
        result = SubmissionScore(
            submission_id=submission_dir.name,
            valid=agentic_valid,
            score=agentic_score,
            breakdown=agentic_breakdown,
            gates=agentic_gates,
            scored_at=datetime.now(timezone.utc).isoformat(),
            notes=agentic_notes,
            wall_time=WallTimeRecord(value_sec=load_wall_time(submission_dir)),
            ruler_id=ruler_id,
        )
    else:
        # Full cfd composition (inputs -> gates/validity/metrics) is policy
        # and lives in the anchored judge_policy module (Codex R5 P1);
        # this branch only wires it and copies the outputs into the record.
        notes: list[str] = []
        gates, valid, metric_values, wall_time = assemble_cfd(
            contract, case, case_dir, submission_dir, notes
        )
        score, breakdown = assemble_score(contract, valid, metric_values, notes)

        result = SubmissionScore(
            submission_id=submission_dir.name,
            valid=valid,
            score=score,
            breakdown=breakdown,
            gates=gates,
            scored_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
            wall_time=WallTimeRecord(value_sec=wall_time),
            ruler_id=ruler_id,
        )

    # The recorded identity must be OF the judged bytes (Codex R6-R2 P2):
    # if the tree changed while the checker/sandbox ran, the verdict
    # describes content the pre-run attempt_id does not — refuse instead
    # of ledgering a detached identity.
    post_attempt_id = _submission_digest(submission_dir)
    if post_attempt_id != attempt_id:
        raise ValueError(
            "submission content changed during judging (content identity "
            f"{attempt_id[:16]} -> {post_attempt_id[:16]}): refusing to record "
            "a score detached from the judged snapshot"
        )

    result.attempt_id = attempt_id
    if ledger_path is not None:
        append_ledger(ledger_path, result)
    return result


MAX_SUBMISSION_ENTRIES = 10_000
"""Platform cap on a submission tree's entry count (Codex R8 P2): byte
caps alone let a tree of empty files stay at zero bytes while every
downstream walk (digest sort, checker traversal, sandbox mount) pays
per-entry cost — count and bytes are bounded in the same pre-judging
walk, both as structured input errors."""

MAX_SUBMISSION_BYTES = 64 * 1024 * 1024
"""Platform cap on a submission tree's total file bytes (R8 backlog).

The judge hashes every file (content identity), the agentic checker reads
artifacts, and the coding sandbox mounts the tree — all linear in
submission size, so an unbounded tree is a denial-of-service on the judge
host, not a legitimate submission. Refused as a structured input error
(never judged, never ledgered). 64 MiB is generous for every shipped case
family; raising it is a deliberate platform decision, not a per-case knob."""


LEDGER_CHAIN_GENESIS = "0" * 64
"""Chain seed for the first chained row of a ledger file."""


class LedgerChainReport(NamedTuple):
    """Result of :func:`verify_ledger_chain`."""

    unchained_prefix: int
    """Rows without a chain field before the chain starts (pre-R7 legacy)."""

    n_chained: int
    """Rows carrying a chain link."""

    head: str | None
    """The last stored chain link (None when no row is chained yet) —
    compare against an externally recorded head (git) to detect tail
    truncation, which the file alone cannot reveal."""

    problems: list[str]
    """Chain violations, each naming its 1-based ledger line. Empty for a
    structurally intact ledger."""


def _chain_hash(prev_chain: str, row: dict) -> str:
    """Chain link for one ledger row: sha256(prev + canonical payload).

    The payload is the row's JSON object minus the ``chain`` key, dumped
    with sorted keys and compact separators — both the appender and the
    verifier pass values through ``json.loads``/``json.dumps``, so float
    representation converges and the form is stable across model versions
    (verification recomputes from the STORED line, never by re-serializing
    through the current pydantic model).
    """
    payload = {k: v for k, v in row.items() if k != "chain"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev_chain + "\n" + canonical).encode("utf-8")).hexdigest()


def verify_ledger_chain(ledger_path: Path) -> LedgerChainReport:
    """Verify the hash chain of a JSONL ledger file.

    Rules: rows without a chain are tolerated only as a contiguous prefix
    (they predate chaining, an honestly disclosed legacy population, like
    ``attempt_id is None`` rows in pass@k). Once a chained row appears,
    every later row must be chained and link from the previous chained
    row — an in-file edit, insertion, mid-file deletion, or unchained
    forgery therefore breaks the chain at a named line.

    Honest boundary (also on :attr:`SubmissionScore.chain`): full-chain
    rewrites and pure tail truncation are undetectable from the file
    alone; the committed ledger in git is the external trust root. This
    check is tamper-evidence between commits, not cryptographic authority.

    Args:
        ledger_path: Ledger file (missing file = empty, clean report).

    Returns:
        A :class:`LedgerChainReport`.
    """
    if not ledger_path.is_file():
        return LedgerChainReport(0, 0, None, [])
    problems: list[str] = []
    prev = LEDGER_CHAIN_GENESIS
    head: str | None = None
    unchained_prefix = 0
    n_chained = 0
    for lineno, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() == "":
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {lineno}: not valid JSON")
            continue
        if not isinstance(row, dict):
            problems.append(f"line {lineno}: not a JSON object")
            continue
        chain = row.get("chain")
        if chain is None:
            if head is not None:
                problems.append(f"line {lineno}: unchained row after the chain started")
            else:
                unchained_prefix += 1
            continue
        # A chain value must be a 64-char hex string BEFORE it is hashed,
        # sliced or adopted as the next link (Codex R7 P2: a corrupt line
        # storing a JSON number/object must be a named violation, never a
        # TypeError traceback).
        if not isinstance(chain, str) or len(chain) != 64:
            problems.append(f"line {lineno}: chain value is not a 64-char string")
            continue
        expected = _chain_hash(prev, row)
        if chain != expected:
            problems.append(
                f"line {lineno}: chain mismatch (stored {chain[:16]}, expected {expected[:16]})"
            )
        # Continue from the STORED link so one tampered row is one named
        # problem instead of cascading over every later (honest) row.
        prev = chain
        head = chain
        n_chained += 1
    return LedgerChainReport(unchained_prefix, n_chained, head, problems)


def append_ledger(ledger_path: Path, score: SubmissionScore) -> None:
    """Append one hash-chained score to the JSONL ledger (append-only).

    The row's ``chain`` link is computed here (R7 backlog): sha256 over the
    previous chained row's link (genesis for the first) and this row's
    canonical payload. Appending to a ledger whose existing chain is broken
    is refused — fresh rows must never bury tampering.

    Args:
        ledger_path: Ledger file (parent directories are created).
        score: Score to append (its ``chain`` field is stamped in place).

    Raises:
        ValueError: If the existing ledger's chain is broken.
    """
    report = verify_ledger_chain(ledger_path)
    if len(report.problems) > 0:
        raise ValueError(
            f"ledger chain broken in {ledger_path} — refusing to append: "
            + "; ".join(report.problems)
        )
    prev = report.head if report.head is not None else LEDGER_CHAIN_GENESIS
    row = json.loads(score.model_dump_json(exclude={"chain"}))
    score.chain = _chain_hash(prev, row)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(score.model_dump_json() + "\n")


def read_ledger(ledger_path: Path) -> list[SubmissionScore]:
    """Read all scores from a JSONL ledger in append order.

    Args:
        ledger_path: Ledger file.

    Returns:
        All ledger entries; empty when the ledger does not exist.

    Raises:
        ValueError: If a ledger line is corrupt (fail-closed: a tampered or
            damaged ledger is reported, never silently skipped).
    """
    if not ledger_path.is_file():
        return []
    entries: list[SubmissionScore] = []
    for lineno, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() == "":
            continue
        try:
            entries.append(SubmissionScore.model_validate_json(line))
        except ValidationError as e:
            raise ValueError(f"corrupt ledger line {lineno} in {ledger_path}: {e}") from e
    return entries


def _is_rankable(entry: SubmissionScore) -> bool:
    """Structurally re-verify one ledger entry before it may rank.

    A ledger line is data, not authority: beyond ``valid is True`` and a
    present score, the score must be finite, every recorded gate must have
    passed, every breakdown term must be finite, and the score must equal
    the sum of its own breakdown. A forged score field that does not follow
    from the recorded breakdown never ranks.

    Args:
        entry: Ledger entry.

    Returns:
        True only if the entry is internally consistent and rankable.
    """
    if entry.valid is not True:
        return False
    if entry.score is None or not math.isfinite(entry.score):
        return False
    if any(passed is not True for passed in entry.gates.values()):
        return False
    if any(not math.isfinite(v) for v in entry.breakdown.values()):
        return False
    recomputed = sum(entry.breakdown.values())
    tolerance = 1e-9 * max(1.0, abs(entry.score))
    if abs(entry.score - recomputed) > tolerance:
        logger.warning(
            "ledger entry '%s' score %.17g does not match its breakdown sum "
            "%.17g: excluded from ranking",
            entry.submission_id,
            entry.score,
            recomputed,
        )
        return False
    return True


PASS_AT_K_BINARY_GATES: dict[str, str] = {
    "coding": "tests_all_pass",
    "agentic": "checker_ok",
}
"""The explicit binary success gate per pass@k-capable domain (Codex R6-R1
P1). Domain membership alone does not establish that rankable means
correct: a custom coding ruler may omit ``tests_all_pass`` from its
validity gates, making a partial hidden-test failure valid-and-rankable.
pass@k therefore requires this gate to be IN the frozen gate list and
recorded True on every row of a passing attempt. cfd has no binary gate —
its scores are continuous error magnitudes — so it is absent here and
pass@k is refused."""


def _submission_digest(submission_dir: Path) -> str:
    """Content identity of a submission tree (Codex R6-R1 P1, hardened R6-R2).

    sha256 over the sorted manifest of the tree: files as (relative POSIX
    path, file sha256) pairs — the original scheme, unchanged, so flat
    file-only trees keep their already-ledgered identity — plus directories
    as ("<relpath>/", "dir") entries (Codex R6-R2 P1: the shipped
    dir_organize checker judges directory layout, so an empty directory IS
    content and two trees differing by one must not collapse into a single
    attempt). Symlinks are refused outright: hashing the link target would
    record an identity the judge may not reproduce (the link can dangle or
    point elsewhere inside the sandbox mount).

    Args:
        submission_dir: Submission directory.

    Returns:
        64-char lowercase hex digest.

    Raises:
        ValueError: If the tree contains a symlink, or an entry cannot be
            read while hashing (Codex R6-R2 P2: I/O failures surface as a
            structured input error, never a raw OSError).
    """
    from cfdb.agentbench.contract import canonical_digest, sha256_file

    pairs: list[list[str]] = []
    try:
        for path in sorted(submission_dir.rglob("*")):
            rel = path.relative_to(submission_dir).as_posix()
            if path.is_symlink():
                raise ValueError(
                    f"submission contains a symlink at '{rel}': refusing to hash "
                    "link targets into the content identity"
                )
            if path.is_dir():
                pairs.append([rel + "/", "dir"])
            elif path.is_file():
                pairs.append([rel, sha256_file(path)])
    except OSError as e:
        raise ValueError(f"submission unreadable while computing content identity: {e}") from e
    return canonical_digest(pairs)


def pass_at_k(
    entries: list[SubmissionScore],
    k: int,
    ruler_id: str | None = None,
    *,
    contract: ScoringContract,
    domain: str,
) -> tuple[float, int, int] | None:
    """Unbiased pass@k over ledger attempts (Chen et al. 2021 estimator).

    A sample is one unique attempt identified by CONTENT
    (:attr:`SubmissionScore.attempt_id`, Codex R6-R1 P1) — never by the
    caller-controlled directory basename. Rescoring identical content
    collapses into one attempt; an attempt passes only if EVERY one of its
    rows is rankable AND carries the domain's explicit binary success gate
    recorded True. Legacy rows without an attempt identity are excluded
    (fail-closed: they can neither pass nor pad n).
    pass@k = 1 - C(n-c, k)/C(n, k), stable product form.

    Fail-closed rules: the domain must have a binary success gate
    (:data:`PASS_AT_K_BINARY_GATES`) AND that gate must be in the FROZEN
    contract's validity gates (Codex R6-R1 P1: a coding ruler that omitted
    ``tests_all_pass`` makes partial failures rankable — such a ruler has
    no binary signal and is refused). Fewer unique attempts than ``k`` (or
    k < 1) returns None — never extrapolated. When ``ruler_id`` is given,
    only rows scored under that exact ruler participate (like-with-like).

    Args:
        entries: Ledger entries.
        k: Number of draws.
        ruler_id: When given, restrict samples to this ruler lineage.
        contract: The frozen contract whose gate list defines "pass".
        domain: Case domain (must be verified against the frozen case.yaml
            by the caller — see the CLI, which refuses on ruler drift
            before trusting the live spec).

    Returns:
        ``(pass_at_k, n_unique_attempts, n_passes)``, or None when not
        honestly computable from the available samples.

    Raises:
        ValueError: If the domain has no binary success gate, or the
            frozen ruler does not gate on it.
    """
    binary_gate = PASS_AT_K_BINARY_GATES.get(domain)
    if binary_gate is None:
        raise ValueError(
            f"pass@k requires a binary success signal; domain '{domain}' "
            "scores are continuous (rankable does not mean correct) — refusing "
            "to fabricate a pass rate"
        )
    if binary_gate not in contract.validity_gates:
        raise ValueError(
            f"pass@k requires the frozen ruler to gate on '{binary_gate}', but "
            f"this contract's validity gates are {contract.validity_gates} — "
            "without it, rankable does not mean correct (refusing)"
        )
    if k < 1:
        return None
    rows = entries
    if ruler_id is not None:
        rows = [e for e in rows if e.ruler_id == ruler_id]
    by_attempt: dict[str, list[SubmissionScore]] = {}
    for entry in rows:
        if entry.attempt_id is None:
            continue  # legacy row: no content identity, never a sample
        by_attempt.setdefault(entry.attempt_id, []).append(entry)
    n = len(by_attempt)
    if n < k:
        return None
    c = sum(
        1
        for attempt_rows in by_attempt.values()
        if all(_is_rankable(e) is True and e.gates.get(binary_gate) is True for e in attempt_rows)
    )
    if n - c < k:
        return 1.0, n, c
    estimate = 1.0
    for i in range(k):
        estimate *= (n - c - i) / (n - i)
    return 1.0 - estimate, n, c


def ranked(
    entries: list[SubmissionScore],
    ruler_id: str | None = None,
) -> list[SubmissionScore]:
    """Return only rankable entries, best score first.

    A submission ranks only if ``valid is True`` **and** it carries a real,
    finite score that is consistent with its own recorded gates and
    breakdown (see :func:`_is_rankable`) — invalid samples and forged or
    non-finite score fields never enter the ranking.

    Args:
        entries: Ledger entries.
        ruler_id: When given, only entries scored under this exact ruler
            rank — rows from an older (re-anchored) contract, and legacy
            rows with unknown lineage (``ruler_id is None``), are excluded
            so the leaderboard always compares like with like.

    Returns:
        Valid, consistent, scored entries sorted by score descending.
    """
    rankable = [e for e in entries if _is_rankable(e) is True]
    if ruler_id is not None:
        rankable = [e for e in rankable if e.ruler_id == ruler_id]
    return sorted(rankable, key=lambda e: e.score or 0.0, reverse=True)
