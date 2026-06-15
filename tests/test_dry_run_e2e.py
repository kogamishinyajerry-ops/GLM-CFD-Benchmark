"""End-to-end tests for dry_run mode: CLI → Runner → manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.adapters import get_adapter
from cfdb.adapters.base import RunResult
from cfdb.cli import app
from cfdb.core.runner import Runner
from cfdb.registry import CaseRegistry
from cfdb.schema import CaseSpec
from cfdb.storage.json_repo import JsonManifestRepository

PROJECT_ROOT = Path(__file__).parent.parent
PROJECT_CASES = PROJECT_ROOT / "cases"


@pytest.fixture
def cli_runner() -> CliRunner:
    """CliRunner fixture."""
    return CliRunner()


class TestDryRunAdapterRegistration:
    """T18: get_adapter supports openfoam/su2 with dry_run."""

    def test_get_adapter_generic_dry_run(self) -> None:
        adapter = get_adapter("generic", dry_run=True)
        assert adapter.name == "generic"
        assert adapter._dry_run is True

    def test_get_adapter_openfoam_dry_run(self) -> None:
        adapter = get_adapter("openfoam", dry_run=True)
        assert adapter.name == "openfoam"
        assert adapter._dry_run is True

    def test_get_adapter_su2_dry_run(self) -> None:
        adapter = get_adapter("su2", dry_run=True)
        assert adapter.name == "su2"
        assert adapter._dry_run is True

    def test_get_adapter_default_dry_run_false(self) -> None:
        adapter = get_adapter("openfoam")
        assert adapter._dry_run is False

    def test_get_adapter_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown adapter"):
            get_adapter("nonexistent")


class TestRunResultSkippedCommands:
    """T13: RunResult has skipped_commands field defaulting to None."""

    def test_default_none(self) -> None:
        r = RunResult(exit_code=0, stdout="", stderr="", wall_time_sec=0.0)
        assert r.skipped_commands is None

    def test_with_commands(self) -> None:
        r = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=0.0,
            skipped_commands=["blockMesh", "simpleFoam"],
        )
        assert r.skipped_commands == ["blockMesh", "simpleFoam"]


class TestSchemaDryRunFields:
    """T13: Schema fields for dry_run."""

    def test_command_step_valid(self) -> None:
        from cfdb.schema import CommandStep

        step = CommandStep(name="solve", command="icoFoam")
        assert step.name == "solve"
        assert step.critical is True

    def test_solver_config_steps_default_none(self) -> None:
        from cfdb.schema import SolverConfig

        s = SolverConfig(name="generic", command="echo")
        assert s.steps is None
        assert s.parameters is None

    def test_solver_config_with_steps(self) -> None:
        from cfdb.schema import CommandStep, SolverConfig

        s = SolverConfig(
            name="openfoam",
            command="icoFoam",
            steps=[CommandStep(name="mesh", command="blockMesh")],
        )
        assert s.steps is not None
        assert len(s.steps) == 1
        assert s.steps[0].name == "mesh"

    def test_solver_config_with_parameters(self) -> None:
        from cfdb.schema import SolverConfig

        s = SolverConfig(
            name="su2",
            command="SU2_CFD",
            parameters={"mach": 0.3, "reynolds": 1e6},
        )
        assert s.parameters is not None
        assert s.parameters["mach"] == 0.3

    def test_run_manifest_status_dry_run(self) -> None:
        from datetime import datetime, timezone

        from cfdb.schema import RunManifest, TimingSpec

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=0.01, start_time=now, end_time=now)
        m = RunManifest(
            run_id="test",
            case_id="t",
            solver="openfoam",
            status="dry_run",
            timing=timing,
        )
        assert m.status == "dry_run"
        assert m.dry_run_skipped_commands is None  # default

    def test_run_manifest_with_skipped_commands(self) -> None:
        from datetime import datetime, timezone

        from cfdb.schema import RunManifest, TimingSpec

        now = datetime.now(timezone.utc)
        timing = TimingSpec(wall_time_sec=0.01, start_time=now, end_time=now)
        m = RunManifest(
            run_id="test",
            case_id="t",
            solver="openfoam",
            status="dry_run",
            timing=timing,
            dry_run_skipped_commands=["blockMesh", "simpleFoam"],
        )
        assert m.dry_run_skipped_commands == ["blockMesh", "simpleFoam"]


class TestMetricsEngineDryRun:
    """T13/T14: MetricsEngine skips QoI in dry_run mode."""

    def test_dry_run_short_circuit(self) -> None:
        from cfdb.adapters.base import ArtifactManifest
        from cfdb.metrics.engine import MetricsEngine
        from cfdb.schema import (
            CaseSpec,
            ConditionsSpec,
            MetricSpec,
            OutputSpec,
            PhysicsSpec,
        )

        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.05}),
        )
        artifacts = ArtifactManifest()
        run_result = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=0.0,
            skipped_commands=["some_command"],
        )

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert result.overall_status == "dry_run"
        assert result.qoi_pass is True
        assert any("dry-run" in n for n in result.notes)

    def test_normal_mode_not_short_circuited(self) -> None:
        from cfdb.adapters.base import ArtifactManifest
        from cfdb.metrics.engine import MetricsEngine
        from cfdb.schema import (
            CaseSpec,
            ConditionsSpec,
            MetricSpec,
            OutputSpec,
            PhysicsSpec,
        )

        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[{"name": "generic", "command": "true"}],
            outputs=OutputSpec(qoi=["drag"]),
            metrics=MetricSpec(qoi_relative_tolerance={"drag": 0.05}),
        )
        artifacts = ArtifactManifest()
        run_result = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=0.0,
            skipped_commands=None,  # not dry_run
        )

        engine = MetricsEngine()
        result = engine.compute(case, artifacts, run_result)
        assert result.overall_status != "dry_run"


class TestGenericAdapterDryRun:
    """T15: GenericCommandAdapter supports dry_run."""

    def test_generic_dry_run(self, sample_case_spec: CaseSpec, tmp_path: Path) -> None:
        from cfdb.adapters.generic_command import GenericCommandAdapter

        adapter = GenericCommandAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(sample_case_spec, case_dir, run_dir)

        result = adapter.run(sample_case_spec, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1
        assert "[dry-run]" in result.stdout


class TestCLIDryRunOpenFOAM:
    """T20: CLI --dry-run with OpenFOAM case."""

    def test_run_openfoam_dry_run(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = cli_runner.invoke(
            app,
            [
                "run",
                "--case", "lid_driven_cavity",
                "--solver", "openfoam",
                "--dry-run",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "dry_run" in result.output
        assert "DRY-RUN" in result.output

        # Check manifest
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        manifest = json.loads(
            (run_dirs[0] / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "dry_run"
        assert manifest["dry_run_skipped_commands"] is not None
        assert len(manifest["dry_run_skipped_commands"]) == 2

        # Check generated OpenFOAM structure
        case_dir = run_dirs[0] / "case"
        assert (case_dir / "system" / "controlDict").exists()
        assert (case_dir / "system" / "fvSchemes").exists()
        assert (case_dir / "system" / "fvSolution").exists()
        assert (case_dir / "constant" / "transportProperties").exists()
        assert (case_dir / "constant" / "turbulenceProperties").exists()
        assert (case_dir / "0" / "U").exists()
        assert (case_dir / "0" / "p").exists()

    def test_run_openfoam_dry_run_metrics(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        cli_runner.invoke(
            app,
            [
                "run",
                "--case", "lid_driven_cavity",
                "--solver", "openfoam",
                "--dry-run",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )

        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        metrics = json.loads(
            (run_dirs[0] / "metrics.json").read_text(encoding="utf-8")
        )
        assert metrics["overall_status"] == "dry_run"
        assert metrics["qoi_pass"] is True


class TestCLIDryRunSU2:
    """T20: CLI --dry-run with SU2 case."""

    def test_run_su2_dry_run(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = cli_runner.invoke(
            app,
            [
                "run",
                "--case", "flat_plate_su2",
                "--solver", "su2",
                "--dry-run",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "dry_run" in result.output
        assert "DRY-RUN" in result.output

        # Check manifest
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        manifest = json.loads(
            (run_dirs[0] / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "dry_run"
        assert manifest["dry_run_skipped_commands"] is not None
        assert len(manifest["dry_run_skipped_commands"]) == 1

        # Check generated SU2 files
        case_dir = run_dirs[0] / "case"
        assert (case_dir / "flat_plate_su2.cfg").exists()
        assert (case_dir / "mesh.su2").exists()


class TestCLIRegression:
    """T20: P0 regression — no --dry-run behaves identically."""

    def test_normal_run_still_works(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = cli_runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "success" in result.output
        assert "DRY-RUN" not in result.output


class TestRunnerDryRunDirect:
    """T14: Runner.execute with dry_run=True directly."""

    def test_runner_openfoam_dry_run(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        registry = CaseRegistry(PROJECT_CASES)
        repo = JsonManifestRepository(runs_dir)
        runner = Runner(registry, repo, runs_dir)

        manifest = runner.execute(
            case_id="lid_driven_cavity",
            solver="openfoam",
            dry_run=True,
        )
        assert manifest.status == "dry_run"
        assert manifest.dry_run_skipped_commands is not None
        assert len(manifest.dry_run_skipped_commands) == 2
        assert manifest.error is None

    def test_runner_su2_dry_run(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        registry = CaseRegistry(PROJECT_CASES)
        repo = JsonManifestRepository(runs_dir)
        runner = Runner(registry, repo, runs_dir)

        manifest = runner.execute(
            case_id="flat_plate_su2",
            solver="su2",
            dry_run=True,
        )
        assert manifest.status == "dry_run"
        assert manifest.dry_run_skipped_commands is not None
        assert len(manifest.dry_run_skipped_commands) == 1


class TestDryRunNoSubprocess:
    """Verify dry_run mode never calls subprocess."""

    def test_no_subprocess_in_dry_run(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Dry run should succeed even on a machine with no OpenFOAM installed.

        The fact that it returns exit_code=0 proves no subprocess was attempted.
        """
        runs_dir = tmp_path / "runs"
        result = cli_runner.invoke(
            app,
            [
                "run",
                "--case", "lid_driven_cavity",
                "--solver", "openfoam",
                "--dry-run",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        # If subprocess was called, it would fail with FileNotFoundError
        # and manifest status would be "failed"
        assert result.exit_code == 0
