"""End-to-end tests: complete run -> manifest -> metrics -> report flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.cli import app
from cfdb.registry import CaseRegistry
from cfdb.storage.json_repo import JsonManifestRepository

PROJECT_ROOT = Path(__file__).parent.parent
PROJECT_CASES = PROJECT_ROOT / "cases"


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner fixture."""
    return CliRunner()


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    """Isolated workspace with runs directory."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return tmp_path


class TestE2ESmoke:
    def test_list_cases_shows_all_four(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(PROJECT_CASES)])
        assert result.exit_code == 0
        for name in ["mock_success", "mock_failure", "mock_missing_reference", "mock_missing_qoi"]:
            assert name in result.output

    def test_validate_all_four_cases(self, runner: CliRunner) -> None:
        for name in ["mock_success", "mock_failure", "mock_missing_reference", "mock_missing_qoi"]:
            yaml_path = PROJECT_CASES / "smoke" / name / "case.yaml"
            result = runner.invoke(app, ["validate-case", str(yaml_path)])
            assert result.exit_code == 0

    def test_full_success_pipeline(self, runner: CliRunner, isolated_workspace: Path) -> None:
        runs_dir = isolated_workspace / "runs"

        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--backend", "local",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
                "--report",
            ],
        )
        assert result.exit_code == 0

        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]

        manifest_path = run_dir / "manifest.json"
        metrics_path = run_dir / "metrics.json"
        report_path = run_dir / "report.html"

        assert manifest_path.exists()
        assert metrics_path.exists()
        assert report_path.exists()

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))

        assert manifest_data["status"] == "success"
        assert manifest_data["case_id"] == "mock_success"
        assert manifest_data["solver"] == "generic"
        assert metrics_data["qoi_pass"] is True
        assert metrics_data["overall_status"] == "pass"
        assert "centerline_umax" in metrics_data["qoi_relative_errors"]

        report_content = report_path.read_text(encoding="utf-8")
        assert manifest_data["run_id"] in report_content

    def test_failure_pipeline(self, runner: CliRunner, isolated_workspace: Path) -> None:
        runs_dir = isolated_workspace / "runs"

        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_failure",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1

        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        manifest_data = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest_data["status"] == "failed"
        assert manifest_data["error"] is not None

    def test_missing_qoi_pipeline(self, runner: CliRunner, isolated_workspace: Path) -> None:
        runs_dir = isolated_workspace / "runs"

        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_missing_qoi",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0

        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        manifest_data = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        metrics_data = json.loads((run_dirs[0] / "metrics.json").read_text(encoding="utf-8"))

        assert manifest_data["status"] == "success"
        assert metrics_data["overall_status"] == "incomplete"
        assert any("missing" in n for n in metrics_data["notes"])

    def test_report_command_standalone(
        self, runner: CliRunner, isolated_workspace: Path
    ) -> None:
        runs_dir = isolated_workspace / "runs"
        runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--cases-dir", str(PROJECT_CASES),
                "--runs-dir", str(runs_dir),
            ],
        )

        run_dir = next(d for d in runs_dir.iterdir() if d.is_dir())
        report_html_before = run_dir / "report.html"
        if report_html_before.exists():
            report_html_before.unlink()

        result = runner.invoke(app, ["report", "--run-dir", str(run_dir)])
        assert result.exit_code == 0
        assert (run_dir / "report.html").exists()

    def test_repository_list_runs(
        self, runner: CliRunner, isolated_workspace: Path
    ) -> None:
        runs_dir = isolated_workspace / "runs"
        for case in ["mock_success", "mock_failure"]:
            runner.invoke(
                app,
                [
                    "run",
                    "--case", case,
                    "--cases-dir", str(PROJECT_CASES),
                    "--runs-dir", str(runs_dir),
                ],
            )

        repo = JsonManifestRepository(runs_dir)
        all_runs = repo.list_runs()
        assert len(all_runs) == 2

        success_runs = repo.list_runs(case_id="mock_success")
        assert len(success_runs) == 1
        assert success_runs[0].case_id == "mock_success"

    def test_registry_integration(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        cases = registry.list_all()
        # P0: 4 mock cases (smoke) + P1-a: 2 cases (lid_driven_cavity, flat_plate_su2) = 6
        # P2-b: +1 NACA0012 case = 7
        # P2-c: +3 NACA0012 alpha sweep cases (a5/a10/a15) = 10
        # v5.0: +3 coding_tasks + 2 agentic_tasks = 15
        # v5.0 R9: +1 coding_task (smoke_add_two_io, IO-oracle pilot) = 16
        # v5.0 R9 rollout: +1 coding_task (roman_to_int, IO-oracle) = 17
        assert len(cases) == 17

        for name in ["mock_success", "mock_failure", "mock_missing_reference", "mock_missing_qoi"]:
            case = registry.load(name)
            assert case.id == name
            case_dir = registry.get_case_dir(name)
            assert (case_dir / "case.yaml").exists()
