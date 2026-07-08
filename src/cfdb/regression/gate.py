"""Regression gate (P4-D): recompute-everything verdict against a baseline.

The gate never trusts self-reported values. ``evaluate()`` re-reads the
candidate run's metrics.json from disk, re-hashes the baseline run's
metrics.json against the anchored SHA-256, and cross-checks the anchored
QoI values in baselines.json against the re-read baseline file. Any
mismatch is TAMPERED; a missing baseline is NO_BASELINE and is never PASS.
"""

from __future__ import annotations

import logging
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
    """Per-QoI (new_error - baseline_error); positive means worse."""

    reasons: list[str] = Field(default_factory=list)
    """Human-readable justifications for the verdict."""


def _check_baseline_integrity(
    store: BaselineStore, entry: BaselineEntry
) -> tuple[bool, list[str], MetricsResult | None]:
    """Verify the baseline anchor against the on-disk run artifacts.

    Checks, in order (all fail-closed):
      1. The baseline run's metrics.json still exists.
      2. Its SHA-256 matches the anchored ``metrics_sha256``.
      3. The QoI values/errors anchored in baselines.json equal the values
         re-read from that file (an edit of baselines.json that bypasses the
         hash is caught here).

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

    reread = MetricsResult.model_validate_json(metrics_path.read_text(encoding="utf-8"))
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
    intact = (errors_match is True) and (values_match is True)
    return intact, reasons, reread


def evaluate(run_id: str, store: BaselineStore) -> GateVerdict:
    """Evaluate a candidate run against its promoted baseline.

    Everything is recomputed from disk; no self-reported verdict is trusted:

      1. Re-read the candidate's manifest.json / metrics.json. Missing files,
         a non-success run status, or a non-pass recomputed overall_status
         -> INVALID_RUN.
      2. Look up the baseline for (case_id, solver). Absent -> NO_BASELINE
         (never PASS).
      3. Verify baseline integrity (hash + anchored-value cross-check)
         -> TAMPERED on any mismatch.
      4. Per QoI, using the re-read baseline errors:
         ``new_err > base_err + max(margin.absolute, margin.relative * base_err)``
         -> REGRESSION. A baseline QoI missing from the candidate is also a
         REGRESSION (fail-closed: absence of evidence never passes).

    Args:
        run_id: Candidate run identifier.
        store: Baseline store providing baselines.json and runs_root.

    Returns:
        GateVerdict with per-QoI deltas and reasons.
    """
    # Step 1: candidate run must be readable and structurally valid.
    try:
        manifest, metrics = store.read_run(run_id)
    except FileNotFoundError as exc:
        return GateVerdict(verdict="INVALID_RUN", reasons=[str(exc)])

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

    # Step 2: a missing baseline is never a pass.
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

    # Step 4: regression math against the *re-read* baseline errors.
    margin = data.regression_margin
    deltas: dict[str, float] = {}
    reasons: list[str] = []
    regressed = False
    for qoi, base_err in baseline_metrics.qoi_relative_errors.items():
        if qoi not in metrics.qoi_relative_errors:
            regressed = True
            reasons.append(
                f"QoI '{qoi}' is anchored in the baseline but missing from "
                f"run '{run_id}' (fail-closed)"
            )
            continue
        new_err = metrics.qoi_relative_errors[qoi]
        deltas[qoi] = new_err - base_err
        band = max(margin.absolute, margin.relative * base_err)
        exceeded = new_err > base_err + band
        if exceeded is True:
            regressed = True
            reasons.append(
                f"QoI '{qoi}' regressed: error {new_err:.6g} > baseline "
                f"{base_err:.6g} + band {band:.6g}"
            )

    if regressed is True:
        return GateVerdict(verdict="REGRESSION", deltas=deltas, reasons=reasons)

    reasons.append(
        f"all {len(deltas)} baseline QoI(s) within tolerance band "
        f"(absolute={margin.absolute}, relative={margin.relative})"
    )
    logger.info("gate PASS for run %s against baseline %s", run_id, entry.run_id)
    return GateVerdict(verdict="PASS", deltas=deltas, reasons=reasons)
