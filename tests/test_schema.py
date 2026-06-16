"""Tests for cfdb.schema — Pydantic model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cfdb.schema import (
    CaseSpec,
    ConditionsSpec,
    MetricsResult,
    OutputSpec,
    PhysicsSpec,
    RunManifest,
    SolverConfig,
    TimingSpec,
)


class TestPhysicsSpec:
    def test_valid_physics(self) -> None:
        spec = PhysicsSpec(flow="incompressible", dimensionality="2d", steady=True)
        assert spec.flow == "incompressible"
        assert spec.steady is True

    def test_default_dimensionality(self) -> None:
        spec = PhysicsSpec(flow="rans")
        assert spec.dimensionality == "2d"

    def test_invalid_flow(self) -> None:
        with pytest.raises(ValidationError):
            PhysicsSpec(flow="invalid_flow")

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            PhysicsSpec(flow="rans", extra_field="bad")


class TestConditionsSpec:
    def test_valid_conditions(self) -> None:
        c = ConditionsSpec(reynolds=100.0, mach=0.3, alpha_deg=5.0)
        assert c.reynolds == 100.0

    def test_reynolds_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ConditionsSpec(reynolds=-1.0)

    def test_reynolds_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConditionsSpec(reynolds=0)

    def test_alpha_range(self) -> None:
        with pytest.raises(ValidationError):
            ConditionsSpec(alpha_deg=100.0)

    def test_mach_can_be_zero(self) -> None:
        c = ConditionsSpec(mach=0.0)
        assert c.mach == 0.0


class TestSolverConfig:
    def test_valid_config(self) -> None:
        s = SolverConfig(name="generic", command="bash run.sh")
        assert s.name == "generic"

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SolverConfig(name="generic", command="run", timeout_sec=-1)


class TestOutputSpec:
    def test_defaults(self) -> None:
        o = OutputSpec()
        assert o.fields == []
        assert o.curves == []
        assert o.qoi == []

    def test_with_values(self) -> None:
        o = OutputSpec(fields=["U", "p"], qoi=["drag_coeff"])
        assert "U" in o.fields
        assert "drag_coeff" in o.qoi


class TestCaseSpec:
    def test_valid_case(self, sample_case_spec_data: dict) -> None:
        case = CaseSpec.model_validate(sample_case_spec_data)
        assert case.id == "test_case"
        assert case.category == "smoke"
        assert len(case.solvers) == 1

    def test_invalid_id_uppercase(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data["id"] = "TestCase"
        with pytest.raises(ValidationError):
            CaseSpec.model_validate(sample_case_spec_data)

    def test_invalid_id_starts_with_digit(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data["id"] = "1test"
        with pytest.raises(ValidationError):
            CaseSpec.model_validate(sample_case_spec_data)

    def test_invalid_category(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data["category"] = "invalid"
        with pytest.raises(ValidationError):
            CaseSpec.model_validate(sample_case_spec_data)

    def test_empty_solvers(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data["solvers"] = []
        with pytest.raises(ValidationError):
            CaseSpec.model_validate(sample_case_spec_data)

    def test_extra_field_forbidden(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data["custom_field"] = "bad"
        with pytest.raises(ValidationError):
            CaseSpec.model_validate(sample_case_spec_data)

    def test_optional_geometry_none(self, sample_case_spec_data: dict) -> None:
        case = CaseSpec.model_validate(sample_case_spec_data)
        assert case.geometry is None

    def test_budget_default(self, sample_case_spec_data: dict) -> None:
        sample_case_spec_data.pop("budget", None)
        case = CaseSpec.model_validate(sample_case_spec_data)
        assert case.budget.max_runtime_sec is None


class TestTimingSpec:
    def test_valid_timing(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        t = TimingSpec(wall_time_sec=1.5, start_time=now, end_time=now)
        assert t.wall_time_sec == 1.5

    def test_negative_wall_time(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            TimingSpec(wall_time_sec=-1, start_time=now, end_time=now)


class TestRunManifest:
    def test_valid_manifest(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="generic",
            status="success",
            timing=timing,
        )
        assert m.backend == "local"
        assert m.error is None

    def test_invalid_status(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        with pytest.raises(ValidationError):
            RunManifest(
                run_id="test", case_id="t", solver="generic",
                status="running", timing=timing,
            )

    def test_p1b_solver_version_and_residuals_default_none(self) -> None:
        """P1-b fields default to None (backward compat with P0/P1-a)."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="generic",
            status="success",
            timing=timing,
        )
        assert m.solver_version is None
        assert m.final_residuals is None

    def test_p1b_solver_version_and_residuals_set(self) -> None:
        """P1-b fields can be set with values."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="openfoam",
            status="success",
            timing=timing,
            solver_version="OpenFOAM v2406",
            final_residuals={"Ux": 1.2e-6, "Uy": 2.1e-6, "p": 3.4e-5},
        )
        assert m.solver_version == "OpenFOAM v2406"
        assert m.final_residuals is not None
        assert m.final_residuals["Ux"] == 1.2e-6

    def test_p1b_json_roundtrip(self) -> None:
        """JSON serialize/deserialize preserves P1-b fields."""
        import json

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="openfoam",
            status="success",
            timing=timing,
            solver_version="OpenFOAM v2406",
            final_residuals={"Ux": 1.2e-6},
        )
        json_str = m.model_dump_json()
        data = json.loads(json_str)
        assert data["solver_version"] == "OpenFOAM v2406"
        assert data["final_residuals"]["Ux"] == 1.2e-6

        # Round-trip: reconstruct from JSON
        m2 = RunManifest.model_validate_json(json_str)
        assert m2.solver_version == "OpenFOAM v2406"
        assert m2.final_residuals == {"Ux": 1.2e-6}

    def test_p1b_backward_compat_no_fields(self) -> None:
        """Old JSON without P1-b fields loads fine (fields default to None)."""
        import json

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        old_data = {
            "run_id": "20260616T120000Z_test_generic_abcd1234",
            "case_id": "test",
            "solver": "generic",
            "status": "success",
            "timing": timing.model_dump(mode="json"),
        }
        m = RunManifest.model_validate(old_data)
        assert m.solver_version is None
        assert m.final_residuals is None

    def test_p2a_new_fields_default_none(self) -> None:
        """P2-a fields (cell_count, step_details, residuals_history) default to None."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="generic",
            status="success",
            timing=timing,
        )
        assert m.cell_count is None
        assert m.step_details is None
        assert m.residuals_history is None

    def test_p2a_new_fields_set(self) -> None:
        """P2-a fields can be set with values."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="openfoam",
            status="success",
            timing=timing,
            cell_count=400,
            step_details=[
                {"name": "block_mesh", "exit_code": 0, "wall_time_sec": 1.5, "status": "success"},
            ],
            residuals_history={"Ux": [1e-1, 1e-2, 1e-3]},
        )
        assert m.cell_count == 400
        assert m.step_details is not None
        assert len(m.step_details) == 1
        assert m.residuals_history is not None
        assert m.residuals_history["Ux"] == [1e-1, 1e-2, 1e-3]

    def test_p2a_json_roundtrip(self) -> None:
        """P2-a fields survive JSON serialize/deserialize."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        m = RunManifest(
            run_id="20260616T120000Z_test_generic_abcd1234",
            case_id="test",
            solver="openfoam",
            status="success",
            timing=timing,
            cell_count=400,
            residuals_history={"Ux": [1e-1, 1e-2, 1e-3]},
        )
        json_str = m.model_dump_json()
        m2 = RunManifest.model_validate_json(json_str)
        assert m2.cell_count == 400
        assert m2.residuals_history is not None
        assert m2.residuals_history["Ux"] == [1e-1, 1e-2, 1e-3]

    def test_p2a_backward_compat_old_json(self) -> None:
        """Old JSON without P2-a fields loads fine."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
        old_data = {
            "run_id": "20260616T120000Z_test_generic_abcd1234",
            "case_id": "test",
            "solver": "generic",
            "status": "success",
            "timing": timing.model_dump(mode="json"),
        }
        m = RunManifest.model_validate(old_data)
        assert m.cell_count is None
        assert m.step_details is None
        assert m.residuals_history is None


class TestMetricsResult:
    def test_defaults(self) -> None:
        m = MetricsResult()
        assert m.qoi_pass is False
        assert m.overall_status == "unknown"
        assert m.notes == []

    def test_with_errors(self) -> None:
        m = MetricsResult(
            qoi_relative_errors={"drag": 0.01},
            qoi_pass=True,
            overall_status="pass",
        )
        assert m.qoi_pass is True
        assert m.overall_status == "pass"
