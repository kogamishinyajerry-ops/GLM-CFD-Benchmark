"""Tests for cfdb.adapters.openfoam.OpenFOAMAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.base import RunResult, StepResult
from cfdb.adapters.openfoam import OpenFOAMAdapter
from cfdb.schema import CaseSpec, SolverConfig


def make_openfoam_case(steps=None, parameters=None) -> CaseSpec:
    """Create a minimal CaseSpec with an 'openfoam' solver config."""
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    solver = SolverConfig(
        name="openfoam",
        command="icoFoam",
        steps=steps,
        parameters=parameters,
    )
    return CaseSpec(
        id="test_cavity",
        name="Test Cavity",
        category="validation",
        physics=PhysicsSpec(flow="incompressible", dimensionality="2d", steady=False),
        conditions=ConditionsSpec(reynolds=100.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["U", "p"], qoi=["centerline_umax"]),
        metrics=MetricSpec(qoi_relative_tolerance={"centerline_umax": 0.05}),
    )


@pytest.fixture
def openfoam_case() -> CaseSpec:
    """CaseSpec with openfoam solver steps."""
    from cfdb.schema import CommandStep

    steps = [
        CommandStep(name="block_mesh", command="blockMesh -case {{ run_dir }}/case"),
        CommandStep(name="solve", command="icoFoam -case {{ run_dir }}/case"),
    ]
    return make_openfoam_case(steps=steps)


@pytest.fixture
def openfoam_case_no_steps() -> CaseSpec:
    """CaseSpec with openfoam solver but no steps (single command fallback)."""
    return make_openfoam_case(steps=None)


class TestOpenFOAMAdapterInit:
    def test_name(self) -> None:
        adapter = OpenFOAMAdapter()
        assert adapter.name == "openfoam"

    def test_default_dry_run_false(self) -> None:
        adapter = OpenFOAMAdapter()
        assert adapter._dry_run is False

    def test_dry_run_true(self) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        assert adapter._dry_run is True


class TestOpenFOAMPrepare:
    def test_prepare_creates_full_structure(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(openfoam_case, case_dir, run_dir)

        case_out = run_dir / "case"
        # System files
        assert (case_out / "system" / "controlDict").exists()
        assert (case_out / "system" / "fvSchemes").exists()
        assert (case_out / "system" / "fvSolution").exists()
        # Constant files
        assert (case_out / "constant" / "transportProperties").exists()
        assert (case_out / "constant" / "turbulenceProperties").exists()
        # polyMesh placeholder dir
        assert (case_out / "constant" / "polyMesh").is_dir()
        # Initial fields
        assert (case_out / "0" / "U").exists()
        assert (case_out / "0" / "p").exists()

    def test_controlDict_contains_reynolds(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(openfoam_case, case_dir, run_dir)

        control = (run_dir / "case" / "system" / "controlDict").read_text()
        assert "100" in control  # reynolds rendered

    def test_transportProperties_contains_nu(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(openfoam_case, case_dir, run_dir)

        transport = (run_dir / "case" / "constant" / "transportProperties").read_text()
        # nu = 0.1 / 100 = 0.001
        assert "0.001" in transport

    def test_prepare_with_parameters_override(
        self, tmp_path: Path
    ) -> None:
        from cfdb.schema import CommandStep

        case = make_openfoam_case(
            steps=[CommandStep(name="solve", command="icoFoam -case {{ run_dir }}/case")],
            parameters={"nu": 0.005},
        )
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        transport = (run_dir / "case" / "constant" / "transportProperties").read_text()
        # parameters nu=0.005 overrides computed nu=0.001
        assert "0.005" in transport


class TestOpenFOAMRun:
    def test_run_dry_run_with_steps(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 2
        assert "blockMesh" in result.skipped_commands[0]
        assert "icoFoam" in result.skipped_commands[1]

    def test_run_dry_run_without_steps(
        self, openfoam_case_no_steps: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case_no_steps, case_dir, run_dir)

        result = adapter.run(openfoam_case_no_steps, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1

    def test_run_real_execution_all_steps_success(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test real execution path with mocked LocalExecutionBackend."""
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD"
                " Toolbox           |\n"
                "|  \\\\    /   O peration     | Version:  v2406"
                "                                 |\n"
                "Build  : 7cf83b7-OpenFOAM-v2406\n"
                "Solving for Ux, Initial residual = 1.2e-6\n"
                "Solving for Uy, Initial residual = 2.1e-6\n"
                "Solving for p, Initial residual = 3.4e-5\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)

        assert result.exit_code == 0
        assert result.skipped_commands is None  # not dry_run
        assert result.solver_version is not None
        assert "v2406" in (result.solver_version or "")
        assert result.final_residuals is not None
        assert "Ux" in result.final_residuals

    def test_critical_step_failure_aborts(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        """Test that critical step failure aborts the run."""
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        mock_fail = RunResult(
            exit_code=1,
            stdout="",
            stderr="FOAM FATAL ERROR",
            wall_time_sec=0.1,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_fail,
        ):
            result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)

        # Critical step failed -> overall exit code should be non-zero
        assert result.exit_code != 0


def _make_sst_dry_run_case() -> CaseSpec:
    """Build a minimal SST NACA case for potentialFoam injection tests.

    Mirrors the real naca0012_a5 case shape: 3 steps (block_mesh, snappy_mesh,
    solve) and physics.turbulence='rans_kwsst' so the adapter routes to the
    kOmegaSST template branch.
    """
    from cfdb.schema import (
        CommandStep,
        ConditionsSpec,
        GeometrySpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    solver = SolverConfig(
        name="openfoam",
        command="",
        timeout_sec=600,
        steps=[
            CommandStep(name="block_mesh", command="blockMesh -case {{ case_dir }}"),
            CommandStep(
                name="snappy_mesh",
                command="snappyHexMesh -overwrite -case {{ case_dir }}",
            ),
            CommandStep(name="solve", command="simpleFoam -case {{ case_dir }}"),
        ],
        parameters={
            "nu": 1.6667e-5,
            "u_inf": 100.0,
            "l_ref": 1.0,
            "alpha_deg": 5.0,
            "n_iter": 1000,
        },
    )
    return CaseSpec(
        id="naca0012_a5",
        name="NACA0012 alpha=5 SST",
        category="validation",
        physics=PhysicsSpec(
            flow="rans",
            turbulence="rans_kwsst",
            dimensionality="2d",
            steady=True,
        ),
        conditions=ConditionsSpec(reynolds=6.0e6, mach=0.3, alpha_deg=5.0),
        geometry=GeometrySpec(type="external"),
        solvers=[solver],
        outputs=OutputSpec(fields=["U", "p", "k", "omega"], qoi=["cl", "cd"]),
        metrics=MetricSpec(qoi_relative_tolerance={"cl": 0.10, "cd": 0.10}),
    )


class TestSSTNoPotentialFoamInjection:
    """SST cases must NOT inject a potentialFoam step.

    An earlier revision injected potentialFoam as a U-field pre-init, but
    this was abandoned because potentialFoam overwrites U with ∇Φ, and with
    a freestream farfield BC the Φ Poisson problem is ill-posed — U came
    out as garbage (mean 0.6 vs freestream 100) and made simpleFoam's k
    blowup WORSE than without. The fix is now done purely via relaxation
    (k=0.2), lower initial k (Tu=0.03%), and kLowReWallFunction.

    These tests verify: (1) SST produces the 3 base commands unchanged,
    (2) SA is also unchanged — Iron Rule #1.
    """

    def test_sst_no_potentialfoam_injection(self, tmp_path: Path) -> None:
        """SST dry_run → 3 skipped_commands, no potentialFoam anywhere."""
        case = _make_sst_dry_run_case()
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(case, case_dir, run_dir)

        result = adapter.run(case, case_dir, run_dir, resources=None)

        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 3
        # Order: block_mesh, snappy_mesh, solve
        assert "blockMesh" in result.skipped_commands[0]
        assert "snappyHexMesh" in result.skipped_commands[1]
        assert "simpleFoam" in result.skipped_commands[2]
        for cmd in result.skipped_commands:
            assert "potentialFoam" not in cmd

    def test_sa_same_three_steps(self, tmp_path: Path) -> None:
        """SA case unchanged — 3 steps, no potentialFoam. Iron Rule #1."""
        case = _make_sst_dry_run_case()
        # Flip turbulence to SA (mutate physics in place)
        case.physics = case.physics.model_copy(
            update={"turbulence": "rans_sa"}
        )
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(case, case_dir, run_dir)

        result = adapter.run(case, case_dir, run_dir, resources=None)

        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 3
        for cmd in result.skipped_commands:
            assert "potentialFoam" not in cmd

    def test_sst_renders_klowre_wallfunction(self, tmp_path: Path) -> None:
        """SST case k field must use kLowReWallFunction, not kqRWallFunction.

        This is the wall-BC half of the k blowup fix — kqRWallFunction's
        log-layer k value is applied to the first prism cell and creates
        a stiff source in the k transport equation that overwhelms
        under-relaxation. kLowReWallFunction blends smoothly.
        """
        case = _make_sst_dry_run_case()
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(case, case_dir, run_dir)

        # prepare() writes the generated case into run_dir/case
        k_path = run_dir / "case" / "0" / "k"
        assert k_path.exists(), f"k file not written at {k_path}"
        k_text = k_path.read_text(encoding="utf-8")
        # Check the BC type assignment in the airfoil block, not the whole
        # file (the comments mention both names for context).
        import re

        # Find the airfoil block: from `airfoil` up to the closing `}`.
        m = re.search(
            r"airfoil\s*\{(.*?)[\n\r]\s*\}", k_text, re.DOTALL
        )
        assert m is not None, "could not locate airfoil BC block in k"
        airfoil_block = m.group(1)
        assert "kLowReWallFunction" in airfoil_block, (
            f"airfoil BC must be kLowReWallFunction, got: {airfoil_block}"
        )

    def test_sst_lower_initial_k(self, tmp_path: Path) -> None:
        """SST case k internalField must use Tu=0.03% (lower than SA's 0.1%).

        k_init = 1.5 * (u_inf * 0.0003)^2 = 1.5 * (100 * 0.0003)^2
              = 0.00135
        vs the SA/global default of 1.5 * (100 * 0.001)^2 = 0.015.
        Lower initial k reduces the absolute k value that bounding has
        to clip when P_k spikes at the first near-wall cell.
        """
        case = _make_sst_dry_run_case()
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(case, case_dir, run_dir)

        k_path = run_dir / "case" / "0" / "k"
        assert k_path.exists(), f"k file not written at {k_path}"
        k_text = k_path.read_text(encoding="utf-8")
        # Extract internalField value
        import re

        m = re.search(r"internalField\s+uniform\s+([\d.eE+-]+)", k_text)
        assert m is not None, "could not parse k internalField"
        k_init = float(m.group(1))
        # Expected ~0.00135 (Tu=0.03%, U=100). Allow small float slack.
        assert abs(k_init - 0.00135) < 1e-6, f"k_init={k_init}, expected 0.00135"
        # Sanity: must be smaller than SA default of 0.015
        assert k_init < 0.015


class TestOpenFOAMCollectOutputs:
    def test_collect_outputs(self, openfoam_case: CaseSpec, tmp_path: Path) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        artifacts = adapter.collect_outputs(openfoam_case, run_dir)
        files = artifacts.files
        # Should contain at least 7 files
        assert "case/system/controlDict" in files
        assert "case/system/fvSchemes" in files
        assert "case/system/fvSolution" in files
        assert "case/constant/transportProperties" in files
        assert "case/constant/turbulenceProperties" in files
        assert "case/0/U" in files
        assert "case/0/p" in files
        assert artifacts.qoi_values is None


class TestOpenFOAMFindSolverConfig:
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
        adapter = OpenFOAMAdapter()
        with pytest.raises(ValueError, match="no 'openfoam'"):
            adapter._find_solver_config(case)


class TestStepResultToDict:
    def test_to_dict_success(self) -> None:
        """StepResult.to_dict() returns correct status for success."""
        sr = StepResult(
            name="block_mesh",
            exit_code=0,
            wall_time_sec=1.5,
            stdout="",
            stderr="",
        )
        d = sr.to_dict()
        assert d["name"] == "block_mesh"
        assert d["exit_code"] == 0
        assert d["wall_time_sec"] == 1.5
        assert d["status"] == "success"

    def test_to_dict_failed(self) -> None:
        """StepResult.to_dict() returns 'failed' for non-zero exit code."""
        sr = StepResult(
            name="solve",
            exit_code=1,
            wall_time_sec=2.0,
            stdout="",
            stderr="error",
        )
        d = sr.to_dict()
        assert d["status"] == "failed"

    def test_to_dict_has_all_keys(self) -> None:
        """to_dict() has exactly name, exit_code, wall_time_sec, status."""
        sr = StepResult(
            name="step1",
            exit_code=0,
            wall_time_sec=0.1,
            stdout="",
            stderr="",
        )
        d = sr.to_dict()
        assert set(d.keys()) == {"name", "exit_code", "wall_time_sec", "status"}


class TestOpenFOAMP2aFields:
    def test_merge_produces_step_details(self, openfoam_case: CaseSpec, tmp_path: Path) -> None:
        """_merge_step_results populates step_details (P2-a)."""
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout="OpenFOAM v2406\nSolving for Ux, Initial residual = 1.2e-6\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)

        assert result.step_details is not None
        assert len(result.step_details) == 2
        assert result.step_details[0]["name"] == "block_mesh"
        assert result.step_details[1]["name"] == "solve"

    def test_merge_produces_cell_count(self, openfoam_case: CaseSpec, tmp_path: Path) -> None:
        """_merge_step_results extracts cell_count from blockMesh log (P2-a)."""
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout="nCells: 400\nSolving for Ux, Initial residual = 1.2e-6\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)

        assert result.cell_count == 400

    def test_merge_produces_residuals_history(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        """_merge_step_results populates residuals_history (P2-a)."""
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        mock_result = RunResult(
            exit_code=0,
            stdout=(
                "OpenFOAM v2406\n"
                "Solving for Ux, Initial residual = 1e-1\n"
                "Solving for Ux, Initial residual = 1e-3\n"
                "Solving for p, Initial residual = 5e-2\n"
            ),
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = adapter.run(openfoam_case, case_dir, run_dir, resources=None)

        assert result.residuals_history is not None
        assert "Ux" in result.residuals_history
        assert len(result.residuals_history["Ux"]) == 2
