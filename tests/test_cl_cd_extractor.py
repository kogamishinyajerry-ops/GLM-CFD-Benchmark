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

    def test_stable_forces_uses_last_step(self, tmp_path: Path) -> None:
        """When forces are stable (no exponential growth), Cl/Cd come from
        the last time step. Monotonic increase is NOT treated as divergence
        — only a >10x magnitude jump triggers rollback."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        # Forces that are monotonically increasing (normal convergence trend)
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

    def test_diverging_forces_rollback_to_pre_jump_step(self, tmp_path: Path) -> None:
        """A >10x force magnitude jump between consecutive steps indicates
        divergence; Cl/Cd should come from the step immediately before the
        jump, NOT the (diverged) last step."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = (
            "0.000 (1.0 1.0 0) (0 0 0)\n"     # mag=1.414
            "1.000 (1.1 1.1 0) (0 0 0)\n"     # mag=1.556
            "2.000 (1.2 1.2 0) (0 0 0)\n"     # mag=1.697
            "3.000 (50.0 50.0 0) (0 0 0)\n"   # mag=70.71 → >10x of prev → divergence
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        assert result is not None
        # Expect index 2 (pre-jump) not index 3 (diverged)
        # q_inf = 0.5; cl = 1.2 / 0.5 = 2.4
        cl, cd = result
        assert math.isclose(cl, 2.4, rel_tol=1e-4)
        assert math.isclose(cd, 2.4, rel_tol=1e-4)

    def test_cascading_divergence_uses_pre_first_jump_step(self, tmp_path: Path) -> None:
        """Once a run diverges, every subsequent step also diverges (each
        ~10x the previous). The algorithm must scan FORWARD to catch the
        FIRST jump and return the pre-divergence step — a backward scan
        would land on an already-diverged step near the end.

        Reproduces the P3-hotfix bug where the OpenCFD forces.dat from a
        diverged simpleFoam run (cascading 10x jumps from step 200 onward)
        produced Cl/Cd on the order of 1e4 instead of the physical ~1e-3."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        # Magnitudes: ~26, ~27, ~47 (first jump here, 47/27 > 10x... actually
        # 47/27 ≈ 1.74 — not yet. Let's construct a clean cascading case).
        content = (
            "50   (20.0 -10.0 0) (0 0 0)\n"     # mag 22.36 (stable)
            "100  (21.0 -10.5 0) (0 0 0)\n"     # mag 23.43 (stable)
            "150  (22.0 -11.0 0) (0 0 0)\n"     # mag 24.60 (stable, last good)
            "200  (300.0 -150.0 0) (0 0 0)\n"   # mag 335.4 → first 10x jump
            "250  (5000.0 -2500.0 0) (0 0 0)\n" # mag 5590  → cascading
            "300  (80000.0 -40000.0 0) (0 0 0)\n"  # mag 89443 → cascading
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        assert result is not None
        cl, cd = result
        # Pre-first-jump step is index 2 (time 150, Fx=22, Fy=-11).
        # q_inf = 0.5 → cl = -11/0.5 = -22, cd = 22/0.5 = 44
        assert math.isclose(cl, -22.0, rel_tol=1e-4)
        assert math.isclose(cd, 44.0, rel_tol=1e-4)

    def test_converging_forces_uses_last_step(self, tmp_path: Path) -> None:
        """Monotonically decreasing force magnitude (typical of a settling
        initial transient) is NOT divergence — Cl/Cd come from the last step."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = (
            "0.000 (5.0 5.0 0) (0 0 0)\n"
            "1.000 (3.0 3.0 0) (0 0 0)\n"
            "2.000 (1.0 1.0 0) (0 0 0)\n"
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        cl, cd = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        # last step 1.0 / 0.5 = 2.0
        assert math.isclose(cl, 2.0, rel_tol=1e-4)
        assert math.isclose(cd, 2.0, rel_tol=1e-4)

    def test_bounded_oscillation_uses_last_step(self, tmp_path: Path) -> None:
        """Bounded oscillation (each step within 10x of previous) is NOT
        treated as divergence — Cl/Cd come from the last step."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = (
            "0.000 (2.0 2.0 0) (0 0 0)\n"
            "1.000 (1.0 1.0 0) (0 0 0)\n"
            "2.000 (3.0 3.0 0) (0 0 0)\n"   # mag 4.24, prev 1.41 → 3x, <10x
            "3.000 (2.0 2.0 0) (0 0 0)\n"
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        cl, cd = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        # last step 2.0 / 0.5 = 4.0
        assert math.isclose(cl, 4.0, rel_tol=1e-4)
        assert math.isclose(cd, 4.0, rel_tol=1e-4)

    def test_opencfd_v2406_9column_format(self, tmp_path: Path) -> None:
        """OpenCFD v2312/v2406 forces.dat: 10-column space-separated format
        (time + total(3) + pressure(3) + viscous(3)), no parentheses."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = (
            "# Forces\n"
            "# time forces (total_x total_y total_z) "
            "pressure (px py pz) viscous (vx vy vz)\n"
            "0.000 0.50 12.00 0.00 0.45 11.80 0.00 0.05 0.20 0.00\n"
            "1.000 0.52 12.10 0.00 0.47 11.90 0.00 0.05 0.20 0.00\n"
            "2.000 0.54 12.20 0.00 0.49 12.00 0.00 0.05 0.20 0.00\n"
        )
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        assert result is not None
        cl, cd = result
        # No >10x jump → last step wins; last total_x=0.54, total_y=12.20
        # q_inf = 0.5 → cl = 12.20/0.5 = 24.4, cd = 0.54/0.5 = 1.08
        assert math.isclose(cl, 24.4, rel_tol=1e-4)
        assert math.isclose(cd, 1.08, rel_tol=1e-4)

    def test_single_step_history(self, tmp_path: Path) -> None:
        """Only one data point: divergence detection is skipped, that step
        is returned directly."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        content = "5.000 (7.0 7.0 0) (0 0 0)\n"
        path = tmp_path / "forces.dat"
        path.write_text(content, encoding="utf-8")
        cl, cd = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        # q_inf = 0.5 → cl = 7.0/0.5 = 14.0
        assert math.isclose(cl, 14.0, rel_tol=1e-4)
        assert math.isclose(cd, 14.0, rel_tol=1e-4)

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

    def test_alpha_zero_is_unrotated_backcompat(self, tmp_path: Path) -> None:
        """alpha_deg defaults to 0 → cl=Fy/qA, cd=Fx/qA (historical)."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        path.write_text("1.0 (3.0 7.0 0) (0 0 0)\n", encoding="utf-8")
        cl, cd = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        # q_inf = 0.5 → cl = 7/0.5 = 14, cd = 3/0.5 = 6
        assert math.isclose(cl, 14.0)
        assert math.isclose(cd, 6.0)

    def test_alpha_rotation_projects_to_wind_axes(self, tmp_path: Path) -> None:
        """Non-zero alpha rotates the global-frame force onto wind axes:
        Lift = Fy·cosα − Fx·sinα, Drag = Fx·cosα + Fy·sinα."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        # Global force Fx=10 (streamwise), Fy=100 (vertical).
        path.write_text("1.0 (10.0 100.0 0) (0 0 0)\n", encoding="utf-8")
        alpha = 5.0
        result = extract_cl_cd_openfoam(
            path, rho=1.0, u_inf=1.0, a_ref=1.0, alpha_deg=alpha
        )
        assert result is not None
        cl, cd = result
        ar = math.radians(alpha)
        q = 0.5
        lift = 100.0 * math.cos(ar) - 10.0 * math.sin(ar)
        drag = 10.0 * math.cos(ar) + 100.0 * math.sin(ar)
        assert math.isclose(cl, lift / q, rel_tol=1e-9)
        assert math.isclose(cd, drag / q, rel_tol=1e-9)
        # Sanity: rotation lowers Cl and raises Cd vs the unrotated case.
        assert cl < 100.0 / q
        assert cd > 10.0 / q

    def test_alpha_90_turns_vertical_force_into_pure_drag(self, tmp_path: Path) -> None:
        """At α=90° the freestream points along +y, so a purely vertical
        global force becomes pure drag and zero lift (rotation sanity)."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        path.write_text("1.0 (0.0 10.0 0) (0 0 0)\n", encoding="utf-8")
        cl, cd = extract_cl_cd_openfoam(
            path, rho=1.0, u_inf=1.0, a_ref=1.0, alpha_deg=90.0
        )
        assert math.isclose(cl, 0.0, abs_tol=1e-9)
        assert math.isclose(cd, 10.0 / 0.5, rel_tol=1e-9)

    def test_a_ref_scales_coefficients_inversely(self, tmp_path: Path) -> None:
        """Halving the reference area doubles the coefficients (Aref bug fix:
        2D span thickness 0.1 → Aref 0.1, not 1.0)."""
        from cfdb.post.qoi_extractor import extract_cl_cd_openfoam
        path = tmp_path / "forces.dat"
        path.write_text("1.0 (1.0 1.0 0) (0 0 0)\n", encoding="utf-8")
        cl1, cd1 = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=1.0)
        cl2, cd2 = extract_cl_cd_openfoam(path, rho=1.0, u_inf=1.0, a_ref=0.1)
        assert math.isclose(cl2, cl1 * 10.0, rel_tol=1e-9)
        assert math.isclose(cd2, cd1 * 10.0, rel_tol=1e-9)


class TestExtractClCdCoefficientDat:
    """Tests for extract_cl_cd_coefficient_dat (forceCoeffs coefficient.dat)."""

    def test_basic_header_parse_last_row(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        content = (
            "# Force coefficients\n"
            "# dragDir : (0.9962 0.0872 0)\n"
            "# Time          Cd             Cs             Cl"
            "             CmPitch\n"
            "500   0.012000  0.0  0.440000  -0.010\n"
            "694   0.009500  0.0  0.456000  -0.012\n"
        )
        path = tmp_path / "coefficient.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_coefficient_dat(path)
        assert result is not None
        cl, cd = result
        assert math.isclose(cl, 0.456000)
        assert math.isclose(cd, 0.009500)

    def test_columns_located_by_name_not_position(self, tmp_path: Path) -> None:
        """Cl/Cd found by column name; a 'Cd(f)' decoy must not match 'Cd'."""
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        content = (
            "# Time   Cl      Cd      Cd(f)    Cd(r)\n"
            "100  0.812000  0.012500  0.006  0.0065\n"
        )
        path = tmp_path / "coefficient.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_coefficient_dat(path)
        assert result is not None
        cl, cd = result
        assert math.isclose(cl, 0.812000)
        assert math.isclose(cd, 0.012500)

    def test_columns_decoy_before_real_cd(self, tmp_path: Path) -> None:
        """'Cd(f)' decoy appearing BEFORE the real Cd column must not be
        matched — index() returns the first EXACT 'cd', not 'cd(f)'."""
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        content = (
            "# Time   Cd(f)    Cd(r)   Cd       Cl\n"
            "100  0.006  0.0065  0.012500  0.812000\n"
        )
        path = tmp_path / "coefficient.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_coefficient_dat(path)
        assert result is not None
        cl, cd = result
        assert math.isclose(cl, 0.812000)
        assert math.isclose(cd, 0.012500)

    def test_diverged_last_row_rolls_back_to_plausible(self, tmp_path: Path) -> None:
        """A late-diverged run writes finite-but-garbage coefficients to the
        final rows; the scan rolls back to the most recent plausible row."""
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        content = (
            "# Time   Cd        Cs    Cl        CmPitch\n"
            "600  0.009500  0.0  0.456000  -0.012\n"
            "650  1.2e+14    0.0  3.4e+15   -9.9e+13\n"   # diverged
            "694  8.7e+15    0.0  -2.2e+16  -1.0e+14\n"   # diverged worse
        )
        path = tmp_path / "coefficient.dat"
        path.write_text(content, encoding="utf-8")
        result = extract_cl_cd_coefficient_dat(path)
        assert result is not None
        cl, cd = result
        # Rolls back to time 600 (the last physically plausible row).
        assert math.isclose(cl, 0.456000)
        assert math.isclose(cd, 0.009500)

    def test_all_rows_diverged_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        content = (
            "# Time   Cd       Cs    Cl       CmPitch\n"
            "650  1.2e+14  0.0  3.4e+15  -9.9e+13\n"
            "694  8.7e+15  0.0  -2.2e+16  -1.0e+14\n"
        )
        path = tmp_path / "coefficient.dat"
        path.write_text(content, encoding="utf-8")
        assert extract_cl_cd_coefficient_dat(path) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        assert extract_cl_cd_coefficient_dat(tmp_path / "no.dat") is None

    def test_no_column_header_returns_none(self, tmp_path: Path) -> None:
        """Data rows present but no Cd/Cl column-name comment → None."""
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        path = tmp_path / "coefficient.dat"
        path.write_text("# Force coefficients\n694 0.0095 0.456\n", encoding="utf-8")
        assert extract_cl_cd_coefficient_dat(path) is None

    def test_no_data_rows_returns_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        path = tmp_path / "coefficient.dat"
        path.write_text("# Time Cd Cs Cl CmPitch\n", encoding="utf-8")
        assert extract_cl_cd_coefficient_dat(path) is None

    def test_short_row_returns_none(self, tmp_path: Path) -> None:
        """Header names a Cl column index beyond the data row width → None."""
        from cfdb.post.qoi_extractor import extract_cl_cd_coefficient_dat
        path = tmp_path / "coefficient.dat"
        path.write_text(
            "# Time Cd Cs Cl CmPitch\n694 0.0095\n", encoding="utf-8"
        )
        assert extract_cl_cd_coefficient_dat(path) is None


class TestSafeFloatFromLine:
    """Tests for the _safe_float_from_line helper (time token parser)."""

    def test_valid_time_token(self) -> None:
        from cfdb.post.qoi_extractor import _safe_float_from_line

        assert _safe_float_from_line("1.500 (1 2 3) (0 0 0)") == 1.5
        assert _safe_float_from_line("  0.000 1.0 2.0 3.0") == 0.0
        assert _safe_float_from_line("100") == 100.0

    def test_empty_line_returns_zero(self) -> None:
        from cfdb.post.qoi_extractor import _safe_float_from_line

        assert _safe_float_from_line("") == 0.0
        assert _safe_float_from_line("   ") == 0.0

    def test_non_numeric_first_token_returns_zero(self) -> None:
        """When the first token is not a float (e.g. a stray header line that
        slipped past the # filter), return 0.0 — divergence detection uses
        only (fx, fy), so a 0 time value is harmless."""
        from cfdb.post.qoi_extractor import _safe_float_from_line

        assert _safe_float_from_line("nan_token 1 2 3") == 0.0
        assert _safe_float_from_line("alpha 0.5 0.5 0") == 0.0


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
        ref_path = (
        Path(__file__).parent.parent
        / "cases" / "validation" / "naca0012" / "reference" / "ladson_polar.csv"
    )
        if not ref_path.exists():
            pytest.skip(f"reference file not found: {ref_path}")
        result = load_ladson_polar(ref_path)
        assert result is not None
        assert len(result) == 4  # α=0/5/10/15
        assert result[0][0] == 0.0
        assert result[-1][0] == 15.0
