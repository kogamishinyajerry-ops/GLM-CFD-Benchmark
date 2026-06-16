"""DockerExecutionBackend — Docker container execution backend.

Executes commands inside a Docker container. The host working directory
(cwd) is bind-mounted into the container at a fixed path (``/work`` by
default) rather than the host absolute path. This avoids Windows path
compatibility issues (drive letters / backslashes are invalid inside a Linux
container) while letting the solver adapter code stay container-agnostic.

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

    Executes commands inside a Docker container. The host working directory
    (cwd) is bind-mounted into the container at a *fixed* path
    (``workdir_in_container``, default ``/work``) rather than the host
    absolute path. This is required for Windows compatibility: host paths
    like ``D:\\GLM-CFD-Benchmark\\runs\\...`` are not valid absolute paths
    inside a Linux container. Command arguments that reference the host cwd
    are rewritten to the container path via :meth:`_rewrite_cmd_paths`, so
    solver adapters remain unaware of the container layout.

    Args:
        image: Docker image reference (e.g. 'openfoam/openfoam:v2406'). Required.
        pull_policy: Image pull policy.
            'always'  — pull before every execution
            'missing' — pull only if image not present locally (default)
            'never'   — never pull (assume image exists locally)
        workdir_in_container: Absolute path inside the container where cwd is
            bind-mounted (default ``/work``).
    """

    name: str = "docker"

    def __init__(
        self,
        image: str,
        pull_policy: Literal["always", "missing", "never"] = "missing",
        workdir_in_container: str = "/work",
    ) -> None:
        """Initialize DockerBackend.

        Args:
            image: Docker image reference (name:tag). Must be non-empty.
            pull_policy: Pull policy (default 'missing').
            workdir_in_container: Absolute path inside the container where the
                host cwd is bind-mounted (default '/work'). Using a fixed
                container path avoids Windows drive letters / backslashes
                leaking into ``--workdir`` and command arguments, which are
                invalid inside a Linux container.

        Raises:
            ValueError: If image is empty.
        """
        if not image:
            raise ValueError("image must be a non-empty string")
        self._image = image
        self._pull_policy = pull_policy
        self._workdir_in_container = workdir_in_container
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

    def _rewrite_cmd_paths(self, command: list[str], cwd: Path) -> list[str]:
        """Replace host cwd path in command args with the container path.

        Solver adapters render command strings that embed the host case
        directory (e.g. ``blockMesh -case D:/GLM-CFD-Benchmark/runs/...``).
        Inside the container that host path does not exist — the same
        directory is mounted at ``self._workdir_in_container``. This helper
        rewrites any command argument containing the host cwd so the solver
        resolves the case directory correctly inside the container.

        Handles three textual forms of the same absolute path:

        - POSIX form (``D:/GLM-CFD-Benchmark/runs/...``) — the most common,
          produced by :meth:`Path.as_posix`.
        - Lowercase drive letter variant (``d:/...``) — some templates
          lowercase the drive letter.
        - Native backslash form (``D:\\GLM-CFD-Benchmark\\runs\\...``) —
          defensive handling for templates that use ``str(path)``.

        Matching is case-sensitive on the path body but case-insensitive on
        the drive letter (Windows is case-insensitive for drive letters).

        Args:
            command: Original command + args potentially containing host paths.
            cwd: Host working directory whose absolute path should be rewritten.

        Returns:
            New list of strings with host cwd replaced by the container path.
        """
        cwd_abs = cwd.resolve()
        host_posix = cwd_abs.as_posix()  # e.g. D:/GLM-CFD-Benchmark/runs/xxx/case
        host_native = str(cwd_abs)  # e.g. D:\GLM-CFD-Benchmark\runs\xxx\case
        host_posix_lower = host_posix[0].lower() + host_posix[1:]  # e.g. d:/...
        container_path = self._workdir_in_container

        rewritten: list[str] = []
        for arg in command:
            new_arg = arg
            # POSIX-form host path (forward slashes, uppercase drive).
            if host_posix in new_arg:
                new_arg = new_arg.replace(host_posix, container_path)
            # Lowercase drive letter variant.
            if host_posix_lower in new_arg:
                new_arg = new_arg.replace(host_posix_lower, container_path)
            # Native backslash form (defensive).
            if host_native in new_arg:
                new_arg = new_arg.replace(host_native, container_path)
            rewritten.append(new_arg)
        return rewritten

    def _build_command(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
    ) -> list[str]:
        """Build the full `docker run ...` command list.

        The host ``cwd`` is bind-mounted to a *fixed* container path
        (``self._workdir_in_container``, default ``/work``) rather than the
        host absolute path. This is required because Windows host paths
        (e.g. ``D:\\GLM-CFD-Benchmark\\runs\\...``) are not valid absolute
        paths inside a Linux container — Docker Desktop rejects them for
        ``--workdir`` and the bind-mount target. ``as_posix()`` converts the
        host side of the mount to forward slashes which Docker Desktop on
        Windows understands for the *source*.

        Args:
            command: Inner command to execute inside the container.
            cwd: Working directory (bind-mounted into the container).
            env: Optional environment variable overrides.

        Returns:
            Full `docker run ...` command as list of strings.
        """
        cwd_abs = cwd.resolve()
        # POSIX-form host path (Docker Desktop on Windows accepts this
        # as the mount source).
        host_path = cwd_abs.as_posix()
        container_path = self._workdir_in_container

        docker_args: list[str] = [
            "docker", "run", "--rm",
            "--workdir", container_path,
            "-v", f"{host_path}:{container_path}",
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

        # 4. Rewrite host paths in the inner command to container paths,
        #    then build the full docker run command.
        rewritten_command = self._rewrite_cmd_paths(command, cwd)
        full_cmd = self._build_command(rewritten_command, cwd, env)

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
