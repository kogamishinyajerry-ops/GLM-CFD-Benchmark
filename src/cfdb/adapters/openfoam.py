"""OpenFOAMAdapter — generates OpenFOAM case structure with dry_run support."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Template

from cfdb.adapters.base import ArtifactManifest, ResourceSpec, RunResult, SolverAdapter
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

        # P1-b: real execution
        raise NotImplementedError(
            "OpenFOAMAdapter real execution is not implemented yet (P1-b scope). "
            "Use --dry-run for P1-a."
        )

    def collect_outputs(self, case: CaseSpec, run_dir: Path) -> ArtifactManifest:
        """Scan run_dir/case/ for all generated files.

        Args:
            case: CaseSpec configuration.
            run_dir: Run directory.

        Returns:
            ArtifactManifest with file listing.
        """
        case_dir_out = run_dir / "case"
        files: dict[str, Path] = {}
        if case_dir_out.exists():
            for path in sorted(case_dir_out.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(run_dir)
                    files[rel.as_posix()] = rel
        return ArtifactManifest(files=files, qoi_values=None, curves=None)


# Ensure the class satisfies the SolverAdapter protocol
_SolverAdapter: type[SolverAdapter] = OpenFOAMAdapter  # type: ignore[assignment]
