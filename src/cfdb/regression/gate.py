"""Regression gate (P4-D): recompute-everything verdict against a baseline.

Trust model: the integrity anchor covers the baseline side; the candidate's
metrics.json is trusted as produced by the local pipeline. ``evaluate()``
re-reads the candidate run's manifest.json / metrics.json from disk (so no
stored verdict is ever reused), re-hashes the baseline run's metrics.json
against the anchored SHA-256, and cross-checks the anchored QoI values in
baselines.json against the re-read baseline file. Any baseline-side mismatch
is TAMPERED; a missing baseline is NO_BASELINE and is never PASS; an
unreadable or invalid candidate is INVALID_RUN, never a crash.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cfdb.regression.baseline import BaselineEntry, BaselineStore, baseline_key, sha256_of_file
from cfdb.schema import MetricsResult

logger = logging.getLogger(__name__)


class GateVerdict(BaseModel):
    """Result of one regression-gate evaluation."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "REGRESSION", "NO_BASELINE", "TAMPERED", "INVALID_RUN"]
    """Five-valued verdict. NO_BASELINE is explicitly not PASS (fail-closed)."""

    deltas: dict[str, float] = Field(default_factory=dict)
    """Per-QoI (new_error - baseline_error); positive means worse.

    Relative-channel deltas are keyed by the QoI name; absolute-channel
    (zero-reference) deltas are keyed ``<qoi> (abs)``."""

    reasons: list[str] = Field(default_factory=list)
    """Human-readable justifications for the verdict."""


def _check_baseline_integrity(
    store: BaselineStore, entry: BaselineEntry
) -> tuple[bool, list[str], MetricsResult | None]:
    """Verify the baseline anchor against the on-disk run artifacts.

    Checks, in order (all fail-closed):
      1. The baseline run's metrics.json still exists.
      2. Its SHA-256 matches the anchored ``metrics_sha256``.
      3. It still parses under the current MetricsResult schema.
      4. The QoI values/errors anchored in baselines.json equal the values
         re-read from that file (an edit of baselines.json that bypasses the
         hash is caught here). The absolute-error cross-check is skipped for
         legacy entries whose anchored dict is empty; this is safe because
         the gate compares against the re-read (hash-anchored) file, never
         against the baselines.json copy.

    Args:
        store: Baseline store (provides runs_root path resolution).
        entry: Baseline entry to verify.

    Returns:
        Tuple of (intact, reasons, reread_metrics). ``intact`` is True only
        when every check passes; ``reread_metrics`` is None when the file is
        missing or unreadable.
    """
    reasons: list[str] = []
    metrics_path = store.run_metrics_path(entry.run_id)
    if not metrics_path.exists():
        reasons.append(
            f"baseline run '{entry.run_id}' metrics.json is missing at {metrics_path}"
        )
        return False, reasons, None

    actual_sha = sha256_of_file(metrics_path)
    hash_matches = actual_sha == entry.metrics_sha256
    if hash_matches is False:
        reasons.append(
            f"baseline run '{entry.run_id}' metrics.json hash mismatch: "
            f"anchored {entry.metrics_sha256[:12]}..., found {actual_sha[:12]}..."
        )
        return False, reasons, None

    try:
        reread = MetricsResult.model_validate_json(
            metrics_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        # Hash matched but the file no longer parses (e.g. schema hardening
        # after promotion). Fail closed: an unparseable anchor is TAMPERED.
        reasons.append(
            f"baseline run '{entry.run_id}' metrics.json does not parse under "
            f"the current schema (fail-closed): {exc}"
        )
        return False, reasons, None

    errors_match = entry.qoi_relative_errors == reread.qoi_relative_errors
    values_match = entry.qoi_values == dict(reread.qoi_computed_values or {})
    if errors_match is False:
        reasons.append(
            "anchored qoi_relative_errors in baselines.json do not match the "
            f"re-read metrics.json of run '{entry.run_id}'"
        )
    if values_match is False:
        reasons.append(
            "anchored qoi_values in baselines.json do not match the "
            f"re-read metrics.json of run '{entry.run_id}'"
        )
    if len(entry.qoi_absolute_errors) > 0:
        abs_match = entry.qoi_absolute_errors == reread.qoi_absolute_errors
    else:
        # Legacy entry promoted before the absolute channel was anchored.
        abs_match = True
    if abs_match is False:
        reasons.append(
            "anchored qoi_absolute_errors in baselines.json do not match the "
            f"re-read metrics.json of run '{entry.run_id}'"
        )
    intact = (errors_match is True) and (values_match is True) and (abs_match is True)
    return intact, reasons, reread


def _gate_channel(
    channel: str,
    base_errors: dict[str, float],
    cand_errors: dict[str, float],
    band_of: Callable[[float], float],
    run_id: str,
    deltas: dict[str, float],
    reasons: list[str],
    delta_suffix: str = "",
) -> bool:
    """Compare one error channel (relative or absolute) QoI by QoI.

    Fail-closed rules:
      - A baseline QoI missing from the candidate is a regression.
      - A non-finite error on either side is a regression: a NaN/Inf
        comparison would silently evaluate False and slip through the
        band check, so finiteness is asserted before any comparison.

    Args:
        channel: Channel label used in reasons ("relative" / "absolute").
        base_errors: Anchored errors re-read from the baseline run file.
        cand_errors: Errors re-read from the candidate run file.
        band_of: Tolerance band as a function of the baseline error.
        run_id: Candidate run id (for messages).
        deltas: Output dict of per-QoI deltas (mutated in place).
        reasons: Output list of justifications (mutated in place).
        delta_suffix: Suffix appended to delta keys for this channel.

    Returns:
        True when at least one QoI in this channel regressed.
    """
    regressed = False
    for qoi, base_err in base_errors.items():
        if qoi not in cand_errors:
            regressed = True
            reasons.append(
                f"{channel} QoI '{qoi}' is anchored in the baseline but "
                f"missing from run '{run_id}' (fail-closed)"
            )
            continue
        new_err = cand_errors[qoi]
        finite = math.isfinite(new_err) and math.isfinite(base_err)
        if finite is False:
            regressed = True
            reasons.append(
                f"{channel} QoI '{qoi}': non-finite error never passes "
                f"(candidate={new_err!r}, baseline={base_err!r})"
            )
            continue
        deltas[f"{qoi}{delta_suffix}"] = new_err - base_err
        band = band_of(base_err)
        exceeded = new_err > base_err + band
        if exceeded is True:
            regressed = True
            reasons.append(
                f"{channel} QoI '{qoi}' regressed: error {new_err:.6g} > "
                f"baseline {base_err:.6g} + band {band:.6g}"
            )
    return regressed


def evaluate(run_id: str, store: BaselineStore) -> GateVerdict:
    """Evaluate a candidate run against its promoted baseline.

    All inputs are re-read from disk; no stored verdict is reused:

      1. Re-read the candidate's manifest.json / metrics.json. Missing,
         truncated, or schema-invalid files, a non-success run status, or a
         non-pass recomputed overall_status -> INVALID_RUN (never a crash).
      2. Look up the baseline for (case_id, solver). Absent -> NO_BASELINE
         (never PASS).
      3. Verify baseline integrity (hash + anchored-value cross-check)
         -> TAMPERED on any mismatch.
      4. Per QoI on the relative channel, using the re-read baseline errors:
         ``new_err > base_err + max(margin.absolute, margin.relative * base_err)``
         -> REGRESSION. Per QoI on the absolute channel (zero-reference
         QoIs): ``new_err > base_err + margin.absolute`` -> REGRESSION.
         A baseline QoI missing from the candidate or a non-finite error on
         either side is also a REGRESSION (fail-closed: absence of evidence
         and non-finite evidence never pass).

    Args:
        run_id: Candidate run identifier.
        store: Baseline store providing baselines.json and runs_root.

    Returns:
        GateVerdict with per-QoI deltas and reasons.

    Raises:
        BaselineFileError: If baselines.json itself is corrupt (propagated
            from ``store.load()`` so the CLI can fail closed with a
            dedicated exit code).
    """
    # Step 1: candidate run must be readable and structurally valid.
    # ValueError covers pydantic ValidationError and json.JSONDecodeError;
    # OSError covers FileNotFoundError and read failures.
    try:
        manifest, metrics = store.read_run(run_id)
    except (OSError, ValueError) as exc:
        return GateVerdict(
            verdict="INVALID_RUN",
            reasons=[f"candidate run '{run_id}' is unreadable or invalid: {exc}"],
        )

    if manifest.status != "success":
        return GateVerdict(
            verdict="INVALID_RUN",
            reasons=[f"run '{run_id}' has status='{manifest.status}' (expected 'success')"],
        )
    if metrics.overall_status != "pass":
        return GateVerdict(
            verdict="INVALID_RUN",
            reasons=[
                f"run '{run_id}' has overall_status='{metrics.overall_status}' "
                "(expected 'pass')"
            ],
        )

    # Step 2: a missing baseline is never a pass. A corrupt baselines.json
    # raises BaselineFileError (fail-closed, handled by the CLI).
    data = store.load()
    entry = data.baselines.get(baseline_key(manifest.case_id, manifest.solver))
    if entry is None:
        return GateVerdict(
            verdict="NO_BASELINE",
            reasons=[
                f"no baseline promoted for case '{manifest.case_id}' "
                f"solver '{manifest.solver}' (NO_BASELINE is not PASS)"
            ],
        )

    # Step 3: tamper detection (fail-closed).
    intact, tamper_reasons, baseline_metrics = _check_baseline_integrity(store, entry)
    if intact is False or baseline_metrics is None:
        return GateVerdict(verdict="TAMPERED", deltas={}, reasons=tamper_reasons)

    # Step 4: regression math against the *re-read* baseline errors, on both
    # the relative channel and the absolute (zero-reference) channel.
    margin = data.regression_margin
    deltas: dict[str, float] = {}
    reasons: list[str] = []
    regressed_rel = _gate_channel(
        "relative",
        baseline_metrics.qoi_relative_errors,
        metrics.qoi_relative_errors,
        lambda base_err: max(margin.absolute, margin.relative * base_err),
        run_id,
        deltas,
        reasons,
    )
    regressed_abs = _gate_channel(
        "absolute",
        baseline_metrics.qoi_absolute_errors,
        metrics.qoi_absolute_errors,
        lambda _base_err: margin.absolute,
        run_id,
        deltas,
        reasons,
        delta_suffix=" (abs)",
    )

    if (regressed_rel is True) or (regressed_abs is True):
        return GateVerdict(verdict="REGRESSION", deltas=deltas, reasons=reasons)

    reasons.append(
        f"all {len(deltas)} anchored QoI comparison(s) within tolerance "
        f"(absolute={margin.absolute}, relative={margin.relative})"
    )
    logger.info("gate PASS for run %s against baseline %s", run_id, entry.run_id)
    return GateVerdict(verdict="PASS", deltas=deltas, reasons=reasons)
