"""SU2Adapter — generates SU2 CFG configuration with dry_run support."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Template

from cfdb.adapters.base import ArtifactManifest, ResourceSpec, RunResult, SolverAdapter
from cfdb.schema import CaseSpec, SolverConfig

logger = logging.getLogger(__name__)


class SU2Adapter:
    """SU2 adapter with dry_run support.

    In dry_run mode: generates SU2 .cfg config file and mesh placeholder,
    but does NOT execute SU2_CFD. Real execution (P1-b) will call SU2_CFD subprocess.
    """

    name: str = "su2"

    def __init__(self, dry_run: bool = False) -> None:
        """Initialize SU2 adapter.

        Args:
            dry_run: If True, run() returns synthetic result without executing subprocess.
        """
        self._dry_run = dry_run
        self._template_dir = Path(__file__).parent / "templates" / "su2"

    def _find_solver_config(self, case: CaseSpec) -> SolverConfig:
        """Find the 'su2' solver config in the case.

        Args:
            case: CaseSpec with solver configs.

        Returns:
            The SolverConfig for 'su2'.

        Raises:
            ValueError: If no 'su2' solver config found.
        """
        for solver in case.solvers:
            if solver.name == "su2":
                return solver
        raise ValueError(f"no 'su2' solver config found in case '{case.id}'")

    def _build_context(
        self, case: CaseSpec, case_dir: Path, run_dir: Path
    ) -> dict[str, Any]:
        """Build Jinja2 template context.

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
            "solver": "su2",
            "mesh_level": mesh_level,
            "case_dir": case_dir.resolve().as_posix(),
            "run_dir": run_dir.resolve().as_posix(),
            "mach": case.conditions.mach or 0.3,
            "reynolds": case.conditions.reynolds or 1e6,
            "aoa": case.conditions.alpha_deg or 0.0,
        }
        if solver_config.parameters:
            context.update(solver_config.parameters)
        return context

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        """Load and render a Jinja2 template from the su2 template dir.

        Args:
            template_name: Template filename (e.g. 'base.cfg.j2').
            context: Jinja2 template context.

        Returns:
            Rendered template string.
        """
        template_path = self._template_dir / template_name
        template = Template(template_path.read_text(encoding="utf-8"))
        return template.render(**context)

    def prepare(self, case: CaseSpec, case_dir: Path, run_dir: Path) -> None:
        """Generate SU2 case directory with CFG file and mesh placeholder.

        Creates run_dir/case/ with:
        - <case_id>.cfg (Jinja2-rendered SU2 configuration)
        - mesh.su2 (placeholder mesh file)

        Args:
            case: CaseSpec configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
        """
        case_dir_out = run_dir / "case"
        context = self._build_context(case, case_dir, run_dir)

        case_dir_out.mkdir(parents=True, exist_ok=True)

        # Render CFG file → run_dir/case/<case_id>.cfg
        cfg_content = self._render_template("base.cfg.j2", context)
        cfg_path = case_dir_out / f"{case.id}.cfg"
        cfg_path.write_text(cfg_content, encoding="utf-8")

        # Write placeholder mesh file
        mesh_path = case_dir_out / "mesh.su2"
        mesh_path.write_text(
            "% SU2 placeholder mesh file (dry_run)\n"
            "% N_ELEM= 0\n% N_POINTS= 0\n",
            encoding="utf-8",
        )

        logger.debug("SU2 case structure prepared at %s", case_dir_out)

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
            logger.info("[dry-run] skipping %d SU2 command(s)", len(skipped))
            return RunResult(
                exit_code=0,
                stdout="[dry-run] commands not executed",
                stderr="",
                wall_time_sec=0.0,
                timed_out=False,
                skipped_commands=skipped,
            )

        raise NotImplementedError(
            "SU2Adapter real execution is not implemented yet (P1-b scope). "
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
_SolverAdapter: type[SolverAdapter] = SU2Adapter  # type: ignore[assignment]
