"""Tests for cfdb.data.dvc — DVC CLI wrapper.

All subprocess calls are mocked so tests do not require DVC installation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.data.dvc import DVCError, dvc_available, dvc_pull, dvc_status


class TestDVCAvailable:
    """Tests for dvc_available()."""

    def test_available_when_dvc_on_path_and_ok(self) -> None:
        with (
            patch("cfdb.data.dvc.shutil.which", return_value="/usr/bin/dvc"),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "--version"], returncode=0, stdout="3.0.0\n", stderr=""
                )
                assert dvc_available() is True

    def test_unavailable_when_not_on_path(self) -> None:
        with patch("cfdb.data.dvc.shutil.which", return_value=None):
            assert dvc_available() is False

    def test_unavailable_when_version_fails(self) -> None:
        with (
            patch("cfdb.data.dvc.shutil.which", return_value="/usr/bin/dvc"),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.side_effect = subprocess.CalledProcessError(
                    returncode=1, cmd=["dvc", "--version"]
                )
                assert dvc_available() is False

    def test_unavailable_when_timeout(self) -> None:
        with (
            patch("cfdb.data.dvc.shutil.which", return_value="/usr/bin/dvc"),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.side_effect = subprocess.TimeoutExpired(
                    cmd=["dvc", "--version"], timeout=5
                )
                assert dvc_available() is False


class TestDVCPull:
    """Tests for dvc_pull()."""

    def test_pull_all_success(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "pull"], returncode=0, stdout="Downloading...\nDone\n", stderr=""
                )
                output = dvc_pull(cwd=tmp_path)
        assert "Done" in output
        assert mock_run.call_args[0][0] == ["dvc", "pull"]
        assert mock_run.call_args[1]["cwd"] == str(tmp_path)

    def test_pull_with_targets(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "pull"], returncode=0, stdout="", stderr=""
                )
                dvc_pull(targets=["mesh.dvc", "reference.dvc"], cwd=tmp_path)
        cmd = mock_run.call_args[0][0]
        assert cmd == ["dvc", "pull", "mesh.dvc", "reference.dvc"]

    def test_pull_dvc_not_installed_raises(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=False),
            pytest.raises(DVCError, match="dvc not found"),
        ):
                dvc_pull(cwd=tmp_path)

    def test_pull_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "pull"], returncode=1, stdout="", stderr="connection timeout\n"
                )
                with pytest.raises(DVCError, match="dvc pull failed"):
                    dvc_pull(cwd=tmp_path)

    def test_pull_uses_default_cwd_when_none(self) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "pull"], returncode=0, stdout="", stderr=""
                )
                dvc_pull()
        cwd_arg = mock_run.call_args[1]["cwd"]
        # Should be a non-empty string path
        assert isinstance(cwd_arg, str) and len(cwd_arg) > 0


class TestDVCStatus:
    """Tests for dvc_status()."""

    def test_status_up_to_date(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "status"], returncode=0, stdout="{}", stderr=""
                )
                status = dvc_status(cwd=tmp_path)
        assert status == {}

    def test_status_with_changes(self, tmp_path: Path) -> None:
        status_json = '{"mesh.dvc": {"changed": true}}'
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "status"], returncode=0, stdout=status_json, stderr=""
                )
                status = dvc_status(cwd=tmp_path)
        assert "mesh.dvc" in status

    def test_status_dvc_not_installed_raises(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=False),
            pytest.raises(DVCError, match="dvc not found"),
        ):
                dvc_status(cwd=tmp_path)

    def test_status_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "status"], returncode=1, stdout="", stderr="not a dvc repo\n"
                )
                with pytest.raises(DVCError, match="dvc status failed"):
                    dvc_status(cwd=tmp_path)

    def test_status_empty_stdout_returns_empty_dict(self, tmp_path: Path) -> None:
        """If dvc returns empty stdout, json.loads('{}') should succeed."""
        with (
            patch("cfdb.data.dvc.dvc_available", return_value=True),
            patch("cfdb.data.dvc.subprocess.run") as mock_run,
        ):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["dvc", "status"], returncode=0, stdout="", stderr=""
                )
                status = dvc_status(cwd=tmp_path)
        assert status == {}


class TestPackageImport:
    """Tests for package-level imports."""

    def test_imports_available(self) -> None:
        from cfdb.data import DVC_AVAILABLE, DVCError, dvc_available, dvc_pull, dvc_status
        # All symbols importable
        assert DVCError is not None
        assert callable(dvc_available)
        assert callable(dvc_pull)
        assert callable(dvc_status)
        # DVC_AVAILABLE is a bool (value depends on test environment)
        assert isinstance(DVC_AVAILABLE, bool)

