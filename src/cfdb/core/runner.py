"""Runner: the pipeline orchestrator.

Coordinates adapter -> backend -> metrics -> repository.
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone
from pathlib import Path

from cfdb.adapters import get_adapter
from cfdb.adapters.base import SolverAdapter
from cfdb.execution import get_backend
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
        generate_report: bool = False,
        cli_args: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> RunManifest:
        """Execute a complete case run.

        Args:
            case_id: The case identifier to run.
            solver: Solver/adapter name.
            backend: Execution backend name.
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

        adapter = get_adapter(solver, dry_run=dry_run)
        _ = get_backend(backend)  # Validate backend exists

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
            container_digest=None,
            error=error_msg,
            cli_args=cli_args,
            dry_run_skipped_commands=run_result.skipped_commands,
            # === P1-b new fields ===
            solver_version=run_result.solver_version,
            final_residuals=run_result.final_residuals,
        )

        self._repo.save_run(manifest, metrics)
        logger.info("run %s completed with status=%s", run_id, status)

        if generate_report:
            self._generate_report(manifest, metrics, run_dir)

        return manifest

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

            generate_html_report(manifest, metrics, run_dir)
            logger.info("HTML report generated at %s/report.html", run_dir)
        except Exception as e:
            logger.warning("failed to generate report: %s", e)
