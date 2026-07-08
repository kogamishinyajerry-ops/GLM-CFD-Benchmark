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
the budget gate and the efficiency weight, and its absence fails closed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cfdb.agentbench.contract import FrozenDriftError, ScoringContract, verify_frozen
from cfdb.schema import CaseSpec

logger = logging.getLogger(__name__)

QOI_FILENAME = "qoi.json"
MANIFEST_FILENAME = "manifest.json"


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
        values[key] = float(value)
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
    return float(candidate)


def _load_reference_qoi(case: CaseSpec, case_dir: Path) -> dict[str, float]:
    """Load reference QoI values from the case (inline values preferred).

    Args:
        case: Case spec.
        case_dir: Case directory for resolving relative reference paths.

    Returns:
        Reference QoI values; empty when the case has no usable reference.
    """
    if case.reference is None:
        return {}
    if case.reference.qoi_values is not None:
        return dict(case.reference.qoi_values)
    for key in ("qoi", "qoi_values"):
        if key in case.reference.files:
            raw = _load_json_dict(case_dir / case.reference.files[key])
            if raw is None:
                return {}
            try:
                return {k: float(v) for k, v in raw.items()}  # type: ignore[arg-type]
            except (TypeError, ValueError) as e:
                logger.warning("invalid reference QoI file for %s: %s", case.id, e)
                return {}
    return {}


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
        terms.append(diff if ref_val == 0 else diff / abs(ref_val))
    if len(terms) == 0:
        notes.append("qoi_error: no recomputable QoI (fail-closed: metric unavailable)")
        return None
    return sum(terms) / len(terms)


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
        else:
            ok = False
            notes.append(f"unknown validity gate '{gate}' (fail-closed: cannot pass)")
        results[gate] = ok
    return results


def score_submission(
    contract: ScoringContract,
    case: CaseSpec,
    case_dir: Path,
    submission_dir: Path,
    ledger_path: Path | None = None,
) -> SubmissionScore:
    """Score one agent submission against a frozen contract.

    Args:
        contract: Frozen scoring contract for the case.
        case: Case spec (must match ``contract.case_id``).
        case_dir: Case directory (frozen paths and reference resolve here).
        submission_dir: Directory holding ``qoi.json`` (+ optional
            ``manifest.json``).
        ledger_path: When given, the score is appended to this JSONL ledger.

    Returns:
        The submission score. Invalid or unscorable submissions carry
        ``score=None`` and are excluded from :func:`ranked`.

    Raises:
        FrozenDriftError: If any frozen path drifted (ruler changed —
            scoring is refused before anything else happens).
        ValueError: If ``case.id`` does not match the contract.
    """
    if case.id != contract.case_id:
        raise ValueError(f"case '{case.id}' does not match contract case '{contract.case_id}'")

    drifted = verify_frozen(contract, case_dir)
    if len(drifted) > 0:
        raise FrozenDriftError(drifted)

    notes: list[str] = []
    computed = _load_submission_qoi(submission_dir, notes)
    wall_time = _load_wall_time(submission_dir)

    self_reported = sorted(set(computed) - set(case.outputs.qoi))
    if len(self_reported) > 0:
        notes.append(
            f"ignored self-reported fields {self_reported}: "
            "scoring metrics are recomputed, never trusted"
        )

    gates = _evaluate_gates(contract, case, computed, wall_time, notes)
    valid = all(gates[g] is True for g in contract.validity_gates)

    metric_values: dict[str, float] = {}
    reference = _load_reference_qoi(case, case_dir)
    qoi_error = _recompute_qoi_error(case, reference, computed, notes)
    if qoi_error is not None:
        metric_values["qoi_error"] = qoi_error
    if wall_time is not None:
        metric_values["wall_time_sec"] = wall_time

    score: float | None = None
    breakdown: dict[str, float] = {}
    if valid is True:
        missing_metrics = sorted(m for m in contract.weights if m not in metric_values)
        if len(missing_metrics) > 0:
            notes.append(
                f"cannot compute score: metrics {missing_metrics} unavailable "
                "(fail-closed: score=None)"
            )
        else:
            breakdown = {m: w * metric_values[m] for m, w in contract.weights.items()}
            score = sum(breakdown.values())
    else:
        notes.append("submission invalid: no score assigned (score=None)")

    result = SubmissionScore(
        submission_id=submission_dir.name,
        valid=valid,
        score=score,
        breakdown=breakdown,
        gates=gates,
        scored_at=datetime.now(timezone.utc).isoformat(),
        notes=notes,
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
    for lineno, line in enumerate(
        ledger_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.strip() == "":
            continue
        try:
            entries.append(SubmissionScore.model_validate_json(line))
        except ValidationError as e:
            raise ValueError(f"corrupt ledger line {lineno} in {ledger_path}: {e}") from e
    return entries


def ranked(entries: list[SubmissionScore]) -> list[SubmissionScore]:
    """Return only rankable entries, best score first.

    A submission ranks only if ``valid is True`` **and** it carries a real
    score — invalid samples never enter the ranking, even if a ledger line
    was forged to carry a score.

    Args:
        entries: Ledger entries.

    Returns:
        Valid, scored entries sorted by score descending.
    """
    rankable = [e for e in entries if e.valid is True and e.score is not None]
    return sorted(rankable, key=lambda e: e.score or 0.0, reverse=True)
