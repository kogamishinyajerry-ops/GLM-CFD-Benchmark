"""Shared judging policy — ANCHORED JUDGING MATERIAL for every domain.

This module exists to make the anchor surface exact (closing the root
cause behind Codex R2 P1 / R3 P2: policy code scattered through the
orchestration module forced anchoring all of ``scorer.py``, so unrelated
ledger/ranking edits drifted every contract). Everything here — and only
what is here — decides the (submission → gates/validity/score) mapping
shared across domains:

- what counts as a readable QoI / wall-time input (non-finite rejection),
- which reference drives scoring (held-out preference),
- how the aggregate QoI error is recomputed,
- how each declared validity gate is evaluated (unknown gates fail closed),
- how a checker verdict becomes gates/validity/metrics,
- how gate results and metric values assemble into a score (never
  fabricated: any unavailable weighted metric or non-finite candidate
  yields ``score=None``).

Its source sha256 is frozen into every contract as
``judge_source:judge_policy`` (:mod:`cfdb.agentbench.contract`): any byte
change here drifts every existing contract (exit 3) and forces a
deliberate re-anchor. KEEP NON-POLICY CODE OUT OF THIS FILE — ledger IO,
ranking, orchestration, and record schemas live in ``scorer.py``, which is
deliberately NOT anchored (its integrity is protected by the test suite
and git, the same trust root that protects the verification machinery
itself).

Domain-specific judge policy stays in the dedicated, separately anchored
modules: ``sandbox_scorer`` (coding) and ``checker_scorer`` (agentic).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from cfdb.schema import CaseSpec

if TYPE_CHECKING:
    from cfdb.agentbench.checker_scorer import CheckerVerdict
    from cfdb.agentbench.contract import ScoringContract

logger = logging.getLogger(__name__)

QOI_FILENAME = "qoi.json"
MANIFEST_FILENAME = "manifest.json"


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


def load_submission_qoi(submission_dir: Path, notes: list[str]) -> dict[str, float]:
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


def load_wall_time(submission_dir: Path) -> float | None:
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


def load_reference_qoi(case: CaseSpec, case_dir: Path) -> dict[str, float]:
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


def recompute_qoi_error(
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


def evaluate_gates(
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


def assemble_agentic(
    verdict: CheckerVerdict,
    validity_gates: list[str],
    notes: list[str],
) -> tuple[dict[str, bool], bool, dict[str, float]]:
    """Convert a checker verdict into gates, validity, and metric values.

    Extracted verbatim from the orchestration layer (it is pass/fail
    policy, so it must live in this anchored module): ``checker_ok`` maps
    to the checker verdict; any unknown gate name in the FULL frozen gate
    list fails closed (Codex R0 P2) — a contract can only rank what its
    declared gates actually gated. The verdict is always recorded even if
    the frozen gate list omitted ``checker_ok`` (visibility; it cannot make
    the score rankable).

    Args:
        verdict: Reduced checker outcome from ``checker_scorer``.
        validity_gates: The contract's frozen gate list.
        notes: Audit note sink (mutated in place).

    Returns:
        ``(gates, valid, metric_values)``. ``valid`` is True only when
        every frozen gate passed AND the checker judged success;
        ``metric_values`` carries ``checker_success`` only when the checker
        itself ran to a well-formed verdict (a broken ruler never yields a
        metric).
    """
    checker_ok = verdict.mode == "CHECKER_OK"
    if not checker_ok:
        notes.append(f"checker_error: {verdict.error}")
    elif len(verdict.evidence) > 0:
        notes.append(f"checker evidence: {'; '.join(verdict.evidence)}")

    gates: dict[str, bool] = {}
    for gate_name in validity_gates:
        if gate_name == "checker_ok":
            gates[gate_name] = checker_ok
        else:
            gates[gate_name] = False
            notes.append(f"unknown agentic gate '{gate_name}' failed closed")
    if "checker_ok" not in gates:
        gates["checker_ok"] = checker_ok

    gates_pass = all(gates[g] is True for g in validity_gates)
    valid = gates_pass and verdict.success is True
    metric_values = (
        {"checker_success": 1.0 if verdict.success is True else 0.0} if checker_ok else {}
    )
    return gates, valid, metric_values


def assemble_score(
    contract: ScoringContract,
    valid: bool,
    metric_values: dict[str, float],
    notes: list[str],
) -> tuple[float | None, dict[str, float]]:
    """Assemble the weighted score from recomputed metric values.

    Shared by every domain so the fail-closed assembly rule is defined
    exactly once: any weighted metric that is unavailable, or a non-finite
    candidate score, yields ``score=None`` — never fabricated.

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
