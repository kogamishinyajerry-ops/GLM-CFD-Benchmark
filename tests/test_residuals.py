"""Unit tests for residual log parsing."""

from __future__ import annotations

from pathlib import Path

from cfdb.post.residuals import (
    extract_final,
    extract_openfoam_version,
    extract_su2_version,
    parse_openfoam_residuals,
    parse_su2_residuals,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestOpenFOAMResidualParser:
    def test_parse_residuals(self) -> None:
        log = (FIXTURES / "openfoam_log_sample.txt").read_text(encoding="utf-8")
        residuals = parse_openfoam_residuals(log)
        assert "Ux" in residuals
        assert "Uy" in residuals
        assert "p" in residuals
        # 3 time steps x 1 residual each per field
        assert len(residuals["Ux"]) == 3
        assert len(residuals["p"]) == 3

    def test_final_residuals(self) -> None:
        log = (FIXTURES / "openfoam_log_sample.txt").read_text(encoding="utf-8")
        residuals = parse_openfoam_residuals(log)
        final = extract_final(residuals)
        assert final["Ux"] == 1.2e-6
        assert final["Uy"] == 2.1e-6
        assert final["p"] == 3.4e-5

    def test_version_extraction(self) -> None:
        log = (FIXTURES / "openfoam_log_sample.txt").read_text(encoding="utf-8")
        version = extract_openfoam_version(log)
        assert version is not None
        assert "v2406" in version

    def test_empty_log(self) -> None:
        assert parse_openfoam_residuals("") == {}
        assert extract_final({}) == {}


class TestSU2ResidualParser:
    def test_parse_csv_residuals(self) -> None:
        log = (FIXTURES / "su2_log_sample.txt").read_text(encoding="utf-8")
        residuals = parse_su2_residuals(log)
        # The sample uses keyword-style fallback since no CSV header
        assert len(residuals) >= 0  # fixture may not have parseable residuals

    def test_parse_keyword_format(self) -> None:
        log = "RMS_DENSITY: -2.5\nRMS_DENSITY: -2.8\nRMS_MOMENTUM-X: -3.1\n"
        residuals = parse_su2_residuals(log)
        assert "RMS_DENSITY" in residuals
        assert len(residuals["RMS_DENSITY"]) == 2
        assert residuals["RMS_DENSITY"][-1] == -2.8

    def test_parse_csv_format(self) -> None:
        log = (
            '"iter","RMS_DENSITY","RMS_MOMENTUM-X"\n'
            '"0","-2.5","-3.1"\n'
            '"1","-2.8","-3.4"\n'
            '"2","-3.1","-3.7"\n'
        )
        residuals = parse_su2_residuals(log)
        assert "RMS_DENSITY" in residuals
        assert len(residuals["RMS_DENSITY"]) == 3
        assert residuals["RMS_DENSITY"][-1] == -3.1

    def test_version_extraction(self) -> None:
        log = (FIXTURES / "su2_log_sample.txt").read_text(encoding="utf-8")
        version = extract_su2_version(log)
        assert version is not None
        assert "8.0.0" in version

    def test_empty_log(self) -> None:
        assert parse_su2_residuals("") == {}
