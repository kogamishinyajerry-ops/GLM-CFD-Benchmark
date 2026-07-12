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
"""

from __future__ import annotations

import json
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
from cfdb.schema import CaseSpec

if TYPE_CHECKING:
    from cfdb.agentbench.sandbox_scorer import BackendFactory

logger = logging.getLogger(__name__)

QOI_FILENAME = "qoi.json"
MANIFEST_FILENAME = "manifest.json"


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


def _load_json_dict(path: Path) -> dict[str, object] | None:
    """Load a JSON file expected to contain an object.

    Args:
        path: JSON file to read.

    Returns:
        The parsed dict, or None if the file is missing, unreadable, or not
        a JSON object.
    """
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # manifest.json is an optional side-channel (wall-time only, and
        # wall time is self-reported anyway) — absence is normal for
        # coding/agentic submissions and must not read like an error.
        logger.debug("optional %s not present", path)
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read %s: %s", path, e)
        return None
    if not isinstance(parsed, dict):
        logger.warning("%s does not contain a JSON object", path)
        return None
    return parsed


def _load_submission_qoi(submission_dir: Path, notes: list[str]) -> dict[str, float]:
    """Load numeric QoI values from the submission's ``qoi.json``.

    Args:
        submission_dir: Submission directory.
        notes: Audit note sink (mutated in place).

    Returns:
        Mapping of QoI name to numeric value; empty when missing/unreadable.
    """
    raw = _load_json_dict(submission_dir / QOI_FILENAME)
    if raw is None:
        notes.append(f"missing or unreadable {QOI_FILENAME} in submission")
        return {}
    values: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            notes.append(f"non-numeric QoI '{key}' in {QOI_FILENAME} ignored")
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            notes.append(
                f"non-finite QoI '{key}' in {QOI_FILENAME} rejected "
                "(fail-closed: treated as missing)"
            )
            continue
        values[key] = numeric
    return values


def _load_wall_time(submission_dir: Path) -> float | None:
    """Extract wall time in seconds from the submission's ``manifest.json``.

    Accepts either ``timing.wall_time_sec`` (RunManifest layout) or a
    top-level ``wall_time_sec``.

    Args:
        submission_dir: Submission directory.

    Returns:
        Wall time in seconds, or None if unavailable (fail-closed upstream).
    """
    manifest = _load_json_dict(submission_dir / MANIFEST_FILENAME)
    if manifest is None:
        return None
    candidate: object = manifest.get("wall_time_sec")
    timing = manifest.get("timing")
    if candidate is None and isinstance(timing, dict):
        candidate = timing.get("wall_time_sec")
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        return None
    wall_time = float(candidate)
    if not math.isfinite(wall_time):
        logger.warning("non-finite wall_time_sec in %s rejected", MANIFEST_FILENAME)
        return None
    return wall_time


def _load_reference_qoi(case: CaseSpec, case_dir: Path) -> dict[str, float]:
    """Load reference QoI values from the case (inline values preferred).

    v5.0 Wave D2: when the case declares ``held_out_files``, scoring reads
    the held-out copy instead of the public reference (submission-authenticity
    mitigation) — the public reference stays anchored in the frozen map and
    keeps driving the visible case surface, it just does not drive scoring
    when a held-out counterpart exists.

    Args:
        case: Case spec.
        case_dir: Case directory for resolving relative reference paths.

    Returns:
        Reference QoI values; empty when the case has no usable reference.
    """
    if case.reference is None:
        return {}
    held_out = case.reference.held_out_files
    if len(held_out) > 0:
        for key in ("qoi", "qoi_values"):
            if key in held_out:
                raw = _load_json_dict(case_dir / held_out[key])
                if raw is None:
                    return {}
                try:
                    values = {k: float(v) for k, v in raw.items()}  # type: ignore[arg-type]
                except (TypeError, ValueError) as e:
                    logger.warning("invalid held-out reference QoI file for %s: %s", case.id, e)
                    return {}
                return _finite_only(values, case.id)
        logger.warning(
            "held_out_files declared for %s but none of the keys %s are present "
            "(fail-closed: no held-out QoI reference usable)",
            case.id,
            ("qoi", "qoi_values"),
        )
        return {}
    if case.reference.qoi_values is not None:
        return _finite_only(dict(case.reference.qoi_values), case.id)
    for key in ("qoi", "qoi_values"):
        if key in case.reference.files:
            raw = _load_json_dict(case_dir / case.reference.files[key])
            if raw is None:
                return {}
            try:
                values = {k: float(v) for k, v in raw.items()}  # type: ignore[arg-type]
            except (TypeError, ValueError) as e:
                logger.warning("invalid reference QoI file for %s: %s", case.id, e)
                return {}
            return _finite_only(values, case.id)
    return {}


def _finite_only(values: dict[str, float], case_id: str) -> dict[str, float]:
    """Drop non-finite reference values (a NaN reference can never be a ruler).

    Args:
        values: Raw reference QoI values.
        case_id: Case id, for logging.

    Returns:
        Only the finite entries.
    """
    finite = {k: v for k, v in values.items() if math.isfinite(v)}
    dropped = sorted(set(values) - set(finite))
    if len(dropped) > 0:
        logger.warning("non-finite reference QoI for %s dropped: %s", case_id, dropped)
    return finite


def _recompute_qoi_error(
    case: CaseSpec,
    reference: dict[str, float],
    computed: dict[str, float],
    notes: list[str],
) -> float | None:
    """Recompute the aggregate QoI error against the case reference.

    Per expected QoI: relative error ``|c - r| / |r|`` when the reference is
    nonzero, absolute error ``|c - r|`` when it is zero. The aggregate is the
    mean over all recomputable QoIs. Self-reported error values inside the
    submission are never consulted.

    Args:
        case: Case spec (defines the expected QoI list).
        reference: Reference QoI values.
        computed: Submission-computed QoI values.
        notes: Audit note sink (mutated in place).

    Returns:
        Mean recomputed error, or None when no expected QoI is recomputable
        (missing reference/computed values; fail-closed: never returns 0).
    """
    terms: list[float] = []
    for name in case.outputs.qoi:
        if name not in computed:
            notes.append(f"qoi_error: missing computed QoI '{name}'")
            continue
        if name not in reference:
            notes.append(f"qoi_error: missing reference QoI '{name}'")
            continue
        ref_val = reference[name]
        diff = abs(computed[name] - ref_val)
        term = diff if ref_val == 0 else diff / abs(ref_val)
        if not math.isfinite(term):
            notes.append(f"qoi_error: non-finite error term for '{name}' skipped (fail-closed)")
            continue
        terms.append(term)
    if len(terms) == 0:
        notes.append("qoi_error: no recomputable QoI (fail-closed: metric unavailable)")
        return None
    mean_error = sum(terms) / len(terms)
    if not math.isfinite(mean_error):
        notes.append("qoi_error: non-finite aggregate (fail-closed: metric unavailable)")
        return None
    return mean_error


def _evaluate_gates(
    contract: ScoringContract,
    case: CaseSpec,
    computed: dict[str, float],
    wall_time: float | None,
    notes: list[str],
) -> dict[str, bool]:
    """Recompute every validity gate declared by the contract.

    Unknown gate names fail closed (an unverifiable gate can never pass).

    Args:
        contract: Scoring contract (declares the gate list).
        case: Case spec.
        computed: Submission-computed QoI values.
        wall_time: Submission wall time, None if unavailable.
        notes: Audit note sink (mutated in place).

    Returns:
        Gate name -> recomputed pass/fail.
    """
    results: dict[str, bool] = {}
    for gate in contract.validity_gates:
        if gate == "qoi_complete":
            missing = [q for q in case.outputs.qoi if q not in computed]
            ok = len(missing) == 0
            if not ok:
                notes.append(f"gate qoi_complete failed: missing QoI {missing}")
        elif gate == "within_budget":
            budget = case.budget.max_runtime_sec
            if budget is None:
                ok = True
            elif wall_time is None:
                ok = False
                notes.append("gate within_budget failed: wall time unavailable (fail-closed)")
            else:
                ok = wall_time <= budget
                if not ok:
                    notes.append(
                        f"gate within_budget failed: wall_time_sec={wall_time:g} "
                        f"> max_runtime_sec={budget}"
                    )
        elif gate == "tests_all_pass":
            # v5.0 Wave B: coding domain. sandbox_scorer encodes the recomputed
            # hidden-test verdict as a 1.0/0.0 sentinel in `computed` — never a
            # bare bool, so the existing FiniteFloat-shaped `computed` dict stays
            # the single source of truth this function reads from.
            ok = computed.get("tests_all_pass") == 1.0
            if not ok:
                notes.append("gate tests_all_pass failed: hidden test suite did not fully pass")
        elif gate == "sandbox_used":
            ok = computed.get("sandbox_used") == 1.0
            if not ok:
                notes.append("gate sandbox_used failed: submission was not scored in a sandbox")
        else:
            ok = False
            notes.append(f"unknown validity gate '{gate}' (fail-closed: cannot pass)")
        results[gate] = ok
    return results


def _assemble_score(
    contract: ScoringContract,
    valid: bool,
    metric_values: dict[str, float],
    notes: list[str],
) -> tuple[float | None, dict[str, float]]:
    """Assemble the weighted score from recomputed metric values.

    Shared by every domain branch of :func:`score_submission` so the
    fail-closed assembly rule is defined exactly once: any weighted metric
    that is unavailable, or a non-finite candidate score, yields
    ``score=None`` — never fabricated. This is a pure extraction of the
    pre-v5.0 inline cfd logic; behavior for the cfd path is unchanged.

    Args:
        contract: Frozen scoring contract (declares the weights).
        valid: Recomputed gate verdict (``all(gates[g] is True for g in
            contract.validity_gates)``).
        metric_values: Recomputed metric name -> value.
        notes: Audit note sink (mutated in place).

    Returns:
        ``(score, breakdown)``; ``score`` is None whenever it cannot be
        honestly computed.
    """
    if valid is not True:
        notes.append("submission invalid: no score assigned (score=None)")
        return None, {}
    missing_metrics = sorted(m for m in contract.weights if m not in metric_values)
    if len(missing_metrics) > 0:
        notes.append(
            f"cannot compute score: metrics {missing_metrics} unavailable (fail-closed: score=None)"
        )
        return None, {}
    breakdown = {m: w * metric_values[m] for m, w in contract.weights.items()}
    candidate = sum(breakdown.values())
    if math.isfinite(candidate) and all(math.isfinite(v) for v in breakdown.values()):
        return candidate, breakdown
    notes.append("non-finite score (fail-closed: score=None)")
    return None, {}


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
    against the (possibly held-out, see :func:`_load_reference_qoi`)
    reference exactly as before — this branch is byte-for-byte the pre-v5.0
    logic, only extracted into :func:`_assemble_score`. ``coding`` delegates
    to :func:`cfdb.agentbench.sandbox_scorer.score_coding`, which runs the
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
        # contract-driven gate/score assembly around that verdict is not owned
        # by that sibling module (it has no SubmissionScore-shaped API) — it is
        # assembled here, symmetric to the coding branch, so the ledger keeps
        # a single append point regardless of domain.
        agentic_notes: list[str] = []
        verdict = score_agentic(case_dir, submission_dir)

        # Post-run ruler re-verification (Codex R0 P2, mirrors the coding
        # branch): a host-side race or an accidental checker write during
        # execution must abort with exit 3, never ledger a score taken with
        # a disturbed ruler.
        post_drift = verify_frozen(contract, case_dir)
        if len(post_drift) > 0:
            raise FrozenDriftError(post_drift)

        checker_ok = verdict.mode == "CHECKER_OK"
        if not checker_ok:
            agentic_notes.append(f"checker_error: {verdict.error}")
        elif len(verdict.evidence) > 0:
            agentic_notes.append(f"checker evidence: {'; '.join(verdict.evidence)}")

        # Evaluate the FULL frozen gate list (Codex R0 P2): 'checker_ok'
        # maps to the checker verdict; any unknown gate name fails closed —
        # a contract can only rank what its declared gates actually gated.
        agentic_gates: dict[str, bool] = {}
        for gate_name in contract.validity_gates:
            if gate_name == "checker_ok":
                agentic_gates[gate_name] = checker_ok
            else:
                agentic_gates[gate_name] = False
                agentic_notes.append(f"unknown agentic gate '{gate_name}' failed closed")
        if "checker_ok" not in agentic_gates:
            # The verdict is always recorded even if the frozen gate list
            # omitted it (visibility; it cannot make the score rankable).
            agentic_gates["checker_ok"] = checker_ok

        gates_pass = all(agentic_gates[g] is True for g in contract.validity_gates)
        agentic_valid = gates_pass and verdict.success is True
        agentic_metric_values = (
            {"checker_success": 1.0 if verdict.success is True else 0.0} if checker_ok else {}
        )
        agentic_score, agentic_breakdown = _assemble_score(
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
            wall_time=WallTimeRecord(value_sec=_load_wall_time(submission_dir)),
            ruler_id=ruler_id,
        )
    else:
        notes: list[str] = []
        computed = _load_submission_qoi(submission_dir, notes)
        wall_time = _load_wall_time(submission_dir)

        self_reported = sorted(set(computed) - set(case.outputs.qoi))
        if len(self_reported) > 0:
            notes.append(
                f"ignored self-reported fields {self_reported}: "
                "scoring metrics are recomputed, never trusted"
            )

        if "wall_time_sec" in contract.weights:
            notes.append("wall_time_sec is self-reported (weighted by explicit contract choice)")
            logger.warning("wall_time_sec is self-reported: it is weighted in this contract")

        gates = _evaluate_gates(contract, case, computed, wall_time, notes)
        valid = all(gates[g] is True for g in contract.validity_gates)

        metric_values: dict[str, float] = {}
        reference = _load_reference_qoi(case, case_dir)
        qoi_error = _recompute_qoi_error(case, reference, computed, notes)
        if qoi_error is not None:
            metric_values["qoi_error"] = qoi_error
        if wall_time is not None:
            metric_values["wall_time_sec"] = wall_time

        score, breakdown = _assemble_score(contract, valid, metric_values, notes)

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
