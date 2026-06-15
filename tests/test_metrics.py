"""Tests for cfdb.metrics."""

from __future__ import annotations

from datetime import datetime, timezone

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.metrics.curves import compute_curve_l2
from cfdb.metrics.engine import MetricsEngine
from cfdb.metrics.performance import check_budget
from cfdb.metrics.qoi import compute_qoi_errors
from cfdb.schema import (
    BudgetSpec,
    CaseSpec,
    ConditionsSpec,
    MetricSpec,
    OutputSpec,
    PhysicsSpec,
    TimingSpec,
)


class TestComputeQoiErrors:
    def test_basic_error(self) -> None:
        reference = {"drag": 0.371}
        computed = {"drag": 0.380}
        errors = compute_qoi_errors(reference, computed)
        assert "drag" in errors
        expected = abs(0.380 - 0.371) / abs(0.371)
        assert abs(errors["drag"] - expected) < 1e-9

    def test_missing_in_computed(self) -> None:
        reference = {"drag": 0.371, "lift": 0.1}
        computed = {"drag": 0.380}
        errors = compute_qoi_errors(reference, computed)
        assert "drag" in errors
        assert "lift" not in errors

    def test_zero_reference_skipped(self) -> None:
        reference = {"zero_qoi": 0.0}
        computed = {"zero_qoi": 1.0}
        errors = compute_qoi_errors(reference, computed)
        assert "zero_qoi" not in errors

    def test_empty_dicts(self) -> None:
        errors = compute_qoi_errors({}, {})
        assert errors == {}


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
