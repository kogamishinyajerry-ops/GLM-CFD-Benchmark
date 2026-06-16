"""Tests for cfdb.post.mesh_stats — cell count extraction."""

from __future__ import annotations

from pathlib import Path

from cfdb.post.mesh_stats import extract_openfoam_cell_count, extract_su2_cell_count

_FIXTURES = Path(__file__).parent / "fixtures"


class TestOpenFOAMCellCount:
    def test_extract_from_fixture(self) -> None:
        """Extract cell count from the real blockMesh log fixture."""
        log = (_FIXTURES / "openfoam_blockmesh_log.txt").read_text(encoding="utf-8")
        result = extract_openfoam_cell_count(log)
        assert result == 400

    def test_extract_inline(self) -> None:
        """Extract from inline text."""
        log = "some output\nnCells: 1234;\nmore output"
        assert extract_openfoam_cell_count(log) == 1234

    def test_extract_case_insensitive(self) -> None:
        """Pattern should be case-insensitive."""
        log = "ncells: 5678"
        assert extract_openfoam_cell_count(log) == 5678

    def test_extract_large_count(self) -> None:
        """Large cell counts."""
        log = "nCells: 1000000"
        assert extract_openfoam_cell_count(log) == 1000000

    def test_not_found(self) -> None:
        """Return None when no match."""
        assert extract_openfoam_cell_count("no cell info here") is None

    def test_empty_string(self) -> None:
        """Empty string returns None."""
        assert extract_openfoam_cell_count("") is None


class TestSU2CellCount:
    def test_extract_from_fixture(self) -> None:
        """Extract cell count from the real SU2 mesh stats fixture."""
        log = (_FIXTURES / "su2_mesh_stats_log.txt").read_text(encoding="utf-8")
        result = extract_su2_cell_count(log)
        assert result == 33024

    def test_extract_with_commas(self) -> None:
        """Comma-separated numbers handled correctly."""
        log = "1,234,567 volume elements."
        assert extract_su2_cell_count(log) == 1234567

    def test_extract_no_commas(self) -> None:
        """No commas."""
        log = "33024 volume elements."
        assert extract_su2_cell_count(log) == 33024

    def test_extract_case_insensitive(self) -> None:
        """Case-insensitive match."""
        log = "1000 Volume Elements."
        assert extract_su2_cell_count(log) == 1000

    def test_not_found(self) -> None:
        """Return None when no match."""
        assert extract_su2_cell_count("no mesh info") is None

    def test_empty_string(self) -> None:
        """Empty string returns None."""
        assert extract_su2_cell_count("") is None
