"""GenericCommandAdapter — wraps any shell command as a solver adapter."""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

from jinja2 import Template

from cfdb.adapters.base import ArtifactManifest, ResourceSpec, RunResult, SolverAdapter
from cfdb.execution.base import ExecutionBackend
from cfdb.schema import CaseSpec

logger = logging.getLogger(__name__)


class GenericCommandAdapter:
    """Generic command adapter — wraps any shell command.

    The only adapter implementation in P0. Renders SolverConfig.command
    via Jinja2, then delegates execution to an ExecutionBackend.
    """

    name: str = "generic"

    def __init__(
        self,
        dry_run: bool = False,
        backend: ExecutionBackend | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            dry_run: If True, run() returns synthetic result without executing subprocess.
            backend: Execution backend to use (v5.0). If None, defaults to
                LocalExecutionBackend (P0 behavior unchanged). Pass a sandbox-profile
                backend (e.g. DockerBackend) to satisfy CaseSpec.execution.requires_sandbox.
        """
        self._dry_run = dry_run
        if backend is None:
            from cfdb.execution.local import LocalExecutionBackend

            backend = LocalExecutionBackend()
        self._backend = backend

    def _find_solver_config(self, case: CaseSpec) -> str:
        """Find the command template for 'generic' solver in the case.

        Args:
            case: CaseSpec with solver configs.

        Returns:
            The command template string.

        Raises:
            ValueError: If no 'generic' solver config found.
        """
        for solver in case.solvers:
            if solver.name == "generic":
                return solver.command
        raise ValueError(f"no 'generic' solver config found in case '{case.id}'")

    def _get_timeout(self, case: CaseSpec, resources: ResourceSpec | None) -> int | None:
        """Determine the timeout: resources.wall_time_sec or SolverConfig.timeout_sec.

        Args:
            case: CaseSpec.
            resources: Resource spec (optional).

        Returns:
            Timeout in seconds, or None for no timeout.
        """
        if resources is not None and resources.wall_time_sec is not None:
            return resources.wall_time_sec
        for solver in case.solvers:
            if solver.name == "generic":
                return solver.timeout_sec
        return None

    def prepare(self, case: CaseSpec, case_dir: Path, run_dir: Path) -> None:
        """Prepare the run environment.

        1. Create run_dir
        2. Render command template with Jinja2
        3. Write run_dir/run.sh

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Isolated run directory.
        """
        run_dir.mkdir(parents=True, exist_ok=True)

        command_template = self._find_solver_config(case)

        mesh_level = "single"
        if case.mesh is not None and len(case.mesh.levels) > 0:
            mesh_level = case.mesh.levels[0]

        context = {
            "case_id": case.id,
            "solver": "generic",
            "mesh_level": mesh_level,
            "case_dir": case_dir.resolve().as_posix(),
        }

        rendered_cmd = Template(command_template).render(**context)

        script_content = f"""#!/usr/bin/env bash
set -euo pipefail
{rendered_cmd}
"""
        run_script = run_dir / "run.sh"
        run_script.write_text(script_content, encoding="utf-8")
        run_script.chmod(run_script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        logger.debug("prepared run.sh at %s for case '%s'", run_script, case.id)

    def run(
        self,
        case: CaseSpec,
        case_dir: Path,
        run_dir: Path,
        resources: ResourceSpec | None,
    ) -> RunResult:
        """Execute the solver by delegating to the execution backend.

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
            resources: Resource limits (optional).

        Returns:
            RunResult with exit_code, stdout, stderr, wall_time, timed_out.
        """
        if self._dry_run:
            command_template = self._find_solver_config(case)
            mesh_level = "single"
            if case.mesh is not None and len(case.mesh.levels) > 0:
                mesh_level = case.mesh.levels[0]
            context = {
                "case_id": case.id,
                "solver": "generic",
                "mesh_level": mesh_level,
                "case_dir": case_dir.resolve().as_posix(),
                "run_dir": run_dir.resolve().as_posix(),
            }
            rendered = Template(command_template).render(**context)
            logger.info("[dry-run] skipping command: %s", rendered)
            return RunResult(
                exit_code=0,
                stdout="[dry-run] command not executed",
                stderr="",
                wall_time_sec=0.0,
                timed_out=False,
                skipped_commands=[rendered],
            )

        timeout = self._get_timeout(case, resources)
        result = self._backend.execute(
            command=["bash", "run.sh"],
            cwd=run_dir,
            timeout=timeout,
        )
        logger.debug(
            "run completed for case '%s': exit_code=%d, wall_time=%.3fs",
            case.id,
            result.exit_code,
            result.wall_time_sec,
        )
        return result

    def collect_outputs(self, case: CaseSpec, run_dir: Path) -> ArtifactManifest:
        """Collect run artifacts from run_dir, recursively (v5.0 A5).

        Nested output directories (e.g. pytest junitxml under a subdir) are
        no longer dropped — the P0 implementation only scanned the top level.

        Args:
            case: CaseSpec configuration.
            run_dir: Run directory.

        Returns:
            ArtifactManifest with files, qoi_values, and curves.
        """
        files: dict[str, Path] = {}
        qoi_values: dict[str, float] | None = None

        for entry in sorted(run_dir.rglob("*")):
            if entry.is_file():
                rel = entry.relative_to(run_dir)
                files[str(rel)] = rel

        qoi_path = run_dir / "qoi.json"
        if qoi_path.exists():
            try:
                raw = qoi_path.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    qoi_values = {k: float(v) for k, v in parsed.items()}
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning("failed to parse qoi.json: %s", e)
                qoi_values = None

        return ArtifactManifest(files=files, qoi_values=qoi_values, curves=None)


# Ensure the class satisfies the SolverAdapter protocol
_SolverAdapter: type[SolverAdapter] = GenericCommandAdapter  # type: ignore[assignment]
