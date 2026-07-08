"""Regression pins for the LDC real-run path.

The first live lid_driven_cavity validation (OpenFOAM v2312, docker backend)
exposed four latent defects on a path that had only ever been exercised by
mocks. Each test below pins one root-cause fix; a live re-run then passed
against Ghia 1982 (u_max/U_lid = 0.6765 vs 0.665 interpolated, 1.7% rel err).

1. prepare() never staged the case-shipped blockMeshDict into system/.
2. 0/U and 0/p placeholders lacked FoamFile class/object + boundaryField.
3. controlDict.j2 hardcoded endTime/deltaT (case parameters were ignored).
4. extract_openfoam_centerline_umax read only the FIRST probe vector per
   line (re.search), silently discarding every other probe.
"""

from __future__ import annotations

from pathlib import Path

from cfdb.adapters.openfoam import OpenFOAMAdapter
from cfdb.post.qoi_extractor import extract_openfoam_centerline_umax
from cfdb.registry import CaseRegistry

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


def _prepare_ldc(tmp_path: Path) -> Path:
    registry = CaseRegistry(CASES_DIR)
    case = registry.load("lid_driven_cavity")
    case_dir = registry.get_case_dir("lid_driven_cavity")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    adapter = OpenFOAMAdapter(dry_run=True)
    adapter.prepare(case, case_dir, run_dir)
    return run_dir / "case"


def test_prepare_stages_blockmeshdict_into_system(tmp_path: Path) -> None:
    out = _prepare_ldc(tmp_path)
    staged = out / "system" / "blockMeshDict"
    assert staged.exists(), "blockMeshDict must be staged where v2312 looks"
    assert "movingWall" in staged.read_text(encoding="utf-8")


def test_initial_fields_are_complete_foam_dicts(tmp_path: Path) -> None:
    out = _prepare_ldc(tmp_path)
    u = (out / "0" / "U").read_text(encoding="utf-8")
    p = (out / "0" / "p").read_text(encoding="utf-8")
    # FoamFile header completeness (icoFoam FATALs without class/object)
    assert "class       volVectorField;" in u
    assert "object      U;" in u
    assert "class       volScalarField;" in p
    # boundaryField must cover every patch of the shipped blockMeshDict
    for patch in ("movingWall", "fixedWalls", "frontAndBack"):
        assert patch in u, f"0/U missing patch {patch}"
        assert patch in p, f"0/p missing patch {patch}"
    assert "noSlip" in u
    assert "empty" in u


def test_controldict_renders_case_parameters(tmp_path: Path) -> None:
    out = _prepare_ldc(tmp_path)
    control = (out / "system" / "controlDict").read_text(encoding="utf-8")
    # Values come from case.yaml solver parameters, not template hardcodes.
    assert "endTime         5.0;" in control
    assert "deltaT          0.002;" in control
    # Probes must sit inside the 0.1 x 0.1 domain (the old template probed
    # x=0.5 — outside the mesh — so the QoI chain could never produce data).
    assert "(0.05 0.095 0.005)" in control
    assert "(0.5 0.05 0)" not in control


def test_fvsolution_has_final_correctors(tmp_path: Path) -> None:
    out = _prepare_ldc(tmp_path)
    fv = (out / "system" / "fvSolution").read_text(encoding="utf-8")
    assert "pFinal" in fv, "icoFoam (v2312) requires pFinal solver entry"


def test_centerline_umax_reads_every_probe_on_a_line(tmp_path: Path) -> None:
    """Tamper witness for the re.search -> finditer fix: the max lives in the
    LAST vector of the line; the old parser returned 0.03 (first vector)."""
    probes = tmp_path / "postProcessing" / "probes" / "0"
    probes.mkdir(parents=True)
    (probes / "U").write_text(
        "# Probe 0 (0.05 0.005 0.005)\n"
        "# Probe 1 (0.05 0.095 0.005)\n"
        "5    (-0.03 0.001 0)    (0.67 0.002 0)\n",
        encoding="utf-8",
    )
    umax = extract_openfoam_centerline_umax(probes.parent, "U")
    assert umax is not None
    assert abs(umax - 0.67) < 1e-3, f"must see probe 1's 0.67, got {umax}"
