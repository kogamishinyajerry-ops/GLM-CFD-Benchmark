"""LocalExecutionBackend — local subprocess execution."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cfdb.adapters.base import RunResult
from cfdb.execution.base import ExecutionBackend

logger = logging.getLogger(__name__)


def _resolve_bash() -> str:
    """Resolve the bash executable path.

    On Windows, prefers Git Bash over WSL bash to ensure script compatibility.
    On other platforms, returns 'bash' and relies on PATH.

    Returns:
        Path to the bash executable.
    """
    if sys.platform == "win32":
        git_bash = shutil.which("bash")
        git_lower = git_bash.lower() if git_bash else ""
        if git_bash and "system32" not in git_lower and "windowsapps" not in git_lower:
            return git_bash
        candidates = [
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
    return "bash"


class LocalExecutionBackend:
    """Local subprocess execution backend.

    Executes commands via subprocess.run on the local machine.
    Captures stdout/stderr/exit_code/wall_time and writes logs to cwd.
    """

    name: str = "local"

    def _resolve_command(self, command: list[str]) -> list[str]:
        """Resolve command executable, handling bash on Windows.

        Args:
            command: Original command list.

        Returns:
            Command list with resolved executable.
        """
        if command and command[0] == "bash":
            bash_path = _resolve_bash()
            return [bash_path, "--login"] + command[1:]
        return command

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute a command locally via subprocess.

        Args:
            command: Command and arguments list.
            cwd: Working directory.
            timeout: Timeout in seconds (None = unlimited).
            env: Environment variable overrides.

        Returns:
            RunResult with exit_code, stdout, stderr, wall_time_sec, timed_out.
        """
        start = datetime.now(timezone.utc)
        full_env: dict[str, str] | None = None
        if env is not None:
            full_env = {**os.environ, **env}

        resolved = self._resolve_command(command)

        try:
            proc = subprocess.run(
                resolved,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=full_env,
                check=False,
            )
            end = datetime.now(timezone.utc)
            wall = (end - start).total_seconds()

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            self._write_logs(cwd, stdout, stderr)

            return RunResult(
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                wall_time_sec=wall,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            end = datetime.now(timezone.utc)
            wall = (end - start).total_seconds()
            stdout_raw = e.stdout or b""
            stderr_raw = e.stderr or b""
            stdout = (
                stdout_raw
                if isinstance(stdout_raw, str)
                else stdout_raw.decode("utf-8", errors="replace")
            )
            stderr_decoded = (
                stderr_raw
                if isinstance(stderr_raw, str)
                else stderr_raw.decode("utf-8", errors="replace")
            )
            combined_stderr = f"Timeout after {timeout}s\n{stderr_decoded}"
            self._write_logs(cwd, stdout, combined_stderr)
            return RunResult(
                exit_code=-1,
                stdout=stdout,
                stderr=combined_stderr,
                wall_time_sec=float(timeout) if timeout is not None else wall,
                timed_out=True,
            )
        except (FileNotFoundError, OSError) as e:
            end = datetime.now(timezone.utc)
            wall = (end - start).total_seconds()
            err_msg = f"Failed to execute command: {e}"
            self._write_logs(cwd, "", err_msg)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=err_msg,
                wall_time_sec=wall,
                timed_out=False,
            )

    def _write_logs(self, cwd: Path, stdout: str, stderr: str) -> None:
        """Write stdout and stderr to log files in cwd.

        Args:
            cwd: Working directory.
            stdout: stdout content.
            stderr: stderr content.
        """
        try:
            (cwd / "stdout.log").write_text(stdout, encoding="utf-8")
            (cwd / "stderr.log").write_text(stderr, encoding="utf-8")
        except OSError as e:
            logger.warning("failed to write logs to %s: %s", cwd, e)


# Protocol compliance marker
_ExecutionBackend: type[ExecutionBackend] = LocalExecutionBackend  # type: ignore[assignment]
