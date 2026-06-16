"""Tests for cfdb.cli data subcommands (P2-b DVC wrapper).

All subprocess calls mocked — no real DVC installation required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cfdb.cli import app


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner fixture."""
    return CliRunner()


class TestDataStatusCommand:
    """Tests for `cfdb data status`."""

    def test_status_dvc_not_installed(self, runner: CliRunner, tmp_path: Path) -> None:
        """When DVC not on PATH, status should print WARN and exit 0 (graceful)."""
        with patch("cfdb.data.dvc_available", return_value=False):
            result = runner.invoke(app, ["data", "status", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert "DVC not installed" in result.output

    def test_status_up_to_date(self, runner: CliRunner, tmp_path: Path) -> None:
        """When DVC returns empty status dict, workspace is up to date."""
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch("cfdb.data.dvc_status", return_value={}):
                result = runner.invoke(app, ["data", "status", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert "up to date" in result.output

    def test_status_with_changes(self, runner: CliRunner, tmp_path: Path) -> None:
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch(
                "cfdb.data.dvc_status",
                return_value={"mesh.dvc": {"changed": True}, "ref.dvc": {"missing": True}},
            ):
                result = runner.invoke(app, ["data", "status", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert "mesh.dvc" in result.output
        assert "ref.dvc" in result.output

    def test_status_dvc_error_propagates(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        from cfdb.data import DVCError
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch(
                "cfdb.data.dvc_status",
                side_effect=DVCError("not a dvc repo"),
            ):
                result = runner.invoke(app, ["data", "status", "--cwd", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "not a dvc repo" in result.output


class TestDataPullCommand:
    """Tests for `cfdb data pull`."""

    def test_pull_dvc_not_installed(self, runner: CliRunner, tmp_path: Path) -> None:
        """When DVC not on PATH, pull should FAIL with exit 1."""
        with patch("cfdb.data.dvc_available", return_value=False):
            result = runner.invoke(app, ["data", "pull", "--cwd", str(tmp_path)])
        assert result.exit_code == 1
        assert "DVC not installed" in result.output

    def test_pull_all_success(self, runner: CliRunner, tmp_path: Path) -> None:
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch("cfdb.data.dvc_pull", return_value="Downloading mesh...\nDone"):
                result = runner.invoke(app, ["data", "pull", "--cwd", str(tmp_path)])
        assert result.exit_code == 0
        assert "DVC pull complete" in result.output
        assert "Done" in result.output

    def test_pull_with_targets(self, runner: CliRunner, tmp_path: Path) -> None:
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch("cfdb.data.dvc_pull", return_value="") as mock_pull:
                result = runner.invoke(
                    app,
                    [
                        "data", "pull",
                        "--cwd", str(tmp_path),
                        "mesh.dvc",
                        "reference.dvc",
                    ],
                )
        assert result.exit_code == 0
        # Verify targets passed through
        call_kwargs = mock_pull.call_args
        assert call_kwargs.kwargs.get("targets") == ["mesh.dvc", "reference.dvc"]

    def test_pull_dvc_error_propagates(self, runner: CliRunner, tmp_path: Path) -> None:
        from cfdb.data import DVCError
        with patch("cfdb.data.dvc_available", return_value=True):
            with patch(
                "cfdb.data.dvc_pull",
                side_effect=DVCError("network timeout"),
            ):
                result = runner.invoke(app, ["data", "pull", "--cwd", str(tmp_path)])
        assert result.exit_code == 1
        assert "network timeout" in result.output


class TestRunCommandDockerBackend:
    """P2-b: `cfdb run --backend docker` requires --image."""

    def test_docker_without_image_fails(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        """`--backend docker` without `--image` should exit 1 with a clear message."""
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--backend", "docker",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1
        assert "--backend docker requires --image" in result.output

    def test_invalid_pull_policy_fails(
        self, runner: CliRunner, project_cases: Path, tmp_path: Path
    ) -> None:
        """`--pull invalid` should exit 1."""
        runs_dir = tmp_path / "runs"
        result = runner.invoke(
            app,
            [
                "run",
                "--case", "mock_success",
                "--solver", "generic",
                "--backend", "local",
                "--pull", "invalid_policy",
                "--cases-dir", str(project_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1
        assert "--pull must be one of" in result.output


@pytest.fixture
def project_cases(tmp_path: Path) -> Path:
    """Point to the real project cases/ directory."""
    return Path(__file__).parent.parent / "cases"
