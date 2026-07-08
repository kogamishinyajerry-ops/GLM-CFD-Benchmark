"""Tests for cfdb.execution.docker.DockerBackend.

All tests mock subprocess.run so they do not require a real Docker daemon
or real Docker images. This satisfies iron rule #5 (DockerBackend tests must
not depend on a real Docker daemon).

For tests that exercise a real Docker daemon, see test_docker_backend_real.py
marked with @pytest.mark.real_docker (deselected in CI by default).
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import pytest

from cfdb.execution.base import ExecutionBackend
from cfdb.execution.docker import BackendError, DockerBackend


class TestDockerBackendInit:
    """Constructor and property tests."""

    def test_default_init(self) -> None:
        b = DockerBackend(image="openfoam/openfoam:v2406")
        assert b.name == "docker"
        assert b.image == "openfoam/openfoam:v2406"
        assert b.pull_policy == "missing"
        assert b.digest is None  # not resolved until execute()

    def test_init_with_pull_policy(self) -> None:
        b = DockerBackend(image="su2code/su2", pull_policy="always")
        assert b.pull_policy == "always"

    def test_empty_image_raises(self) -> None:
        with pytest.raises(ValueError, match="image must be a non-empty string"):
            DockerBackend(image="")

    def test_none_image_raises(self) -> None:
        with pytest.raises(ValueError, match="image must be a non-empty string"):
            DockerBackend(image=None)  # type: ignore[arg-type]

    def test_protocol_compliance(self) -> None:
        """DockerBackend satisfies ExecutionBackend Protocol (structural subtyping)."""
        b: ExecutionBackend = DockerBackend(image="test/image:latest")  # type: ignore[assignment]
        assert hasattr(b, "name")
        assert hasattr(b, "execute")


class TestDaemonCheck:
    """Tests for _check_daemon error paths (all mocked)."""

    def test_daemon_unreachable_raises(self) -> None:
        b = DockerBackend(image="test/img")
        # Simulate docker CLI returning non-zero (daemon not running)
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["docker", "version"], stderr="connection refused"
            )
            with pytest.raises(BackendError, match="docker daemon not reachable"):
                b._check_daemon()

    def test_daemon_timeout_raises(self) -> None:
        b = DockerBackend(image="test/img")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["docker", "version"], timeout=10
            )
            with pytest.raises(BackendError, match="timed out"):
                b._check_daemon()

    def test_docker_not_installed_raises(self) -> None:
        b = DockerBackend(image="test/img")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("docker not on PATH")
            with pytest.raises(BackendError, match="docker executable not found"):
                b._check_daemon()

    def test_daemon_ok(self) -> None:
        b = DockerBackend(image="test/img")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "version"], returncode=0, stdout="20.10.0\n", stderr=""
            )
            # Should not raise
            b._check_daemon()


class TestPullPolicy:
    """Tests for _pull_image behavior."""

    def test_policy_never_skips_pull(self) -> None:
        b = DockerBackend(image="test/img", pull_policy="never")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            b._pull_image()
            # No subprocess calls at all
            mock_run.assert_not_called()

    def test_policy_missing_skips_if_present(self) -> None:
        b = DockerBackend(image="test/img", pull_policy="missing")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            # image inspect succeeds → image present locally → skip pull
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "image", "inspect"], returncode=0, stdout="{}", stderr=""
            )
            b._pull_image()
            # Only inspect was called, not pull
            assert mock_run.call_count == 1
            assert mock_run.call_args[0][0][1] == "image"

    def test_policy_missing_pulls_if_absent(self) -> None:
        b = DockerBackend(image="test/img", pull_policy="missing")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            # First call (inspect) returns non-zero → image absent
            # Second call (pull) returns zero → success
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker", "image", "inspect"], returncode=1,
                stdout="", stderr="no such image"
                ),
                subprocess.CompletedProcess(
                    args=["docker", "pull"], returncode=0, stdout="pulled", stderr=""
                ),
            ]
            b._pull_image()
            assert mock_run.call_count == 2

    def test_policy_missing_pull_failure_raises(self) -> None:
        b = DockerBackend(image="test/img", pull_policy="missing")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker", "image", "inspect"], returncode=1,
                stdout="", stderr="no such image"
                ),
                subprocess.CompletedProcess(
                    args=["docker", "pull"], returncode=1, stdout="", stderr="manifest not found"
                ),
            ]
            with pytest.raises(BackendError, match="failed to pull"):
                b._pull_image()

    def test_policy_always_pulls(self) -> None:
        b = DockerBackend(image="test/img", pull_policy="always")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "pull"], returncode=0, stdout="ok", stderr=""
            )
            b._pull_image()
            # Should only call pull (no inspect check)
            assert mock_run.call_count == 1
            assert mock_run.call_args[0][0][1] == "pull"


class TestDigestResolution:
    """Tests for _resolve_digest."""

    def test_digest_from_repodigests(self) -> None:
        b = DockerBackend(image="openfoam/openfoam:v2406")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "inspect"],
                returncode=0,
                stdout="openfoam/openfoam@sha256:abc123def\n",
                stderr="",
            )
            digest = b._resolve_digest()
            assert digest == "sha256:abc123def"

    def test_digest_fallback_to_image_id(self) -> None:
        b = DockerBackend(image="local/build:latest")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            # First call (RepoDigests) fails
            # Second call (.Id) succeeds
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker", "inspect"], returncode=1, stdout="", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "inspect"],
                    returncode=0,
                    stdout="sha256:abc123\n",
                    stderr="",
                ),
            ]
            digest = b._resolve_digest()
            assert digest == "sha256:abc123"

    def test_digest_empty_on_total_failure(self) -> None:
        b = DockerBackend(image="bad/image:latest")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("docker not on PATH")
            digest = b._resolve_digest()
            assert digest == ""


class TestCommandBuild:
    """Tests for _build_command."""

    def test_basic_command(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img")
        cmd = b._build_command(["blockMesh"], cwd=tmp_path, env=None)
        assert cmd[0:3] == ["docker", "run", "--rm"]
        assert "--workdir" in cmd
        assert "-v" in cmd
        assert cmd[-2] == "test/img"  # image before inner cmd
        assert cmd[-1] == "blockMesh"

    def test_env_vars_injected(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img")
        cmd = b._build_command(
            ["run.sh"],
            cwd=tmp_path,
            env={"FOO": "bar", "BAZ": "qux"},
        )
        assert "-e" in cmd
        # Find env var entries
        env_entries = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-e"]
        assert "FOO=bar" in env_entries
        assert "BAZ=qux" in env_entries

    def test_user_mapping_non_windows(self, tmp_path: Path) -> None:
        """User mapping (--user uid:gid) is added on non-Windows platforms."""
        b = DockerBackend(image="test/img")
        cmd = b._build_command(["ls"], cwd=tmp_path, env=None)
        import sys
        if sys.platform != "win32":
            assert "--user" in cmd
        # On Windows, DockerDesktop handles uid; no --user flag
        # (We don't assert Windows case since tests may run on Linux CI)


class TestExecute:
    """Tests for execute() — full path with daemon/pull/digest mocked."""

    def test_execute_success(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", pull_policy="never")
        # Patch all subprocess calls to simulate success
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # 1. _check_daemon (docker version),
                subprocess.CompletedProcess(
                    args=["docker", "version"], returncode=0, stdout="20.10\n", stderr=""
                ),
                # 2. _resolve_digest (docker inspect RepoDigests),
                subprocess.CompletedProcess(
                    args=["docker", "inspect"],
                    returncode=0,
                    stdout="test/img@sha256:abc\n",
                    stderr="",
                ),
                # 3. actual execution (docker run),
                subprocess.CompletedProcess(
                    args=["docker", "run"],
                    returncode=0,
                    stdout="solver output\n",
                    stderr="",
                ),
            ]
            result = b.execute(["blockMesh"], cwd=tmp_path)

        assert result.exit_code == 0
        assert "solver output" in result.stdout
        assert result.timed_out is False
        assert b.digest == "sha256:abc"

    def test_execute_failure_exit_code(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", pull_policy="never")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker",
                    "version"], returncode=0, stdout="ok", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "inspect"], returncode=0, stdout="x@sha256:y\n", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "run"], returncode=1, stdout="", stderr="blockMesh failed\n"
                ),
            ]
            result = b.execute(["blockMesh"], cwd=tmp_path)

        assert result.exit_code == 1
        assert "blockMesh failed" in result.stderr

    def test_execute_timeout(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", pull_policy="never")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker",
                    "version"], returncode=0, stdout="ok", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "inspect"], returncode=0, stdout="x@sha256:y\n", stderr=""
                ),
                    subprocess.TimeoutExpired(
                        cmd=["docker",
                        "run"], timeout=2
                    ),
            ]
            result = b.execute(["blockMesh"], cwd=tmp_path, timeout=2)

        assert result.timed_out is True
        assert result.exit_code == -1
        assert "Timeout" in result.stderr

    def test_execute_writes_logs(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", pull_policy="never")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker",
                    "version"], returncode=0, stdout="ok", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "inspect"], returncode=0, stdout="x@sha256:y\n", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "run"], returncode=0, stdout="out line\n", stderr="err line\n"
                ),
            ]
            b.execute(["ls"], cwd=tmp_path)

        assert (tmp_path / "stdout.log").exists()
        assert (tmp_path / "stderr.log").exists()
        assert "out line" in (tmp_path / "stdout.log").read_text(encoding="utf-8")
        assert "err line" in (tmp_path / "stderr.log").read_text(encoding="utf-8")

    def test_execute_digest_cached(self, tmp_path: Path) -> None:
        """digest is resolved on first execute() and cached thereafter."""
        b = DockerBackend(image="test/img", pull_policy="never")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker",
                    "version"], returncode=0, stdout="ok", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "inspect"], returncode=0, stdout="x@sha256:first\n", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "run"], returncode=0, stdout="ok", stderr=""
                ),
            ]
            b.execute(["ls"], cwd=tmp_path)

        # Second execute — digest already cached, no new inspect call
        with patch("cfdb.execution.docker.subprocess.run") as mock_run2:
            mock_run2.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker",
                    "version"], returncode=0, stdout="ok", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker",
                    "run"], returncode=0, stdout="ok", stderr=""
                ),
            ]
            b.execute(["ls"], cwd=tmp_path)
            # Verify no inspect call on second execute
            for call_args in mock_run2.call_args_list:
                cmd = call_args[0][0]
                assert "inspect" not in cmd

    def test_execute_daemon_error_propagates(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["docker", "version"], stderr="daemon down"
            )
            with pytest.raises(BackendError, match="daemon not reachable"):
                b.execute(["blockMesh"], cwd=tmp_path)


class TestExecutionBaseProtocol:
    """Verify DockerBackend satisfies the ExecutionBackend Protocol."""

    def test_isinstance_check(self) -> None:
        """runtime_checkable Protocol — isinstance should work for class with name+execute."""
        b: ExecutionBackend = DockerBackend(image="test/img")  # type: ignore[assignment]
        # Protocol is runtime_checkable, so isinstance() checks presence of attrs
        assert isinstance(b, ExecutionBackend)


class _FakeWindowsPath(PureWindowsPath):
    """Windows-semantics path usable on any host OS in these tests.

    ``Path("D:/...")`` is *relative* on POSIX, so ``resolve()`` would prepend
    the real cwd and the tests would only exercise Windows semantics when run
    on Windows. ``PureWindowsPath`` gives faithful Windows behavior
    (``as_posix()`` -> ``D:/...``, ``str()`` -> ``D:\\...``) on every
    platform; the backend only calls ``resolve()`` on the cwd, which we make
    a no-op since the fake path is already absolute.
    """

    def resolve(self, strict: bool = False) -> _FakeWindowsPath:
        """Return self: the fake Windows path is already absolute."""
        return self

    def write_text(self, *args: object, **kwargs: object) -> int:
        """Discard writes: the fake Windows dir does not exist on this host.

        ``execute()`` persists stdout/stderr logs into cwd; that filesystem
        I/O is irrelevant to the path-rewrite assertions under test.
        """
        return 0


class TestWindowsPathCompatibility:
    """Tests for Windows host path handling (bind-mount to fixed container path).

    These tests verify that:
    - ``--workdir`` uses the container path (``/work``), not the host path.
    - The bind-mount uses POSIX-form host path as source and container path
      as target.
    - Inner command arguments containing the host cwd are rewritten to the
      container path.
    - Commands without host paths pass through unchanged.
    All tests mock subprocess.run; no real Docker daemon is needed.
    """

    def test_docker_workdir_uses_container_path(self) -> None:
        """--workdir should be /work, not the host path."""
        b = DockerBackend(image="test/img")
        # Windows absolute path with true Windows semantics on any host OS.
        cwd = _FakeWindowsPath("D:/GLM-CFD-Benchmark/runs/naca0012/case")
        cmd = b._build_command(["blockMesh"], cwd=cwd, env=None)
        workdir_idx = cmd.index("--workdir")
        assert cmd[workdir_idx + 1] == "/work"
        # The bind-mount should map host POSIX path -> /work
        vol_idx = cmd.index("-v")
        assert cmd[vol_idx + 1] == "D:/GLM-CFD-Benchmark/runs/naca0012/case:/work"

    def test_docker_workdir_custom_container_path(self) -> None:
        """A custom workdir_in_container is honored in --workdir and -v."""
        b = DockerBackend(image="test/img", workdir_in_container="/workspace")
        cwd = Path("/tmp/run")
        cmd = b._build_command(["ls"], cwd=cwd, env=None)
        workdir_idx = cmd.index("--workdir")
        assert cmd[workdir_idx + 1] == "/workspace"
        vol_idx = cmd.index("-v")
        # Host path is POSIX-form; target is the custom container path.
        assert cmd[vol_idx + 1].endswith(":/workspace")

    def test_rewrite_cmd_paths_posix_form(self) -> None:
        """blockMesh -case D:/host/case should become blockMesh -case /work."""
        b = DockerBackend(image="test/img")
        cwd = _FakeWindowsPath("D:/host/case")
        original = ["blockMesh", "-case", "D:/host/case/0"]
        rewritten = b._rewrite_cmd_paths(original, cwd)
        assert rewritten == ["blockMesh", "-case", "/work/0"]
        # Tamper witness: an unrelated host path must NOT be rewritten.
        untouched = b._rewrite_cmd_paths(["ls", "D:/other/case"], cwd)
        assert untouched == ["ls", "D:/other/case"]

    def test_rewrite_cmd_paths_native_backslash_form(self) -> None:
        """Native backslash host path (str(path)) is also rewritten.

        We pass the exact native string that ``str(cwd)`` would produce,
        so this test works identically on Windows and POSIX CI.
        """
        b = DockerBackend(image="test/img")
        cwd = Path("/host/case")
        # Compute the native form the same way _rewrite_cmd_paths does.
        native = str(cwd.resolve())
        original = ["ls", native]
        rewritten = b._rewrite_cmd_paths(original, cwd)
        assert rewritten == ["ls", "/work"]

    def test_rewrite_cmd_paths_no_path_no_rewrite(self) -> None:
        """Commands without host path should pass through unchanged."""
        b = DockerBackend(image="test/img")
        cwd = Path("/tmp/abc")
        original = ["ls", "-la"]
        rewritten = b._rewrite_cmd_paths(original, cwd)
        assert rewritten == ["ls", "-la"]

    def test_execute_rewrites_command_paths(self) -> None:
        """execute() rewrites host paths in the inner command before running."""
        b = DockerBackend(image="test/img", pull_policy="never")
        host_case = Path("/tmp/rewrite_case")
        # Build the command argument from the resolved POSIX path so it
        # matches what the adapter would embed and what _rewrite_cmd_paths
        # computes internally (resolve() may add a drive letter on Windows).
        case_arg = host_case.resolve().as_posix()
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker", "version"], returncode=0, stdout="ok\n", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "inspect"],
                    returncode=0,
                    stdout="test/img@sha256:abc\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["docker", "run"], returncode=0, stdout="done\n", stderr=""
                ),
            ]
            b.execute(
                ["blockMesh", "-case", case_arg],
                cwd=host_case,
            )

        # The docker run call is the 3rd subprocess.run invocation.
        # Slice the inner command (everything after the image name) to avoid
        # matching the host path that legitimately appears in the -v mount.
        run_cmd = mock_run.call_args_list[2][0][0]
        image_idx = run_cmd.index("test/img")
        inner_cmd = run_cmd[image_idx + 1:]
        inner_str = " ".join(inner_cmd)
        # The inner command should reference /work, not the host path.
        assert case_arg not in inner_str
        assert "/work" in inner_str

    def test_execute_path_rewrite_windows_style(self) -> None:
        """A Windows-style host path in the command is rewritten to /work."""
        b = DockerBackend(image="test/img", pull_policy="never")
        cwd = _FakeWindowsPath("D:/proj/runs/case")
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["docker", "version"], returncode=0, stdout="ok\n", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "inspect"],
                    returncode=0,
                    stdout="test/img@sha256:abc\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["docker", "run"], returncode=0, stdout="done\n", stderr=""
                ),
            ]
            b.execute(
                ["blockMesh", "-case", "D:/proj/runs/case"],
                cwd=cwd,
            )

        run_cmd = mock_run.call_args_list[2][0][0]
        # Slice the inner command (after the image) to avoid matching the
        # host path in the -v mount target.
        image_idx = run_cmd.index("test/img")
        inner_cmd = run_cmd[image_idx + 1:]
        inner_str = " ".join(inner_cmd)
        # Ensure the Windows path was rewritten in the inner command.
        assert "D:/proj/runs/case" not in inner_str
        assert "/work" in inner_cmd

