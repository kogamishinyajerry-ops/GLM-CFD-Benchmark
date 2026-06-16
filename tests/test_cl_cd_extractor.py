"""Tests for cfdb.post.qoi_extractor Cl/Cd extractors (P2-c)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest


class TestExtractClCdOpenFOAM:
    """Tests for extract_cl_cd_openfoam."""

    def test_valid_forces_dat(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        # forces.dat with header + 2 time steps. Last step Fx=0.0125, Fy=-0.5
        content = (
            "# Forces\n"
            "# time forces (Fx Fy Fz) moments (Mx My Mz)\n"
            "0.000 (0.00123 -0.00045 0) (0 0 0.00001)\n"
            "1.000 (0.01250 -0.50000 0) (0 0 0.00001)\n"
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        # rho=1.225, u_inf=100, a_ref=1 → q_inf = 0.5 * 1.225 * 100^2 = 6125
        # cl = -0.5 / 6125 / 1 = -8.16e-5 ; cd = 0.0125 / 6125 / 1 = 2.04e-6
        result = extract_cl_cd_openfoam(path, rho=1.225, u_inf=100.0, a_ref=1.0)
        assert result is not None
        cl, cd = result
        assert math.isclose(cl, -0.5 / 6125.0, rel_tol=1e-4)
        assert math.isclose(cd, 0.0125 / 6125.0, rel_tol=1e-4)

    def test_uses_last_time_step(self, tmp_path: Path) -> None:
        """Final Cl/Cd must come from the LAST time step."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = (
            "0.000 (1.0 1.0 0) (0 0 0)\n"
            "1.000 (2.0 2.0 0) (0 0 0)\n"
            "2.000 (3.0 3.0 0) (0 0 0)\n"
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        assert result is not None
        # q_inf = 0.5 * 1 * 1 * 1 = 0.5; cl = 3.0 / 0.5 = 6.0
        cl, cd = result
        assert math.isclose(cl, 6.0)
        assert math.isclose(cd, 6.0)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        result = extract_cl_cd_openfoam(tmp_path / "nonexistent.dat")
        assert result is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        path.write_text("# Forces\n# header only\n", encoding="utf-8")
        result = extract_cl_cd_openfoam(path)
        assert result is None

    def test_invalid_q_inf_returns_none(self, tmp_path: Path) -> None:
        """u_inf=0 → q_inf=0 → division by zero → return None."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        path.write_text("1.000 (1.0 1.0 0) (0 0 0)\n", encoding="utf-8")
        result = extract_cl_cd_openfoam(path, u_inf=0.0)
        assert result is None


class TestExtractClCdSU2:
    """Tests for extract_cl_cd_su2."""

    def test_valid_csv_upper_lower(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_su2
        # 4 upper points + 4 lower points along chord
        csv_content = (
            '"Point_ID","x","y","Pressure_Coefficient"\n'
            "0,0.0,0.05,1.0\n"     # upper LE
            "1,0.5,0.04,-0.5\n"    # upper mid
            "2,1.0,0.001,-0.1\n"   # upper TE
            "3,0.0,-0.05,1.0\n"    # lower LE
            "4,0.5,-0.04,0.3\n"    # lower mid
            "5,1.0,-0.001,0.1\n"   # lower TE
        )
        path = tmp_path / "surface_flow.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_cl_cd_su2(path)
        assert result is not None
        cl, cd = result
        # cl should be positive (lower Cp > upper Cp on average → lift)
        assert cl > 0
        # cd is approximate, just check it's non-negative
        assert cd >= 0

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_su2
        result = extract_cl_cd_su2(tmp_path / "no.csv")
        assert result is None

    def test_missing_cp_column_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_su2
        csv_content = '"x","y"\n0.5,0.05\n'
        path = tmp_path / "test.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_cl_cd_su2(path)
        assert result is None

    def test_only_upper_surface_returns_none(self, tmp_path: Path) -> None:
        """Need both upper and lower surface points to integrate Cl."""
        from cfdb.post.qoi_extractor import extract_cl_cd_su2
        csv_content = (
            '"x","y","Pressure_Coefficient"\n'
            "0.5,0.05,-0.5\n"   # upper only
        )
        path = tmp_path / "test.csv"
        path.write_text(csv_content, encoding="utf-8")
        result = extract_cl_cd_su2(path)
        assert result is None


class TestLoadLadsonPolar:
    """Tests for load_ladson_polar."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import load_ladson_polar
        content = "alpha_deg, Cl, Cd\n0.0,0.0,0.0086\n5.0,0.456,0.0095\n"
        path = tmp_path / "polar.csv"
        path.write_text(content, encoding="utf-8")
        result = load_ladson_polar(path)
        assert result is not None
        assert len(result) == 2
        assert result[0] == (0.0, 0.0, 0.0086)
        assert result[1] == (5.0, 0.456, 0.0095)

    def test_sorted_by_alpha(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import load_ladson_polar
        content = "alpha_deg, Cl, Cd\n10.0,0.862,0.0125\n0.0,0.0,0.0086\n5.0,0.456,0.0095\n"
        path = tmp_path / "polar.csv"
        path.write_text(content, encoding="utf-8")
        result = load_ladson_polar(path)
        assert result is not None
        alphas = [p[0] for p in result]
        assert alphas == [0.0, 5.0, 10.0]

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import load_ladson_polar
        result = load_ladson_polar(tmp_path / "no.csv")
        assert result is None

    def test_actual_reference_file(self) -> None:
        """Load the real ladson_polar.csv in the repo."""
        from cfdb.post.qoi_extractor import load_ladson_polar
        ref_path = Path(__file__).parent.parent / "cases" / "validation" / "naca0012" / "reference" / "ladson_polar.csv"
        if not ref_path.exists():
            pytest.skip(f"reference file not found: {ref_path}")
        result = load_ladson_polar(ref_path)
        assert result is not None
        assert len(result) == 4  # α=0/5/10/15
        assert result[0][0] == 0.0
        assert result[-1][0] == 15.0
