"""DockerExecutionBackend — Docker container execution backend.

Executes commands inside a Docker container. The working directory (cwd) is
bind-mounted into the container at the same absolute path, so relative paths
work identically inside and outside the container.

P2-b feature. Requires Docker daemon running on the host.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cfdb.adapters.base import RunResult
from cfdb.execution.base import ExecutionBackend

logger = logging.getLogger(__name__)


class BackendError(Exception):
    """Backend infrastructure error (daemon down, image missing, etc).

    Raised when the backend itself cannot function (not when the executed
    command fails — that's a normal RunResult with non-zero exit_code).
    """


class DockerBackend:
    """Docker container execution backend.

    Executes commands inside a Docker container. The working directory (cwd)
    is bind-mounted into the container at the same absolute path, so relative
    paths work identically inside and outside the container.

    Args:
        image: Docker image reference (e.g. 'openfoam/openfoam:v2406'). Required.
        pull_policy: Image pull policy.
            'always'  — pull before every execution
            'missing' — pull only if image not present locally (default)
            'never'   — never pull (assume image exists locally)
    """

    name: str = "docker"

    def __init__(
        self,
        image: str,
        pull_policy: Literal["always", "missing", "never"] = "missing",
    ) -> None:
        """Initialize DockerBackend.

        Args:
            image: Docker image reference (name:tag). Must be non-empty.
            pull_policy: Pull policy (default 'missing').

        Raises:
            ValueError: If image is empty.
        """
        if not image:
            raise ValueError("image must be a non-empty string")
        self._image = image
        self._pull_policy = pull_policy
        self._digest: str | None = None  # cached after first execution

    @property
    def image(self) -> str:
        """Return the configured image reference."""
        return self._image

    @property
    def digest(self) -> str | None:
        """Resolved image digest (sha256:... or short image ID).

        Available after the first execute() call. None before.
        """
        return self._digest

    @property
    def pull_policy(self) -> str:
        """Return the configured pull policy."""
        return self._pull_policy

    def _check_daemon(self) -> None:
        """Check Docker daemon is reachable.

        Raises:
            BackendError: If daemon is not reachable or docker CLI missing.
        """
        try:
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except FileNotFoundError as e:
            raise BackendError(
                "docker executable not found on PATH. Install Docker Desktop / engine."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise BackendError(
                "docker daemon check timed out (>10s). Is Docker Desktop running?"
            ) from e
        except subprocess.CalledProcessError as e:
            raise BackendError(
                f"docker daemon not reachable (exit {e.returncode}): "
                f"{(e.stderr or '').strip()}. Start Docker Desktop / engine."
            ) from e

    def _resolve_digest(self) -> str:
        """Resolve image to sha256 digest (best-effort).

        Tries RepoDigest first, falls back to image ID.

        Returns:
            Digest string (e.g. 'sha256:abc123...'), or empty string if
            unresolvable (e.g. locally-built image without RepoDigest).
        """
        # Try RepoDigest first (works for images pulled from a registry)
        try:
            proc = subprocess.run(
                [
                    "docker", "inspect",
                    "--format", "{{index .RepoDigests 0}}",
                    self._image,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                # Output: openfoam/openfoam@sha256:abc123...
                return proc.stdout.strip().split("@")[-1]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: image ID (short, locally unique)
        try:
            proc = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", self._image],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return ""

    def _pull_image(self) -> None:
        """Pull the image according to pull_policy.

        Raises:
            BackendError: If pull fails (network / non-existent image).
        """
        if self._pull_policy == "never":
            return

        if self._pull_policy == "missing":
            # Check if image exists locally — skip pull if present
            check_proc = subprocess.run(
                ["docker", "image", "inspect", self._image],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if check_proc.returncode == 0:
                return  # already present

        logger.info("pulling docker image %s", self._image)
        pull_proc = subprocess.run(
            ["docker", "pull", self._image],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if pull_proc.returncode != 0:
            raise BackendError(
                f"failed to pull image '{self._image}': {(pull_proc.stderr or '').strip()}"
            )

    def _build_command(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
    ) -> list[str]:
        """Build the full `docker run ...` command list.

        Args:
            command: Inner command to execute inside the container.
            cwd: Working directory (bind-mounted at same absolute path).
            env: Optional environment variable overrides.

        Returns:
            Full `docker run ...` command as list of strings.
        """
        cwd_abs = cwd.resolve()

        docker_args: list[str] = [
            "docker", "run", "--rm",
            "--workdir", str(cwd_abs),
            "-v", f"{cwd_abs}:{cwd_abs}",
        ]

        # User mapping: avoid root-owned files on Linux/macOS host.
        # Skip on Windows (Docker Desktop handles file ownership via vxfsd).
        if sys.platform != "win32":
            uid = os.getuid()
            gid = os.getgid()
            docker_args.extend(["--user", f"{uid}:{gid}"])

        # Environment variable overrides
        if env:
            for k, v in env.items():
                docker_args.extend(["-e", f"{k}={v}"])

        # Image must come before the inner command
        docker_args.append(self._image)

        # The actual command to run inside the container
        docker_args.extend(command)
        return docker_args

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute a command inside a Docker container.

        Args:
            command: Command and args to run (e.g. ['blockMesh']).
            cwd: Working directory (bind-mounted into container at same path).
            timeout: Container execution timeout in seconds (None = unlimited).
            env: Environment variables to inject into the container.

        Returns:
            RunResult with exit_code, stdout, stderr, wall_time_sec, timed_out.

        Raises:
            BackendError: If daemon unreachable or image pull fails. Note: this
                is a backend infrastructure error, distinct from a command
                execution failure (which returns RunResult with non-zero exit_code).
        """
        # 1. Check daemon reachable
        self._check_daemon()

        # 2. Pull image according to pull_policy
        self._pull_image()

        # 3. Resolve & cache digest (after pull, before execute)
        if self._digest is None:
            self._digest = self._resolve_digest()

        # 4. Build full docker run command
        full_cmd = self._build_command(command, cwd, env)

        # 5. Execute via subprocess (same pattern as LocalExecutionBackend)
        start = datetime.now(timezone.utc)
        try:
            proc = subprocess.run(
                full_cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            wall = (datetime.now(timezone.utc) - start).total_seconds()

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
            wall = (datetime.now(timezone.utc) - start).total_seconds()
            raw_stdout = e.stdout or b""
            raw_stderr = e.stderr or b""
            stdout = raw_stdout if isinstance(raw_stdout, str) else raw_stdout.decode("utf-8", errors="replace")
            stderr_decoded = raw_stderr if isinstance(raw_stderr, str) else raw_stderr.decode("utf-8", errors="replace")
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
            wall = (datetime.now(timezone.utc) - start).total_seconds()
            err_msg = f"Docker executable not found or OS error: {e}"
            self._write_logs(cwd, "", err_msg)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=err_msg,
                wall_time_sec=wall,
                timed_out=False,
            )

    def _write_logs(self, cwd: Path, stdout: str, stderr: str) -> None:
        """Write stdout/stderr to log files in cwd.

        Same convention as LocalExecutionBackend — overwrites stdout.log /
        stderr.log in the working directory.

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


# Protocol compliance marker (structural subtyping, like LocalExecutionBackend)
_ExecutionBackend: type[ExecutionBackend] = DockerBackend  # type: ignore[assignment]
