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

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

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

    result.attempt_id = attempt_id
    if ledger_path is not None:
        append_ledger(ledger_path, result)
    return result


def append_ledger(ledger_path: Path, score: SubmissionScore) -> None:
    """Append one score to the JSONL ledger (append-only, never rewrites).

    Args:
        ledger_path: Ledger file (parent directories are created).
        score: Score to append.
    """
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
    """Content identity of a submission tree (Codex R6-R1 P1).

    sha256 over the sorted (relative POSIX path, file sha256) pairs of
    every file under the submission directory: identical content under any
    basename is one attempt; different content is a different attempt —
    the directory name (caller-controlled, non-unique) never decides
    sample identity.

    Args:
        submission_dir: Submission directory.

    Returns:
        64-char lowercase hex digest.
    """
    from cfdb.agentbench.contract import canonical_digest, sha256_file

    pairs = [
        [path.relative_to(submission_dir).as_posix(), sha256_file(path)]
        for path in sorted(submission_dir.rglob("*"))
        if path.is_file()
    ]
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
