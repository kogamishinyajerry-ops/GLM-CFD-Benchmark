"""OpenFOAMAdapter — generates OpenFOAM case structure with dry_run support."""

from __future__ import annotations

import logging
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
from cfdb.schema import CaseSpec, SolverConfig

logger = logging.getLogger(__name__)


class OpenFOAMAdapter:
    """OpenFOAM adapter with dry_run support.

    In dry_run mode: generates complete case directory structure (system/, constant/,
    0/) with Jinja2-rendered config files, but does NOT execute blockMesh/simpleFoam.
    Real execution (P1-b) will call subprocess for each SolverConfig.steps entry.
    """

    name: str = "openfoam"

    def __init__(self, dry_run: bool = False) -> None:
        """Initialize OpenFOAM adapter.

        Args:
            dry_run: If True, run() returns synthetic result without executing subprocess.
        """
        self._dry_run = dry_run
        self._template_dir = Path(__file__).parent / "templates" / "openfoam"

    def _find_solver_config(self, case: CaseSpec) -> SolverConfig:
        """Find the 'openfoam' solver config in the case.

        Args:
            case: CaseSpec with solver configs.

        Returns:
            The SolverConfig for 'openfoam'.

        Raises:
            ValueError: If no 'openfoam' solver config found.
        """
        for solver in case.solvers:
            if solver.name == "openfoam":
                return solver
        raise ValueError(f"no 'openfoam' solver config found in case '{case.id}'")

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
        mesh_level = "single"
        if case.mesh is not None and len(case.mesh.levels) > 0:
            mesh_level = case.mesh.levels[0]
        context: dict[str, Any] = {
            "case_id": case.id,
            "solver": "openfoam",
            "mesh_level": mesh_level,
            "case_dir": case_dir.resolve().as_posix(),
            "run_dir": run_dir.resolve().as_posix(),
            "reynolds": case.conditions.reynolds or 100.0,
        }
        # Compute kinematic viscosity: nu = U_ref * L_ref / Re
        # For cavity tutorial: U_ref=1, L_ref=0.1 → nu = 0.1 / Re
        re = context["reynolds"]
        context["nu"] = 0.1 / re
        # Merge solver parameters (if any)
        if solver_config.parameters:
            context.update(solver_config.parameters)
        return context

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        """Load and render a Jinja2 template from the openfoam template dir.

        Args:
            template_name: Template filename (e.g. 'controlDict.j2').
            context: Jinja2 template context.

        Returns:
            Rendered template string.
        """
        template_path = self._template_dir / template_name
        template = Template(template_path.read_text(encoding="utf-8"))
        return template.render(**context)

    def prepare(self, case: CaseSpec, case_dir: Path, run_dir: Path) -> None:
        """Generate OpenFOAM case directory structure.

        Creates run_dir/case/ with:
        - system/controlDict, system/fvSchemes, system/fvSolution
        - constant/transportProperties, constant/turbulenceProperties
        - constant/polyMesh/ (placeholder dir)
        - 0/U, 0/p (placeholder initial fields)

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
        """
        case_dir_out = run_dir / "case"
        context = self._build_context(case, case_dir, run_dir)

        # Create directory structure
        (case_dir_out / "system").mkdir(parents=True, exist_ok=True)
        (case_dir_out / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
        (case_dir_out / "0").mkdir(parents=True, exist_ok=True)

        # Render and write 5 config files
        (case_dir_out / "system" / "controlDict").write_text(
            self._render_template("controlDict.j2", context), encoding="utf-8"
        )
        (case_dir_out / "system" / "fvSchemes").write_text(
            self._render_template("fvSchemes.j2", context), encoding="utf-8"
        )
        (case_dir_out / "system" / "fvSolution").write_text(
            self._render_template("fvSolution.j2", context), encoding="utf-8"
        )
        (case_dir_out / "constant" / "transportProperties").write_text(
            self._render_template("transportProperties.j2", context), encoding="utf-8"
        )
        (case_dir_out / "constant" / "turbulenceProperties").write_text(
            self._render_template("turbulenceProperties.j2", context), encoding="utf-8"
        )

        # Write placeholder initial fields 0/U and 0/p
        (case_dir_out / "0" / "U").write_text(
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n}\n"
            "dimensions [0 1 -1 0 0 0 0];\n"
            "internalField uniform (0 0 0);\n",
            encoding="utf-8",
        )
        (case_dir_out / "0" / "p").write_text(
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n}\n"
            "dimensions [0 2 -2 0 0 0 0];\n"
            "internalField uniform 0;\n",
            encoding="utf-8",
        )

        logger.debug("OpenFOAM case structure prepared at %s", case_dir_out)

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

        Raises:
            NotImplementedError: In real execution mode (P1-b scope).
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
            logger.info("[dry-run] skipping %d OpenFOAM command(s)", len(skipped))
            return RunResult(
                exit_code=0,
                stdout="[dry-run] commands not executed",
                stderr="",
                wall_time_sec=0.0,
                timed_out=False,
                skipped_commands=skipped,
            )

        # === P1-b: real execution ===
        from cfdb.execution.local import LocalExecutionBackend

        if solver_config.steps is None:
            raise ValueError(
                "OpenFOAM adapter requires SolverConfig.steps for real execution. "
                f"Case '{case.id}' solver '{solver_config.name}' has steps=None."
            )

        backend = LocalExecutionBackend()
        step_results: list[StepResult] = []
        case_dir_out = run_dir / "case"

        # solver_version detection (from first step's stdout, zero extra cost)
        solver_version: str | None = None

        for i, step in enumerate(solver_config.steps):
            rendered_cmd = Template(step.command).render(**context)
            cmd_list = shlex.split(rendered_cmd)

            # Execute via LocalExecutionBackend (iron rule #3)
            result = backend.execute(
                cmd_list,
                cwd=case_dir_out,
                timeout=step.timeout_sec,
            )

            # Write a log file named after the step
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

            # Detect solver_version from the first step (usually blockMesh) stdout
            if i == 0:
                from cfdb.post.residuals import extract_openfoam_version

                solver_version = extract_openfoam_version(result.stdout)

            # CommandStep.critical takes effect
            if result.exit_code != 0:
                if step.critical:
                    # Critical step failed, abort the entire run
                    logger.error(
                        "critical step '%s' failed (exit_code=%d), aborting run",
                        step.name,
                        result.exit_code,
                    )
                    break
                else:
                    # Non-critical step failed, log warning and continue
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

        - exit_code: 0 if all steps succeeded, else first non-zero exit code
        - stdout: concatenated stdout from all executed steps
        - stderr: concatenated stderr from all executed steps
        - wall_time_sec: sum of all step wall times
        - timed_out: True if any step timed out
        - solver_version: detected version (or None)
        - final_residuals: parsed from the last step's stdout (the solve step)

        Args:
            step_results: List of per-step results.
            solver_version: Detected solver version string (or None).

        Returns:
            Merged RunResult.
        """
        # Determine overall exit code
        overall_exit = 0
        for sr in step_results:
            if sr.exit_code != 0:
                overall_exit = sr.exit_code
                break

        # Concatenate stdout/stderr with step headers
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

        # Parse final_residuals from the last step's stdout (the solver step)
        # P2-a: also keep full residuals_history
        final_residuals: dict[str, float] | None = None
        residuals_history: dict[str, list[float]] | None = None
        if overall_exit == 0 and step_results:
            last_stdout = step_results[-1].stdout
            from cfdb.post.residuals import extract_final, parse_openfoam_residuals

            residuals = parse_openfoam_residuals(last_stdout)
            if residuals:
                final_residuals = extract_final(residuals)
                residuals_history = residuals  # P2-a: full history

        # P2-a: step_details from StepResult.to_dict()
        step_details = [sr.to_dict() for sr in step_results] if step_results else None

        # P2-a: cell_count from blockMesh log (first step)
        cell_count: int | None = None
        if step_results:
            from cfdb.post.mesh_stats import extract_openfoam_cell_count

            cell_count = extract_openfoam_cell_count(step_results[0].stdout)

        return RunResult(
            exit_code=overall_exit,
            stdout="\n".join(stdout_parts),
            stderr="\n".join(stderr_parts),
            wall_time_sec=total_wall,
            timed_out=any_timed_out,
            skipped_commands=None,
            solver_version=solver_version,
            final_residuals=final_residuals,
            # === P2-a new fields ===
            cell_count=cell_count,
            step_details=step_details,
            residuals_history=residuals_history,
        )

    def collect_outputs(self, case: CaseSpec, run_dir: Path) -> ArtifactManifest:
        """Scan run_dir/case/ for all generated files and extract QoI.

        Args:
            case: CaseSpec configuration.
            run_dir: Run directory.

        Returns:
            ArtifactManifest with file listing and QoI values (if probes output exists).
        """
        case_dir_out = run_dir / "case"
        files: dict[str, Path] = {}
        qoi_values: dict[str, float] = {}

        if case_dir_out.exists():
            for path in sorted(case_dir_out.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(run_dir)
                    files[rel.as_posix()] = rel

        # P1-b: Extract centerline_umax from probes (if probes output exists)
        probes_dir = case_dir_out / "postProcessing" / "probes"
        if probes_dir.exists() and not self._dry_run:
            from cfdb.post.qoi_extractor import extract_openfoam_centerline_umax

            umax = extract_openfoam_centerline_umax(probes_dir, "U")
            if umax is not None:
                qoi_values["centerline_umax"] = umax

        return ArtifactManifest(
            files=files,
            qoi_values=qoi_values if qoi_values else None,
            curves=None,
        )


# Ensure the class satisfies the SolverAdapter protocol
_SolverAdapter: type[SolverAdapter] = OpenFOAMAdapter  # type: ignore[assignment]
