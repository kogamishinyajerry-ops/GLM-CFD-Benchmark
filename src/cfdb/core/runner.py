"""Runner: the pipeline orchestrator.

Coordinates adapter -> backend -> metrics -> repository.
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cfdb.adapters import get_adapter
from cfdb.adapters.base import SolverAdapter
from cfdb.execution import get_backend
from cfdb.execution.base import ExecutionBackend
from cfdb.metrics.engine import MetricsEngine
from cfdb.registry import CaseRegistry
from cfdb.schema import CaseSpec, MetricsResult, RunManifest, TimingSpec
from cfdb.storage.base import ResultRepository
from cfdb.utils import generate_run_id, get_git_commit

logger = logging.getLogger(__name__)


class Runner:
    """Pipeline orchestrator: adapter -> backend -> metrics -> repository.

    Executes a full case run: prepare -> run -> collect -> metrics -> save.
    Optionally generates an HTML report.
    """

    def __init__(
        self,
        registry: CaseRegistry,
        repository: ResultRepository,
        runs_root: Path,
    ) -> None:
        """Initialize the Runner.

        Args:
            registry: Case registry for loading CaseSpecs.
            repository: Result repository for saving manifests.
            runs_root: Root directory for run outputs.
        """
        self._registry = registry
        self._repo = repository
        self._runs_root = runs_root
        self._metrics_engine = MetricsEngine()

    def execute(
        self,
        case_id: str,
        solver: str = "generic",
        backend: str = "local",
        backend_options: dict[str, Any] | None = None,
        generate_report: bool = False,
        cli_args: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> RunManifest:
        """Execute a complete case run.

        Args:
            case_id: The case identifier to run.
            solver: Solver/adapter name.
            backend: Execution backend name ('local' or 'docker').
            backend_options: Backend-specific options (P2-b). For Docker:
                {'image': '...', 'pull_policy': 'always|missing|never'}.
            generate_report: If True, generate HTML report after run.
            cli_args: Original CLI arguments for reproducibility.
            dry_run: If True, skip solver execution and return synthetic result.

        Returns:
            RunManifest with the run result.
        """
        case = self._registry.load(case_id)
        case_dir = self._registry.get_case_dir(case_id)

        run_id = generate_run_id(case_id, solver)
        run_dir = self._runs_root / run_id

        # P2-b: construct backend instance (replaces simple get_backend() factory)
        backend_inst = self._build_backend(backend, backend_options)

        adapter = get_adapter(solver, dry_run=dry_run, backend=backend_inst)

        logger.info("starting run %s for case '%s' with solver '%s'", run_id, case_id, solver)

        start_time = datetime.now(timezone.utc)

        run_result = self._execute_phases(adapter, case, case_dir, run_dir, solver, backend)

        end_time = datetime.now(timezone.utc)
        timing = TimingSpec(
            wall_time_sec=(end_time - start_time).total_seconds(),
            start_time=start_time,
            end_time=end_time,
        )

        artifacts = self._collect_artifacts(adapter, case, run_dir)

        metrics = self._metrics_engine.compute(
            case, artifacts, run_result, timing, case_dir
        )

        status = self._determine_status(run_result, dry_run=dry_run)
        error_msg = self._build_error_message(run_result, status)

        # P2-b: extract container_digest from DockerBackend if applicable
        container_digest = None
        if backend == "docker" and hasattr(backend_inst, "digest"):
            container_digest = getattr(backend_inst, "digest", None)

        # P2-b: build backend_options snapshot for manifest reproducibility
        manifest_backend_options: dict[str, Any] | None = None
        if backend == "docker":
            manifest_backend_options = {
                "image": getattr(backend_inst, "image", None),
                "digest": container_digest,
                "pull_policy": getattr(backend_inst, "_pull_policy", "missing"),
            }

        manifest = RunManifest(
            run_id=run_id,
            case_id=case_id,
            solver=solver,
            backend=backend,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            timing=timing,
            host=platform.node(),
            artifacts={k: v for k, v in artifacts.files.items()},
            git_commit=get_git_commit(),
            container_digest=container_digest,
            error=error_msg,
            cli_args=cli_args,
            dry_run_skipped_commands=run_result.skipped_commands,
            # === P1-b new fields ===
            solver_version=run_result.solver_version,
            final_residuals=run_result.final_residuals,
            # === P2-a new fields ===
            cell_count=run_result.cell_count,
            step_details=run_result.step_details,
            residuals_history=run_result.residuals_history,
            # === P2-b new fields ===
            backend_options=manifest_backend_options,
        )

        self._repo.save_run(manifest, metrics)
        logger.info("run %s completed with status=%s", run_id, status)

        if generate_report:
            self._generate_report(manifest, metrics, run_dir)

        return manifest

    def _build_backend(
        self,
        name: str,
        options: dict[str, Any] | None,
    ) -> ExecutionBackend:
        """Construct a backend instance from name + options.

        Args:
            name: Backend name ('local' or 'docker').
            options: Backend-specific options. For Docker: {'image': ..., 'pull_policy': ...}.

        Returns:
            ExecutionBackend instance.

        Raises:
            ValueError: If backend name is unknown or required options missing.
        """
        opts = options or {}
        if name == "local":
            return get_backend("local")
        elif name == "docker":
            from cfdb.execution.docker import DockerBackend
            image = opts.get("image")
            if not image:
                raise ValueError(
                    "docker backend requires 'image' option (e.g. --image openfoam/openfoam:v2406)"
                )
            return DockerBackend(
                image=image,
                pull_policy=opts.get("pull_policy", "missing"),
            )
        else:
            raise ValueError(
                f"Unknown backend: '{name}'. Available: ['local', 'docker']"
            )

    def _execute_phases(
        self,
        adapter: SolverAdapter,
        case: CaseSpec,
        case_dir: Path,
        run_dir: Path,
        solver: str,
        backend: str,
    ):
        """Run prepare and execute phases.

        Args:
            adapter: Solver adapter.
            case: CaseSpec.
            case_dir: Case directory.
            run_dir: Run directory.
            solver: Solver name.
            backend: Backend name.

        Returns:
            RunResult from the adapter.
        """
        from cfdb.adapters.base import RunResult

        try:
            adapter.prepare(case, case_dir, run_dir)
        except Exception as e:
            logger.error("prepare failed for case '%s': %s", case.id, e)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=f"prepare failed: {e}",
                wall_time_sec=0.0,
                timed_out=False,
            )

        try:
            return adapter.run(case, case_dir, run_dir, resources=None)
        except Exception as e:
            logger.error("run failed for case '%s': %s", case.id, e)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=f"run failed: {e}",
                wall_time_sec=0.0,
                timed_out=False,
            )

    def _collect_artifacts(self, adapter: SolverAdapter, case: CaseSpec, run_dir: Path):
        """Collect outputs phase.

        Args:
            adapter: Solver adapter.
            case: CaseSpec.
            run_dir: Run directory.

        Returns:
            ArtifactManifest.
        """
        try:
            return adapter.collect_outputs(case, run_dir)
        except Exception as e:
            logger.error("collect_outputs failed for case '%s': %s", case.id, e)
            from cfdb.adapters.base import ArtifactManifest

            return ArtifactManifest(files={}, qoi_values=None, curves=None)

    def _determine_status(self, run_result, dry_run: bool = False) -> str:
        """Determine run status from RunResult.

        Args:
            run_result: The execution result.
            dry_run: Whether this is a dry-run.

        Returns:
            'success', 'failed', 'timeout', or 'dry_run'.
        """
        if dry_run:
            return "dry_run"
        if run_result.timed_out:
            return "timeout"
        if run_result.exit_code != 0:
            return "failed"
        return "success"

    def _build_error_message(self, run_result, status: str) -> str | None:
        """Build error message for failed/timeout runs.

        Args:
            run_result: The execution result.
            status: The determined status.

        Returns:
            Error message string, or None if success or dry_run.
        """
        if status in ("success", "dry_run"):
            return None
        parts: list[str] = []
        if run_result.timed_out:
            parts.append("Execution timed out.")
        if run_result.stderr:
            parts.append(f"stderr:\n{run_result.stderr}")
        if run_result.exit_code != 0 and not run_result.timed_out:
            parts.append(f"exit_code={run_result.exit_code}")
        return "\n".join(parts) if parts else None

    def _generate_report(
        self,
        manifest: RunManifest,
        metrics: MetricsResult,
        run_dir: Path,
    ) -> None:
        """Generate HTML report.

        Args:
            manifest: Run manifest.
            metrics: Metrics result.
            run_dir: Run directory.
        """
        try:
            from cfdb.reporting.html import generate_html_report

            # P2-a: Generate residual SVG if residuals_history is available
            residuals_svg: str | None = None
            if manifest.residuals_history:
                from cfdb.reporting.svg_residuals import render_residual_svg

                residuals_svg = render_residual_svg(
                    residuals=manifest.residuals_history,
                    title=f"Residual Convergence — {manifest.case_id} ({manifest.solver})",
                    log_scale=True,
                )

            generate_html_report(
                manifest, metrics, run_dir,
                residuals_svg=residuals_svg,
            )
            logger.info("HTML report generated at %s/report.html", run_dir)
        except Exception as e:
            logger.warning("failed to generate report: %s", e)
