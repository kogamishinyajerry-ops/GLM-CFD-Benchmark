"""Tests for P4-G metrics honesty hardening (fail-closed silent-pass holes).

Covers the three recon-verified silent-pass holes:

1. ref==0 exemption: zero-reference QoIs must be gated via absolute
   tolerance, or drive the run to 'incomplete' (never silent pass).
2. Unconfigured tolerance: QoIs with a computed error but no tolerance
   stay ungated (compatible) but are disclosed in ``ungated_qoi``.
3. Budget overrun: still warning-only, but exposed as ``budget_exceeded``.

Each hole has a regression test plus a tamper witness (tampering the
input must flip the verdict).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.metrics.engine import MetricsEngine
from cfdb.schema import (
    BudgetSpec,
    CaseSpec,
    ConditionsSpec,
    MetricSpec,
    MetricsResult,
    OutputSpec,
    PhysicsSpec,
    TimingSpec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
NACA0012_YAML = REPO_ROOT / "cases" / "validation" / "naca0012" / "case.yaml"


def make_case(
    qoi_list: list[str],
    reference_qoi: dict[str, float],
    relative_tolerance: dict[str, float] | None = None,
    absolute_tolerance: dict[str, float] | None = None,
    budget: BudgetSpec | None = None,
) -> CaseSpec:
    """Create a minimal CaseSpec for hardening tests."""
    return CaseSpec(
        id="hardening_test",
        name="Hardening Test",
        category="smoke",
        physics=PhysicsSpec(flow="incompressible"),
        conditions=ConditionsSpec(),
        solvers=[{"name": "generic", "command": "true"}],
        outputs=OutputSpec(qoi=qoi_list),
        metrics=MetricSpec(
            qoi_relative_tolerance=relative_tolerance or {},
            qoi_absolute_tolerance=absolute_tolerance or {},
        ),
        reference={"type": "analytical", "qoi_values": reference_qoi},
        budget=budget or BudgetSpec(),
    )


def make_run(wall_time_sec: float = 1.0) -> RunResult:
    """Create a successful RunResult."""
    return RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=wall_time_sec)


class TestZeroReferenceAbsoluteTolerance:
    """Hole 1: ref==0 QoIs must be gated or drive incomplete."""

    def test_zero_ref_with_absolute_tolerance_passes(self) -> None:
        case = make_case(
            ["cl"], {"cl": 0.0}, absolute_tolerance={"cl": 0.01}
        )
        artifacts = ArtifactManifest(qoi_values={"cl": 0.005})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"
        assert result.qoi_pass is True
        assert any("passed absolute tolerance" in n for n in result.notes)

    def test_zero_ref_absolute_tolerance_bites_on_bad_value(self) -> None:
        """Tamper witness: computed value beyond tolerance must fail."""
        case = make_case(
            ["cl"], {"cl": 0.0}, absolute_tolerance={"cl": 0.01}
        )
        artifacts = ArtifactManifest(qoi_values={"cl": 0.05})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_pass is False
        assert any("failed absolute tolerance" in n for n in result.notes)

    def test_zero_ref_without_absolute_tolerance_is_incomplete(self) -> None:
        """No configured absolute tolerance -> incomplete, never silent pass."""
        case = make_case(["cl"], {"cl": 0.0})
        artifacts = ArtifactManifest(qoi_values={"cl": 0.005})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "incomplete"
        assert result.qoi_pass is False
        assert any(
            "missing absolute tolerance for zero-reference QoI 'cl'" in n
            for n in result.notes
        )

    def test_boundary_error_equal_to_tolerance_passes(self) -> None:
        case = make_case(
            ["cl"], {"cl": 0.0}, absolute_tolerance={"cl": 0.01}
        )
        artifacts = ArtifactManifest(qoi_values={"cl": 0.01})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"

    def test_nonzero_ref_still_uses_relative_error(self) -> None:
        """Absolute tolerance only applies to zero-reference QoIs."""
        case = make_case(
            ["cd"],
            {"cd": 0.0086},
            relative_tolerance={"cd": 0.10},
            absolute_tolerance={"cd": 1e-9},
        )
        artifacts = ArtifactManifest(qoi_values={"cd": 0.0090})
        result = MetricsEngine().compute(case, artifacts, make_run())
        # Relative error ~4.7% < 10% -> pass; the (tight) absolute
        # tolerance is not consulted because ref != 0.
        assert result.overall_status == "pass"


class TestNaca0012TamperWitness:
    """Contract-mandated witness on the real naca0012 case.yaml."""

    def _load_case(self) -> dict:
        with NACA0012_YAML.open(encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_shipped_yaml_has_cl_absolute_tolerance(self) -> None:
        raw = self._load_case()
        assert raw["metrics"]["qoi_absolute_tolerance"] == {"cl": 0.01}

    def test_shipped_yaml_gates_zero_reference_cl(self) -> None:
        case = CaseSpec.model_validate(self._load_case())
        artifacts = ArtifactManifest(qoi_values={"cl": 0.002, "cd": 0.0088})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"
        assert any("passed absolute tolerance" in n for n in result.notes)

    def test_removing_cl_absolute_tolerance_forces_incomplete(self) -> None:
        """Tamper witness: delete the cl absolute tolerance -> incomplete."""
        raw = copy.deepcopy(self._load_case())
        del raw["metrics"]["qoi_absolute_tolerance"]["cl"]
        case = CaseSpec.model_validate(raw)
        artifacts = ArtifactManifest(qoi_values={"cl": 0.002, "cd": 0.0088})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "incomplete"
        assert result.qoi_pass is False
        assert any(
            "missing absolute tolerance for zero-reference QoI 'cl'" in n
            for n in result.notes
        )


class TestUngatedQoiDisclosure:
    """Hole 2: declared QoIs without a tolerance are disclosed, not hidden."""

    def test_ungated_qoi_listed_without_affecting_verdict(self) -> None:
        case = make_case(
            ["drag", "lift"],
            {"drag": 0.371, "lift": 0.5},
            relative_tolerance={"drag": 0.05},
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372, "lift": 5.0})
        result = MetricsEngine().compute(case, artifacts, make_run())
        # lift is wildly off (900% error) but has no tolerance: verdict
        # unchanged (compatible), yet the gap is disclosed.
        assert result.overall_status == "pass"
        assert result.ungated_qoi == ["lift"]
        assert any("ungated QoI 'lift'" in n for n in result.notes)

    def test_all_gated_yields_empty_ungated_list(self) -> None:
        case = make_case(
            ["drag"], {"drag": 0.371}, relative_tolerance={"drag": 0.05}
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.ungated_qoi == []

    def test_configuring_tolerance_makes_gate_bite(self) -> None:
        """Tamper witness: same bad value, tolerance added -> fail."""
        case = make_case(
            ["drag", "lift"],
            {"drag": 0.371, "lift": 0.5},
            relative_tolerance={"drag": 0.05, "lift": 0.05},
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372, "lift": 5.0})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.ungated_qoi == []


class TestBudgetExceededFlag:
    """Hole 3: budget overrun exposed as a structured flag."""

    def _timing(self, wall: float) -> TimingSpec:
        now = datetime.now(timezone.utc)
        return TimingSpec(wall_time_sec=wall, start_time=now, end_time=now)

    def test_within_budget_flag_false(self) -> None:
        case = make_case(
            ["drag"],
            {"drag": 0.371},
            relative_tolerance={"drag": 0.05},
            budget=BudgetSpec(max_runtime_sec=10),
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372})
        result = MetricsEngine().compute(
            case, artifacts, make_run(), timing=self._timing(5.0)
        )
        assert result.budget_exceeded is False

    def test_exceeded_budget_flag_true_but_status_unchanged(self) -> None:
        """Tamper witness: exceed the budget -> flag must flip to True."""
        case = make_case(
            ["drag"],
            {"drag": 0.371},
            relative_tolerance={"drag": 0.05},
            budget=BudgetSpec(max_runtime_sec=10),
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372})
        result = MetricsEngine().compute(
            case, artifacts, make_run(), timing=self._timing(15.0)
        )
        assert result.budget_exceeded is True
        # Warning semantics preserved: budget never flips the verdict.
        assert result.overall_status == "pass"
        assert any("budget exceeded" in n for n in result.notes)

    def test_wall_time_fallback_path_sets_flag(self) -> None:
        """timing=None path (wall time from RunResult) also sets the flag."""
        case = make_case(
            ["drag"],
            {"drag": 0.371},
            relative_tolerance={"drag": 0.05},
            budget=BudgetSpec(max_runtime_sec=10),
        )
        artifacts = ArtifactManifest(qoi_values={"drag": 0.372})
        result = MetricsEngine().compute(
            case, artifacts, make_run(wall_time_sec=20.0)
        )
        assert result.budget_exceeded is True


class TestBackwardCompatibility:
    """New MetricsResult fields default so old persisted data stays readable."""

    def test_old_payload_without_new_fields_validates(self) -> None:
        old_payload = {
            "qoi_relative_errors": {"drag": 0.01},
            "qoi_pass": True,
            "overall_status": "pass",
            "notes": [],
        }
        result = MetricsResult.model_validate(old_payload)
        assert result.ungated_qoi == []
        assert result.budget_exceeded is False

    def test_old_metric_spec_without_absolute_tolerance_validates(self) -> None:
        spec = MetricSpec.model_validate(
            {"qoi_relative_tolerance": {"drag": 0.05}}
        )
        assert spec.qoi_absolute_tolerance == {}
