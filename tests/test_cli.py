"""Tests for cfdb.cli — 4 CLI commands via Typer CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.cli import app


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner fixture."""
    return CliRunner()


@pytest.fixture
def project_cases(tmp_path: Path) -> Path:
    """Point to the real project cases/ directory."""
    return Path(__file__).parent.parent / "cases"


class TestVersionCommand:
    def test_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "cfdb" in result.output
        assert "0.1.0" in result.output

    def test_version_short_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestListCasesCommand:
    def test_list_cases(self, runner: CliRunner, project_cases: Path) -> None:
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(project_cases)])
        assert result.exit_code == 0
        assert "mock_success" in result.output
        assert "mock_failure" in result.output
        assert "Total:" in result.output

    def test_list_cases_empty(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No cases found" in result.output


class TestValidateCaseCommand:
    def test_validate_valid_case(self, runner: CliRunner, project_cases: Path) -> None:
        yaml_path = project_cases / "smoke" / "mock_success" / "case.yaml"
        result = runner.invoke(app, ["validate-case", str(yaml_path)])
        assert result.exit_code == 0
        assert "validation passed" in result.output

    def test_validate_all_mock_cases(self, runner: CliRunner, project_cases: Path) -> None:
        case_names = [
            "mock_success", "mock_failure",
            "mock_missing_reference", "mock_missing_qoi",
        ]
        for case_name in case_names:
            yaml_path = project_cases / "smoke" / case_name / "case.yaml"
            result = runner.invoke(app, ["validate-case", str(yaml_path)])
            assert result.exit_code == 0, f"validation failed for {case_name}: {result.output}"

    def test_validate_invalid_case(self, runner: CliRunner, tmp_path: Path) -> None:
        bad_dir = tmp_path / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        bad_yaml = bad_dir / "case.yaml"
        bad_yaml.write_text(
            "id: BAD_ID\nname: Bad\n category: smoke\n", encoding="utf-8"
        )
        result = runner.invoke(app, ["validate-case", str(bad_yaml)])
        assert result.exit_code == 1


class TestRunCommand:
    def test_run_success(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--backend", "local",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Status:" in result.output
        assert "success" in result.output

    def test_run_failure(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_failure",
                "--solver", "generic",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1
        assert "failed" in result.output

    def test_run_with_report(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
                "--report",
            ],
        )
        assert result.exit_code == 0

    def test_run_missing_qoi(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_missing_qoi",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0

    def test_run_with_sqlite_storage(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        """P2-a: --storage sqlite creates a .db file."""
        runs_dir = tmp_path / "runs"
        db_path = tmp_path / "test.db"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
                "--storage", "sqlite",
                "--db-path", str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert db_path.exists()
        # Dual-write: JSON manifest also exists
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) >= 1
        assert (run_dirs[0] / "manifest.json").exists()

    def test_run_default_json_storage(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        """P2-a: default --storage json does not create .db file."""
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        # No .db file created
        assert not (runs_dir / "cfdb.db").exists()
        # JSON manifest exists
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) >= 1
        assert (run_dirs[0] / "manifest.json").exists()


class TestReportCommand:
    def test_report_from_existing_run(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )

        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) >= 1
        run_dir = run_dirs[0]

        result = runner.invoke(app, ["report", "--run-dir", str(run_dir)])
        assert result.exit_code == 0
        assert "Report generated" in result.output
        assert (run_dir / "report.html").exists()

    def test_report_nonexistent_run(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        fake_run = runs_dir / "nonexistent"
        fake_run.mkdir()
        result = runner.invoke(app, ["report", "--run-dir", str(fake_run)])
        assert result.exit_code == 1
