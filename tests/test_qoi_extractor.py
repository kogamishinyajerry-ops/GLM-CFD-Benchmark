"""Unit tests for QoI extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.post.qoi_extractor import (
    _is_float,
    extract_naca0012_cp_openfoam,
    extract_naca0012_cp_su2,
    extract_openfoam_centerline_umax,
    extract_su2_skin_friction_coeff,
    load_ladson_reference,
)


class TestOpenFOAMProbesQoI:
    def test_extract_umax(self, tmp_path: Path) -> None:
        """Test probes parsing with a fixture probes file."""
        probes_dir = tmp_path / "postProcessing" / "probes"
        probes_dir.mkdir(parents=True)
        (probes_dir / "U").write_text(
            "# Probe 0 (0.5 0.05 0)\n"
            "# Probe 1 (0.5 0.15 0)\n"
            "# Time\n"
            "0.005  (0.0123 0.00456 0)\n"
            "0.005  (0.0234 0.00567 0)\n"
            "0.010  (0.0345 0.00678 0)\n"
            "0.010  (0.0456 0.00789 0)\n",
            encoding="utf-8",
        )
        umax = extract_openfoam_centerline_umax(probes_dir, "U")
        assert umax is not None
        assert umax > 0.04  # max should be around 0.0463

    def test_missing_probes_dir(self, tmp_path: Path) -> None:
        umax = extract_openfoam_centerline_umax(tmp_path / "nonexistent")
        assert umax is None


class TestSU2CsvQoI:
    def test_extract_cf_average(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surface_flow.csv"
        csv_path.write_text(
            '"Point_ID","x","y","Cf"\n'
            "0,0.001,0.0,0.0028\n"
            "1,0.002,0.0,0.0027\n"
            "2,0.003,0.0,0.0026\n",
            encoding="utf-8",
        )
        cf = extract_su2_skin_friction_coeff(csv_path, method="average")
        assert cf is not None
        assert abs(cf - 0.0027) < 0.0001  # average of 0.0028, 0.0027, 0.0026

    def test_extract_cf_trailing_edge(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surface_flow.csv"
        csv_path.write_text(
            '"Point_ID","x","y","Cf"\n'
            "0,0.001,0.0,0.0028\n"
            "1,0.002,0.0,0.0027\n"
            "2,0.003,0.0,0.0026\n",
            encoding="utf-8",
        )
        cf = extract_su2_skin_friction_coeff(csv_path, method="trailing_edge")
        assert cf is not None
        assert abs(cf - 0.0026) < 0.0001  # Cf at max x (0.003)

    def test_missing_csv(self, tmp_path: Path) -> None:
        cf = extract_su2_skin_friction_coeff(tmp_path / "nonexistent.csv")
        assert cf is None


class TestExtractOpenFOAMCenterlineUmax:
    """Coverage for extract_openfoam_centerline_umax (probes parser)."""

    def test_returns_max_vector_magnitude(self, tmp_path: Path) -> None:
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        (probes_dir / "U").write_text(
            "0.005  (0.300 0.400 0)\n"   # mag 0.5
            "0.010  (0.600 0.800 0)\n"   # mag 1.0 ← umax
            "0.015  (0.030 0.040 0)\n",  # mag 0.05
            encoding="utf-8",
        )
        umax = extract_openfoam_centerline_umax(probes_dir, "U")
        assert umax is not None
        assert abs(umax - 1.0) < 1e-6

    def test_time_dir_fallback_legacy_openfoam(self, tmp_path: Path) -> None:
        """Older OpenFOAM writes probe files under time subdirectories when
        no top-level field file exists. The function should pick the latest
        numeric time dir."""
        probes_dir = tmp_path / "probes"
        (probes_dir / "0").mkdir(parents=True)
        (probes_dir / "1").mkdir()
        (probes_dir / "10").mkdir()
        (probes_dir / "0" / "U").write_text("0.0 (0.1 0.1 0)\n", encoding="utf-8")
        (probes_dir / "1" / "U").write_text("1.0 (0.2 0.2 0)\n", encoding="utf-8")
        (probes_dir / "10" / "U").write_text(
            "10.0 (0.9 0.9 0)\n", encoding="utf-8"   # mag ≈ 1.27 ← latest time
        )
        umax = extract_openfoam_centerline_umax(probes_dir, "U")
        assert umax is not None
        assert abs(umax - (0.9 * 0.9 + 0.9 * 0.9) ** 0.5) < 1e-6

    def test_empty_probes_dir_returns_none(self, tmp_path: Path) -> None:
        """No field file AND no time subdirs → None."""
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        assert extract_openfoam_centerline_umax(probes_dir, "U") is None

    def test_field_file_exists_but_no_vectors(self, tmp_path: Path) -> None:
        """Field file with only headers/comments → found_any stays False → None."""
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        (probes_dir / "U").write_text(
            "# Probe 0 (0.5 0.05 0)\n# Time\n", encoding="utf-8"
        )
        assert extract_openfoam_centerline_umax(probes_dir, "U") is None

    def test_read_text_oserror_returns_none(self, tmp_path: Path) -> None:
        """I/O failure on the field file is caught and returned as None."""
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        field_file = probes_dir / "U"
        field_file.write_text("0.0 (1 0 0)\n", encoding="utf-8")

        original_read_text = Path.read_text

        def _raise(self: Path, *a: object, **kw: object) -> str:
            if self == field_file:
                raise OSError("simulated read failure")
            return original_read_text(self, *a, **kw)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise):
            assert extract_openfoam_centerline_umax(probes_dir, "U") is None


class TestExtractSU2SkinFrictionCoeff:
    """Coverage for extract_su2_skin_friction_coeff edge cases."""

    def test_cf_x_alias_column(self, tmp_path: Path) -> None:
        """Header alias 'Cf_x' should also resolve to the Cf column."""
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text(
            '"x","Cf_x"\n0.5,0.0030\n0.6,0.0050\n', encoding="utf-8"
        )
        cf = extract_su2_skin_friction_coeff(csv_path, method="average")
        assert cf is not None
        assert abs(cf - 0.004) < 1e-6

    def test_empty_csv_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("", encoding="utf-8")
        assert extract_su2_skin_friction_coeff(csv_path) is None

    def test_no_cf_column_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"x","y"\n0.5,0.0\n', encoding="utf-8")
        assert extract_su2_skin_friction_coeff(csv_path) is None

    def test_header_only_no_data_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"x","Cf"\n', encoding="utf-8")
        assert extract_su2_skin_friction_coeff(csv_path) is None

    def test_read_text_oserror_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"x","Cf"\n0.5,0.1\n', encoding="utf-8")
        original_read_text = Path.read_text

        def _raise(self: Path, *a: object, **kw: object) -> str:
            if self == csv_path:
                raise OSError("simulated")
            return original_read_text(self, *a, **kw)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise):
            assert extract_su2_skin_friction_coeff(csv_path) is None

    def test_malformed_row_skipped(self, tmp_path: Path) -> None:
        """A non-numeric Cf cell should be skipped, not crash."""
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text(
            '"x","Cf"\n0.5,n/a\n0.6,0.004\n', encoding="utf-8"
        )
        cf = extract_su2_skin_friction_coeff(csv_path, method="average")
        assert cf is not None
        assert abs(cf - 0.004) < 1e-6

    def test_trailing_edge_without_x_column_falls_back_to_average(
        self, tmp_path: Path
    ) -> None:
        """method='trailing_edge' but no x column → falls through to average."""
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text(
            '"Cf"\n0.002\n0.006\n', encoding="utf-8"
        )
        cf = extract_su2_skin_friction_coeff(csv_path, method="trailing_edge")
        assert cf is not None
        assert abs(cf - 0.004) < 1e-6


class TestExtractNaca0012CpSU2:
    """Coverage for extract_naca0012_cp_su2 (SU2 Cp distribution parser)."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surface_flow.csv"
        csv_path.write_text(
            '"Point_ID","x","y","Pressure_Coefficient"\n'
            "0,0.0,0.05,1.00\n"
            "1,0.5,0.04,-0.50\n"
            "2,1.0,0.001,-0.10\n",
            encoding="utf-8",
        )
        result = extract_naca0012_cp_su2(csv_path)
        assert result is not None
        xs, cps = result
        assert len(xs) == 3
        assert len(cps) == 3
        assert xs[0] == 0.0
        assert cps[1] == -0.50

    def test_cp_alias_pressure_coeff(self, tmp_path: Path) -> None:
        """Header alias 'Pressure_Coeff' should also resolve."""
        csv_path = tmp_path / "surface_flow.csv"
        csv_path.write_text(
            '"x","Pressure_Coeff"\n0.5,-0.4\n', encoding="utf-8"
        )
        result = extract_naca0012_cp_su2(csv_path)
        assert result is not None
        assert result[1][0] == -0.4

    def test_out_of_range_x_filtered(self, tmp_path: Path) -> None:
        """Points with x/c outside [−0.1, 1.1] are filtered out."""
        csv_path = tmp_path / "surface_flow.csv"
        csv_path.write_text(
            '"x","Pressure_Coefficient"\n'
            "0.5,0.3\n"        # kept
            "2.0,0.9\n"        # filtered (x > 1.1)
            "-0.5,0.9\n",      # filtered (x < -0.1)
            encoding="utf-8",
        )
        result = extract_naca0012_cp_su2(csv_path)
        assert result is not None
        assert len(result[0]) == 1

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_naca0012_cp_su2(tmp_path / "no.csv") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("", encoding="utf-8")
        assert extract_naca0012_cp_su2(csv_path) is None

    def test_no_x_column_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"y","Pressure_Coefficient"\n0.05,0.3\n', encoding="utf-8")
        assert extract_naca0012_cp_su2(csv_path) is None

    def test_no_cp_column_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"x","y"\n0.5,0.05\n', encoding="utf-8")
        assert extract_naca0012_cp_su2(csv_path) is None

    def test_only_invalid_points_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text(
            '"x","Pressure_Coefficient"\n'
            "2.0,0.9\n-0.5,0.9\n",   # all filtered out
            encoding="utf-8",
        )
        assert extract_naca0012_cp_su2(csv_path) is None

    def test_read_text_oserror_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sf.csv"
        csv_path.write_text('"x","Cp"\n0.5,0.3\n', encoding="utf-8")
        original_read_text = Path.read_text

        def _raise(self: Path, *a: object, **kw: object) -> str:
            if self == csv_path:
                raise OSError("simulated")
            return original_read_text(self, *a, **kw)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise):
            assert extract_naca0012_cp_su2(csv_path) is None


class TestExtractNaca0012CpOpenFOAM:
    """Coverage for extract_naca0012_cp_openfoam (forces CSV Cp parser)."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text(
            '"x","Cp"\n0.0,1.0\n0.5,-0.4\n1.0,-0.05\n', encoding="utf-8"
        )
        result = extract_naca0012_cp_openfoam(csv_path)
        assert result is not None
        xs, cps = result
        assert len(xs) == 3
        assert cps[1] == -0.4

    def test_pressure_coefficient_alias(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text(
            '"x","Pressure_Coefficient"\n0.5,-0.4\n', encoding="utf-8"
        )
        result = extract_naca0012_cp_openfoam(csv_path)
        assert result is not None
        assert result[1][0] == -0.4

    def test_out_of_range_x_filtered(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text(
            '"x","Cp"\n0.5,0.3\n5.0,0.9\n',   # second row filtered
            encoding="utf-8",
        )
        result = extract_naca0012_cp_openfoam(csv_path)
        assert result is not None
        assert len(result[0]) == 1

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_naca0012_cp_openfoam(tmp_path / "no.dat") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.dat"
        csv_path.write_text("", encoding="utf-8")
        assert extract_naca0012_cp_openfoam(csv_path) is None

    def test_missing_columns_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text('"y","Fx"\n0.05,1.0\n', encoding="utf-8")
        assert extract_naca0012_cp_openfoam(csv_path) is None

    def test_all_rows_filtered_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text('"x","Cp"\n-1.0,0.5\n2.0,0.5\n', encoding="utf-8")
        assert extract_naca0012_cp_openfoam(csv_path) is None

    def test_read_text_oserror_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "surfaceFields.dat"
        csv_path.write_text('"x","Cp"\n0.5,0.3\n', encoding="utf-8")
        original_read_text = Path.read_text

        def _raise(self: Path, *a: object, **kw: object) -> str:
            if self == csv_path:
                raise OSError("simulated")
            return original_read_text(self, *a, **kw)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise):
            assert extract_naca0012_cp_openfoam(csv_path) is None


class TestLoadLadsonReference:
    """Coverage for load_ladson_reference (Cp distribution reference loader)."""

    def test_valid_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ladson.csv"
        csv_path.write_text(
            "x/c,Cp\n0.0,1.0000\n0.025,-1.2140\n1.0,0.0300\n", encoding="utf-8"
        )
        result = load_ladson_reference(csv_path)
        assert result is not None
        xs, cps = result
        assert len(xs) == 3
        assert cps[0] == 1.0
        assert cps[1] == -1.214

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_ladson_reference(tmp_path / "no.csv") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("", encoding="utf-8")
        assert load_ladson_reference(csv_path) is None

    def test_header_only_returns_none(self, tmp_path: Path) -> None:
        """Header + zero data rows → empty x_list → None."""
        csv_path = tmp_path / "header_only.csv"
        csv_path.write_text("x/c,Cp\n", encoding="utf-8")
        assert load_ladson_reference(csv_path) is None

    def test_rows_with_short_columns_skipped(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "mixed.csv"
        csv_path.write_text(
            "x/c,Cp\n0.5\n0.6,-0.3\n",   # first data row has 1 col → skipped
            encoding="utf-8",
        )
        result = load_ladson_reference(csv_path)
        assert result is not None
        assert len(result[0]) == 1

    def test_read_text_oserror_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ladson.csv"
        csv_path.write_text("x/c,Cp\n0.5,0.3\n", encoding="utf-8")
        original_read_text = Path.read_text

        def _raise(self: Path, *a: object, **kw: object) -> str:
            if self == csv_path:
                raise OSError("simulated")
            return original_read_text(self, *a, **kw)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise):
            assert load_ladson_reference(csv_path) is None

    def test_actual_reference_file(self) -> None:
        """Load the real ladson1988.csv shipped in cases/validation/naca0012."""
        from cfdb.post.qoi_extractor import load_ladson_reference

        ref_path = (
            Path(__file__).parent.parent
            / "cases"
            / "validation"
            / "naca0012"
            / "reference"
            / "ladson1988.csv"
        )
        if not ref_path.exists():
            pytest.skip(f"reference file not found: {ref_path}")
        result = load_ladson_reference(ref_path)
        assert result is not None
        assert len(result[0]) > 10   # Ladson table has dozens of points
        assert result[0][0] == 0.0   # starts at LE


class TestIsFloat:
    """Coverage for the _is_float helper."""

    def test_integer_string(self) -> None:
        assert _is_float("100") is True

    def test_float_string(self) -> None:
        assert _is_float("0.005") is True

    def test_scientific_notation(self) -> None:
        assert _is_float("1.23e-5") is True

    def test_signed_string(self) -> None:
        assert _is_float("-1.214") is True

    def test_non_numeric_string(self) -> None:
        assert _is_float("probe_0") is False

    def test_empty_string(self) -> None:
        assert _is_float("") is False

    def test_none_like(self) -> None:
        """NaN/inf strings parse as float (Python behaviour) — this is the
        guard for time-dir name detection in extract_openfoam_centerline_umax."""
        assert _is_float("nan") is True
        assert _is_float("inf") is True
