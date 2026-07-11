"""Tests for the DockerBackend sandbox profile (Architecture v5.0 §3.2).

Structural (mocked) tests exercise the command-construction path only — no
real Docker daemon required. The single real-docker smoke test at the bottom
(marked ``real_docker``, deselected by default via pyproject addopts) proves
the constructed flags actually enforce the three-zone mount model and
network isolation at runtime; it is skipped when Docker is unavailable.

Tamper-witness discipline (project constitution): the structural key test
asserts every sandbox flag individually (one assertion per flag) so that
dropping any single flag from ``_build_command`` flips exactly that
assertion red — "拆任一 flag 必红". The non-sandbox counterpart proves the
same command builder produces zero sandbox flags when ``sandbox=False``
(the existing 34 tests in test_docker_backend.py already pin that path
byte-for-byte; this file adds the negative assertion at the flag level).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.execution.docker import DockerBackend

# Sandbox-only flags that must never leak into a non-sandbox command.
_SANDBOX_ONLY_FLAGS = (
    "--network",
    "--memory",
    "--pids-limit",
    "--cap-drop",
    "--security-opt",
    "--read-only",
)


def _docker_available() -> bool:
    """Best-effort check that a real Docker daemon is reachable."""
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class TestSandboxInit:
    """Constructor / property surface for the sandbox profile."""

    def test_default_not_sandbox(self) -> None:
        b = DockerBackend(image="test/img")
        assert b.is_sandbox is False

    def test_sandbox_true(self) -> None:
        b = DockerBackend(image="test/img", sandbox=True)
        assert b.is_sandbox is True

    def test_ro_mounts_default_empty(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", sandbox=True)
        # No public accessor for ro_mounts is required by the contract;
        # absence of a crash and is_sandbox=True is the surface we pin.
        assert b.is_sandbox is True

    def test_ro_mounts_accepted(self, tmp_path: Path) -> None:
        judge_dir = tmp_path / "judge"
        b = DockerBackend(
            image="test/img",
            sandbox=True,
            ro_mounts=[(judge_dir, "/judge")],
        )
        assert b.is_sandbox is True


class TestSandboxStructuralKeyWitness:
    """Structural key witness (Architecture v5.0 §6, P2-6 audit item).

    Each assertion below anchors exactly one flag. Deleting any single flag
    from ``_build_command``'s sandbox block turns exactly that assertion
    red — this is the "拆任一 flag 必红" regression gate, and it is a
    permanent CI-checked mechanism (not a one-time human review).
    """

    def test_sandbox_command_contains_every_required_flag(
        self, tmp_path: Path
    ) -> None:
        judge_dir = tmp_path / "judge"
        submission_dir = tmp_path / "submission"
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        b = DockerBackend(
            image="test/img",
            sandbox=True,
            ro_mounts=[(judge_dir, "/judge"), (submission_dir, "/submission")],
        )
        cmd = b._build_command(["pytest"], cwd=work_dir, env=None)

        # --network none
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

        # --memory 2g
        idx = cmd.index("--memory")
        assert cmd[idx + 1] == "2g"

        # --pids-limit 256
        idx = cmd.index("--pids-limit")
        assert cmd[idx + 1] == "256"

        # --cap-drop ALL
        idx = cmd.index("--cap-drop")
        assert cmd[idx + 1] == "ALL"

        # --security-opt no-new-privileges
        idx = cmd.index("--security-opt")
        assert cmd[idx + 1] == "no-new-privileges"

        # --read-only (root filesystem, no value)
        assert "--read-only" in cmd

        # --name cfdb-sbx-<suffix>
        idx = cmd.index("--name")
        name = cmd[idx + 1]
        assert name.startswith("cfdb-sbx-")
        assert len(name) > len("cfdb-sbx-")  # a real random suffix was appended

        # ro_mounts: each entry present as its own -v ...:ro
        vol_entries = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
        judge_entry = next(
            (v for v in vol_entries if v.endswith(":/judge:ro")), None
        )
        submission_entry = next(
            (v for v in vol_entries if v.endswith(":/submission:ro")), None
        )
        assert judge_entry is not None
        assert submission_entry is not None
        assert judge_entry.endswith(":ro")
        assert submission_entry.endswith(":ro")

        # work zone (cwd) is still mounted rw — no :ro suffix on that entry.
        work_entry = next(
            (v for v in vol_entries if v.endswith(f":{b._workdir_in_container}")),
            None,
        )
        assert work_entry is not None
        assert not work_entry.endswith(":ro")

    def test_sandbox_name_honors_explicit_container_name(
        self, tmp_path: Path
    ) -> None:
        b = DockerBackend(image="test/img", sandbox=True)
        cmd = b._build_command(
            ["pytest"], cwd=tmp_path, env=None, container_name="cfdb-sbx-fixed01"
        )
        idx = cmd.index("--name")
        assert cmd[idx + 1] == "cfdb-sbx-fixed01"

    def test_sandbox_env_injection(self, tmp_path: Path) -> None:
        """execute() injects TMPDIR + PYTHONDONTWRITEBYTECODE for sandbox runs."""
        b = DockerBackend(image="test/img", sandbox=True, pull_policy="never")
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
                    args=["docker", "run"], returncode=0, stdout="ok\n", stderr=""
                ),
            ]
            b.execute(["pytest"], cwd=tmp_path)

        run_cmd = mock_run.call_args_list[2][0][0]
        env_entries = [
            run_cmd[i + 1] for i, c in enumerate(run_cmd) if c == "-e"
        ]
        assert "TMPDIR=/work/tmp" in env_entries
        assert "PYTHONDONTWRITEBYTECODE=1" in env_entries
        # execute() must have created the tmp dir on the rw work zone.
        assert (tmp_path / "tmp").is_dir()

    def test_non_sandbox_command_omits_all_sandbox_flags(
        self, tmp_path: Path
    ) -> None:
        """Fail-closed direction: sandbox=False must not leak any sandbox flag.

        This is the other half of the structural key witness — proves the
        additive flag block in ``_build_command`` is truly gated on
        ``self._sandbox`` and not accidentally always-on.
        """
        b = DockerBackend(image="test/img", sandbox=False)
        cmd = b._build_command(["blockMesh"], cwd=tmp_path, env=None)

        for flag in _SANDBOX_ONLY_FLAGS:
            assert flag not in cmd, f"non-sandbox command leaked sandbox flag {flag}"
        assert "--name" not in cmd

    def test_non_sandbox_ignores_ro_mounts(self, tmp_path: Path) -> None:
        """ro_mounts passed to a non-sandbox backend must not be applied.

        Contract: ro_mounts is meaningless without sandbox=True — the
        three-zone mount model only exists in the sandbox profile.
        """
        judge_dir = tmp_path / "judge"
        b = DockerBackend(
            image="test/img", sandbox=False, ro_mounts=[(judge_dir, "/judge")]
        )
        cmd = b._build_command(["blockMesh"], cwd=tmp_path, env=None)
        assert not any(":/judge:ro" in c for c in cmd)


class TestSandboxTimeoutCleanup:
    """Orphaned-container cleanup on the sandbox timeout path (§3.2 fix)."""

    def test_timeout_triggers_kill_and_rm(self, tmp_path: Path) -> None:
        b = DockerBackend(image="test/img", sandbox=True, pull_policy="never")
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
                subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=2),
                # cleanup: docker kill, docker rm -f
                subprocess.CompletedProcess(
                    args=["docker", "kill"], returncode=0, stdout="", stderr=""
                ),
                subprocess.CompletedProcess(
                    args=["docker", "rm"], returncode=0, stdout="", stderr=""
                ),
            ]
            result = b.execute(["pytest"], cwd=tmp_path, timeout=2)

        assert result.timed_out is True
        assert mock_run.call_count == 5
        kill_call = mock_run.call_args_list[3][0][0]
        rm_call = mock_run.call_args_list[4][0][0]
        assert kill_call[:2] == ["docker", "kill"]
        assert rm_call[:3] == ["docker", "rm", "-f"]
        # both cleanup calls target the same sandbox container name
        assert kill_call[2].startswith("cfdb-sbx-")
        assert kill_call[2] == rm_call[3]

    def test_timeout_cleanup_not_triggered_for_non_sandbox(
        self, tmp_path: Path
    ) -> None:
        """Non-sandbox timeouts must not attempt docker kill/rm (no --name)."""
        b = DockerBackend(image="test/img", sandbox=False, pull_policy="never")
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
                subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=2),
            ]
            result = b.execute(["blockMesh"], cwd=tmp_path, timeout=2)

        assert result.timed_out is True
        # Exactly 3 calls (version, inspect, run) — no extra kill/rm calls.
        assert mock_run.call_count == 3

    def test_cleanup_swallows_errors(self, tmp_path: Path) -> None:
        """_cleanup_sandbox_container must not raise even if docker CLI is gone."""
        b = DockerBackend(image="test/img", sandbox=True)
        with patch("cfdb.execution.docker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("docker not on PATH")
            b._cleanup_sandbox_container("cfdb-sbx-deadbeef")  # must not raise


@pytest.mark.real_docker
@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not reachable")
class TestSandboxRealDockerSmoke:
    """Real-daemon smoke test proving the sandbox flags enforce isolation.

    Uses ``ubuntu:22.04`` with pull_policy='never' — the image must already
    be present locally (avoids a network-dependent pull inside a test that
    is itself proving --network none). Deselected by default
    (pyproject addopts: ``-m 'not real_solver and not real_docker'``); run
    explicitly with ``-m real_docker`` to exercise it.
    """

    def test_ro_rw_and_network_isolation(self, tmp_path: Path) -> None:
        judge_dir = tmp_path / "judge"
        judge_dir.mkdir()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        b = DockerBackend(
            image="ubuntu:22.04",
            pull_policy="never",
            sandbox=True,
            ro_mounts=[(judge_dir, "/judge")],
        )
        script = (
            "touch /judge/x 2>/dev/null && echo RO_WRITE_OK || echo RO_WRITE_BLOCKED; "
            "touch /work/x 2>/dev/null && echo RW_WRITE_OK || echo RW_WRITE_BLOCKED; "
            "timeout 3 bash -c 'echo > /dev/tcp/8.8.8.8/53' 2>/dev/null "
            "&& echo NET_OK || echo NET_BLOCKED"
        )
        result = b.execute(["bash", "-c", script], cwd=work_dir, timeout=30)

        assert "RO_WRITE_BLOCKED" in result.stdout
        assert "RW_WRITE_OK" in result.stdout
        assert "NET_BLOCKED" in result.stdout
