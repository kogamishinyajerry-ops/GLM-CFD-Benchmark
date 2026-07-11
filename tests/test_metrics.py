"""Tests for cfdb.metrics."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.metrics.curves import compute_curve_l2
from cfdb.metrics.engine import MetricsEngine
from cfdb.metrics.performance import check_budget
from cfdb.schema import (
    BudgetSpec,
    CaseSpec,
    ConditionsSpec,
    MetricSpec,
    OutputSpec,
    PhysicsSpec,
    TimingSpec,
)

# NOTE (v5.0 A3): metrics/qoi.py (compute_qoi_errors) was deleted -- it was
# a dead P0 fork that silently skipped ref==0 QoIs, superseded by the
# engine.compute() path (absolute-tolerance gating, see
# tests/test_metrics_hardening.py::TestZeroReferenceAbsoluteTolerance).
# Its unit tests here are replaced by the equivalent engine-level
# assertions already present below (basic error / missing QoI) or in
# test_metrics_hardening.py (zero-reference handling); nothing here
# preserves the old silent-skip semantics.


class TestComputeCurveL2:
    def test_basic_l2(self) -> None:
        reference = {"cl": [(0.0, 0.0), (1.0, 1.0)]}
        computed = {"cl": [(0.0, 0.0), (1.0, 2.0)]}
        result = compute_curve_l2(reference, computed)
        assert "cl" in result
        assert abs(result["cl"] - 1.0) < 1e-9

    def test_length_mismatch(self) -> None:
        reference = {"cl": [(0.0, 0.0), (1.0, 1.0)]}
        computed = {"cl": [(0.0, 0.0)]}
        result = compute_curve_l2(reference, computed)
        assert "cl" not in result


class TestCheckBudget:
    def test_within_budget(self) -> None:
        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=5.0, start_time=now, end_time=now)
        budget = BudgetSpec(max_runtime_sec=10)
        notes = check_budget(timing, budget)
        assert notes == []

    def test_exceeds_budget(self) -> None:
        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=15.0, start_time=now, end_time=now)
        budget = BudgetSpec(max_runtime_sec=10)
        notes = check_budget(timing, budget)
        assert len(notes) == 1
        assert "budget exceeded" in notes[0]

    def test_no_budget_limit(self) -> None:
        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=100.0, start_time=now, end_time=now)
        budget = BudgetSpec()
        notes = check_budget(timing, budget)
        assert notes == []


def make_case(qoi_list: list[str], tolerance: dict[str, float]) -> CaseSpec:
    """Create a minimal CaseSpec for metrics testing."""
    return CaseSpec(
        id="test",
        name="Test",
        category="smoke",
        physics=PhysicsSpec(flow="incompressible"),
        conditions=ConditionsSpec(),
        solvers=[{"name": "generic", "command": "true"}],
        outputs=OutputSpec(qoi=qoi_list),
        metrics=MetricSpec(qoi_relative_tolerance=tolerance),
    )


class TestMetricsEngine:
    def test_compute_success_pass(self) -> None:
        artifacts = ArtifactManifest(qoi_values={"drag": 0.378})
        run_result = RunResult(
            exit_code=0, stdout="", stderr="", wall_time_sec=1.0
        )

        # We need a reference; use inline
        case_ref = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.05}),
            reference={"type": "analytical", "qoi_values": {"drag": 0.371}},
        )

        engine = MetricsEngine()
        result = engine.compute(case_ref, artifacts, run_result)
        assert result.qoi_pass is True
        assert result.overall_status == "pass"

    def test_compute_failed_run(self) -> None:
        case = make_case(["drag"], {"drag": 0.05})
        artifacts = ArtifactManifest(qoi_values={})
        run_result = RunResult(
            exit_code=1, stdout="", stderr="crash", wall_time_sec=0.5
        )

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert result.qoi_pass is False
        assert result.overall_status == "fail"
        assert "exited with code 1" in result.notes[0]

    def test_compute_missing_qoi(self) -> None:
        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.05}),
            reference={"type": "analytical", "qoi_values": {"drag": 0.371}},
        )
        artifacts = ArtifactManifest(qoi_values=None)
        run_result = RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0)

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert "missing computed QoI: drag" in result.notes
        assert result.overall_status == "incomplete"

    def test_compute_timeout(self) -> None:
        case = make_case(["drag"], {"drag": 0.05})
        artifacts = ArtifactManifest(qoi_values={})
        run_result = RunResult(
            exit_code=-1, stdout="", stderr="timeout", wall_time_sec=10.0, timed_out=True
        )

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert result.overall_status == "fail"
        assert any("timed out" in n for n in result.notes)

    def test_compute_tolerance_exceeded(self) -> None:
        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.01}),
            reference={"type": "analytical", "qoi_values": {"drag": 0.371}},
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.500})
        run_result = RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0)

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert result.qoi_pass is False
        assert result.overall_status == "fail"


def make_curve_case(
    curve_tolerance: dict[str, float] | None = None,
    reference_files: dict[str, str] | None = None,
) -> CaseSpec:
    """Create a minimal CaseSpec declaring one curve output ('cl_alpha')."""
    return CaseSpec(
        id="curve_test",
        name="Curve Test",
        category="smoke",
        physics=PhysicsSpec(flow="incompressible"),
        conditions=ConditionsSpec(),
        solvers=[{"name": "generic", "command": "true"}],
        outputs=OutputSpec(curves=["cl_alpha"]),
        metrics=MetricSpec(curve_l2_tolerance=curve_tolerance),
        reference={
            "type": "analytical",
            "files": reference_files or {},
        },
    )


class TestCurveL2Judgment:
    """v5.0 D1: engine.compute() wires compute_curve_l2 into the gate."""

    def test_no_curves_configured_is_zero_behavior_change(self) -> None:
        """outputs.curves == [] (every existing case) -> curve fields empty,
        overall_status driven by QoI alone (regression guard)."""
        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.05}),
            reference={"type": "analytical", "qoi_values": {"drag": 0.371}},
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372})
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
        )
        assert result.overall_status == "pass"
        assert result.curve_l2_errors == {}
        assert result.curves_failed == []
        assert result.ungated_curves == []

    def test_curve_within_tolerance_passes(self, tmp_path) -> None:
        ref_file = tmp_path / "cl_alpha.json"
        ref_file.write_text(json.dumps([[0.0, 0.0], [1.0, 1.0]]))
        case = make_curve_case(
            curve_tolerance={"cl_alpha": 0.5},
            reference_files={"cl_alpha": "cl_alpha.json"},
        )
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0), (1.0, 1.2)]})
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
            case_dir=tmp_path,
        )
        assert result.overall_status == "pass"
        assert result.curves_failed == []
        assert result.curve_l2_errors["cl_alpha"] == pytest.approx(0.2)

    def test_missing_computed_curve_is_incomplete(self, tmp_path) -> None:
        """artifacts.curves={} (adapter ran, produced no curves) -- distinct
        from curves=None (no collection infrastructure at all, see
        test_curves_is_none_is_zero_behavior_change)."""
        ref_file = tmp_path / "cl_alpha.json"
        ref_file.write_text(json.dumps([[0.0, 0.0], [1.0, 1.0]]))
        case = make_curve_case(
            curve_tolerance={"cl_alpha": 0.5},
            reference_files={"cl_alpha": "cl_alpha.json"},
        )
        artifacts = ArtifactManifest(curves={})
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
            case_dir=tmp_path,
        )
        assert result.overall_status == "incomplete"
        assert any("missing computed curve: cl_alpha" in n for n in result.notes)
        assert result.curve_l2_errors == {}

    def test_curves_is_none_is_zero_behavior_change(self, tmp_path) -> None:
        """artifacts.curves is None (every shipped adapter today) -> curve
        gating is a full no-op even when outputs.curves + tolerance are
        both configured (regression guard for the real naca0012 case)."""
        ref_file = tmp_path / "cl_alpha.json"
        ref_file.write_text(json.dumps([[0.0, 0.0], [1.0, 1.0]]))
        case = make_curve_case(
            curve_tolerance={"cl_alpha": 0.5},
            reference_files={"cl_alpha": "cl_alpha.json"},
        )
        artifacts = ArtifactManifest(curves=None)
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
            case_dir=tmp_path,
        )
        assert result.overall_status == "pass"
        assert result.curve_l2_errors == {}
        assert result.curves_failed == []
        assert result.ungated_curves == []
        assert not any("curve" in n.lower() for n in result.notes)

    def test_missing_reference_curve_is_incomplete(self, tmp_path) -> None:
        case = make_curve_case(curve_tolerance={"cl_alpha": 0.5}, reference_files={})
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0), (1.0, 1.2)]})
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
            case_dir=tmp_path,
        )
        assert result.overall_status == "incomplete"
        assert any("missing reference curve: cl_alpha" in n for n in result.notes)

    def test_shape_mismatch_is_incomplete(self, tmp_path) -> None:
        ref_file = tmp_path / "cl_alpha.json"
        ref_file.write_text(json.dumps([[0.0, 0.0], [1.0, 1.0]]))
        case = make_curve_case(
            curve_tolerance={"cl_alpha": 0.5},
            reference_files={"cl_alpha": "cl_alpha.json"},
        )
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0)]})
        result = MetricsEngine().compute(
            case,
            artifacts,
            RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=1.0),
            case_dir=tmp_path,
        )
        assert result.overall_status == "incomplete"
        assert any("shape mismatch" in n for n in result.notes)
