"""Real SU2 run test — marked real_solver, skipped in CI by default.

Run manually with: pytest -m real_solver tests/test_su2_real_run.py
Requires SU2_CFD installed and on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.mark.real_solver
def test_su2_real_run_flat_plate() -> None:
    """End-to-end real SU2 run of flat_plate_su2 case.

    Skips if SU2_CFD is not installed.
    """
    if shutil.which("SU2_CFD") is None:
        pytest.skip("SU2 not installed (SU2_CFD not on PATH)")

    from cfdb.core.runner import Runner
    from cfdb.registry import CaseRegistry
    from cfdb.storage.json_repo import JsonManifestRepository

    project_root = Path(__file__).resolve().parent.parent
    registry = CaseRegistry(project_root / "cases")
    runs_dir = project_root / "runs"
    repo = JsonManifestRepository(runs_dir)
    runner = Runner(registry, repo, runs_dir)

    manifest = runner.execute(
        case_id="flat_plate_su2",
        solver="su2",
        backend="local",
        cli_args={"case": "flat_plate_su2", "solver": "su2", "backend": "local"},
        dry_run=False,
    )

    assert manifest.status == "success"
    assert manifest.solver_version is not None
