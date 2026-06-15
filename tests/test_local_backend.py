"""Tests for cfdb.execution.local.LocalExecutionBackend."""

from __future__ import annotations

import stat
from pathlib import Path

from cfdb.execution.local import LocalExecutionBackend


def make_script(dir_path: Path, content: str) -> Path:
    """Create an executable run.sh in the given directory."""
    script = dir_path / "run.sh"
    script.write_text(content, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


class TestLocalExecutionBackend:
    def test_name(self) -> None:
        backend = LocalExecutionBackend()
        assert backend.name == "local"

    def test_execute_success(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(tmp_path, "#!/usr/bin/env bash\necho 'hello world'\nexit 0\n")
        result = backend.execute(["bash", "run.sh"], cwd=tmp_path)
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.timed_out is False
        assert result.wall_time_sec >= 0

    def test_execute_failure(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(
            tmp_path,
            "#!/usr/bin/env bash\necho 'error msg' >&2\nexit 1\n",
        )
        result = backend.execute(["bash", "run.sh"], cwd=tmp_path)
        assert result.exit_code == 1
        assert "error msg" in result.stderr

    def test_execute_timeout(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(
            tmp_path,
            "#!/usr/bin/env bash\nsleep 10\nexit 0\n",
        )
        result = backend.execute(["bash", "run.sh"], cwd=tmp_path, timeout=2)
        assert result.timed_out is True
        assert result.exit_code == -1
        assert "Timeout" in result.stderr

    def test_execute_writes_logs(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(tmp_path, "#!/usr/bin/env bash\necho 'out'\necho 'err' >&2\nexit 0\n")
        backend.execute(["bash", "run.sh"], cwd=tmp_path)
        assert (tmp_path / "stdout.log").exists()
        assert (tmp_path / "stderr.log").exists()
        assert "out" in (tmp_path / "stdout.log").read_text(encoding="utf-8")
        assert "err" in (tmp_path / "stderr.log").read_text(encoding="utf-8")

    def test_execute_command_not_found(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        result = backend.execute(["nonexistent_cmd_xyz"], cwd=tmp_path)
        assert result.exit_code == -1
        assert "Failed" in result.stderr or "No such" in result.stderr

    def test_execute_with_env(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(
            tmp_path,
            "#!/usr/bin/env bash\necho $MY_TEST_VAR\nexit 0\n",
        )
        result = backend.execute(
            ["bash", "run.sh"], cwd=tmp_path, env={"MY_TEST_VAR": "custom_value"}
        )
        assert result.exit_code == 0
        assert "custom_value" in result.stdout

    def test_wall_time_positive(self, tmp_path: Path) -> None:
        backend = LocalExecutionBackend()
        make_script(tmp_path, "#!/usr/bin/env bash\necho hi\nexit 0\n")
        result = backend.execute(["bash", "run.sh"], cwd=tmp_path)
        assert result.wall_time_sec >= 0
