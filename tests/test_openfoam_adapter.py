"""Tests for cfdb.adapters.openfoam.OpenFOAMAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

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

    def test_run_real_raises_not_implemented(
        self, openfoam_case: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = OpenFOAMAdapter(dry_run=False)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(openfoam_case, case_dir, run_dir)

        with pytest.raises(NotImplementedError, match="P1-b"):
            adapter.run(openfoam_case, case_dir, run_dir, resources=None)


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
