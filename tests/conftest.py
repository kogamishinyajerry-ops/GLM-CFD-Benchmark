"""Shared pytest fixtures for all test modules."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cfdb.schema import CaseSpec

# P2-b: Allow tests to import case-specific modules (e.g. NACA0012 geometry
# generator lives in cases/validation/naca0012/gen_geometry.py, which is a
# case-owned helper script, not a cfdb package module).
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Generic fixtures (unchanged)
# ============================================================================


@pytest.fixture
def sample_case_spec_data() -> dict:
    """Minimal valid CaseSpec dict (mock_success style)."""
    return {
        "id": "test_case",
        "name": "Test Case",
        "category": "smoke",
        "description": "A test case for unit testing.",
        "physics": {
            "flow": "incompressible",
            "dimensionality": "2d",
            "steady": True,
        },
        "conditions": {
            "reynolds": 100.0,
            "mach": None,
            "alpha_deg": None,
        },
        "solvers": [
            {
                "name": "generic",
                "command": "bash {{ case_dir }}/run.sh",
            }
        ],
        "outputs": {
            "fields": [],
            "curves": [],
            "qoi": ["centerline_umax"],
        },
        "reference": {
            "type": "analytical",
            "files": {"qoi": "reference/qoi.json"},
            "qoi_values": None,
        },
        "metrics": {
            "qoi_relative_tolerance": {"centerline_umax": 0.05},
        },
        "budget": {
            "max_runtime_sec": 30,
        },
    }


@pytest.fixture
def sample_case_spec(sample_case_spec_data: dict) -> CaseSpec:
    """Valid CaseSpec object."""
    return CaseSpec.model_validate(sample_case_spec_data)


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> Path:
    """Isolated temporary run directory."""
    d = tmp_path / "run_test"
    d.mkdir()
    return d


@pytest.fixture
def tmp_cases_root(tmp_path: Path, sample_case_spec_data: dict) -> Path:
    """Temporary cases/ directory with a valid mock case."""
    cases_root = tmp_path / "cases"
    case_dir = cases_root / "smoke" / "test_case"
    case_dir.mkdir(parents=True)

    import yaml

    case_yaml = case_dir / "case.yaml"
    case_yaml.write_text(yaml.dump(sample_case_spec_data), encoding="utf-8")

    ref_dir = case_dir / "reference"
    ref_dir.mkdir()
    (ref_dir / "qoi.json").write_text(
        json.dumps({"centerline_umax": 0.371}), encoding="utf-8"
    )

    run_sh = case_dir / "run.sh"
    run_sh.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncat > qoi.json <<'EOF'\n"
        '{"centerline_umax": 0.373}\nEOF\nexit 0\n',
        encoding="utf-8",
    )

    return cases_root


@pytest.fixture
def tmp_runs_root(tmp_path: Path) -> Path:
    """Temporary runs/ directory."""
    d = tmp_path / "runs"
    d.mkdir()
    return d


# ============================================================================
# Star-CCM+ shared fixtures (P4.5b – test refactor)
# ============================================================================


def _build_naca_casespec(
    solver_name: str,
    command: str,
    steps: list | None = None,
    parameters: dict[str, Any] | None = None,
    alpha_deg: float = 5.0,
    reynolds: float = 6e6,
    mach: float = 0.3,
    u_inf: float = 100.0,
    n_iter: int = 500,
    prism_layers: int | None = None,
    prism_thickness: float | None = None,
) -> CaseSpec:
    """Build a NACA0012 CaseSpec for any solver.

    Shared factory used by starccm_naca_case, openfoam NACA fixtures, etc.
    """
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
        SolverConfig,
    )

    merged_params: dict[str, Any] = {
        "alpha_deg": alpha_deg,
        "u_inf": u_inf,
        "n_iter": n_iter,
    }
    if prism_layers is not None:
        merged_params["prism_layers"] = prism_layers
    if prism_thickness is not None:
        merged_params["prism_thickness"] = prism_thickness
    if parameters:
        merged_params.update(parameters)

    solver_config = SolverConfig(
        name=solver_name,
        command=command,
        steps=steps,
        parameters=merged_params,
    )

    return CaseSpec(
        id="naca0012_a5",
        name="NACA0012 Alpha=5",
        category="validation",
        physics=PhysicsSpec(
            flow="compressible", turbulence="rans_sa",
            dimensionality="2d", steady=True,
        ),
        conditions=ConditionsSpec(
            reynolds=reynolds, mach=mach, alpha_deg=alpha_deg,
        ),
        solvers=[solver_config],
        outputs=OutputSpec(fields=["velocity", "p"], qoi=["cl", "cd"]),
        metrics=MetricSpec(
            qoi_relative_tolerance={"cl": 0.05, "cd": 0.05},
        ),
    )


# -- Star-CCM+ adapter fixtures --


@pytest.fixture(scope="module")
def starccm_case() -> CaseSpec:
    """Flat-plate SolverConfig for Star-CCM+ (module-scoped, immutable)."""
    from cfdb.schema import (
        CommandStep,
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
        SolverConfig,
    )

    solver = SolverConfig(
        name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=[
            CommandStep(
                name="solve",
                command="starccm+ -batch {{ case_dir }}/run.java -nproc {{ cpu_cores }}",
            ),
        ],
        parameters={
            "mach": 0.3,
            "reynolds": 1_000_000,
            "alpha_deg": 0.0,
            "u_inf": 100.0,
            "n_iter": 500,
        },
    )

    return CaseSpec(
        id="flat_plate_starccm",
        name="Flat Plate StarCCM",
        category="smoke",
        physics=PhysicsSpec(
            flow="compressible", turbulence="rans_sa",
            dimensionality="2d", steady=True,
        ),
        conditions=ConditionsSpec(reynolds=1_000_000, alpha_deg=0.0),
        solvers=[solver],
        outputs=OutputSpec(fields=[], qoi=[]),
        metrics=MetricSpec(),
    )


@pytest.fixture(scope="module")
def starccm_case_no_steps() -> CaseSpec:
    """Flat-plate SolverConfig without steps (module-scoped)."""
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
        SolverConfig,
    )

    solver = SolverConfig(
        name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=None,
        parameters={"alpha_deg": 0.0, "u_inf": 100.0, "n_iter": 500},
    )

    return CaseSpec(
        id="flat_plate_no_steps",
        name="Flat Plate No Steps",
        category="smoke",
        physics=PhysicsSpec(
            flow="compressible", turbulence="rans_sa",
            dimensionality="2d", steady=True,
        ),
        conditions=ConditionsSpec(reynolds=1_000_000, alpha_deg=0.0),
        solvers=[solver],
        outputs=OutputSpec(fields=[], qoi=[]),
        metrics=MetricSpec(),
    )


@pytest.fixture(scope="module")
def starccm_naca_case() -> CaseSpec:
    """NACA0012 CaseSpec for Star-CCM+ (module-scoped, canonical)."""
    from cfdb.schema import CommandStep

    return _build_naca_casespec(
        solver_name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=[
            CommandStep(
                name="solve",
                command="starccm+ -batch {{ case_dir }}/run.java -nproc {{ cpu_cores }}",
            ),
        ],
        alpha_deg=5.0,
        reynolds=6e6,
    )


@pytest.fixture(scope="module")
def starccm_naca_mesh_conv_case() -> CaseSpec:
    """NACA0012 CaseSpec with mesh convergence parameters (module-scoped)."""
    from cfdb.schema import CommandStep

    return _build_naca_casespec(
        solver_name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=[
            CommandStep(
                name="solve",
                command="starccm+ -batch {{ case_dir }}/run.java -nproc 4",
            ),
        ],
        alpha_deg=5.0,
        reynolds=5e6,
        n_iter=2000,
        prism_layers=8,
        prism_thickness=0.002,
    )


# -- Composite adapter + prepare fixtures --


@pytest.fixture
def starccm_adapter_dry() -> Any:
    """StarCCMAdapter in dry_run mode (function-scoped)."""
    from cfdb.adapters.starccm import StarCCMAdapter

    return StarCCMAdapter(dry_run=True)


@pytest.fixture
def starccm_adapter_real() -> Any:
    """StarCCMAdapter in real (non-dry) mode (function-scoped)."""
    from cfdb.adapters.starccm import StarCCMAdapter

    return StarCCMAdapter(dry_run=False)


@pytest.fixture
def starccm_prepared(
    tmp_path: Path,
    starccm_case: CaseSpec,
    starccm_adapter_dry: Any,
) -> tuple[Path, Path, str]:
    """Prepare a flat-plate case and return (case_dir, run_dir, macro content)."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    run_dir = tmp_path / "run"
    starccm_adapter_dry.prepare(starccm_case, case_dir, run_dir)
    macro = (run_dir / "case" / "run.java").read_text(encoding="utf-8")
    return case_dir, run_dir, macro


@pytest.fixture
def starccm_naca_prepared(
    tmp_path: Path,
    starccm_naca_case: CaseSpec,
    starccm_adapter_dry: Any,
) -> tuple[Path, Path, str]:
    """Prepare a NACA case and return (case_dir, run_dir, macro content)."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    run_dir = tmp_path / "run"
    starccm_adapter_dry.prepare(starccm_naca_case, case_dir, run_dir)
    macro = (run_dir / "case" / "run.java").read_text(encoding="utf-8")
    return case_dir, run_dir, macro


# -- Mock stdout helpers --


@pytest.fixture
def starccm_stdout_version() -> str:
    """Mock Star-CCM+ stdout containing version banner."""
    return (
        "STAR-CCM+ 19.02.009 (windows/intel18.3-r8)\n"
        "License build date: 19 March 2025\n"
    )


@pytest.fixture
def starccm_stdout_residuals_csv() -> str:
    """Mock residuals in CSV format (continuity + momentum)."""
    return (
        "Iteration,Continuity,X-Momentum,Y-Momentum\n"
        "1,1.000e+00,1.000e+00,1.000e+00\n"
        "2,5.000e-01,5.000e-01,5.000e-01\n"
        "3,1.000e-02,1.000e-02,1.000e-02\n"
    )


@pytest.fixture
def starccm_stdout_residuals_singleline() -> str:
    """Mock residuals as single-line per iteration (legacy format)."""
    return (
        "Iteration: 1  Continuity: 1.0e+00  X-Momentum: 1.0e+00  "
        "Y-Momentum: 1.0e+00\n"
        "Iteration: 2  Continuity: 5.0e-01  X-Momentum: 5.0e-01  "
        "Y-Momentum: 5.0e-01\n"
    )


@pytest.fixture
def starccm_stdout_cell_count() -> str:
    """Mock mesh output containing cell count."""
    return "Mesh generated. N cells: 400\nReady for solve."
