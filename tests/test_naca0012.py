"""Tests for NACA0012 geometry generation and Cp extraction.

P2-b feature.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest


class TestNACA4Thickness:
    """Tests for naca4_thickness symmetric airfoil generator."""

    def test_basic_shape(self) -> None:
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        x, y = naca4_thickness(t=0.12, n=100)
        # Total points = 2*n (upper + lower)
        assert len(x) == 200
        assert len(y) == 200
        # Upper surface from x=0 to x=1, then lower surface from x=1 to x=0
        assert math.isclose(x[0], 0.0, abs_tol=1e-6)
        assert math.isclose(x[99], 1.0, abs_tol=1e-6)
        assert math.isclose(x[100], 1.0, abs_tol=1e-6)
        assert math.isclose(x[199], 0.0, abs_tol=1e-6)

    def test_symmetric_airfoil_upper_lower_mirror(self) -> None:
        """For symmetric airfoil (m=p=0), lower surface = -upper surface."""
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        x, y = naca4_thickness(t=0.12, n=50)
        # Upper surface points 0..49, lower surface points 50..99 (reversed)
        upper_y = y[:50]
        lower_y = y[50:][::-1]  # un-reverse to compare point-by-point
        for uy, ly in zip(upper_y, lower_y, strict=False):
            assert math.isclose(uy, -ly, abs_tol=1e-6)

    def test_thickness_at_midchord(self) -> None:
        """At x=0.5, NACA0012 total thickness ≈ 0.106 (half-thickness ≈ 0.053).

        Standard NACA 4-digit thickness formula:
        yt(x) = 5t(0.2969√x - 0.126x - 0.3516x² + 0.2843x³ - 0.1015x⁴)
        At x=0.5, t=0.12: yt ≈ 0.053, total = 2*yt ≈ 0.106.
        """
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        x, y = naca4_thickness(t=0.12, n=200)
        # Find the index closest to x=0.5 on upper surface
        upper_x = x[:200]
        idx = int(np.argmin(np.abs(upper_x - 0.5)))
        assert math.isclose(upper_x[idx], 0.5, abs_tol=0.01)
        half_thickness_at_05 = abs(y[idx])  # upper y = half thickness
        total_thickness_at_05 = 2.0 * half_thickness_at_05
        # Standard NACA thickness formula: half ≈ 0.053, total ≈ 0.106
        assert 0.045 < half_thickness_at_05 < 0.060
        assert 0.090 < total_thickness_at_05 < 0.120

    def test_leading_edge_x_zero(self) -> None:
        """Leading edge at x=0, y=0 (stagnation point)."""
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        x, y = naca4_thickness(t=0.12, n=100)
        assert math.isclose(x[0], 0.0, abs_tol=1e-6)
        assert math.isclose(y[0], 0.0, abs_tol=1e-6)

    def test_trailing_edge_closed(self) -> None:
        """Closed trailing edge: thickness → 0 at x=1 (small residual OK).

        NACA 4-digit with -0.1015 coefficient leaves small residual at TE
        (the formula is yt = 5t(0.2969√x - ... - 0.1015x⁴); at x=1, yt=0.00126
        for t=0.12, which is 1% of chord — acceptable for mesh generators).
        """
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        x, y = naca4_thickness(t=0.12, n=100)
        # Upper surface endpoint (x=1)
        assert math.isclose(x[99], 1.0, abs_tol=1e-6)
        # TE residual is small (within 0.15% of chord)
        assert abs(y[99]) < 0.0015

    def test_invalid_thickness_raises(self) -> None:
        from cases.validation.naca0012.gen_geometry import naca4_thickness
        with pytest.raises(ValueError, match="thickness"):
            naca4_thickness(t=0.0)
        with pytest.raises(ValueError, match="thickness"):
            naca4_thickness(t=-0.1)
        with pytest.raises(ValueError, match="thickness"):
            naca4_thickness(t=1.0)  # > 0.5


class TestSeligFormat:
    """Tests for write_selig_format."""

    def test_writes_dat_file(self, tmp_path: Path) -> None:
        from cases.validation.naca0012.gen_geometry import write_selig_format
        x = np.array([0.0, 0.5, 1.0])
        y = np.array([0.0, 0.06, 0.0])
        path = tmp_path / "test.dat"
        write_selig_format(x, y, path, name="NACA0012")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        # Header + 3 data points
        assert lines[0] == "NACA0012"
        assert len(lines) == 4
        assert "0.000000 0.000000" in lines[1]
        assert "0.500000 0.060000" in lines[2]

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        from cases.validation.naca0012.gen_geometry import write_selig_format
        nested = tmp_path / "a" / "b" / "c" / "test.dat"
        write_selig_format(np.array([0.0]), np.array([0.0]), nested, name="X")
        assert nested.exists()
        assert nested.parent.exists()


class TestSTLOutput:
    """Tests for write_stl."""

    def test_stl_file_structure(self, tmp_path: Path) -> None:
        from cases.validation.naca0012.gen_geometry import write_stl
        x = np.array([0.0, 0.5, 1.0, 1.0, 0.5, 0.0])  # upper + lower reversed
        y = np.array([0.0, 0.06, 0.0, 0.0, -0.06, 0.0])
        path = tmp_path / "airfoil.stl"
        write_stl(x, y, path, z_extent=0.1, name="naca0012")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert content.startswith("solid naca0012")
        assert content.rstrip().endswith("endsolid naca0012")
        assert "facet normal" in content
        assert "vertex" in content


class TestGenerateNACA0012:
    """Tests for the generate_naca0012 convenience function."""

    def test_generates_both_files(self, tmp_path: Path) -> None:
        from cases.validation.naca0012.gen_geometry import generate_naca0012
        dat, stl = generate_naca0012(tmp_path, n_points=50)
        assert dat.exists()
        assert stl.exists()
        assert dat.name == "naca0012.dat"
        assert stl.name == "naca0012.stl"
        # Verify .dat header
        content = dat.read_text(encoding="utf-8")
        assert content.startswith("NACA0012")


class TestNACA0012CpExtractorSU2:
    """Tests for extract_naca0012_cp_su2."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_naca0012_cp_su2
        csv_content = (
            '"Point_ID","x","y","Pressure","Pressure_Coefficient"\n'
            "0,0.0,0.0,101325.0,1.0000\n"
            "1,0.5,0.05,95000.0,-0.6\n"
            "2,1.0,0.0,101000.0,0.05\n"
        )
        path = tmp_path / "surface_flow.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_naca0012_cp_su2(path)
        assert result is not None
        x_list, cp_list = result
        assert len(x_list) == 3
        assert math.isclose(x_list[0], 0.0)
        assert math.isclose(cp_list[0], 1.0)
        assert math.isclose(cp_list[1], -0.6)

    def test_cp_column_named_cp(self, tmp_path: Path) -> None:
        """Alternative column name 'Cp' should also work."""
        from cfdb.post.qoi_extractor import extract_naca0012_cp_su2
        csv_content = (
            '"Point_ID","x","y","Cp"\n'
            "0,0.0,0.0,1.0\n"
        )
        path = tmp_path / "test.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_naca0012_cp_su2(path)
        assert result is not None
        assert result[1][0] == 1.0

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_naca0012_cp_su2
        result = extract_naca0012_cp_su2(tmp_path / "nonexistent.csv")
        assert result is None

    def test_missing_cp_column_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_naca0012_cp_su2
        csv_content = '"x","y"\n0.5,0.0\n'
        path = tmp_path / "test.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_naca0012_cp_su2(path)
        assert result is None

    def test_out_of_range_x_filtered(self, tmp_path: Path) -> None:
        """x/c values outside [-0.1, 1.1] should be filtered out."""
        from cfdb.post.qoi_extractor import extract_naca0012_cp_su2
        csv_content = (
            '"x","Pressure_Coefficient"\n'
            "0.5,-0.5\n"
            "2.0,0.0\n"   # out of range, should be filtered
            "-0.5,0.0\n"  # out of range
        )
        path = tmp_path / "test.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_naca0012_cp_su2(path)
        assert result is not None
        x_list, _ = result
        assert len(x_list) == 1
        assert math.isclose(x_list[0], 0.5)


class TestNACA0012CpExtractorOpenFOAM:
    """Tests for extract_naca0012_cp_openfoam."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_naca0012_cp_openfoam
        csv_content = (
            "x,Cp\n"
            "0.0,1.0\n"
            "0.5,-0.6\n"
            "1.0,0.05\n"
        )
        path = tmp_path / "forces.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_naca0012_cp_openfoam(path)
        assert result is not None
        x_list, cp_list = result
        assert len(x_list) == 3

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_naca0012_cp_openfoam
        result = extract_naca0012_cp_openfoam(tmp_path / "nonexistent.csv")
        assert result is None


class TestLoadLadsonReference:
    """Tests for load_ladson_reference."""

    def test_loads_reference_data(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import load_ladson_reference
        csv_content = (
            "x/c,Cp\n"
            "0.0,1.0\n"
            "0.5,-0.235\n"
            "1.0,0.05\n"
        )
        path = tmp_path / "ladson.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = load_ladson_reference(path)
        assert result is not None
        x_list, cp_list = result
        assert len(x_list) == 3
        assert math.isclose(x_list[1], 0.5)
        assert math.isclose(cp_list[1], -0.235)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import load_ladson_reference
        result = load_ladson_reference(tmp_path / "no.csv")
        assert result is None

    def test_actual_reference_file(self) -> None:
        """Load the real ladson1988.csv in the repo and verify shape."""
        from cfdb.post.qoi_extractor import load_ladson_reference
        ref_path = Path(__file__).parent.parent / "cases" / "validation" / "naca0012" / "reference" / "ladson1988.csv"
        if not ref_path.exists():
            pytest.skip(f"reference file not found: {ref_path}")
        result = load_ladson_reference(ref_path)
        assert result is not None
        x_list, cp_list = result
        assert len(x_list) >= 10  # at least 10 reference points
        # Stagnation point at x=0 should have Cp ≈ 1.0
        assert math.isclose(x_list[0], 0.0)
        assert math.isclose(cp_list[0], 1.0)


class TestNACA0012CaseSchema:
    """Tests that the case.yaml loads correctly."""

    def test_case_yaml_validates(self) -> None:
        """Load naca0012_a0/case.yaml and verify it passes CaseSpec validation."""
        from cfdb.registry import CaseRegistry

        cases_root = Path(__file__).parent.parent / "cases"
        if not (cases_root / "validation" / "naca0012" / "case.yaml").exists():
            pytest.skip("naca0012 case.yaml not found")

        registry = CaseRegistry(cases_root)
        case = registry.load("naca0012_a0")
        assert case.id == "naca0012_a0"
        assert case.category == "validation"
        assert case.physics.flow == "rans"
        assert case.physics.turbulence == "rans_sa"
        assert case.conditions.alpha_deg == 0.0
        # Two solver configs
        assert len(case.solvers) == 2
        assert case.solvers[0].name == "openfoam"
        assert case.solvers[1].name == "su2"
        # Reference is experimental
        assert case.reference is not None
        assert case.reference.type == "experimental"
