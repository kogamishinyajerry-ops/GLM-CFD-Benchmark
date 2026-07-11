"""Tests for P4-G metrics honesty hardening (fail-closed silent-pass holes).

Covers the three recon-verified silent-pass holes:

1. ref==0 exemption: zero-reference QoIs must be gated via absolute
   tolerance, or drive the run to 'incomplete' (never silent pass).
2. Unconfigured tolerance: QoIs with a computed error but no tolerance
   stay ungated (compatible) but are disclosed in ``ungated_qoi``.
3. Budget overrun: still warning-only, but exposed as ``budget_exceeded``.

Plus the NaN false-green family (Codex P1 + adversarial wave 2xP1):

4. Non-finite computed QoI -> hard 'fail' (diverged solution is never
   'incomplete' or a silent pass), value excluded from errors/values.
5. Schema defense in depth: NaN/Inf rejected at parse time in
   qoi_relative_errors / qoi_absolute_errors / qoi_computed_values.
6. Downstream visibility: qoi_absolute_errors (zero-reference deviations)
   and qoi_failed (sorted union of tolerance violators).

Plus v5.0 Wave D1 (curve L2 gating -- compute_curve_l2 was implemented in
metrics/curves.py but never wired into engine.compute()):

7. Curve L2 gating: TestCurveL2Gating -- over-tolerance curve -> fail,
   missing/mismatched shape -> incomplete, no tolerance -> ungated_curves
   disclosure, non-finite L2 -> hard fail, no-curves-configured -> zero
   behavior change.

Each hole has a regression test plus a tamper witness (tampering the
input must flip the verdict / must be rejected).
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

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
        assert result.qoi_absolute_errors == pytest.approx({"cl": 0.005})
        assert result.qoi_failed == []

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
        assert result.qoi_failed == ["cl"]
        assert result.qoi_absolute_errors == pytest.approx({"cl": 0.05})

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
        # Deviation is still recorded even without a configured tolerance.
        assert result.qoi_absolute_errors == pytest.approx({"cl": 0.005})

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
        assert result.qoi_absolute_errors == pytest.approx({"cl": 0.002})
        assert result.qoi_failed == []

    def test_cl_over_absolute_tolerance_appears_in_qoi_failed(self) -> None:
        """Tamper witness: cl beyond its absolute tolerance -> 'cl' listed."""
        case = CaseSpec.model_validate(self._load_case())
        artifacts = ArtifactManifest(qoi_values={"cl": 0.5, "cd": 0.0088})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert "cl" in result.qoi_failed
        assert result.qoi_absolute_errors == pytest.approx({"cl": 0.5})

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
        assert result.qoi_absolute_errors == {}
        assert result.qoi_failed == []

    def test_old_metric_spec_without_absolute_tolerance_validates(self) -> None:
        spec = MetricSpec.model_validate(
            {"qoi_relative_tolerance": {"drag": 0.05}}
        )
        assert spec.qoi_absolute_tolerance == {}


class TestNonFiniteComputedQoI:
    """NaN family hole 4: non-finite computed values are a hard 'fail'."""

    def test_nan_computed_fails_not_incomplete(self) -> None:
        """Tamper witness: NaN computed value -> fail, excluded everywhere."""
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": float("nan")})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_pass is False
        assert "cd" not in result.qoi_relative_errors
        assert result.qoi_computed_values is None
        assert any("non-finite computed QoI 'cd'" in n for n in result.notes)

    def test_inf_computed_fails(self) -> None:
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": float("inf")})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_computed_values is None

    def test_nan_zero_reference_computed_fails(self) -> None:
        """isfinite guard on the absolute-tolerance path: NaN must fail."""
        case = make_case(
            ["cl"], {"cl": 0.0}, absolute_tolerance={"cl": 0.01}
        )
        artifacts = ArtifactManifest(qoi_values={"cl": float("nan")})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_pass is False
        assert "cl" not in result.qoi_absolute_errors
        assert any("non-finite computed QoI 'cl'" in n for n in result.notes)

    def test_nan_reference_fails(self) -> None:
        """Non-finite reference values fail-closed too (never crash/pass)."""
        case = make_case(
            ["cd"], {"cd": float("nan")}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": 0.009})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert "cd" not in result.qoi_relative_errors
        assert any("non-finite reference QoI 'cd'" in n for n in result.notes)

    def test_non_finite_dominates_missing(self) -> None:
        """Mixed missing + NaN -> 'fail' (divergence outranks incomplete)."""
        case = make_case(
            ["cd", "cm"],
            {"cd": 0.0086, "cm": 0.1},
            relative_tolerance={"cd": 0.10, "cm": 0.10},
        )
        artifacts = ArtifactManifest(qoi_values={"cd": float("nan")})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"

    def test_nan_result_round_trips_through_json(self) -> None:
        """Tamper witness: the fail verdict persists and re-parses cleanly."""
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": float("nan")})
        result = MetricsEngine().compute(case, artifacts, make_run())
        payload = result.model_dump_json()
        restored = MetricsResult.model_validate_json(payload)
        assert restored.overall_status == "fail"
        assert restored.qoi_pass is False

    def test_finite_values_alongside_nan_are_still_reported(self) -> None:
        case = make_case(
            ["cl", "cd"],
            {"cl": 0.5, "cd": 0.0086},
            relative_tolerance={"cl": 0.10, "cd": 0.10},
        )
        artifacts = ArtifactManifest(
            qoi_values={"cl": 0.51, "cd": float("nan")}
        )
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_computed_values == {"cl": 0.51}
        assert "cl" in result.qoi_relative_errors


class TestSchemaRejectsNonFinite:
    """NaN family hole 5: parse-layer rejection (defense in depth)."""

    def test_nan_in_relative_errors_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsResult.model_validate(
                {"qoi_relative_errors": {"cd": float("nan")}}
            )

    def test_inf_in_computed_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsResult.model_validate(
                {"qoi_computed_values": {"cl": float("inf")}}
            )

    def test_nan_in_absolute_errors_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsResult.model_validate(
                {"qoi_absolute_errors": {"cl": float("nan")}}
            )

    def test_nan_in_persisted_json_rejected(self) -> None:
        """Tamper witness: NaN injected into a metrics.json must be refused."""
        payload = (
            '{"qoi_relative_errors": {"cd": NaN}, "qoi_pass": true, '
            '"overall_status": "pass", "notes": []}'
        )
        with pytest.raises(ValidationError):
            MetricsResult.model_validate_json(payload)

    def test_finite_payload_round_trips(self) -> None:
        original = MetricsResult(
            qoi_relative_errors={"cd": 0.02},
            qoi_pass=True,
            overall_status="pass",
            notes=[],
            qoi_computed_values={"cd": 0.0088},
            qoi_absolute_errors={"cl": 0.002},
            qoi_failed=[],
        )
        restored = MetricsResult.model_validate_json(original.model_dump_json())
        assert restored == original
        assert math.isfinite(restored.qoi_relative_errors["cd"])


class TestQoiFailedUnion:
    """NaN family hole 6: qoi_failed is the sorted union of violators."""

    def test_relative_failure_listed(self) -> None:
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.01}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": 0.02})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_failed == ["cd"]

    def test_union_of_relative_and_absolute_failures_sorted(self) -> None:
        case = make_case(
            ["cl", "cd"],
            {"cl": 0.0, "cd": 0.0086},
            relative_tolerance={"cd": 0.01},
            absolute_tolerance={"cl": 0.01},
        )
        artifacts = ArtifactManifest(qoi_values={"cl": 0.5, "cd": 0.02})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "fail"
        assert result.qoi_failed == ["cd", "cl"]

    def test_pass_yields_empty_qoi_failed(self) -> None:
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": 0.0088})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"
        assert result.qoi_failed == []


def make_curve_case(
    tmp_path: Path,
    curve_tolerance: dict[str, float] | None,
    reference_points: list[list[float]],
) -> CaseSpec:
    """Build a CaseSpec declaring curve 'cl_alpha' with a JSON reference file
    written under tmp_path (case_dir), for v5.0 D1 curve-gating tests."""
    ref_file = tmp_path / "cl_alpha.json"
    ref_file.write_text(json.dumps(reference_points))
    return CaseSpec(
        id="curve_hardening_test",
        name="Curve Hardening Test",
        category="smoke",
        physics=PhysicsSpec(flow="incompressible"),
        conditions=ConditionsSpec(),
        solvers=[{"name": "generic", "command": "true"}],
        outputs=OutputSpec(curves=["cl_alpha"]),
        metrics=MetricSpec(curve_l2_tolerance=curve_tolerance),
        reference={
            "type": "analytical",
            "files": {"cl_alpha": "cl_alpha.json"},
        },
    )


class TestCurveL2Gating:
    """v5.0 D1: compute_curve_l2 wired into the gate (previously dead code
    per the wave-D recon: implemented in metrics/curves.py, never called
    from engine.compute()). Mirrors the QoI hardening pattern -- regression
    test + tamper witness for both the 'bites' and 'discloses' directions.
    """

    def test_curve_over_tolerance_fails(self, tmp_path: Path) -> None:
        """Positive bite: L2 exceeds the configured tolerance -> fail."""
        case = make_curve_case(
            tmp_path,
            curve_tolerance={"cl_alpha": 0.05},
            reference_points=[[0.0, 0.0], [1.0, 1.0]],
        )
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0), (1.0, 2.0)]})
        result = MetricsEngine().compute(case, artifacts, make_run(), case_dir=tmp_path)
        assert result.overall_status == "fail"
        assert result.curves_failed == ["cl_alpha"]
        assert result.curve_l2_errors["cl_alpha"] == pytest.approx(1.0)
        assert any("failed L2 tolerance" in n for n in result.notes)

    def test_curve_within_tolerance_passes(self, tmp_path: Path) -> None:
        """Untampered baseline: same shape, well within tolerance -> pass."""
        case = make_curve_case(
            tmp_path,
            curve_tolerance={"cl_alpha": 0.05},
            reference_points=[[0.0, 0.0], [1.0, 1.0]],
        )
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0), (1.0, 1.01)]})
        result = MetricsEngine().compute(case, artifacts, make_run(), case_dir=tmp_path)
        assert result.overall_status == "pass"
        assert result.curves_failed == []

    def test_removing_tolerance_discloses_as_ungated_not_silent(
        self, tmp_path: Path
    ) -> None:
        """Tamper witness (inverse direction): delete the configured
        tolerance on the exact same bad curve from
        test_curve_over_tolerance_fails -- the L2 value must resurface in
        ungated_curves (disclosed) rather than vanishing, and must NOT
        force a fail on its own (compatible/ungated semantics, same as
        ungated_qoi)."""
        case = make_curve_case(
            tmp_path,
            curve_tolerance=None,
            reference_points=[[0.0, 0.0], [1.0, 1.0]],
        )
        artifacts = ArtifactManifest(curves={"cl_alpha": [(0.0, 0.0), (1.0, 2.0)]})
        result = MetricsEngine().compute(case, artifacts, make_run(), case_dir=tmp_path)
        assert result.overall_status == "pass"
        assert result.curves_failed == []
        assert result.ungated_curves == ["cl_alpha"]
        assert result.curve_l2_errors["cl_alpha"] == pytest.approx(1.0)
        assert any("ungated curve 'cl_alpha'" in n for n in result.notes)

    def test_non_finite_curve_l2_is_hard_fail(self, tmp_path: Path) -> None:
        """NaN family parity: a non-finite computed curve point produces a
        non-finite L2 -> hard 'fail', never silently dropped/incomplete."""
        case = make_curve_case(
            tmp_path,
            curve_tolerance={"cl_alpha": 0.05},
            reference_points=[[0.0, 0.0], [1.0, 1.0]],
        )
        artifacts = ArtifactManifest(
            curves={"cl_alpha": [(0.0, 0.0), (1.0, float("nan"))]}
        )
        result = MetricsEngine().compute(case, artifacts, make_run(), case_dir=tmp_path)
        assert result.overall_status == "fail"
        assert "cl_alpha" not in result.curve_l2_errors
        assert any("non-finite curve L2" in n for n in result.notes)

    def test_no_curves_configured_zero_behavior_change(self) -> None:
        """Regression guard: every case shipped today has outputs.curves ==
        [] -- confirm the curve-gating loop is a true no-op alongside a
        QoI-only pass/fail decision."""
        case = make_case(
            ["cd"], {"cd": 0.0086}, relative_tolerance={"cd": 0.10}
        )
        artifacts = ArtifactManifest(qoi_values={"cd": 0.0088})
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"
        assert result.curve_l2_errors == {}
        assert result.curves_failed == []
        assert result.ungated_curves == []

    def test_shipped_naca0012_curve_config_stays_inert_without_adapter_data(
        self,
    ) -> None:
        """Concrete regression guard: the real naca0012 case.yaml already
        declares outputs.curves=['cp_distribution'] + a configured
        curve_l2_tolerance (dead-lettered pre-D1 since nothing ever called
        compute_curve_l2). No shipped adapter populates artifacts.curves
        (always None) -- confirm this real case's QoI-only verdict is
        unaffected by wiring the gate live (mirrors
        TestNaca0012TamperWitness, which exercises the same case.yaml with
        artifacts.curves left at its default None)."""
        with NACA0012_YAML.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        case = CaseSpec.model_validate(raw)
        assert case.outputs.curves == ["cp_distribution"]
        assert case.metrics.curve_l2_tolerance == {"cp_distribution": 0.05}
        artifacts = ArtifactManifest(qoi_values={"cl": 0.002, "cd": 0.0088})
        assert artifacts.curves is None
        result = MetricsEngine().compute(case, artifacts, make_run())
        assert result.overall_status == "pass"
        assert result.curve_l2_errors == {}
        assert result.curves_failed == []
        assert result.ungated_curves == []
