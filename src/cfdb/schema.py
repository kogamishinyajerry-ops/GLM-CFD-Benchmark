"""Core Pydantic v2 data models for CFD-Benchmark.

All models use ConfigDict(extra='forbid') to reject unknown fields.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PhysicsSpec(BaseModel):
    """Physics model description."""

    model_config = ConfigDict(extra="forbid")

    flow: Literal[
        "incompressible",
        "compressible",
        "low_mach",
        "potential",
        "euler",
        "rans",
        "les",
        "dns",
        "surrogate",
    ]
    """Flow type enumeration."""

    turbulence: Literal["none", "rans_sa", "rans_kwsst", "les_smag", "dns"] | None = None
    """Turbulence model (None if no turbulence)."""

    dimensionality: Literal["2d", "3d", "axisymmetric"] = "2d"
    """Dimensionality."""

    steady: bool = True
    """Whether the computation is steady-state (False = transient)."""


class ConditionsSpec(BaseModel):
    """Flow conditions parameters."""

    model_config = ConfigDict(extra="forbid")

    reynolds: float | None = Field(None, gt=0)
    """Reynolds number (must be > 0 if provided)."""

    mach: float | None = Field(None, ge=0)
    """Mach number (must be >= 0 if provided)."""

    alpha_deg: float | None = Field(None, ge=-90, le=90)
    """Angle of attack in degrees, range [-90, 90]."""


class GeometrySpec(BaseModel):
    """Geometry information."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["internal", "external", "periodic", "custom"]
    """Geometry type."""

    source: Path | None = None
    """Geometry file path (relative to case.yaml directory)."""


class MeshSpec(BaseModel):
    """Mesh information."""

    model_config = ConfigDict(extra="forbid")

    family: str | None = None
    """Mesh family name (e.g. 'structured_hex')."""

    levels: list[str] = Field(default_factory=lambda: ["single"])
    """Mesh refinement levels. Default ['single'] for single-level mesh."""

    target_y_plus: float | None = Field(None, gt=0)
    """Target y+ value (first cell height reference)."""


class CommandStep(BaseModel):
    """Single step command in a multi-step sequence.

    Used for solvers that require multiple commands:
    blockMesh → decomposePar → simpleFoam → reconstructPar
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    """Step name (e.g. 'block_mesh', 'solve', 'reconstruct'). Used in logs and manifest."""

    command: str
    """Jinja2 command template. Same variables as SolverConfig.command:
    {{ case_id }}, {{ solver }}, {{ mesh_level }}, {{ case_dir }}, {{ run_dir }}."""

    timeout_sec: int | None = Field(None, gt=0)
    """Timeout for this step in seconds. None = no timeout."""

    critical: bool = True
    """Whether this step is critical.
    True = failure of this step fails the entire run.
    False = failure is logged as warning, subsequent steps continue.
    (In P1-a dry_run mode this field has no behavioral effect — all steps skipped.
    Takes effect in P1-b real execution.)"""


class SolverConfig(BaseModel):
    """Configuration for a single solver."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """Solver name (e.g. 'generic', 'openfoam', 'su2')."""

    command: str
    """Execution command template (Jinja2 syntax).
    Available variables: {{ case_id }}, {{ solver }}, {{ mesh_level }}, {{ case_dir }}.
    Example: 'bash {{ case_dir }}/run.sh'
    """

    timeout_sec: int | None = Field(None, gt=0)
    """Timeout in seconds. None means no timeout."""

    steps: list[CommandStep] | None = None
    """Multi-step command sequence (P1-a).

    - If provided, adapter executes steps in order (real mode) or records them
      to skipped_commands (dry_run mode).
    - If None, adapter falls back to single-command mode (P0 behavior unchanged).
    - OpenFOAM/SU2 adapters use steps; generic_command adapter continues using command.
    """

    parameters: dict[str, Any] | None = None
    """Arbitrary solver-specific parameters injected into Jinja2 template context.
    Used by SU2 adapter for CFG template variables (mach, reynolds, aoa, etc.)
    and can be reused by other adapters."""


class OutputSpec(BaseModel):
    """Expected outputs specification."""

    model_config = ConfigDict(extra="forbid")

    fields: list[str] = Field(default_factory=list)
    """Expected field output names (e.g. ['U', 'p', 'nut'])."""

    curves: list[str] = Field(default_factory=list)
    """Expected curve output names (e.g. ['residual_U', 'cl_alpha'])."""

    qoi: list[str] = Field(default_factory=list)
    """Quantities of Interest list. MetricsEngine checks these exist in output."""


class ReferenceSpec(BaseModel):
    """Reference data specification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["experimental", "dns", "analytical", "manufactured", "previous_run"]
    """Reference data source type."""

    files: dict[str, Path] = Field(default_factory=dict)
    """Reference file mapping. key = data type, value = file path relative to case.yaml.
    Example: {'qoi': Path('reference/qoi.json')}"""

    qoi_values: dict[str, float] | None = None
    """Inline reference QoI values (alternative to files['qoi'])."""


class MetricSpec(BaseModel):
    """Metric tolerance configuration."""

    model_config = ConfigDict(extra="forbid")

    qoi_relative_tolerance: dict[str, float] = Field(default_factory=dict)
    """Per-QoI relative error tolerance.
    key = QoI name, value = max allowed relative error.
    Example: {'drag_coeff': 0.05} means 5% tolerance."""

    curve_l2_tolerance: dict[str, float] | None = None
    """Per-curve L2 norm tolerance (optional)."""

    # === P4-G: absolute tolerance for zero-reference QoIs ===
    qoi_absolute_tolerance: dict[str, float] = Field(default_factory=dict)
    """Per-QoI absolute error tolerance for zero-reference QoIs.

    When a reference value is exactly 0, relative error is undefined.
    If the QoI is listed here, the gate checks |computed - reference|
    against this absolute tolerance instead of silently skipping the QoI.
    Example: {'cl': 0.01} for a symmetric airfoil at alpha=0."""


class BudgetSpec(BaseModel):
    """Resource budget specification."""

    model_config = ConfigDict(extra="forbid")

    max_runtime_sec: int | None = Field(None, gt=0)
    """Maximum allowed wall time in seconds. Exceeding triggers a warning."""

    max_cells: int | None = Field(None, gt=0)
    """Maximum allowed mesh cell count (not enforced in P0)."""


class CaseSpec(BaseModel):
    """Complete specification for a single case.

    Corresponds to cases/<category>/<id>/case.yaml.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    """Case unique identifier (must match directory name)."""

    name: str
    """Human-readable name."""

    category: Literal["smoke", "verification", "validation", "performance", "surrogate"]
    """Case category, determines the subdirectory under cases/."""

    description: str | None = None
    """Detailed description (optional)."""

    physics: PhysicsSpec
    """Physics model description."""

    conditions: ConditionsSpec
    """Flow condition parameters."""

    geometry: GeometrySpec | None = None
    """Geometry info (optional for smoke cases)."""

    mesh: MeshSpec | None = None
    """Mesh info (optional for smoke cases)."""

    solvers: list[SolverConfig]
    """Supported solver configurations (at least 1)."""

    outputs: OutputSpec
    """Expected output fields/curves/qoi lists."""

    reference: ReferenceSpec | None = None
    """Reference data (required for validation/performance, optional for smoke)."""

    metrics: MetricSpec
    """Metric tolerance configuration."""

    budget: BudgetSpec = Field(default_factory=BudgetSpec)  # type: ignore[call-arg]
    """Resource budget (optional, has defaults)."""

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate that id matches ^[a-z][a-z0-9_]*$."""
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(f"case id '{v}' must match ^[a-z][a-z0-9_]*$")
        return v

    @field_validator("solvers")
    @classmethod
    def validate_solvers(cls, v: list[SolverConfig]) -> list[SolverConfig]:
        """Validate that at least one solver is configured."""
        if len(v) == 0:
            raise ValueError("at least one solver config required")
        return v


class TimingSpec(BaseModel):
    """Run timing information."""

    model_config = ConfigDict(extra="forbid")

    wall_time_sec: float = Field(ge=0)
    """Actual wall time in seconds."""

    start_time: datetime
    """Run start time (UTC ISO 8601)."""

    end_time: datetime
    """Run end time (UTC ISO 8601)."""


class RunManifest(BaseModel):
    """Metadata for a single run — the reproducibility core."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    """Run unique identifier. Format: YYYYMMDDTHHMMSSZ_<case_id>_<solver>_<hash8>"""

    case_id: str
    """Associated CaseSpec.id."""

    solver: str
    """Solver name used."""

    backend: Literal["local", "docker", "slurm"] = "local"
    """Execution backend."""

    status: Literal["success", "failed", "timeout", "dry_run"]
    """Run status. P1-a adds 'dry_run' enum value."""

    timing: TimingSpec
    """Run timing information."""

    host: str | None = None
    """Execution hostname."""

    artifacts: dict[str, Path] = Field(default_factory=dict)
    """Artifact file mapping. key = type, value = path relative to run_dir."""

    git_commit: str | None = None
    """Git commit hash for reproducibility."""

    container_digest: str | None = None
    """Container image digest (Docker backend only)."""

    error: str | None = None
    """Error message/traceback when status != success."""

    cli_args: dict[str, str] | None = None
    """Original CLI arguments for reproducibility."""

    dry_run_skipped_commands: list[str] | None = None
    """Commands that would have been executed but were skipped in dry_run mode.
    Each element is the fully-rendered command string.
    None when not in dry_run mode."""

    solver_version: str | None = None
    """Detected solver version string (e.g. 'OpenFOAM v2406', 'SU2 8.0.0').
    None for dry_run / mock cases."""

    final_residuals: dict[str, float] | None = None
    """Final residual values extracted from solver log.
    Keys are field names (e.g. 'Ux', 'Uy', 'p' for OpenFOAM;
    'RMS_DENSITY' for SU2). Values are the final residual magnitudes.
    None for dry_run / mock cases."""

    # === P2-a new fields ===
    cell_count: int | None = None
    """Total mesh cell count (from blockMesh log or SU2 mesh stats).
    None for dry_run/mock cases."""

    step_details: list[dict[str, Any]] | None = None
    """Per-step status records. Each dict: {name, exit_code, wall_time_sec, status}.
    None for dry_run/mock/generic_command adapter (no steps)."""

    residuals_history: dict[str, list[float]] | None = None
    """Full residual history (not just final values). Used for SVG rendering.
    Keys are field names (e.g. 'Ux', 'Uy', 'p'), values are the full residual
    value list over iterations. None for dry_run/mock/generic_command."""

    # === P2-b new fields ===
    backend_options: dict[str, Any] | None = None
    """Backend-specific options snapshot for reproducibility.
    For Docker: {'image': '...', 'digest': 'sha256:...', 'pull_policy': '...'}.
    For local backend: None (default)."""


class MetricsResult(BaseModel):
    """Metric computation results."""

    model_config = ConfigDict(extra="forbid")

    qoi_relative_errors: dict[str, float] = Field(default_factory=dict)
    """Per-QoI relative error. Missing QoIs are not included (noted in notes)."""

    qoi_pass: bool = False
    """Whether all QoIs passed tolerance checks."""

    overall_status: str = "unknown"
    """Overall status: 'pass' / 'fail' / 'incomplete' / 'unknown'.
    - pass: run success + all qoi pass
    - fail: run success but qoi failed, or run failed
    - incomplete: run success but missing required QoI data"""

    notes: list[str] = Field(default_factory=list)
    """Additional notes (budget warnings, missing QoI, etc.)."""

    # === P3-hotfix: computed QoI values for polar rendering ===
    qoi_computed_values: dict[str, float] | None = None
    """Computed QoI values (real Cl, Cd, etc.) from the run.

    Unlike qoi_relative_errors (which stores |computed - ref| / |ref|),
    this stores the actual computed values for polar curve plotting.

    Populated by MetricsEngine.compute() from artifacts.qoi_values.
    None for dry_run / failed runs / old data (backward compatible)."""

    # === P4-G: honesty-hardening fields (defaults keep old data readable) ===
    ungated_qoi: list[str] = Field(default_factory=list)
    """QoIs declared in outputs.qoi whose error was computed but for which
    no tolerance is configured. They do NOT affect pass/fail (backward
    compatible), but reporting layers must disclose that these numbers
    are not constrained by any gate."""

    budget_exceeded: bool = False
    """True when check_budget() reported at least one budget violation.

    Budget overruns keep warning semantics (they never flip pass/fail),
    but showcase/trust efficiency dimensions consume this flag."""
