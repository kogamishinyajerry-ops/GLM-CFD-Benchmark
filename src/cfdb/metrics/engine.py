"""MetricsEngine: orchestrates QoI, curve, and performance metric computation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.metrics.performance import check_budget
from cfdb.schema import CaseSpec, MetricsResult, TimingSpec

logger = logging.getLogger(__name__)


class MetricsEngine:
    """Metrics computation engine.

    Orchestrates QoI relative error, curve L2 norm, and budget checks.
    Determines overall pass/fail/incomplete status.
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
        absolute_tolerances = case.metrics.qoi_absolute_tolerance
        errors: dict[str, float] = {}
        absolute_gate_failed = False
        for qoi_name in case.outputs.qoi:
            if qoi_name not in computed_qoi:
                notes.append(f"missing computed QoI: {qoi_name}")
                continue
            if qoi_name not in reference_qoi:
                notes.append(f"missing reference QoI: {qoi_name}")
                continue
            ref_val = reference_qoi[qoi_name]
            if ref_val == 0:
                if qoi_name in absolute_tolerances:
                    abs_err = abs(computed_qoi[qoi_name] - ref_val)
                    abs_tol = absolute_tolerances[qoi_name]
                    if abs_err > abs_tol:
                        absolute_gate_failed = True
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
                    notes.append(
                        "missing absolute tolerance for zero-reference "
                        f"QoI '{qoi_name}'"
                    )
                continue
            errors[qoi_name] = abs(computed_qoi[qoi_name] - ref_val) / abs(ref_val)

        # 5. Determine pass/fail
        tolerances = case.metrics.qoi_relative_tolerance
        missing_notes = [n for n in notes if n.startswith("missing")]
        qoi_pass = len(missing_notes) == 0 and not absolute_gate_failed
        for qoi_name, err in errors.items():
            if qoi_name in tolerances and err > tolerances[qoi_name]:
                qoi_pass = False

        # P4-G hole 2: QoIs with a computed error but no configured tolerance
        # do not participate in the gate. Keep that (backward compatible) but
        # disclose them so reporting layers can show they are unconstrained.
        ungated_qoi = [name for name in errors if name not in tolerances]
        for qoi_name in ungated_qoi:
            notes.append(
                f"ungated QoI '{qoi_name}': error computed but no tolerance "
                "configured; value does not affect pass/fail"
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

        # 7. Determine overall_status
        if qoi_pass:
            status = "pass"
        elif len(missing_notes) > 0:
            status = "incomplete"
        else:
            status = "fail"

        # === P3-hotfix: populate qoi_computed_values for polar rendering ===
        # Copy computed QoI values (the actual numbers, not relative errors)
        # so report-sweep can plot real Cl/Cd curves.
        qoi_computed: dict[str, float] = {}
        for qoi_name in case.outputs.qoi:
            if qoi_name in computed_qoi:
                qoi_computed[qoi_name] = computed_qoi[qoi_name]

        return MetricsResult(
            qoi_relative_errors=errors,
            qoi_pass=qoi_pass,
            overall_status=status,
            notes=notes,
            qoi_computed_values=qoi_computed if qoi_computed else None,
            ungated_qoi=ungated_qoi,
            budget_exceeded=budget_exceeded,
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
                    logger.warning(
                        "failed to load reference QoI from %s: %s", ref_path, e
                    )

        return ref_qoi

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
