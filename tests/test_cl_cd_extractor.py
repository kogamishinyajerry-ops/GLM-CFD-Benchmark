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
        ref_path = Path(__file__).parent.parent / "cases" / "validation" / "naca0012" / "reference" / "ladson_polar.csv"
        if not ref_path.exists():
            pytest.skip(f"reference file not found: {ref_path}")
        result = load_ladson_polar(ref_path)
        assert result is not None
        assert len(result) == 4  # α=0/5/10/15
        assert result[0][0] == 0.0
        assert result[-1][0] == 15.0
