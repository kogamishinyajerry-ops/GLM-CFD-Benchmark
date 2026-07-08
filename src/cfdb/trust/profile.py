"""TrustProfile — five-dimension capability profile (P4-B, VVUQ).

Builds a per-(case, solver) capability profile from recorded runs
(``runs/<run_id>/manifest.json`` + ``metrics.json``), read through the
storage Repository abstraction. No new storage is introduced.

Honest-floor rules (Architecture v4.0 §0 / §3):

- ``DimensionScore.score = None`` means "insufficient data" — a missing
  score is never fabricated as 0 (which would fake "measured as bad").
- **No aggregate score exists, by design**: a single number invites
  leaderboard thinking, which the trust platform explicitly rejects.
  The profile is five dimensions + an honesty banner, nothing more.
- Every number is derived from recomputed artifacts persisted by the
  metrics engine; self-reported values are never accepted.
- The ``honesty`` banner value is supplied by the caller (CLI layer
  composes it from the provenance module); this module deliberately does
  not import provenance (v4 modules do not import each other).
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cfdb.schema import CaseSpec, MetricsResult, RunManifest
from cfdb.storage.base import ResultRepository

logger = logging.getLogger(__name__)

# Mirror of provenance.HonestyLevel values (kept as data, not an import:
# v4 pillar modules are composed at the CLI layer and must not import
# each other). An unknown level is rejected fail-closed at validation.
HONESTY_LEVELS: frozenset[str] = frozenset(
    {
        "REAL",
        "ANALYTIC",
        "MANUFACTURED",
        "PREVIOUS_RUN",
        "SURROGATE",
        "DECLARED-NOT-VERIFIED",
    }
)

# Reproducibility mapping scale: a worst-QoI coefficient of variation of
# REPRO_CV_SCALE (10%) or more maps to score 0.0; CV 0 maps to 1.0.
REPRO_CV_SCALE: float = 0.10

# Efficiency penalty for budget violations: the wall-time score is reduced
# by BUDGET_EXCEEDED_PENALTY * (fraction of successful runs whose recomputed
# metrics flagged budget_exceeded), then clamped back into [0, 1].
BUDGET_EXCEEDED_PENALTY: float = 0.5

#: Canonical dimension order (also the radar axis order).
DIMENSION_NAMES: tuple[str, ...] = (
    "accuracy",
    "robustness",
    "efficiency",
    "completeness",
    "reproducibility",
)


class DimensionScore(BaseModel):
    """Score of one trust dimension.

    ``score is None`` means the dimension could not be computed from the
    available run data. This is an explicit, honest degradation — it is
    never rendered or aggregated as 0.
    """

    model_config = ConfigDict(extra="forbid")

    score: float | None = Field(None, ge=0.0, le=1.0)
    """Score in [0, 1]; None = insufficient data (never a fabricated 0)."""

    evidence: list[str] = Field(default_factory=list)
    """Human-readable evidence lines (with the actual numbers)."""


class TrustProfile(BaseModel):
    """Five-dimension capability profile for one (case, solver) pair.

    Deliberately has **no aggregate score** field; see module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = ""
    """Case identifier the profile describes."""

    solver: str = ""
    """Solver name the profile describes."""

    n_runs: int = Field(0, ge=0)
    """Number of recorded (non-dry-run) runs considered."""

    honesty: str = "DECLARED-NOT-VERIFIED"
    """Provenance honesty level banner (supplied by the CLI layer).

    Defaults fail-closed to DECLARED-NOT-VERIFIED when not supplied."""

    accuracy: DimensionScore = Field(default_factory=DimensionScore)
    """1 - clamp(mean_err / tolerance) per QoI over the relative and
    absolute error channels (equal weight); worst QoI wins."""

    robustness: DimensionScore = Field(default_factory=DimensionScore)
    """Successful runs / total runs."""

    efficiency: DimensionScore = Field(default_factory=DimensionScore)
    """1 - clamp(mean wall time / runtime budget), minus a disclosed
    penalty for runs flagged budget_exceeded; no budget -> None."""

    completeness: DimensionScore = Field(default_factory=DimensionScore)
    """Expected outputs (fields/curves/qoi) delivery rate, latest run."""

    reproducibility: DimensionScore = Field(default_factory=DimensionScore)
    """QoI coefficient-of-variation over >=2 successful runs; <2 -> None."""

    notes: list[str] = Field(default_factory=list)
    """General notes (excluded runs, unreadable metrics, ...)."""

    @field_validator("honesty")
    @classmethod
    def _validate_honesty(cls, value: str) -> str:
        """Reject unknown honesty levels fail-closed.

        Args:
            value: Candidate honesty level string.

        Returns:
            The validated honesty level.

        Raises:
            ValueError: If the value is not a known honesty level.
        """
        if value not in HONESTY_LEVELS:
            raise ValueError(
                f"unknown honesty level {value!r}; expected one of "
                f"{sorted(HONESTY_LEVELS)}"
            )
        return value

    def dimension(self, name: str) -> DimensionScore:
        """Return a dimension score by canonical name.

        Args:
            name: One of DIMENSION_NAMES.

        Returns:
            The corresponding DimensionScore.

        Raises:
            KeyError: If the name is not a known dimension.
        """
        if name not in DIMENSION_NAMES:
            raise KeyError(f"unknown dimension {name!r}")
        return getattr(self, name)


def _clamp01(value: float) -> float:
    """Clamp a value into [0, 1]."""
    return max(0.0, min(1.0, value))


def _score_accuracy(
    case: CaseSpec, metrics_by_run: dict[str, MetricsResult], success_ids: list[str]
) -> DimensionScore:
    """Compute accuracy from the relative and absolute QoI error channels.

    For every QoI with a relative tolerance the per-QoI score is
    ``1 - clamp(mean_rel_err / tolerance)``; for every QoI with an
    absolute tolerance (the zero-reference channel recorded in
    ``metrics.qoi_absolute_errors``) it is
    ``1 - clamp(mean_abs_err / tolerance)``. Both channels carry equal
    weight: all per-QoI scores are pooled and the worst one wins. Every
    evidence line names its channel.

    A non-positive tolerance cannot be scored (division by zero); that
    QoI is skipped with an explicit evidence note — the dimension
    degrades to None rather than crashing or fabricating a value.

    Args:
        case: Case specification (source of QoI tolerances).
        metrics_by_run: Loaded metrics keyed by run_id.
        success_ids: run_ids of successful runs.

    Returns:
        DimensionScore; None score when no tolerance or no error data.
    """
    rel_tolerances = case.metrics.qoi_relative_tolerance
    abs_tolerances = case.metrics.qoi_absolute_tolerance
    if not rel_tolerances and not abs_tolerances:
        return DimensionScore(
            evidence=["no QoI relative or absolute tolerances defined for this case"]
        )

    evidence: list[str] = []
    per_qoi_scores: list[float] = []

    channels: list[tuple[str, dict[str, float], dict[str, dict[str, float]]]] = [
        (
            "relative",
            rel_tolerances,
            {rid: metrics_by_run[rid].qoi_relative_errors for rid in metrics_by_run},
        ),
        (
            "absolute",
            abs_tolerances,
            {rid: metrics_by_run[rid].qoi_absolute_errors for rid in metrics_by_run},
        ),
    ]
    for channel, tolerances, errors_by_run in channels:
        for qoi, tol in sorted(tolerances.items()):
            if tol <= 0.0:
                evidence.append(
                    f"{qoi} ({channel} channel): non-positive tolerance {tol!r}, "
                    "cannot be scored, skipped"
                )
                continue
            errors = [
                errors_by_run[rid][qoi]
                for rid in success_ids
                if rid in errors_by_run and qoi in errors_by_run[rid]
            ]
            if not errors:
                evidence.append(
                    f"{qoi} ({channel} channel): no recomputed {channel} error "
                    "available in successful runs"
                )
                continue
            mean_err = statistics.fmean(errors)
            score = 1.0 - _clamp01(mean_err / tol)
            per_qoi_scores.append(score)
            evidence.append(
                f"{qoi} ({channel} channel): mean_err={mean_err:.4g} over "
                f"{len(errors)} run(s), tolerance={tol:.4g} -> {score:.3f}"
            )

    if not per_qoi_scores:
        return DimensionScore(evidence=evidence)
    worst = min(per_qoi_scores)
    evidence.append(f"worst QoI score taken: {worst:.3f}")
    return DimensionScore(score=worst, evidence=evidence)


def _score_robustness(manifests: list[RunManifest]) -> DimensionScore:
    """Compute robustness: successful runs / total runs.

    Args:
        manifests: All considered (non-dry-run) manifests.

    Returns:
        DimensionScore; None score when there are no runs at all.
    """
    if not manifests:
        return DimensionScore(evidence=["no runs recorded"])
    n_success = sum(1 for m in manifests if m.status == "success")
    score = n_success / len(manifests)
    return DimensionScore(
        score=score,
        evidence=[f"{n_success}/{len(manifests)} runs succeeded"],
    )


def _score_efficiency(
    case: CaseSpec,
    successes: list[RunManifest],
    metrics_by_run: dict[str, MetricsResult],
) -> DimensionScore:
    """Compute efficiency from wall time and recorded budget violations.

    Base score is ``1 - clamp(mean wall time / runtime budget)``. It is
    then reduced by ``BUDGET_EXCEEDED_PENALTY * f`` where ``f`` is the
    fraction of successful runs whose recomputed metrics flagged
    ``budget_exceeded`` (Stage-A field), clamped back into [0, 1]. Any
    such violation is disclosed as an explicit evidence line. Runs whose
    metrics are unreadable never count as exceeded (explicit degradation,
    never a fabricated violation).

    Args:
        case: Case specification (source of the runtime budget).
        successes: Successful run manifests.
        metrics_by_run: Loaded metrics keyed by run_id (source of the
            ``budget_exceeded`` flag).

    Returns:
        DimensionScore; None score when no budget or no successful runs.
    """
    budget = case.budget.max_runtime_sec
    if budget is None:
        return DimensionScore(evidence=["no runtime budget (budget.max_runtime_sec) defined"])
    if not successes:
        return DimensionScore(evidence=["no successful runs to measure wall time on"])
    mean_wall = statistics.fmean(m.timing.wall_time_sec for m in successes)
    score = 1.0 - _clamp01(mean_wall / budget)
    evidence = [
        f"mean wall time {mean_wall:.2f}s over {len(successes)} successful run(s), "
        f"budget {budget}s -> {score:.3f}"
    ]
    exceeded_ids = [
        m.run_id
        for m in successes
        if m.run_id in metrics_by_run
        and metrics_by_run[m.run_id].budget_exceeded is True
    ]
    if exceeded_ids:
        fraction = len(exceeded_ids) / len(successes)
        penalty = BUDGET_EXCEEDED_PENALTY * fraction
        score = _clamp01(score - penalty)
        evidence.append(
            f"{len(exceeded_ids)}/{len(successes)} successful run(s) flagged "
            f"budget_exceeded ({', '.join(exceeded_ids)}) -> penalty "
            f"{penalty:.3f}, score {score:.3f}"
        )
    return DimensionScore(score=score, evidence=evidence)


def _artifact_delivers(name: str, artifact_keys: Iterable[str]) -> bool:
    """Check whether any manifest artifact key delivers a named output.

    Manifest artifact keys are run-relative POSIX paths as recorded by
    the adapters' ``collect_outputs()`` (e.g. the OpenFOAM field ``U``
    is stored under keys like ``case/1/U``, and a curve ``residuals``
    may be stored as ``case/postProcessing/residuals.csv``). An output
    counts as delivered when the final path segment equals the declared
    name, either exactly or after stripping one file extension.

    Args:
        name: Declared output name (field or curve) from the case spec.
        artifact_keys: Manifest artifact keys of the run.

    Returns:
        True when at least one artifact key matches the name.
    """
    for key in artifact_keys:
        leaf = key.rsplit("/", 1)[-1]
        if leaf == name:
            return True
        stem, sep, _ = leaf.rpartition(".")
        if sep and stem == name:
            return True
    return False


def _score_completeness(
    case: CaseSpec, latest: RunManifest | None, latest_metrics: MetricsResult | None
) -> DimensionScore:
    """Compute completeness: expected outputs delivery rate on latest run.

    Expected outputs are the case's declared fields + curves + qoi. Field
    and curve delivery is checked against the manifest artifact keys,
    which are run-relative paths (see :func:`_artifact_delivers`); QoI
    delivery is checked against recomputed QoI values in metrics.json
    (never against self-reported values).

    Args:
        case: Case specification (source of expected outputs).
        latest: Latest considered run manifest, or None if no runs.
        latest_metrics: Metrics of the latest run, or None if unreadable.

    Returns:
        DimensionScore; None score when no runs or no expected outputs.
    """
    if latest is None:
        return DimensionScore(evidence=["no runs recorded"])

    expected: list[tuple[str, str]] = (
        [("field", name) for name in case.outputs.fields]
        + [("curve", name) for name in case.outputs.curves]
        + [("qoi", name) for name in case.outputs.qoi]
    )
    if not expected:
        return DimensionScore(evidence=["case declares no expected outputs"])

    computed_qoi = (
        latest_metrics.qoi_computed_values or {} if latest_metrics is not None else {}
    )
    evidence: list[str] = [f"checked latest run {latest.run_id}"]
    n_present = 0
    for kind, name in expected:
        present = (
            name in computed_qoi
            if kind == "qoi"
            else _artifact_delivers(name, latest.artifacts)
        )
        if present:
            n_present += 1
        else:
            evidence.append(f"missing {kind} '{name}'")
    score = n_present / len(expected)
    evidence.append(f"{n_present}/{len(expected)} expected outputs delivered")
    return DimensionScore(score=score, evidence=evidence)


def _score_reproducibility(
    metrics_by_run: dict[str, MetricsResult], success_ids: list[str]
) -> DimensionScore:
    """Compute reproducibility from QoI coefficient of variation.

    Requires >=2 successful runs with recomputed QoI values. The worst
    per-QoI CV (stdev / |mean|) is mapped to a score via
    ``1 - clamp(cv / REPRO_CV_SCALE)``.

    Args:
        metrics_by_run: Loaded metrics keyed by run_id.
        success_ids: run_ids of successful runs.

    Returns:
        DimensionScore; None score with fewer than 2 usable runs.
    """
    values_by_qoi: dict[str, list[float]] = {}
    usable_runs = 0
    for rid in success_ids:
        metrics = metrics_by_run.get(rid)
        if metrics is None or not metrics.qoi_computed_values:
            continue
        usable_runs += 1
        for qoi, value in metrics.qoi_computed_values.items():
            values_by_qoi.setdefault(qoi, []).append(value)

    if usable_runs < 2:
        return DimensionScore(
            evidence=[
                f"only {usable_runs} successful run(s) with recomputed QoI values; "
                "need >=2 to assess reproducibility"
            ]
        )

    evidence: list[str] = []
    worst_cv: float | None = None
    for qoi, values in sorted(values_by_qoi.items()):
        if len(values) < 2:
            evidence.append(f"{qoi}: present in only {len(values)} run(s), skipped")
            continue
        mean = statistics.fmean(values)
        if mean == 0.0:
            evidence.append(f"{qoi}: mean is 0, CV undefined, skipped")
            continue
        cv = statistics.stdev(values) / abs(mean)
        evidence.append(f"{qoi}: CV={cv:.4g} over {len(values)} run(s)")
        worst_cv = cv if worst_cv is None else max(worst_cv, cv)

    if worst_cv is None:
        evidence.append("no QoI observed in >=2 runs with a nonzero mean")
        return DimensionScore(evidence=evidence)
    score = 1.0 - _clamp01(worst_cv / REPRO_CV_SCALE)
    evidence.append(f"worst CV {worst_cv:.4g} vs scale {REPRO_CV_SCALE} -> {score:.3f}")
    return DimensionScore(score=score, evidence=evidence)


def build_profile(
    case: CaseSpec,
    solver: str,
    repository: ResultRepository,
    honesty: str = "DECLARED-NOT-VERIFIED",
) -> TrustProfile:
    """Build the TrustProfile for one (case, solver) pair from runs/.

    Args:
        case: Case specification (tolerances, budget, expected outputs).
        solver: Solver name to profile.
        repository: Result repository over the runs/ directory.
        honesty: Provenance honesty level string, composed by the CLI
            layer. Defaults fail-closed to DECLARED-NOT-VERIFIED.

    Returns:
        TrustProfile with per-dimension scores (None = insufficient data).
    """
    notes: list[str] = []

    all_manifests = [m for m in repository.list_runs(case_id=case.id) if m.solver == solver]
    manifests = [m for m in all_manifests if m.status != "dry_run"]
    n_dry = len(all_manifests) - len(manifests)
    if n_dry:
        notes.append(f"excluded {n_dry} dry_run run(s) from the profile")

    # Load metrics per run; a run whose metrics.json cannot be read is
    # kept for status-based dimensions but excluded from metric-based
    # dimensions (explicit degradation, never a fabricated value).
    metrics_by_run: dict[str, MetricsResult] = {}
    for manifest in manifests:
        try:
            _, metrics = repository.load_run(manifest.run_id)
        except Exception as exc:  # noqa: BLE001 — fail-closed per-run degradation
            logger.warning("metrics unreadable for run %s: %s", manifest.run_id, exc)
            notes.append(
                f"run {manifest.run_id}: metrics unreadable, excluded from "
                "metric-based dimensions"
            )
            continue
        metrics_by_run[manifest.run_id] = metrics

    successes = [m for m in manifests if m.status == "success"]
    success_ids = [m.run_id for m in successes]
    latest = manifests[0] if manifests else None  # list_runs is newest-first
    latest_metrics = metrics_by_run.get(latest.run_id) if latest is not None else None

    return TrustProfile(
        case_id=case.id,
        solver=solver,
        n_runs=len(manifests),
        honesty=honesty,
        accuracy=_score_accuracy(case, metrics_by_run, success_ids),
        robustness=_score_robustness(manifests),
        efficiency=_score_efficiency(case, successes, metrics_by_run),
        completeness=_score_completeness(case, latest, latest_metrics),
        reproducibility=_score_reproducibility(metrics_by_run, success_ids),
        notes=notes,
    )
