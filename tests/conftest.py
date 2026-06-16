"""Shared pytest fixtures for all test modules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cfdb.schema import CaseSpec

# P2-b: Allow tests to import case-specific modules (e.g. NACA0012 geometry
# generator lives in cases/validation/naca0012/gen_geometry.py, which is a
# case-owned helper script, not a cfdb package module).
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


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
