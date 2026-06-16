"""Tests for cfdb.adapters.su2.SU2Adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.base import RunResult
from cfdb.adapters.su2 import SU2Adapter
from cfdb.schema import CaseSpec, SolverConfig


def make_su2_case(steps=None, parameters=None) -> CaseSpec:
    """Create a minimal CaseSpec with a 'su2' solver config."""
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    solver = SolverConfig(
        name="su2",
        command="SU2_CFD {{ run_dir }}/case/{{ case_id }}.cfg",
        steps=steps,
        parameters=parameters,
    )
    return CaseSpec(
        id="flat_plate_su2",
        name="Flat Plate",
        category="verification",
        physics=PhysicsSpec(flow="incompressible", dimensionality="2d", steady=True),
        conditions=ConditionsSpec(reynolds=1000000.0, mach=0.3, alpha_deg=0.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["U", "p"], qoi=["skin_friction_coeff"]),
        metrics=MetricSpec(qoi_relative_tolerance={"skin_friction_coeff": 0.1}),
    )


@pytest.fixture
def su2_case() -> CaseSpec:
    """CaseSpec with su2 solver steps."""
    from cfdb.schema import CommandStep

    steps = [
        CommandStep(
            name="solve",
            command="SU2_CFD {{ run_dir }}/case/{{ case_id }}.cfg",
        ),
    ]
    return make_su2_case(steps=steps)


@pytest.fixture
def su2_case_no_steps() -> CaseSpec:
    """CaseSpec with su2 solver but no steps."""
    return make_su2_case(steps=None)


class TestSU2AdapterInit:
    def test_name(self) -> None:
        adapter = SU2Adapter()
        assert adapter.name == "su2"

    def test_default_dry_run_false(self) -> None:
        adapter = SU2Adapter()
        assert adapter._dry_run is False

    def test_dry_run_true(self) -> None:
        adapter = SU2Adapter(dry_run=True)
        assert adapter._dry_run is True


class TestSU2Prepare:
    def test_prepare_creates_cfg_and_mesh(
        self, su2_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(su2_case, case_dir, run_dir)

        case_out = run_dir / "case"
        assert (case_out / "flat_plate_su2.cfg").exists()
        assert (case_out / "mesh.su2").exists()

    def test_cfg_contains_rendered_values(
        self, su2_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(su2_case, case_dir, run_dir)

        cfg = (run_dir / "case" / "flat_plate_su2.cfg").read_text()
        assert "flat_plate_su2" in cfg  # case_id
        assert "0.3" in cfg  # mach
        assert "1000000" in cfg or "1e+06" in cfg  # reynolds
        assert "SOLVER= NAVIER_STOKES" in cfg

    def test_mesh_placeholder(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(su2_case, case_dir, run_dir)

        mesh = (run_dir / "case" / "mesh.su2").read_text()
        assert "SU2 placeholder" in mesh


class TestSU2Run:
    def test_run_dry_run_with_steps(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        result = adapter.run(su2_case, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1
        assert "SU2_CFD" in result.skipped_commands[0]

    def test_run_dry_run_without_steps(
        self, su2_case_no_steps: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case_no_steps, case_dir, run_dir)

        result = adapter.run(su2_case_no_steps, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1

    def test_run_real_execution_success(
        self, su2_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test real execution path with mocked LocalExecutionBackend."""
        adapter = SU2Adapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "SU2 Code Suite, Version 8.0.0\n"
                "RMS_DENSITY: -2.5\n"
                "RMS_DENSITY: -3.1\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(su2_case, case_dir, run_dir, resources=None)

        assert result.exit_code == 0
        assert result.skipped_commands is None
        assert result.solver_version is not None
        assert "8.0.0" in (result.solver_version or "")
        assert result.final_residuals is not None
        assert "RMS_DENSITY" in result.final_residuals

    def test_critical_step_failure_aborts(
        self, su2_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test that critical step failure aborts the run."""
        adapter = SU2Adapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        mock_fail = RunResult(
            exit_code=1,
            stdout="",
            stderr="SU2 FATAL ERROR",
            wall_time_sec=0.1,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_fail,
        ):
            result = adapter.run(su2_case, case_dir, run_dir, resources=None)

        assert result.exit_code != 0


class TestSU2CollectOutputs:
    def test_collect_outputs(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        adapter = SU2Adapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        artifacts = adapter.collect_outputs(su2_case, run_dir)
        files = artifacts.files
        assert "case/flat_plate_su2.cfg" in files
        assert "case/mesh.su2" in files
        assert artifacts.qoi_values is None


class TestSU2FindSolverConfig:
    def test_not_found(self) -> None:
        from cfdb.schema import (
            ConditionsSpec,
            MetricSpec,
            OutputSpec,
            PhysicsSpec,
            SolverConfig,
        )

        case = CaseSpec(
            id="bad",
            name="Bad",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[SolverConfig(name="other", command="echo")],
            outputs=OutputSpec(),
            metrics=MetricSpec(),
        )
        adapter = SU2Adapter()
        with pytest.raises(ValueError, match="no 'su2'"):
            adapter._find_solver_config(case)


class TestSU2P2aFields:
    def test_merge_produces_step_details(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        """_merge_step_results populates step_details (P2-a)."""
        adapter = SU2Adapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout="SU2 Code Suite, Version 8.0.0\nRMS_DENSITY: -2.5\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(su2_case, case_dir, run_dir, resources=None)

        assert result.step_details is not None
        assert len(result.step_details) == 1
        assert result.step_details[0]["name"] == "solve"

    def test_merge_produces_cell_count(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        """_merge_step_results extracts cell_count from SU2 mesh stats (P2-a)."""
        adapter = SU2Adapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "SU2 Code Suite, Version 8.0.0\n"
                "33,024 volume elements.\n"
                "RMS_DENSITY: -2.5\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(su2_case, case_dir, run_dir, resources=None)

        assert result.cell_count == 33024

    def test_merge_produces_residuals_history(self, su2_case: CaseSpec, tmp_path: Path) -> None:
        """_merge_step_results populates residuals_history (P2-a)."""
        adapter = SU2Adapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(su2_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "SU2 Code Suite, Version 8.0.0\n"
                "RMS_DENSITY: -2.5\n"
                "RMS_DENSITY: -3.1\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(su2_case, case_dir, run_dir, resources=None)

        assert result.residuals_history is not None
        assert "RMS_DENSITY" in result.residuals_history
        assert len(result.residuals_history["RMS_DENSITY"]) == 2
