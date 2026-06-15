"""SolverAdapter Protocol and auxiliary types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from cfdb.schema import CaseSpec


@dataclass
class ResourceSpec:
    """Resource request spec (passed to ExecutionBackend)."""

    cpu_cores: int = 1
    memory_mb: int | None = None
    wall_time_sec: int | None = None


@dataclass
class RunResult:
    """Return value of SolverAdapter.run()."""

    exit_code: int
    stdout: str
    stderr: str
    wall_time_sec: float
    timed_out: bool = False
    skipped_commands: list[str] | None = None
    """In dry_run mode: list of rendered command strings that were skipped.
    None in normal mode. Runner reads this to populate manifest."""


@dataclass
class ArtifactManifest:
    """Return value of SolverAdapter.collect_outputs()."""

    files: dict[str, Path] = field(default_factory=dict)
    """key = file type label, value = file path (relative to run_dir)."""

    qoi_values: dict[str, float] | None = None
    """QoI values parsed from qoi.json (if present)."""

    curves: dict[str, list[tuple[float, float]]] | None = None
    """Curve data (None in P0)."""


@runtime_checkable
class SolverAdapter(Protocol):
    """Solver adapter interface.

    Each solver (generic / openfoam / su2 / surrogate) implements this Protocol.
    The Runner calls adapters through this interface, agnostic to solver details.

    Note: Per architecture decision §13.4, prepare() takes a case_dir parameter
    in addition to run_dir. The adapter renders {{ case_dir }} in command templates
    using this value.
    """

    name: str
    """Adapter unique identifier, matches CaseSpec.solvers[].name."""

    def prepare(self, case: CaseSpec, case_dir: Path, run_dir: Path) -> None:
        """Prepare the run environment.

        - Create run_dir directory structure
        - Render command template, generate run_dir/run.sh (or equivalent)
        - Copy/link necessary input files to run_dir

        Args:
            case: CaseSpec complete configuration.
            case_dir: Directory containing case.yaml (source of input files).
            run_dir: Isolated directory for this run.

        Raises:
            FileNotFoundError: Reference file missing.
            jinja2.TemplateError: Template rendering failure.
        """
        ...

    def run(
        self,
        case: CaseSpec,
        case_dir: Path,
        run_dir: Path,
        resources: ResourceSpec | None,
    ) -> RunResult:
        """Execute the solver.

        - Call ExecutionBackend to execute the command in run_dir
        - Capture stdout/stderr/exit_code/wall_time

        Args:
            case: CaseSpec complete configuration.
            case_dir: Directory containing case.yaml.
            run_dir: Run directory.
            resources: Resource limits (None uses defaults).

        Returns:
            RunResult: Execution result.
        """
        ...

    def collect_outputs(self, case: CaseSpec, run_dir: Path) -> ArtifactManifest:
        """Collect run artifacts.

        - Read run_dir/qoi.json (if present)
        - List all files in run_dir as artifacts
        - P1+: add field/curve file parsing

        Args:
            case: CaseSpec complete configuration.
            run_dir: Run directory.

        Returns:
            ArtifactManifest: Artifact manifest.
        """
        ...
