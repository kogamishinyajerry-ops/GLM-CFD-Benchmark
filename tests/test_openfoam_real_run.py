"""Real OpenFOAM run test — marked real_solver, skipped in CI by default.

Run manually with: pytest -m real_solver tests/test_openfoam_real_run.py
Requires OpenFOAM (icoFoam + blockMesh) installed and on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.mark.real_solver
def test_openfoam_real_run_lid_driven_cavity() -> None:
    """End-to-end real OpenFOAM run of lid_driven_cavity case.

    Skips if OpenFOAM (blockMesh / icoFoam) is not installed.
    """
    if shutil.which("blockMesh") is None or shutil.which("icoFoam") is None:
        pytest.skip("OpenFOAM not installed (blockMesh/icoFoam not on PATH)")

    from cfdb.core.runner import Runner
    from cfdb.registry import CaseRegistry
    from cfdb.storage.json_repo import JsonManifestRepository

    project_root = Path(__file__).resolve().parent.parent
    registry = CaseRegistry(project_root / "cases")
    runs_dir = project_root / "runs"
    repo = JsonManifestRepository(runs_dir)
    runner = Runner(registry, repo, runs_dir)

    manifest = runner.execute(
        case_id="lid_driven_cavity",
        solver="openfoam",
        backend="local",
        cli_args={"case": "lid_driven_cavity", "solver": "openfoam", "backend": "local"},
        dry_run=False,
    )

    assert manifest.status == "success"
    assert manifest.solver_version is not None
    assert manifest.final_residuals is not None
    assert "Ux" in manifest.final_residuals
