"""MetricsEngine: orchestrates QoI, curve, and performance metric computation."""

from __future__ import annotations

import csv
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.metrics.curves import compute_curve_l2
from cfdb.metrics.performance import check_budget
from cfdb.schema import CaseSpec, MetricsResult, TimingSpec

logger = logging.getLogger(__name__)


class MetricsEngine:
    """Metrics computation engine.

    Orchestrates QoI relative error, curve L2 norm, and budget checks.
    Determines overall pass/fail/incomplete status.

    v5.0 D1: curve L2 gating. Computed curve data comes from
    ``artifacts.curves`` (adapter-collected, curve name -> [(x, y), ...] --
    the same shape ``compute_curve_l2`` already consumes). Reference curve
    data is loaded per curve name from ``case.reference.files[curve_name]``
    (same file-per-key convention the QoI reference lookup uses), expecting
    a JSON list of [x, y] pairs. A curve is gated (can fail the run) only
    when both sides are present, its L2 is finite, and a tolerance is
    configured in ``case.metrics.curve_l2_tolerance``; otherwise it is
    disclosed via ``ungated_curves`` or a 'missing curve' note, never
    silently dropped.
    """

    def compute(
        self,
        case: CaseSpec,
        artifacts: ArtifactManifest,
        run_result: RunResult,
        timing: TimingSpec | None = None,
        case_dir: Path | None = None,
    ) -> MetricsResult:
        """Compute metrics for a completed run.

        Args:
            case: CaseSpec configuration.
            artifacts: Collected artifacts from the run.
            run_result: Execution result.
            timing: Optional timing info (for budget check). If None,
                uses run_result.wall_time_sec.
            case_dir: Optional case directory for resolving reference file paths.

        Returns:
            MetricsResult with errors, pass/fail status, and notes.
        """
        notes: list[str] = []

        # P1-a: dry_run mode — skip QoI checks
        if run_result.skipped_commands is not None:
            return MetricsResult(
                qoi_relative_errors={},
                qoi_pass=True,
                overall_status="dry_run",
                notes=["dry-run mode: QoI check skipped"],
            )

        # 1. If run failed, return fail immediately
        if run_result.exit_code != 0:
            notes.append(f"run exited with code {run_result.exit_code}")
            if run_result.timed_out:
                notes.append("run timed out")
            return MetricsResult(
                qoi_relative_errors={},
                qoi_pass=False,
                overall_status="fail",
                notes=notes,
            )

        # 2. Get reference QoI values
        reference_qoi = self._get_reference_qoi(case, artifacts, case_dir)

        # 3. Get computed QoI values
        computed_qoi = artifacts.qoi_values or {}

        # 4. Compute relative errors for expected QoIs.
        # P4-G hole 1: a reference value of exactly 0 makes relative error
        # undefined. Instead of silently skipping the QoI, gate it with a
        # configured absolute tolerance, or fail-closed to 'incomplete'.
        # NaN hardening: non-finite computed/reference values are a diverged
        # or corrupted solution -> hard 'fail' (never silent pass, never
        # merely 'incomplete'). Verdicts are driven by explicit counters,
        # not by parsing note strings (notes are prose for humans only).
        absolute_tolerances = case.metrics.qoi_absolute_tolerance
        errors: dict[str, float] = {}
        absolute_errors: dict[str, float] = {}
        failed_qoi: set[str] = set()
        missing_count = 0
        non_finite_count = 0
        for qoi_name in case.outputs.qoi:
            if qoi_name not in computed_qoi:
                missing_count += 1
                notes.append(f"missing computed QoI: {qoi_name}")
                continue
            computed_val = computed_qoi[qoi_name]
            if math.isfinite(computed_val) is False:
                non_finite_count += 1
                notes.append(f"non-finite computed QoI '{qoi_name}'")
                continue
            if qoi_name not in reference_qoi:
                missing_count += 1
                notes.append(f"missing reference QoI: {qoi_name}")
                continue
            ref_val = reference_qoi[qoi_name]
            if math.isfinite(ref_val) is False:
                non_finite_count += 1
                notes.append(f"non-finite reference QoI '{qoi_name}'")
                continue
            if ref_val == 0:
                # Both values are finite here, so abs_err is finite too.
                # Record it whether or not a tolerance is configured.
                abs_err = abs(computed_val - ref_val)
                absolute_errors[qoi_name] = abs_err
                if qoi_name in absolute_tolerances:
                    abs_tol = absolute_tolerances[qoi_name]
                    if abs_err > abs_tol:
                        failed_qoi.add(qoi_name)
                        notes.append(
                            f"zero-reference QoI '{qoi_name}' failed absolute "
                            f"tolerance: |computed - ref| = {abs_err:.6g} > "
                            f"{abs_tol:.6g}"
                        )
                    else:
                        notes.append(
                            f"zero-reference QoI '{qoi_name}' passed absolute "
                            f"tolerance: |computed - ref| = {abs_err:.6g} <= "
                            f"{abs_tol:.6g}"
                        )
                else:
                    missing_count += 1
                    notes.append(f"missing absolute tolerance for zero-reference QoI '{qoi_name}'")
                continue
            errors[qoi_name] = abs(computed_val - ref_val) / abs(ref_val)

        # 5. Determine pass/fail
        tolerances = case.metrics.qoi_relative_tolerance
        for qoi_name, err in errors.items():
            if qoi_name in tolerances and err > tolerances[qoi_name]:
                failed_qoi.add(qoi_name)
        qoi_pass = missing_count == 0 and non_finite_count == 0 and len(failed_qoi) == 0

        # P4-G hole 2: QoIs with a computed error but no configured tolerance
        # do not participate in the gate. Keep that (backward compatible) but
        # disclose them so reporting layers can show they are unconstrained.
        ungated_qoi = [name for name in errors if name not in tolerances]
        for qoi_name in ungated_qoi:
            notes.append(
                f"ungated QoI '{qoi_name}': error computed but no tolerance "
                "configured; value does not affect pass/fail"
            )

        # 5b. Curve L2 norm judgment (v5.0 D1: wire the existing
        # compute_curve_l2 helper into the pass/fail gate -- it was
        # implemented in metrics/curves.py but never called). Mirrors the
        # QoI gate shape: missing data (either side, or a shape mismatch
        # compute_curve_l2 itself skips) -> note, tracked separately so it
        # can drive 'incomplete'; non-finite L2 -> counted into the shared
        # non_finite_count so a divergent curve is a hard 'fail' just like
        # a divergent QoI; tolerance exceeded -> curves_failed; no
        # configured tolerance -> ungated_curves disclosure, never gated.
        #
        # Gate is a no-op unless artifacts.curves is not None. Every
        # shipped adapter (generic/openfoam/su2/starccm) sets curves=None
        # unconditionally today (ArtifactManifest.curves docstring: "None
        # in P0") -- there is no collection infrastructure yet, so None
        # means "this run's pipeline never attempted curve collection",
        # distinct from an adapter that ran and produced an empty dict.
        # Without this guard, the shipped naca0012 case (which already
        # declares outputs.curves + curve_l2_tolerance, dead-lettered
        # since nothing ever consumed it) would regress from its QoI-only
        # verdict to 'incomplete' the moment this gate went live, even
        # though no adapter can deliver curve data yet. Once a real
        # adapter starts setting artifacts.curves, gating activates
        # automatically -- no further engine change needed.
        curve_l2_errors: dict[str, float] = {}
        curves_failed: list[str] = []
        ungated_curves: list[str] = []
        curve_missing_count = 0
        curve_non_finite_count = 0
        if case.outputs.curves and artifacts.curves is not None:
            curve_tolerances = case.metrics.curve_l2_tolerance or {}
            reference_curves = self._get_reference_curves(case, case_dir)
            computed_curves = artifacts.curves or {}
            raw_l2 = compute_curve_l2(reference_curves, computed_curves)
            for curve_name in case.outputs.curves:
                if curve_name not in computed_curves:
                    curve_missing_count += 1
                    notes.append(f"missing computed curve: {curve_name}")
                    continue
                if curve_name not in reference_curves:
                    curve_missing_count += 1
                    notes.append(f"missing reference curve: {curve_name}")
                    continue
                if curve_name not in raw_l2:
                    # compute_curve_l2 itself skips length/shape mismatches.
                    curve_missing_count += 1
                    notes.append(f"curve '{curve_name}' skipped: reference/computed shape mismatch")
                    continue
                l2 = raw_l2[curve_name]
                if math.isfinite(l2) is False:
                    curve_non_finite_count += 1
                    non_finite_count += 1
                    notes.append(f"non-finite curve L2 for '{curve_name}'")
                    continue
                curve_l2_errors[curve_name] = l2
                if curve_name in curve_tolerances:
                    tol = curve_tolerances[curve_name]
                    if l2 > tol:
                        curves_failed.append(curve_name)
                        notes.append(
                            f"curve '{curve_name}' failed L2 tolerance: {l2:.6g} > {tol:.6g}"
                        )
                    else:
                        notes.append(
                            f"curve '{curve_name}' passed L2 tolerance: {l2:.6g} <= {tol:.6g}"
                        )
                else:
                    ungated_curves.append(curve_name)
                    notes.append(
                        f"ungated curve '{curve_name}': L2 computed but no "
                        "tolerance configured; value does not affect pass/fail"
                    )

        # 6. Budget check (P4-G hole 3: expose overrun as a structured flag;
        # warning semantics are kept — the flag never flips pass/fail)
        if timing is not None:
            budget_notes = check_budget(timing, case.budget)
        else:
            now = datetime.now(timezone.utc)
            timing_for_budget = TimingSpec(
                wall_time_sec=run_result.wall_time_sec,
                start_time=now,
                end_time=now,
            )
            budget_notes = check_budget(timing_for_budget, case.budget)
        notes.extend(budget_notes)
        budget_exceeded = len(budget_notes) > 0

        # 7. Determine overall_status from explicit counters (never from
        # note-string parsing). Non-finite values dominate: a diverged
        # solution is a hard 'fail', not 'incomplete'.
        #
        # v5.0 D1: curve_pass extends qoi_pass with the exact same shape
        # (missing == 0, non_finite == 0, failed == []) so that when no
        # curves are configured (curve_missing_count == 0, curves_failed
        # == [], curve_non_finite_count == 0 for every existing case)
        # curve_pass is trivially True and the branch below reduces
        # byte-for-byte to the pre-v5.0 logic -- zero behavior change.
        curve_pass = (
            curve_missing_count == 0 and curve_non_finite_count == 0 and len(curves_failed) == 0
        )
        if qoi_pass and curve_pass:
            status = "pass"
        elif non_finite_count > 0:
            status = "fail"
        elif missing_count > 0 or curve_missing_count > 0:
            status = "incomplete"
        else:
            status = "fail"

        # === P3-hotfix: populate qoi_computed_values for polar rendering ===
        # Copy computed QoI values (the actual numbers, not relative errors)
        # so report-sweep can plot real Cl/Cd curves. Non-finite values are
        # excluded (schema rejects them and they carry no plottable signal).
        qoi_computed: dict[str, float] = {}
        for qoi_name in case.outputs.qoi:
            if qoi_name in computed_qoi and math.isfinite(computed_qoi[qoi_name]):
                qoi_computed[qoi_name] = computed_qoi[qoi_name]

        return MetricsResult(
            qoi_relative_errors=errors,
            qoi_pass=qoi_pass,
            overall_status=status,
            notes=notes,
            qoi_computed_values=qoi_computed if qoi_computed else None,
            ungated_qoi=ungated_qoi,
            budget_exceeded=budget_exceeded,
            qoi_absolute_errors=absolute_errors,
            qoi_failed=sorted(failed_qoi),
            curve_l2_errors=curve_l2_errors,
            curves_failed=curves_failed,
            ungated_curves=ungated_curves,
        )

    def _get_reference_qoi(
        self,
        case: CaseSpec,
        artifacts: ArtifactManifest,
        case_dir: Path | None = None,
    ) -> dict[str, float]:
        """Get reference QoI values from inline values or reference files.

        Args:
            case: CaseSpec configuration.
            artifacts: Collected artifacts (unused, kept for API compat).
            case_dir: Optional case directory for resolving relative reference paths.

        Returns:
            Dict of reference QoI values.
        """
        if case.reference is None:
            return {}

        # Prefer inline qoi_values
        if case.reference.qoi_values is not None:
            return dict(case.reference.qoi_values)

        # Try to load from reference file
        ref_qoi = {}
        if case.reference.files:
            qoi_file_key = None
            for key in ("qoi", "qoi_values"):
                if key in case.reference.files:
                    qoi_file_key = key
                    break
            if qoi_file_key is not None:
                ref_rel = case.reference.files[qoi_file_key]
                ref_path = ref_rel if case_dir is None else case_dir / ref_rel
                try:
                    raw = ref_path.read_text(encoding="utf-8")
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        ref_qoi = {k: float(v) for k, v in parsed.items()}
                except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
                    logger.warning("failed to load reference QoI from %s: %s", ref_path, e)

        return ref_qoi

    def _get_reference_curves(
        self,
        case: CaseSpec,
        case_dir: Path | None = None,
    ) -> dict[str, list[tuple[float, float]]]:
        """Load reference curve data for the case's declared curve outputs.

        Each name in ``case.outputs.curves`` maps to a JSON reference file
        via ``case.reference.files[curve_name]`` (same file-per-key
        convention as the QoI reference lookup). Curves with no matching
        key, or whose file fails to load/parse, are simply absent from the
        returned dict -- the caller reports that as a missing-reference
        note rather than raising (fail-closed, never crashes the run).

        Args:
            case: CaseSpec configuration.
            case_dir: Optional case directory for resolving relative reference paths.

        Returns:
            Dict mapping curve name to a list of (x, y) points.
        """
        if case.reference is None or not case.reference.files:
            return {}
        result: dict[str, list[tuple[float, float]]] = {}
        for curve_name in case.outputs.curves:
            if curve_name not in case.reference.files:
                continue
            ref_rel = case.reference.files[curve_name]
            ref_path = ref_rel if case_dir is None else case_dir / ref_rel
            curve = self._load_reference_curve(ref_path)
            if curve is not None:
                result[curve_name] = curve
        return result

    def _load_reference_curve(self, path: Path) -> list[tuple[float, float]] | None:
        """Load curve data from a reference file.

        Two formats (R7 backlog -- NACA cp reference mapping): a ``.csv``
        file (two numeric columns, an optional single header row like
        ``x/c,Cp``) or, for any other extension, a JSON list of [x, y]
        pairs. Validation is strict either way: one malformed row rejects
        the WHOLE file (None -> upstream 'missing reference curve' note);
        loading is never made lenient just to get a curve on the board.

        Args:
            path: Path to the reference file.

        Returns:
            List of (x, y) points, or None if the file is missing, unreadable,
            or malformed (never raises -- callers treat None as 'not available').
        """
        if path.suffix.lower() == ".csv":
            return self._load_csv_curve(path)
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [(float(pt[0]), float(pt[1])) for pt in parsed]
        except (
            json.JSONDecodeError,
            ValueError,
            TypeError,
            OSError,
            IndexError,
        ) as e:
            logger.warning("failed to load reference curve from %s: %s", path, e)
        return None

    def _load_csv_curve(self, path: Path) -> list[tuple[float, float]] | None:
        """Load a two-column CSV reference curve (strict; see caller).

        The first row may be a non-numeric header (skipped). Every other
        row must be exactly two finite floats; any violation rejects the
        whole file with a logged reason.

        Args:
            path: Path to the CSV file.

        Returns:
            List of (x, y) points, or None when the file is unusable.
        """
        try:
            with path.open(newline="", encoding="utf-8") as f:
                rows = [row for row in csv.reader(f) if len(row) > 0]
        except OSError as e:
            logger.warning("failed to read reference curve CSV %s: %s", path, e)
            return None
        if len(rows) == 0:
            logger.warning("reference curve CSV %s is empty", path)
            return None

        def _parse(row: list[str]) -> tuple[float, float] | None:
            if len(row) != 2:
                return None
            try:
                x, y = float(row[0]), float(row[1])
            except ValueError:
                return None
            if math.isfinite(x) is False or math.isfinite(y) is False:
                return None
            return (x, y)

        first = _parse(rows[0])
        data_rows = rows if first is not None else rows[1:]
        if len(data_rows) == 0:
            logger.warning("reference curve CSV %s has a header but no data rows", path)
            return None
        points: list[tuple[float, float]] = []
        for offset, row in enumerate(data_rows):
            lineno = offset + (1 if first is not None else 2)
            point = _parse(row)
            if point is None:
                logger.warning(
                    "reference curve CSV %s line %d is not two finite floats: %r "
                    "(whole file rejected)",
                    path,
                    lineno,
                    row,
                )
                return None
            points.append(point)
        return points

    def _load_reference_file(self, path: Path) -> dict[str, float]:
        """Load QoI values from a JSON reference file.

        Args:
            path: Path to the JSON file.

        Returns:
            Dict of QoI values, empty if load fails.
        """
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {k: float(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
            logger.warning("failed to load reference file %s: %s", path, e)
        return {}
