"""Tests for cfdb.adapters.starccm.StarCCMAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.base import RunResult
from cfdb.adapters.starccm import StarCCMAdapter
from cfdb.schema import CaseSpec, SolverConfig


def make_starccm_case(steps=None, parameters=None) -> CaseSpec:
    """Create a minimal CaseSpec with a 'starccm' solver config."""
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    solver = SolverConfig(
        name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=steps,
        parameters=parameters,
    )
    return CaseSpec(
        id="flat_plate_starccm",
        name="Flat Plate StarCCM",
        category="verification",
        physics=PhysicsSpec(flow="compressible", dimensionality="2d", steady=True),
        conditions=ConditionsSpec(reynolds=1000000.0, mach=0.3, alpha_deg=0.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["velocity", "pressure"], qoi=["cl", "cd"]),
        metrics=MetricSpec(qoi_relative_tolerance={"cl": 0.1, "cd": 0.1}),
    )


def make_starccm_naca_case(parameters=None) -> CaseSpec:
    """Create a CaseSpec for NACA0012 with starccm solver."""
    from cfdb.schema import (
        CommandStep,
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    params = parameters or {
        "mach": 0.3,
        "reynolds": 6e6,
        "alpha_deg": 5.0,
        "u_inf": 100.0,
        "n_iter": 500,
    }

    solver = SolverConfig(
        name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=[
            CommandStep(
                name="solve",
                command="starccm+ -batch {{ case_dir }}/run.java -nproc {{ cpu_cores }}",
            ),
        ],
        parameters=params,
    )
    return CaseSpec(
        id="naca0012_a5",
        name="NACA0012 Alpha=5",
        category="validation",
        physics=PhysicsSpec(
            flow="compressible", turbulence="rans_sa", dimensionality="2d", steady=True
        ),
        conditions=ConditionsSpec(reynolds=6e6, mach=0.3, alpha_deg=5.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["velocity", "p"], qoi=["cl", "cd"]),
        metrics=MetricSpec(qoi_relative_tolerance={"cl": 0.05, "cd": 0.05}),
    )


@pytest.fixture
def starccm_case() -> CaseSpec:
    """CaseSpec with starccm solver steps."""
    from cfdb.schema import CommandStep

    steps = [
        CommandStep(
            name="solve",
            command="starccm+ -batch {{ case_dir }}/run.java",
        ),
    ]
    return make_starccm_case(steps=steps)


@pytest.fixture
def starccm_case_no_steps() -> CaseSpec:
    """CaseSpec with starccm solver but no steps."""
    return make_starccm_case(steps=None)


@pytest.fixture
def starccm_naca_case() -> CaseSpec:
    """CaseSpec for NACA0012 starccm solver."""
    return make_starccm_naca_case()


class TestStarCCMAdapterInit:
    def test_name(self) -> None:
        adapter = StarCCMAdapter()
        assert adapter.name == "starccm"

    def test_default_dry_run_false(self) -> None:
        adapter = StarCCMAdapter()
        assert adapter._dry_run is False

    def test_dry_run_true(self) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        assert adapter._dry_run is True


class TestStarCCMPrepare:
    def test_prepare_creates_macro(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_case, case_dir, run_dir)

        case_out = run_dir / "case"
        assert case_out.is_dir()
        assert (case_out / "run.java").exists()

    def test_macro_contains_case_id(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "flat_plate_starccm" in macro
        assert "StarMacro" in macro

    def test_macro_contains_parameters(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "100.0" in macro  # u_inf default

    def test_naca_routing_true(self, starccm_naca_case: CaseSpec, tmp_path: Path) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_naca_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "NACA0012" in macro
        assert "Force Coefficient NACA0012" in macro
        assert "forces.csv" in macro

    def test_naca_routing_false(self, starccm_case: CaseSpec, tmp_path: Path) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        # Non-NACA case should NOT have NACA-specific content
        assert "NACA0012" not in macro

    def test_prepare_with_parameters_override(
        self, tmp_path: Path
    ) -> None:
        from cfdb.schema import CommandStep

        case = make_starccm_case(
            steps=[CommandStep(name="solve", command="starccm+ -batch {{ case_dir }}/run.java")],
            parameters={"mach": 0.5, "n_iter": 1000},
        )
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "1000" in macro  # n_iter override


class TestStarCCMRun:
    def test_run_dry_run_with_steps(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        result = adapter.run(starccm_case, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1
        assert "starccm+" in result.skipped_commands[0]

    def test_run_dry_run_without_steps(
        self, starccm_case_no_steps: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case_no_steps, case_dir, run_dir)

        result = adapter.run(starccm_case_no_steps, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1

    def test_run_real_execution_all_steps_success(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test real execution path with mocked LocalExecutionBackend."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "STAR-CCM+ 18.02.008 (windows/intel18.0.1.156)\n"
                "Iteration: 1  Continuity: 1.0e-3  X-Momentum: 1.0e-2\n"
                "Iteration: 2  Continuity: 5.0e-4  X-Momentum: 5.0e-3\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(starccm_case, case_dir, run_dir, resources=None)

        assert result.exit_code == 0
        assert result.skipped_commands is None  # not dry_run
        assert result.solver_version is not None
        assert "18.02.008" in (result.solver_version or "")

    def test_critical_step_failure_aborts(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test that critical step failure aborts the run."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        mock_fail = RunResult(
            exit_code=1,
            stdout="",
            stderr="STARCCM FATAL ERROR",
            wall_time_sec=0.1,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_fail,
        ):
            result = adapter.run(starccm_case, case_dir, run_dir, resources=None)

        assert result.exit_code != 0


class TestStarCCMCollectOutputs:
    def test_collect_outputs_lists_files(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        artifacts = adapter.collect_outputs(starccm_case, run_dir)
        files = artifacts.files
        assert "case/run.java" in files
        assert artifacts.qoi_values is None

    def test_collect_outputs_extracts_cl_cd_from_csv(
        self, starccm_naca_case: CaseSpec, tmp_path: Path
    ) -> None:
        """collect_outputs reads forces.csv for Cl/Cd (NACA case)."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_naca_case, case_dir, run_dir)

        # Write a mock forces.csv
        case_out = run_dir / "case"
        (case_out / "forces.csv").write_text(
            "Iteration, Cd, Cl, Cm\n"
            "0, 0.0, 0.0, 0.0\n"
            "100, 0.005, 0.200, -0.01\n"
            "500, 0.0086, 0.3240, -0.015\n",
            encoding="utf-8",
        )

        artifacts = adapter.collect_outputs(starccm_naca_case, run_dir)
        assert artifacts.qoi_values is not None
        assert artifacts.qoi_values["cl"] == 0.3240
        assert artifacts.qoi_values["cd"] == 0.0086


class TestStarCCMFindSolverConfig:
    def test_not_found_raises_value_error(self) -> None:
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
        adapter = StarCCMAdapter()
        with pytest.raises(ValueError, match="no 'starccm'"):
            adapter._find_solver_config(case)


class TestStarCCMP2aFields:
    def test_merge_step_details(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        """_merge_step_results populates step_details (P2-a)."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout="STAR-CCM+ 18.02.008\nIteration: 1  Continuity: 1.0e-3\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(starccm_case, case_dir, run_dir, resources=None)

        assert result.step_details is not None
        assert len(result.step_details) == 1
        assert result.step_details[0]["name"] == "solve"

    def test_merge_cell_count(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        """_merge_step_results extracts cell_count from mesh import log (P2-a)."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "STAR-CCM+ 18.02.008\n"
                "N cells: 123456\n"
                "Iteration: 1  Continuity: 1.0e-3\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(starccm_case, case_dir, run_dir, resources=None)

        assert result.cell_count == 123456

    def test_merge_residuals_history(
        self, starccm_case: CaseSpec, tmp_path: Path
    ) -> None:
        """_merge_step_results populates residuals_history (P2-a)."""
        adapter = StarCCMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(starccm_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "STAR-CCM+ 18.02.008\n"
                "Iteration: 1  Continuity: 1.0e-1  X-Momentum: 2.0e-1\n"
                "Iteration: 2  Continuity: 1.0e-2  X-Momentum: 2.0e-2\n"
                "Iteration: 3  Continuity: 1.0e-3  X-Momentum: 2.0e-3\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(starccm_case, case_dir, run_dir, resources=None)

        assert result.residuals_history is not None
        assert "Continuity" in result.residuals_history
        assert len(result.residuals_history["Continuity"]) == 3
        assert "X-Momentum" in result.residuals_history
        assert len(result.residuals_history["X-Momentum"]) == 3


class TestStarCCMNacaTemplate:
    def test_naca_macro_has_force_report(
        self, starccm_naca_case: CaseSpec, tmp_path: Path
    ) -> None:
        """NACA template includes ForceCoefficientReport."""
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_naca_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "ForceCoefficientReport" in macro
        assert "airfoil" in macro

    def test_naca_macro_has_cl_cd_export(
        self, starccm_naca_case: CaseSpec, tmp_path: Path
    ) -> None:
        """NACA template exports forces.csv."""
        adapter = StarCCMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(starccm_naca_case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text()
        assert "forces.csv" in macro
        assert "result.sim" in macro


class TestPostFunctions:
    def test_extract_starccm_version(self) -> None:
        from cfdb.post.residuals import extract_starccm_version

        stdout = "STAR-CCM+ 18.02.008 (windows/intel18.0.1.156)\n"
        version = extract_starccm_version(stdout)
        assert version == "StarCCM+ 18.02.008"

    def test_extract_starccm_version_none(self) -> None:
        from cfdb.post.residuals import extract_starccm_version

        stdout = "some random output\nwithout starccm banner\n"
        version = extract_starccm_version(stdout)
        assert version is None

    def test_parse_starccm_residuals_single_line(self) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        log = (
            "Iteration: 1  Continuity: 1.0e-3  X-Momentum: 1.0e-2\n"
            "Iteration: 2  Continuity: 5.0e-4  X-Momentum: 5.0e-3\n"
        )
        residuals = parse_starccm_residuals(log)
        assert "Continuity" in residuals
        assert len(residuals["Continuity"]) == 2
        assert residuals["Continuity"] == [0.001, 0.0005]
        assert residuals["X-Momentum"] == [0.01, 0.005]

    def test_parse_starccm_residuals_csv(self) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        log = (
            "Iteration, Continuity, X-Momentum, Y-Momentum\n"
            "1, 1.0e-3, 1.0e-2, 5.0e-3\n"
            "2, 5.0e-4, 5.0e-3, 2.5e-3\n"
        )
        residuals = parse_starccm_residuals(log)
        assert "Continuity" in residuals
        assert len(residuals["Continuity"]) == 2
        assert residuals["Continuity"] == [0.001, 0.0005]

    def test_parse_starccm_residuals_empty(self) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        log = "No residual data here.\nJust some text.\n"
        residuals = parse_starccm_residuals(log)
        assert residuals == {}

    def test_extract_starccm_cell_count(self) -> None:
        from cfdb.post.mesh_stats import extract_starccm_cell_count

        log = "Importing mesh...\nN cells: 250000\nMesh imported successfully.\n"
        count = extract_starccm_cell_count(log)
        assert count == 250000

    def test_extract_starccm_cell_count_none(self) -> None:
        from cfdb.post.mesh_stats import extract_starccm_cell_count

        log = "No cell info here."
        count = extract_starccm_cell_count(log)
        assert count is None

    def test_extract_cl_cd_starccm(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text(
            "Iteration, Cd, Cl, Cm\n"
            "0, 0.0, 0.0, 0.0\n"
            "500, 0.0086, 0.3240, -0.015\n",
            encoding="utf-8",
        )

        cl, cd = extract_cl_cd_starccm(forces_csv)
        assert cl == 0.3240
        assert cd == 0.0086

    def test_extract_cl_cd_starccm_alt_headers(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text(
            "Iter, Drag_Coefficient, Lift_Coefficient, Cm\n"
            "1, 0.001, 0.05, -0.01\n",
            encoding="utf-8",
        )

        cl, cd = extract_cl_cd_starccm(forces_csv)
        assert cl == 0.05
        assert cd == 0.001

    def test_extract_cl_cd_starccm_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text(
            "X, Y, Z\n"
            "1, 2, 3\n",
            encoding="utf-8",
        )

        result = extract_cl_cd_starccm(forces_csv)
        assert result is None


class TestStarCCMRegistry:
    def test_adapter_registered(self) -> None:
        """StarCCM adapter is in the adapter registry."""
        from cfdb.adapters import get_adapter

        adapter = get_adapter("starccm", dry_run=True)
        assert adapter.name == "starccm"
        assert adapter._dry_run is True
