"""OpenFOAMAdapter — generates OpenFOAM case structure with dry_run support."""

from __future__ import annotations

import logging
import math
import shlex
import shutil
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


class OpenFOAMAdapter:
    """OpenFOAM adapter with dry_run support.

    In dry_run mode: generates complete case directory structure (system/, constant/,
    0/) with Jinja2-rendered config files, but does NOT execute blockMesh/simpleFoam.
    Real execution (P1-b) calls subprocess for each SolverConfig.steps entry via
    the injected ExecutionBackend (P2-b: local or docker).
    """

    name: str = "openfoam"

    def __init__(
        self,
        dry_run: bool = False,
        backend: ExecutionBackend | None = None,
    ) -> None:
        """Initialize OpenFOAM adapter.

        Args:
            dry_run: If True, run() returns synthetic result without executing subprocess.
            backend: Execution backend to use (P2-b). If None, defaults to
                LocalExecutionBackend. For Docker execution, pass DockerBackend instance.
        """
        self._dry_run = dry_run
        if backend is None:
            from cfdb.execution.local import LocalExecutionBackend
            backend = LocalExecutionBackend()
        self._backend = backend
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
        # NOTE: 'case_dir' in the template context points to the OUTPUT
        # case directory (run_dir/case) — not the original case definition
        # directory. This is what solver commands like
        # 'blockMesh -case {{ case_dir }}' need: the runtime workspace where
        # system/ constant/ 0/ were rendered. The original case_dir is still
        # passed separately as a method argument for STL/asset lookups.
        case_dir_out = run_dir / "case"
        context: dict[str, Any] = {
            "case_id": case.id,
            "solver": "openfoam",
            "mesh_level": mesh_level,
            "case_dir": case_dir_out.resolve().as_posix(),
            "run_dir": run_dir.resolve().as_posix(),
            "reynolds": case.conditions.reynolds or 100.0,
        }
        # Compute kinematic viscosity: nu = U_ref * L_ref / Re
        # For cavity tutorial: U_ref=1, L_ref=0.1 → nu = 0.1 / Re
        re = context["reynolds"]
        context["nu"] = 0.1 / re
        # Merge solver parameters (if any) — may override nu, add u_inf, etc.
        if solver_config.parameters:
            context.update(solver_config.parameters)

        # === P3-hotfix: NACA alpha-derived freestream velocity components ===
        # alpha_deg comes from case.conditions.alpha_deg, or fallback to
        # solver parameters["alpha_deg"], or defaults to 0.0.
        alpha_deg: float = 0.0
        if case.conditions.alpha_deg is not None:
            alpha_deg = float(case.conditions.alpha_deg)
        elif solver_config.parameters and "alpha_deg" in solver_config.parameters:
            alpha_deg = float(solver_config.parameters["alpha_deg"])
        alpha_rad = math.radians(alpha_deg)

        # u_inf: from solver parameters or default 100.0
        u_inf = 100.0
        if solver_config.parameters and "u_inf" in solver_config.parameters:
            u_inf = float(solver_config.parameters["u_inf"])

        context["alpha_deg"] = alpha_deg
        context["alpha_rad"] = alpha_rad
        context["u_inf"] = u_inf
        context["u_cos"] = u_inf * math.cos(alpha_rad)
        context["v_sin"] = u_inf * math.sin(alpha_rad)

        # P3.1-SA: lift/drag direction vectors for force projection.
        # liftDir is perpendicular to freestream (rotated 90 deg CCW):
        #   lift_dir = (-sin(alpha), cos(alpha), 0)
        # dragDir is parallel to freestream:
        #   drag_dir = (cos(alpha), sin(alpha), 0)
        context["lift_dir_x"] = -math.sin(alpha_rad)
        context["lift_dir_y"] = math.cos(alpha_rad)
        context["drag_dir_x"] = math.cos(alpha_rad)
        context["drag_dir_y"] = math.sin(alpha_rad)

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
        """Generate OpenFOAM case directory structure.

        P3-hotfix: routes to NACA templates when case.id starts with
        'naca0012', otherwise uses the original LDC (lid-driven cavity)
        template set. The shared directory structure is created first.

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
        """
        case_dir_out = run_dir / "case"
        context = self._build_context(case, case_dir, run_dir)

        # Create shared directory structure
        (case_dir_out / "system").mkdir(parents=True, exist_ok=True)
        (case_dir_out / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
        (case_dir_out / "0").mkdir(parents=True, exist_ok=True)

        if self._is_naca_case(case):
            self._prepare_naca(case, case_dir_out, context, case_dir)
        else:
            self._prepare_ldc(case_dir_out, context)

        logger.debug("OpenFOAM case structure prepared at %s", case_dir_out)

    def _prepare_ldc(self, case_dir_out: Path, context: dict[str, Any]) -> None:
        """Original LDC (lid-driven cavity) prepare logic — unchanged (iron rule #1).

        Renders the 5 LDC config templates and writes placeholder 0/U, 0/p
        fields with zero velocity.

        Args:
            case_dir_out: Output case directory (run_dir/case).
            context: Jinja2 template context.
        """
        # Render and write 5 LDC config files
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

        # Write placeholder initial fields 0/U and 0/p (original LDC values)
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

    def _prepare_naca(
        self,
        case: CaseSpec,
        case_dir_out: Path,
        context: dict[str, Any],
        case_dir: Path,
    ) -> None:
        """Prepare NACA0012 case: render all NACA templates + copy STL.

        Renders to case_dir_out/:
          system/: controlDict, fvSchemes, fvSolution, blockMeshDict,
                   snappyHexMeshDict
          constant/: transportProperties, turbulenceProperties,
                     triSurface/naca0012.stl
          0/: U (with alpha-derived freestream), p, nuTilda (SA field)

        Args:
            case: CaseSpec configuration.
            case_dir_out: Output case directory (run_dir/case).
            context: Jinja2 template context (must include u_cos, v_sin,
                alpha_deg, u_inf, nu, reynolds).
            case_dir: Directory containing case.yaml (for STL lookup).
        """
        system = case_dir_out / "system"
        constant = case_dir_out / "constant"
        zero_dir = case_dir_out / "0"

        # system/ files (5): controlDict, fvSchemes, fvSolution,
        # blockMeshDict, snappyHexMeshDict
        (system / "controlDict").write_text(
            self._render_template("controlDict.naca.j2", context), encoding="utf-8"
        )
        (system / "fvSchemes").write_text(
            self._render_template("fvSchemes.naca.j2", context), encoding="utf-8"
        )
        (system / "fvSolution").write_text(
            self._render_template("fvSolution.naca.j2", context), encoding="utf-8"
        )
        (system / "blockMeshDict").write_text(
            self._render_template("blockMeshDict.naca.j2", context), encoding="utf-8"
        )
        (system / "snappyHexMeshDict").write_text(
            self._render_template("snappyHexMeshDict.j2", context), encoding="utf-8"
        )

        # constant/ files (2): transportProperties, turbulenceProperties
        (constant / "transportProperties").write_text(
            self._render_template("transportProperties.naca.j2", context),
            encoding="utf-8",
        )
        (constant / "turbulenceProperties").write_text(
            self._render_template("turbulenceProperties.naca.j2", context),
            encoding="utf-8",
        )

        # Copy STL geometry (H4): naca0012.stl → constant/triSurface/
        trisurface = constant / "triSurface"
        trisurface.mkdir(parents=True, exist_ok=True)
        stl_src = case_dir / "geometry" / "naca0012.stl"
        if not stl_src.exists():
            # Fallback: parent naca0012 dir (for a5/a10/a15 cases that
            # reference geometry via ../naca0012/geometry/...)
            stl_src = case_dir.parent / "naca0012" / "geometry" / "naca0012.stl"
        if stl_src.exists():
            shutil.copy2(stl_src, trisurface / "naca0012.stl")
        else:
            logger.warning(
                "naca0012.stl not found at %s or fallback location", stl_src
            )

        # 0/ initial fields: U (alpha-derived), p, nuTilda (SA)
        (zero_dir / "U").write_text(
            self._render_template("U.naca.j2", context), encoding="utf-8"
        )
        nu_val = context.get("nu", 1.6667e-5)
        # p: incompressible, uniform 0, with NACA boundary types.
        # farfield uses freestreamPressure (not fixedValue) — fixedValue 0 on
        # the entire farfield causes massive continuity errors and pressure
        # divergence in SIMPLE for external aero. freestreamPressure allows
        # the pressure to adjust on outflow faces while maintaining the
        # freestream reference on inflow faces.
        (zero_dir / "p").write_text(
            "FoamFile\n"
            "{\n"
            "    version     2.0;\n"
            "    format      ascii;\n"
            "    class       volScalarField;\n"
            "    object      p;\n"
            "}\n"
            "dimensions      [0 2 -2 0 0 0 0];\n"
            "internalField   uniform 0;\n"
            "boundaryField\n"
            "{\n"
            "    airfoil\n"
            "    {\n"
            "        type            zeroGradient;\n"
            "    }\n"
            "    farfield\n"
            "    {\n"
            "        type            freestreamPressure;\n"
            "        freestreamValue uniform 0;\n"
            "    }\n"
            "    frontAndBack\n"
            "    {\n"
            "        type            empty;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        # nuTilda: SpalartAllmaras working variable.
        # Freestream value ~ 3 × nu (per NASA SA recommendations for fully
        # turbulent initial state). chi=3 gives nut ≈ 0.2*nu, standard for
        # external aero SA initialization.
        # Wall BC: zeroGradient for high-y+ wall function approach.
        nuTilda_freestream = 3.0 * nu_val
        # nut freestream from SA: nut = nuTilda * chi^3/(chi^3+Cv1^3)
        # where chi = nuTilda/nu = 3, Cv1 = 7.1.
        chi = nuTilda_freestream / nu_val
        cv1 = 7.1
        nut_freestream = nuTilda_freestream * (chi**3) / (chi**3 + cv1**3)
        (zero_dir / "nuTilda").write_text(
            "FoamFile\n"
            "{\n"
            "    version     2.0;\n"
            "    format      ascii;\n"
            "    class       volScalarField;\n"
            "    object      nuTilda;\n"
            "}\n"
            f"dimensions      [0 2 -1 0 0 0 0];\n"
            f"internalField   uniform {nuTilda_freestream};\n"
            "boundaryField\n"
            "{\n"
            "    airfoil\n"
            "    {\n"
            "        type            zeroGradient;\n"
            "    }\n"
            "    farfield\n"
            "    {\n"
            "        type            freestream;\n"
            f"        freestreamValue uniform {nuTilda_freestream};\n"
            "    }\n"
            "    frontAndBack\n"
            "    {\n"
            "        type            empty;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

        # nut: turbulent viscosity (SA model computes it from nuTilda).
        # Wall uses nutUSpaldingWallFunction — the standard high-y+ SA wall
        # function. The Spalding function bridges wall-to-log-law region and
        # is self-consistent with nuTilda zeroGradient at the wall. Combined
        # with snappyHexMesh addLayers providing y+ ~ 30-200 (wall-function
        # range), this is the canonical high-y+ SA setup per Spalart &
        # Allmaras (1992) and OpenFOAM simpleFoam/airFoil2D tutorial.
        # NOTE: forcing nut = calculated 0 (low-Re formulation) on a high-y+
        # mesh over-stimulates the SA production term and drives nuTilda
        # toward divergence — earlier attempts with calculated 0 + n_iter=200
        # failed to converge for this reason.
        (zero_dir / "nut").write_text(
            "FoamFile\n"
            "{\n"
            "    version     2.0;\n"
            "    format      ascii;\n"
            "    class       volScalarField;\n"
            "    object      nut;\n"
            "}\n"
            "dimensions      [0 2 -1 0 0 0 0];\n"
            f"internalField   uniform {nut_freestream};\n"
            "boundaryField\n"
            "{\n"
            "    airfoil\n"
            "    {\n"
            "        type            nutUSpaldingWallFunction;\n"
            "        value           uniform 0;\n"
            "    }\n"
            "    farfield\n"
            "    {\n"
            "        type            calculated;\n"
            f"        value           uniform {nut_freestream};\n"
            "    }\n"
            "    frontAndBack\n"
            "    {\n"
            "        type            empty;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

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
        if solver_config.steps is None:
            raise ValueError(
                "OpenFOAM adapter requires SolverConfig.steps for real execution. "
                f"Case '{case.id}' solver '{solver_config.name}' has steps=None."
            )

        # P2-b: use injected backend (default LocalExecutionBackend, may be DockerBackend)
        backend = self._backend
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

        P3-hotfix: NACA cases extract Cl/Cd from forces.dat via
        extract_cl_cd_openfoam(). LDC cases retain the original probes
        extraction path.

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
            # === P3-hotfix: extract Cl/Cd from forces.dat ===
            from cfdb.post.qoi_extractor import extract_cl_cd_openfoam

            forces_candidates = sorted(
                case_dir_out.glob("postProcessing/forces/*/forces.dat"),
                key=lambda p: _safe_float_dir(p.parent.name),
            )
            # Fallback: force.dat (Foundation spelling — PRD R1)
            if not forces_candidates:
                forces_candidates = sorted(
                    case_dir_out.glob("postProcessing/forces/*/force.dat"),
                    key=lambda p: _safe_float_dir(p.parent.name),
                )

            if forces_candidates:
                forces_dat = forces_candidates[-1]  # latest time directory
                # Derive u_inf from solver parameters
                solver_config = self._find_solver_config(case)
                u_inf = 100.0
                if solver_config.parameters and "u_inf" in solver_config.parameters:
                    u_inf = float(solver_config.parameters["u_inf"])
                rho = 1.225  # sea-level air density (PRD Q2: keep default)

                result = extract_cl_cd_openfoam(
                    forces_dat, rho=rho, u_inf=u_inf, a_ref=1.0
                )
                if result is not None:
                    cl, cd = result
                    qoi_values["cl"] = cl
                    qoi_values["cd"] = cd
                else:
                    logger.warning(
                        "forces.dat found but Cl/Cd extraction returned "
                        "None for case %s",
                        case.id,
                    )
            else:
                logger.warning(
                    "no forces.dat found under postProcessing/forces/ "
                    "for case %s",
                    case.id,
                )
        else:
            # Original LDC probe extraction
            probes_dir = case_dir_out / "postProcessing" / "probes"
            if probes_dir.exists():
                from cfdb.post.qoi_extractor import extract_openfoam_centerline_umax

                umax = extract_openfoam_centerline_umax(probes_dir, "U")
                if umax is not None:
                    qoi_values["centerline_umax"] = umax

        return ArtifactManifest(
            files=files,
            qoi_values=qoi_values if qoi_values else None,
            curves=None,
        )


def _safe_float_dir(name: str) -> float:
    """Safely convert a directory name to float for sorting.

    OpenFOAM time directories may be named '0', '500', '1000', or
    '0.000'. This returns the float value for sorting, or 0.0 if the
    name is not numeric.

    Args:
        name: Directory name (e.g. '500', '1000').

    Returns:
        Float value of the name, or 0.0 if not parseable.
    """
    try:
        return float(name)
    except ValueError:
        return 0.0


# Ensure the class satisfies the SolverAdapter protocol
_SolverAdapter: type[SolverAdapter] = OpenFOAMAdapter  # type: ignore[assignment]
