"""Tests for the P4-C failure mode taxonomy and append-only library."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.failures import (
    FailureLibrary,
    build_signature,
    classify,
    compute_fingerprint,
)
from cfdb.schema import MetricsResult, RunManifest, TimingSpec


def _timing() -> TimingSpec:
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    return TimingSpec(wall_time_sec=10.0, start_time=now, end_time=now)


def _manifest(
    run_id: str = "20260708T120000Z_case_a_solver_x_deadbeef",
    status: str = "failed",
    **kwargs,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        case_id="case_a",
        solver="solver_x",
        status=status,
        timing=_timing(),
        **kwargs,
    )


def _metrics(
    overall_status: str = "fail",
    qoi_pass: bool = False,
    notes: list[str] | None = None,
    qoi_relative_errors: dict[str, float] | None = None,
) -> MetricsResult:
    return MetricsResult(
        qoi_relative_errors=qoi_relative_errors or {},
        qoi_pass=qoi_pass,
        overall_status=overall_status,
        notes=notes or [],
    )


def _write_run(
    runs_dir: Path,
    manifest: RunManifest,
    metrics: MetricsResult | None,
) -> None:
    run_dir = runs_dir / manifest.run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    if metrics is not None:
        (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2), encoding="utf-8")


# ============================================================================
# classify() — mode derivation and priority
# ============================================================================


class TestClassify:
    def test_verified_pass_returns_none(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(overall_status="pass", qoi_pass=True)
        assert classify(manifest, metrics) is None

    def test_dry_run_returns_none(self) -> None:
        manifest = _manifest(status="dry_run")
        assert classify(manifest, None) is None

    def test_timeout(self) -> None:
        manifest = _manifest(status="timeout")
        assert classify(manifest, _metrics()) == "TIMEOUT"

    def test_mesh_failure(self) -> None:
        manifest = _manifest(
            step_details=[
                {"name": "blockMesh", "exit_code": 0, "status": "success"},
                {"name": "snappy_mesh", "exit_code": 1, "status": "failed"},
            ]
        )
        assert classify(manifest, _metrics()) == "MESH_FAILURE"

    def test_divergence_on_huge_residual(self) -> None:
        manifest = _manifest(status="failed", final_residuals={"Ux": 1.0e6, "p": 1e-4})
        assert classify(manifest, _metrics()) == "DIVERGENCE"

    def test_divergence_on_nan_residual(self) -> None:
        manifest = _manifest(status="failed", final_residuals={"Ux": math.nan})
        assert classify(manifest, _metrics()) == "DIVERGENCE"

    def test_converged_residuals_do_not_trigger_divergence(self) -> None:
        manifest = _manifest(status="failed", final_residuals={"Ux": 1e-6, "p": 1e-5})
        assert classify(manifest, _metrics()) != "DIVERGENCE"

    def test_missing_artifact(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(overall_status="incomplete", notes=["missing computed QoI: cl"])
        assert classify(manifest, metrics) == "MISSING_ARTIFACT"

    def test_missing_reference(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(overall_status="incomplete", notes=["missing reference QoI: cd"])
        assert classify(manifest, metrics) == "MISSING_REFERENCE"

    def test_tolerance_exceeded(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(qoi_pass=False, qoi_relative_errors={"cl": 0.5})
        assert classify(manifest, metrics) == "TOLERANCE_EXCEEDED"

    def test_env_missing(self) -> None:
        manifest = _manifest(error="bash: simpleFoam: command not found")
        assert classify(manifest, _metrics()) == "ENV_MISSING"

    def test_env_missing_enoent(self) -> None:
        manifest = _manifest(error="[Errno 2] ENOENT while launching solver")
        assert classify(manifest, _metrics()) == "ENV_MISSING"

    def test_setup_error_for_generic_nonzero_exit(self) -> None:
        manifest = _manifest(error="segmentation fault in decomposePar")
        assert classify(manifest, _metrics()) == "SETUP_ERROR"

    def test_setup_error_for_failed_non_mesh_step(self) -> None:
        manifest = _manifest(
            status="success",
            step_details=[{"name": "decompose_par", "exit_code": 2, "status": "failed"}],
        )
        assert classify(manifest, _metrics()) == "SETUP_ERROR"

    def test_unknown_fallback(self) -> None:
        # success run but recomputed metrics say fail with no other signal
        manifest = _manifest(status="success")
        assert classify(manifest, _metrics()) == "UNKNOWN"

    def test_success_without_metrics_is_not_a_pass(self) -> None:
        # Fail-closed: unverifiable metrics never silently passes.
        manifest = _manifest(status="success")
        assert classify(manifest, None) == "UNKNOWN"
        assert build_signature(manifest, None, "UNKNOWN") == "metrics=missing"

    def test_priority_timeout_beats_mesh_failure(self) -> None:
        manifest = _manifest(
            status="timeout",
            step_details=[{"name": "snappy_mesh", "exit_code": 1, "status": "failed"}],
        )
        assert classify(manifest, _metrics()) == "TIMEOUT"

    def test_priority_mesh_failure_beats_divergence(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "gmsh_mesh", "exit_code": 2, "status": "failed"}],
            final_residuals={"Ux": 1e9},
        )
        assert classify(manifest, _metrics()) == "MESH_FAILURE"


# ============================================================================
# signature / fingerprint stability
# ============================================================================


class TestSignature:
    def test_mesh_signature_is_step_and_exit(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "snappy_mesh", "exit_code": 1, "status": "failed"}]
        )
        assert build_signature(manifest, _metrics(), "MESH_FAILURE") == "step=snappy_mesh exit=1"

    def test_fingerprint_stable_across_run_ids(self) -> None:
        m1 = _manifest(run_id="20260708T120000Z_case_a_solver_x_aaaaaaaa", status="timeout")
        m2 = _manifest(run_id="20260708T130000Z_case_a_solver_x_bbbbbbbb", status="timeout")
        sig1 = build_signature(m1, None, "TIMEOUT")
        sig2 = build_signature(m2, None, "TIMEOUT")
        fp1 = compute_fingerprint(m1.case_id, m1.solver, "TIMEOUT", sig1)
        fp2 = compute_fingerprint(m2.case_id, m2.solver, "TIMEOUT", sig2)
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_divergence_signature_lists_sorted_fields(self) -> None:
        manifest = _manifest(final_residuals={"p": 1e9, "Ux": math.inf})
        assert build_signature(manifest, None, "DIVERGENCE") == "residuals=Ux,p"

    def test_missing_artifact_and_reference_signatures(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(
            overall_status="incomplete",
            notes=["missing computed QoI: cl", "missing reference QoI: cd"],
        )
        assert (
            build_signature(manifest, metrics, "MISSING_ARTIFACT") == "missing computed QoI: cl"
        )
        assert (
            build_signature(manifest, metrics, "MISSING_REFERENCE") == "missing reference QoI: cd"
        )

    def test_tolerance_exceeded_signature_lists_sorted_qoi(self) -> None:
        metrics = _metrics(qoi_pass=False, qoi_relative_errors={"cl": 0.5, "cd": 0.9})
        manifest = _manifest(status="success")
        assert build_signature(manifest, metrics, "TOLERANCE_EXCEEDED") == "qoi=cd,cl"

    def test_env_missing_signature(self) -> None:
        manifest = _manifest(error="bash: simpleFoam: command not found")
        assert build_signature(manifest, None, "ENV_MISSING") == "error=command_not_found"

    def test_setup_error_signature_with_and_without_step(self) -> None:
        with_step = _manifest(
            step_details=[{"name": "decompose_par", "exit_code": 2, "status": "failed"}]
        )
        assert build_signature(with_step, None, "SETUP_ERROR") == "step=decompose_par exit=2"
        without_step = _manifest(error="segfault")
        assert build_signature(without_step, None, "SETUP_ERROR") == "status=failed"

    def test_mesh_failure_signature_fallback_without_step(self) -> None:
        manifest = _manifest()
        assert build_signature(manifest, None, "MESH_FAILURE") == "step=<unknown-mesh-step>"

    def test_unknown_signature_with_metrics_present(self) -> None:
        manifest = _manifest(status="success")
        assert build_signature(manifest, _metrics(), "UNKNOWN") == "status=success"

    def test_fingerprint_differs_by_solver(self) -> None:
        fp1 = compute_fingerprint("case_a", "solver_x", "TIMEOUT", "status=timeout")
        fp2 = compute_fingerprint("case_a", "solver_y", "TIMEOUT", "status=timeout")
        assert fp1 != fp2


# ============================================================================
# FailureLibrary — ingest / dedup / annotate / append-only
# ============================================================================


class TestFailureLibrary:
    def _library(self, tmp_path: Path) -> FailureLibrary:
        return FailureLibrary(tmp_path / "failures" / "library.json")

    def test_ingest_skips_verified_pass(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="success"),
            _metrics(overall_status="pass", qoi_pass=True),
        )
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.passed == 1
        assert summary.new_records == 0
        assert lib.records() == []

    def test_ingest_creates_record(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(notes=["run timed out"]),
        )
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.new_records == 1
        records = lib.records()
        assert len(records) == 1
        record = records[0]
        assert record.mode == "TIMEOUT"
        assert record.count == 1
        assert record.first_seen == record.last_seen
        assert "20260708T120000Z_case_a_solver_x_00000001/manifest.json" in record.evidence

    def test_same_fingerprint_second_ingest_increments_count_no_new_entry(
        self, tmp_path: Path
    ) -> None:
        """Tamper witness: recurrence must count++ and update last_seen, not duplicate."""
        runs = tmp_path / "runs"
        run1 = "20260708T120000Z_case_a_solver_x_00000001"
        run2 = "20260708T130000Z_case_a_solver_x_00000002"
        _write_run(runs, _manifest(run_id=run1, status="timeout"), _metrics())
        lib = self._library(tmp_path)
        lib.ingest(runs)

        _write_run(runs, _manifest(run_id=run2, status="timeout"), _metrics())
        summary = lib.ingest(runs)

        assert summary.new_records == 0
        assert summary.updated_records == 1
        records = lib.records()
        assert len(records) == 1
        assert records[0].count == 2
        assert records[0].first_seen == run1
        assert records[0].last_seen == run2

    def test_reingest_same_runs_is_idempotent(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        summary = lib.ingest(runs)
        assert summary.already_ingested == 1
        assert lib.records()[0].count == 1

    def test_library_persists_across_instances(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        fresh = self._library(tmp_path)
        assert len(fresh.records()) == 1
        assert fresh.records()[0].mode == "TIMEOUT"

    def test_annotate_writes_guard(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        fp = lib.records()[0].fingerprint
        lib.annotate(fp, "raise budget.max_wall_time for this case")
        fresh = self._library(tmp_path)
        assert fresh.get(fp).guard == "raise budget.max_wall_time for this case"

    def test_annotate_unknown_fingerprint_raises(self, tmp_path: Path) -> None:
        """Tamper witness: annotating a nonexistent fingerprint must bite."""
        lib = self._library(tmp_path)
        with pytest.raises(KeyError):
            lib.annotate("0000000000000000", "some guard")

    def test_annotate_empty_guard_raises(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        fp = lib.records()[0].fingerprint
        with pytest.raises(ValueError):
            lib.annotate(fp, "   ")

    def test_save_refuses_to_shrink_history(self, tmp_path: Path) -> None:
        """Tamper witness: append-only red line — shrinking save must raise."""
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)

        tampered = self._library(tmp_path)
        tampered._records = {}
        with pytest.raises(RuntimeError, match="append-only"):
            tampered._save()

    def test_missing_manifest_reported_not_silently_skipped(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        (runs / "20260708T120000Z_case_a_solver_x_00000001").mkdir(parents=True)
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.errors and "manifest.json missing" in summary.errors[0]
        assert lib.records() == []

    def test_corrupt_manifest_reported(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        run_dir = runs / "20260708T120000Z_case_a_solver_x_00000001"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{not json", encoding="utf-8")
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.errors and "unreadable manifest.json" in summary.errors[0]
        assert lib.records() == []

    def test_corrupt_metrics_fail_closed_classified_and_reported(self, tmp_path: Path) -> None:
        """Fail-closed: unreadable metrics.json is reported and never counts as pass."""
        runs = tmp_path / "runs"
        run_id = "20260708T120000Z_case_a_solver_x_00000001"
        _write_run(runs, _manifest(run_id=run_id, status="success"), None)
        (runs / run_id / "metrics.json").write_text("{not json", encoding="utf-8")
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.errors and "unreadable metrics.json" in summary.errors[0]
        assert summary.new_records == 1
        assert lib.records()[0].mode == "UNKNOWN"

    def test_missing_runs_dir_fail_closed(self, tmp_path: Path) -> None:
        lib = self._library(tmp_path)
        summary = lib.ingest(tmp_path / "nonexistent_runs")
        assert summary.errors and "runs directory not found" in summary.errors[0]

    def test_success_run_without_metrics_enters_library(self, tmp_path: Path) -> None:
        """Fail-closed: a success run with no metrics.json is not a verified pass."""
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="success"),
            None,
        )
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.new_records == 1
        record = lib.records()[0]
        assert record.mode == "UNKNOWN"
        assert record.signature == "metrics=missing"

    def test_records_filter_by_mode(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        _write_run(
            runs,
            _manifest(
                run_id="20260708T130000Z_case_a_solver_x_00000002",
                status="failed",
                step_details=[{"name": "snappy_mesh", "exit_code": 1, "status": "failed"}],
            ),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        assert len(lib.records()) == 2
        assert len(lib.records(mode="MESH_FAILURE")) == 1
        assert lib.records(mode="MESH_FAILURE")[0].signature == "step=snappy_mesh exit=1"

    def test_library_json_is_valid_and_grows_only(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)
        path = tmp_path / "failures" / "library.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        assert len(before["records"]) == 1

        _write_run(
            runs,
            _manifest(
                run_id="20260708T140000Z_case_a_solver_x_00000003",
                status="failed",
                error="bash: simpleFoam: command not found",
            ),
            _metrics(),
        )
        lib.ingest(runs)
        after = json.loads(path.read_text(encoding="utf-8"))
        assert len(after["records"]) == 2
        old_fps = {r["fingerprint"] for r in before["records"]}
        new_fps = {r["fingerprint"] for r in after["records"]}
        assert old_fps <= new_fps


class TestEnvMissingIsNotSolverDictError:
    """Regression pin from real runs: OpenFOAM's "Entry 'pFinal' not found in
    dictionary" was misclassified ENV_MISSING by a bare "not found" substring.
    Solver-internal dictionary errors are SETUP_ERROR."""

    def test_foam_dictionary_error_is_setup_error(self) -> None:
        manifest = _manifest(
            error=(
                "FOAM FATAL IO ERROR: (openfoam-2312)\n"
                "Entry 'pFinal' not found in dictionary "
                '"system/fvSolution/solvers"'
            )
        )
        assert classify(manifest, _metrics("fail")) == "SETUP_ERROR"
