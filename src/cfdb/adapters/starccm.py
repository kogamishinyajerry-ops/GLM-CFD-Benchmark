"""StarCCMAdapter — generates Star-CCM+ Java macro with dry_run support."""

from __future__ import annotations

import logging
import math
import shlex
from pathlib import Path
from typing import Any

from jinja2 import Template

from cfdb.adapters.base import (
    ArtifactManifest,
    ResourceSpec,
    RunResult,
    SolverAdapter,
    StepResult,
)
from cfdb.execution.base import ExecutionBackend
from cfdb.schema import CaseSpec, SolverConfig

logger = logging.getLogger(__name__)


class StarCCMAdapter:
    """Star-CCM+ adapter with dry_run support.

    In dry_run mode: generates a complete Star-CCM+ Java macro (.java) in
    the run_dir/case/ directory, but does NOT execute starccm+ -batch.
    Real execution calls starccm+ subprocess via the injected
    ExecutionBackend (local or docker).

    Data extraction: relies on the macro exporting forces.csv and
    residuals.csv to the case directory; collect_outputs() reads
    these CSVs post-run to populate Cl, Cd, and residuals history.
    """

    name: str = "starccm"

    def __init__(
        self,
        dry_run: bool = False,
        backend: ExecutionBackend | None = None,
    ) -> None:
        """Initialize Star-CCM+ adapter.

        Args:
            dry_run: If True, run() returns synthetic result without
                executing subprocess.
            backend: Execution backend to use (P2-b). If None, defaults to
                LocalExecutionBackend.
        """
        self._dry_run = dry_run
        if backend is None:
            from cfdb.execution.local import LocalExecutionBackend

            backend = LocalExecutionBackend()
        self._backend = backend
        self._template_dir = Path(__file__).parent / "templates" / "starccm"

    def _find_solver_config(self, case: CaseSpec) -> SolverConfig:
        """Find the 'starccm' solver config in the case.

        Args:
            case: CaseSpec with solver configs.

        Returns:
            The SolverConfig for 'starccm'.

        Raises:
            ValueError: If no 'starccm' solver config found.
        """
        for solver in case.solvers:
            if solver.name == "starccm":
                return solver
        raise ValueError(f"no 'starccm' solver config found in case '{case.id}'")

    def _build_context(
        self, case: CaseSpec, case_dir: Path, run_dir: Path
    ) -> dict[str, Any]:
        """Build Jinja2 template context from case parameters.

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.

        Returns:
            Template context dict with standard and solver-specific variables.
        """
        solver_config = self._find_solver_config(case)

        case_dir_out = run_dir / "case"
        mach = case.conditions.mach or 0.3
        reynolds = case.conditions.reynolds or 1e6

        context: dict[str, Any] = {
            "case_id": case.id,
            "solver": "starccm",
            "case_dir": case_dir_out.resolve().as_posix(),
            "run_dir": run_dir.resolve().as_posix(),
            "mach": mach,
            "reynolds": reynolds,
        }

        # Alpha (angle of attack)
        alpha_deg: float = 0.0
        if case.conditions.alpha_deg is not None:
            alpha_deg = float(case.conditions.alpha_deg)
        elif solver_config.parameters and "alpha_deg" in solver_config.parameters:
            alpha_deg = float(solver_config.parameters["alpha_deg"])
        alpha_rad = math.radians(alpha_deg)

        # Freestream velocity
        u_inf = 100.0
        if solver_config.parameters and "u_inf" in solver_config.parameters:
            u_inf = float(solver_config.parameters["u_inf"])

        context["alpha_deg"] = alpha_deg
        context["alpha_rad"] = alpha_rad
        context["u_inf"] = u_inf
        context["u_cos"] = u_inf * math.cos(alpha_rad)
        context["v_sin"] = u_inf * math.sin(alpha_rad)

        # Number of iterations (default 500)
        n_iter = 500
        if solver_config.parameters and "n_iter" in solver_config.parameters:
            n_iter = int(solver_config.parameters["n_iter"])
        context["n_iter"] = n_iter

        # Merge remaining solver parameters
        if solver_config.parameters:
            context.update(solver_config.parameters)

        return context

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        """Load and render a Jinja2 template from the starccm template dir.

        Args:
            template_name: Template filename (e.g. 'base.java.j2').
            context: Jinja2 template context.

        Returns:
            Rendered template string.
        """
        template_path = self._template_dir / template_name
        template = Template(template_path.read_text(encoding="utf-8"))
        return template.render(**context)

    def _is_naca_case(self, case: CaseSpec) -> bool:
        """Check if this is a NACA0012 case (triggers NACA template routing).

        Uses case.id prefix matching: 'naca0012', 'naca0012_a0',
        'naca0012_a5', 'naca0012_a10', 'naca0012_a15' all start with
        'naca0012'.

        Args:
            case: CaseSpec configuration.

        Returns:
            True if case.id starts with 'naca0012', False otherwise.
        """
        return case.id.startswith("naca0012")

    def prepare(self, case: CaseSpec, case_dir: Path, run_dir: Path) -> None:
        """Generate Star-CCM+ Java macro in run_dir/case/.

        Routes to NACA template when case.id starts with 'naca0012',
        otherwise uses the general-purpose base template.

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
        """
        case_dir_out = run_dir / "case"
        case_dir_out.mkdir(parents=True, exist_ok=True)
        context = self._build_context(case, case_dir, run_dir)

        if self._is_naca_case(case):
            macro_content = self._render_template("naca0012.java.j2", context)
        else:
            macro_content = self._render_template("base.java.j2", context)

        # Write the macro
        macro_path = case_dir_out / "run.java"
        macro_path.write_text(macro_content, encoding="utf-8")

        logger.debug("StarCCM macro prepared at %s", macro_path)

    def run(
        self,
        case: CaseSpec,
        case_dir: Path,
        run_dir: Path,
        resources: ResourceSpec | None,
    ) -> RunResult:
        """Execute solver or return synthetic result in dry_run mode.

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
            resources: Resource limits (optional).

        Returns:
            RunResult with exit_code, stdout, stderr, wall_time, timed_out.
        """
        solver_config = self._find_solver_config(case)
        context = self._build_context(case, case_dir, run_dir)

        if self._dry_run:
            skipped: list[str] = []
            if solver_config.steps:
                for step in solver_config.steps:
                    rendered = Template(step.command).render(**context)
                    skipped.append(rendered)
            else:
                rendered = Template(solver_config.command).render(**context)
                skipped.append(rendered)
            logger.info("[dry-run] skipping %d StarCCM command(s)", len(skipped))
            return RunResult(
                exit_code=0,
                stdout="[dry-run] commands not executed",
                stderr="",
                wall_time_sec=0.0,
                timed_out=False,
                skipped_commands=skipped,
            )

        # === Real execution ===
        if solver_config.steps is None:
            raise ValueError(
                "StarCCM adapter requires SolverConfig.steps for real execution. "
                f"Case '{case.id}' solver '{solver_config.name}' has steps=None."
            )

        backend = self._backend
        step_results: list[StepResult] = []
        case_dir_out = run_dir / "case"
        solver_version: str | None = None

        for i, step in enumerate(solver_config.steps):
            rendered_cmd = Template(step.command).render(**context)
            cmd_list = shlex.split(rendered_cmd)

            result = backend.execute(
                cmd_list,
                cwd=case_dir_out,
                timeout=step.timeout_sec,
            )

            # Write log file
            log_name = f"log.{step.name}"
            (case_dir_out / log_name).write_text(
                result.stdout + "\n" + result.stderr, encoding="utf-8"
            )

            step_results.append(
                StepResult(
                    name=step.name,
                    exit_code=result.exit_code,
                    wall_time_sec=result.wall_time_sec,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    timed_out=result.timed_out,
                    critical=step.critical,
                )
            )

            # Detect solver version from first step stdout
            if i == 0:
                from cfdb.post.residuals import extract_starccm_version

                solver_version = extract_starccm_version(result.stdout)

            # Critical step handling
            if result.exit_code != 0:
                if step.critical:
                    logger.error(
                        "critical step '%s' failed (exit_code=%d), aborting run",
                        step.name,
                        result.exit_code,
                    )
                    break
                else:
                    logger.warning(
                        "non-critical step '%s' failed (exit_code=%d), continuing",
                        step.name,
                        result.exit_code,
                    )

        return self._merge_step_results(step_results, solver_version)

    def _merge_step_results(
        self,
        step_results: list[StepResult],
        solver_version: str | None,
    ) -> RunResult:
        """Merge multiple StepResult into a single RunResult.

        Args:
            step_results: List of per-step results.
            solver_version: Detected solver version string (or None).

        Returns:
            Merged RunResult.
        """
        overall_exit = 0
        for sr in step_results:
            if sr.exit_code != 0:
                overall_exit = sr.exit_code
                break

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        total_wall = 0.0
        any_timed_out = False
        for sr in step_results:
            stdout_parts.append(f"--- step: {sr.name} ---\n{sr.stdout}")
            stderr_parts.append(f"--- step: {sr.name} ---\n{sr.stderr}")
            total_wall += sr.wall_time_sec
            if sr.timed_out:
                any_timed_out = True

        # Parse final_residuals from the last step's stdout
        final_residuals: dict[str, float] | None = None
        residuals_history: dict[str, list[float]] | None = None
        if overall_exit == 0 and step_results:
            last_stdout = step_results[-1].stdout
            from cfdb.post.residuals import extract_final, parse_starccm_residuals

            residuals = parse_starccm_residuals(last_stdout)
            if residuals:
                final_residuals = extract_final(residuals)
                residuals_history = residuals

        # Step details
        step_details = [sr.to_dict() for sr in step_results] if step_results else None

        # Cell count from mesh import log (first step)
        cell_count: int | None = None
        if step_results:
            from cfdb.post.mesh_stats import extract_starccm_cell_count

            cell_count = extract_starccm_cell_count(step_results[0].stdout)

        return RunResult(
            exit_code=overall_exit,
            stdout="\n".join(stdout_parts),
            stderr="\n".join(stderr_parts),
            wall_time_sec=total_wall,
            timed_out=any_timed_out,
            skipped_commands=None,
            solver_version=solver_version,
            final_residuals=final_residuals,
            cell_count=cell_count,
            step_details=step_details,
            residuals_history=residuals_history,
        )

    def collect_outputs(self, case: CaseSpec, run_dir: Path) -> ArtifactManifest:
        """Scan run_dir/case/ for all generated files and extract QoI.

        For NACA cases: reads forces.csv for Cl/Cd via
        extract_cl_cd_starccm(). Non-NACA cases: standard file scan without
        QoI extraction.

        Args:
            case: CaseSpec configuration.
            run_dir: Run directory.

        Returns:
            ArtifactManifest with file listing and QoI values.
        """
        case_dir_out = run_dir / "case"
        files: dict[str, Path] = {}
        qoi_values: dict[str, float] = {}

        if case_dir_out.exists():
            for path in sorted(case_dir_out.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(run_dir)
                    files[rel.as_posix()] = rel

        if self._dry_run:
            return ArtifactManifest(files=files, qoi_values=None, curves=None)

        if self._is_naca_case(case):
            from cfdb.post.qoi_extractor import extract_cl_cd_starccm

            forces_csv = case_dir_out / "forces.csv"
            if forces_csv.exists():
                solver_config = self._find_solver_config(case)
                u_inf = 100.0
                if solver_config.parameters and "u_inf" in solver_config.parameters:
                    u_inf = float(solver_config.parameters["u_inf"])
                rho = 1.225
                a_ref = 1.0

                result = extract_cl_cd_starccm(forces_csv, rho=rho, u_inf=u_inf, a_ref=a_ref)
                if result is not None:
                    cl, cd = result
                    qoi_values["cl"] = cl
                    qoi_values["cd"] = cd
                else:
                    logger.warning(
                        "forces.csv found but Cl/Cd extraction returned None "
                        "for case %s",
                        case.id,
                    )
            else:
                logger.warning("no forces.csv found in %s for case %s", case_dir_out, case.id)

        return ArtifactManifest(
            files=files,
            qoi_values=qoi_values if qoi_values else None,
            curves=None,
        )


# Ensure the class satisfies the SolverAdapter protocol
_SolverAdapter: type[SolverAdapter] = StarCCMAdapter  # type: ignore[assignment]
