"""Unit tests for QoI extraction."""

from __future__ import annotations

from pathlib import Path

from cfdb.post.qoi_extractor import (
    extract_openfoam_centerline_umax,
    extract_su2_skin_friction_coeff,
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
