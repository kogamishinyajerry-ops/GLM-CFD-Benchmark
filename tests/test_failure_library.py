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
    qoi_failed: list[str] | None = None,
) -> MetricsResult:
    return MetricsResult(
        qoi_relative_errors=qoi_relative_errors or {},
        qoi_pass=qoi_pass,
        overall_status=overall_status,
        notes=notes or [],
        qoi_failed=qoi_failed or [],
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

    def test_tolerance_exceeded_via_qoi_failed_only(self) -> None:
        """Zero-reference absolute-tolerance failure: qoi_failed is set but
        qoi_relative_errors is empty. Must classify TOLERANCE_EXCEEDED, not
        fall into the UNKNOWN blind spot."""
        manifest = _manifest(status="success")
        metrics = _metrics(qoi_pass=False, qoi_failed=["cmy"])
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
# v5 §2.A4 — domain-agnostic generic detectors
# ============================================================================


class TestGenericDetectorsA4:
    """BUILD_FAILURE / TEST_FAILURE / WRONG_ANSWER / RESOURCE_EXCEEDED /
    CHECKER_ERROR: one positive test + one "does not misfire on CFD" test
    per detector, plus explicit priority-chain pins (§2.A4 SPEC)."""

    # --- BUILD_FAILURE ---------------------------------------------------

    def test_build_failure_on_nonzero_build_step(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "build", "exit_code": 1, "status": "failed"}]
        )
        assert classify(manifest, _metrics()) == "BUILD_FAILURE"

    def test_build_failure_on_compile_step(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "compile_submission", "exit_code": 2, "status": "failed"}]
        )
        assert classify(manifest, _metrics()) == "BUILD_FAILURE"

    def test_successful_build_step_does_not_misfire(self) -> None:
        """A passing build step must not trigger BUILD_FAILURE, and must not
        shadow a genuine CFD mesh failure elsewhere in the same run."""
        manifest = _manifest(
            step_details=[
                {"name": "build", "exit_code": 0, "status": "success"},
                {"name": "snappy_mesh", "exit_code": 1, "status": "failed"},
            ]
        )
        assert classify(manifest, _metrics()) == "MESH_FAILURE"

    # --- TEST_FAILURE ------------------------------------------------------

    def test_test_failure_on_nonzero_test_step(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "run_hidden_tests", "exit_code": 1, "status": "failed"}]
        )
        assert classify(manifest, _metrics()) == "TEST_FAILURE"

    def test_priority_build_failure_beats_test_failure(self) -> None:
        """If both a build step and a test step failed, BUILD_FAILURE wins
        (a broken build means the tests never meaningfully ran)."""
        manifest = _manifest(
            step_details=[
                {"name": "build", "exit_code": 1, "status": "failed"},
                {"name": "pytest", "exit_code": 1, "status": "failed"},
            ]
        )
        assert classify(manifest, _metrics()) == "BUILD_FAILURE"

    def test_successful_test_step_does_not_misfire_on_cfd_divergence(self) -> None:
        """A passing test-named step must not shadow a real CFD divergence."""
        manifest = _manifest(
            status="failed",
            step_details=[{"name": "test_smoke", "exit_code": 0, "status": "success"}],
            final_residuals={"Ux": 1.0e6},
        )
        assert classify(manifest, _metrics()) == "DIVERGENCE"

    # --- CHECKER_ERROR -----------------------------------------------------

    def test_checker_error_on_notes_keyword(self) -> None:
        manifest = _manifest(status="failed")
        metrics = _metrics(notes=["checker error: division by zero in checker.py"])
        assert classify(manifest, metrics) == "CHECKER_ERROR"

    def test_priority_checker_error_beats_divergence(self) -> None:
        """CHECKER_ERROR is checked right after TIMEOUT: a broken judge
        invalidates every downstream signal, including a genuine divergence."""
        manifest = _manifest(status="failed", final_residuals={"Ux": 1.0e6})
        metrics = _metrics(notes=["checker crashed while parsing evidence"])
        assert classify(manifest, metrics) == "CHECKER_ERROR"

    def test_unrelated_cfd_notes_do_not_misfire_checker_error(self) -> None:
        """Regression pin: ordinary CFD notes must never be mistaken for a
        checker-runtime failure."""
        manifest = _manifest(status="success")
        metrics = _metrics(overall_status="incomplete", notes=["missing computed QoI: cl"])
        assert classify(manifest, metrics) == "MISSING_ARTIFACT"

    # --- WRONG_ANSWER --------------------------------------------------------

    def test_wrong_answer_on_notes_keyword_without_tolerance_signal(self) -> None:
        """No qoi_failed/qoi_relative_errors data (so TOLERANCE_EXCEEDED's own
        condition is false) — notes-only wrong-answer signal must be caught."""
        manifest = _manifest(status="success")
        metrics = _metrics(qoi_pass=False, notes=["wrong answer on hidden case 3"])
        assert classify(manifest, metrics) == "WRONG_ANSWER"

    def test_wrong_answer_conservative_tolerance_exceeded_wins(self) -> None:
        """Conservative priority: when TOLERANCE_EXCEEDED's own condition is
        also satisfied, it must win over a coincidental wrong-answer-shaped
        note — never over-match WRONG_ANSWER."""
        manifest = _manifest(status="success")
        metrics = _metrics(
            qoi_pass=False,
            qoi_failed=["cl"],
            notes=["wrong answer: cl outside tolerance band"],
        )
        assert classify(manifest, metrics) == "TOLERANCE_EXCEEDED"

    # --- RESOURCE_EXCEEDED -----------------------------------------------

    def test_resource_exceeded_on_out_of_memory_note(self) -> None:
        manifest = _manifest(status="failed")
        metrics = _metrics(notes=["submission process killed: out of memory"])
        assert classify(manifest, metrics) == "RESOURCE_EXCEEDED"

    def test_resource_exceeded_on_rlimit_note(self) -> None:
        manifest = _manifest(status="failed")
        metrics = _metrics(notes=["rlimit exceeded during sandbox execution"])
        assert classify(manifest, metrics) == "RESOURCE_EXCEEDED"

    def test_env_missing_still_wins_when_no_resource_keyword_present(self) -> None:
        """Regression pin: ENV_MISSING's existing manifest.error detector is
        untouched by the new notes-based RESOURCE_EXCEEDED detector."""
        manifest = _manifest(error="bash: simpleFoam: command not found")
        assert classify(manifest, _metrics()) == "ENV_MISSING"


class TestSignatureA4:
    def test_build_failure_signature(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "build", "exit_code": 1, "status": "failed"}]
        )
        assert build_signature(manifest, _metrics(), "BUILD_FAILURE") == "step=build exit=1"

    def test_test_failure_signature(self) -> None:
        manifest = _manifest(
            step_details=[{"name": "pytest", "exit_code": 1, "status": "failed"}]
        )
        assert build_signature(manifest, _metrics(), "TEST_FAILURE") == "step=pytest exit=1"

    def test_checker_error_signature(self) -> None:
        manifest = _manifest(status="failed")
        metrics = _metrics(notes=["checker error: boom"])
        assert build_signature(manifest, metrics, "CHECKER_ERROR") == "checker error: boom"

    def test_wrong_answer_signature(self) -> None:
        manifest = _manifest(status="success")
        metrics = _metrics(qoi_pass=False, notes=["wrong answer on hidden case 3"])
        assert (
            build_signature(manifest, metrics, "WRONG_ANSWER") == "wrong answer on hidden case 3"
        )

    def test_resource_exceeded_signature(self) -> None:
        manifest = _manifest(status="failed")
        metrics = _metrics(notes=["submission process killed: out of memory"])
        assert (
            build_signature(manifest, metrics, "RESOURCE_EXCEEDED")
            == "submission process killed: out of memory"
        )

    def test_build_failure_signature_fallback_without_step(self) -> None:
        manifest = _manifest()
        assert build_signature(manifest, None, "BUILD_FAILURE") == "step=<unknown-build-step>"

    def test_test_failure_signature_fallback_without_step(self) -> None:
        manifest = _manifest()
        assert build_signature(manifest, None, "TEST_FAILURE") == "step=<unknown-test-step>"

    def test_a4_fingerprints_are_distinct_from_each_other(self) -> None:
        """Sanity: the five new modes never collide on the same signature text."""
        fps = {
            compute_fingerprint("case_a", "solver_x", "BUILD_FAILURE", "step=build exit=1"),
            compute_fingerprint("case_a", "solver_x", "TEST_FAILURE", "step=pytest exit=1"),
            compute_fingerprint("case_a", "solver_x", "CHECKER_ERROR", "checker error: boom"),
            compute_fingerprint("case_a", "solver_x", "WRONG_ANSWER", "wrong answer: x"),
            compute_fingerprint(
                "case_a", "solver_x", "RESOURCE_EXCEEDED", "out of memory"
            ),
        }
        assert len(fps) == 5


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
        """Legacy fallback pin: empty qoi_failed (old data) keeps the
        historical all-measured-QoIs signature, so old fingerprints stay
        stable."""
        metrics = _metrics(qoi_pass=False, qoi_relative_errors={"cl": 0.5, "cd": 0.9})
        manifest = _manifest(status="success")
        assert build_signature(manifest, metrics, "TOLERANCE_EXCEEDED") == "qoi=cd,cl"

    def test_tolerance_exceeded_signature_uses_qoi_failed(self) -> None:
        """Stage-A semantics: the signature names only the QoIs that failed
        their gate, not every measured QoI."""
        metrics = _metrics(
            qoi_pass=False,
            qoi_relative_errors={"cl": 0.5, "cd": 0.9},
            qoi_failed=["cl"],
        )
        manifest = _manifest(status="success")
        assert build_signature(manifest, metrics, "TOLERANCE_EXCEEDED") == "qoi=cl"

    def test_distinct_failed_qoi_sets_get_distinct_fingerprints(self) -> None:
        """Over-dedup regression (Codex P2a): two runs measuring the same
        QoIs but failing different ones must not collapse into one record."""
        manifest = _manifest(status="success")
        errors = {"cl": 0.5, "cd": 0.9}
        m_cl = _metrics(qoi_pass=False, qoi_relative_errors=errors, qoi_failed=["cl"])
        m_cd = _metrics(qoi_pass=False, qoi_relative_errors=errors, qoi_failed=["cd"])
        sig_cl = build_signature(manifest, m_cl, "TOLERANCE_EXCEEDED")
        sig_cd = build_signature(manifest, m_cd, "TOLERANCE_EXCEEDED")
        fp_cl = compute_fingerprint("case_a", "solver_x", "TOLERANCE_EXCEEDED", sig_cl)
        fp_cd = compute_fingerprint("case_a", "solver_x", "TOLERANCE_EXCEEDED", sig_cd)
        assert fp_cl != fp_cd

    def test_tolerance_exceeded_signature_for_zero_reference_failure(self) -> None:
        """Absolute-tolerance (zero-reference) failures produce a proper
        qoi=... signature even with empty qoi_relative_errors."""
        metrics = _metrics(qoi_pass=False, qoi_failed=["cmy"])
        manifest = _manifest(status="success")
        assert build_signature(manifest, metrics, "TOLERANCE_EXCEEDED") == "qoi=cmy"

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

    def test_save_refuses_equal_count_replacement(self, tmp_path: Path) -> None:
        """Tamper witness: swapping one record for another (record count
        unchanged) must bite — the guard compares fingerprint sets, not
        sizes."""
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="timeout"),
            _metrics(),
        )
        lib = self._library(tmp_path)
        lib.ingest(runs)

        tampered = self._library(tmp_path)
        original = tampered.records()[0]
        replacement = original.model_copy(
            update={"fingerprint": "feedfacefeedface", "signature": "step=forged exit=1"}
        )
        tampered._records = {replacement.fingerprint: replacement}
        assert len(tampered._records) == 1  # same count as on disk
        with pytest.raises(RuntimeError, match="append-only"):
            tampered._save()
        # On-disk state untouched by the refused write.
        fresh = self._library(tmp_path)
        assert fresh.records()[0].fingerprint == original.fingerprint

    def test_ingest_skips_dry_run_without_counting_as_pass(self, tmp_path: Path) -> None:
        """A dry run verified nothing: it must not enter the library, must
        not count as a verified pass, and must not be marked ingested."""
        runs = tmp_path / "runs"
        _write_run(
            runs,
            _manifest(run_id="20260708T120000Z_case_a_solver_x_00000001", status="dry_run"),
            None,
        )
        lib = self._library(tmp_path)
        summary = lib.ingest(runs)
        assert summary.dry_run_skipped == 1
        assert summary.passed == 0
        assert summary.new_records == 0
        assert lib.records() == []

        # Re-ingest: still counted as dry_run_skipped, never already_ingested.
        summary2 = lib.ingest(runs)
        assert summary2.dry_run_skipped == 1
        assert summary2.already_ingested == 0
        assert summary2.passed == 0

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

    def test_new_a4_mode_ingest_preserves_append_only_superset_guard(
        self, tmp_path: Path
    ) -> None:
        """v5 §2.A4 SPEC item 3: the append-only guard is a fingerprint
        superset comparison, so a brand-new FailureMode (BUILD_FAILURE, not
        in the original v4 FAILURE_MODES tuple) must ingest cleanly alongside
        an existing CFD-mode record, and the on-disk fingerprint set must
        only grow — proving the guard needed zero changes to accept new
        modes."""
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
        assert {r["mode"] for r in before["records"]} == {"TIMEOUT"}

        _write_run(
            runs,
            _manifest(
                run_id="20260708T140000Z_case_b_solver_x_00000003",
                status="failed",
                step_details=[{"name": "build", "exit_code": 1, "status": "failed"}],
            ),
            _metrics(),
        )
        summary = lib.ingest(runs)
        assert summary.new_records == 1

        after = json.loads(path.read_text(encoding="utf-8"))
        after_modes = {r["mode"] for r in after["records"]}
        assert after_modes == {"TIMEOUT", "BUILD_FAILURE"}
        old_fps = {r["fingerprint"] for r in before["records"]}
        new_fps = {r["fingerprint"] for r in after["records"]}
        assert old_fps <= new_fps  # append-only guard: strictly a superset

        # A second FailureLibrary instance loading from disk must also see
        # both records (round-trip through the real pydantic FailureRecord
        # model, whose `mode: FailureMode` field must accept the new value).
        fresh = self._library(tmp_path)
        assert {r.mode for r in fresh.records()} == {"TIMEOUT", "BUILD_FAILURE"}


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
