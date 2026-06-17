"""Tests for StarCCM mesh convergence — GCI, Richardson extrapolation, adapter integration.

Star-CCM+ case fixtures live in tests/conftest.py (P4.5b refactor).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.starccm import StarCCMAdapter
from cfdb.post.mesh_convergence import (
    MeshLevelResult,
    assess_convergence,
    compute_gci,
    extract_mesh_convergence_table,
    richardson_extrapolate,
)
from cfdb.schema import CaseSpec

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_conv_run_dir(tmp_path: Path) -> Path:
    """Create synthetic multi-level run directory with mesh_conv_summary.json."""
    run_dir = tmp_path / "run"
    levels_data = [
        (0, 0.160, 4000, 0.750, 0.0125),
        (1, 0.080, 32000, 0.810, 0.0110),
        (2, 0.040, 256000, 0.845, 0.0102),
        (3, 0.020, 2048000, 0.860, 0.0098),
    ]
    for level, base, cells, cl, cd in levels_data:
        level_dir = run_dir / f"level_{level:02d}" / "case"
        level_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "base_size_m": base,
            "cell_count": cells,
            "iters": 2000,
            "elapsed_sec": 30.0 + level * 60,
            "mach": 0.3,
            "aoa_deg": 5.0,
            "cl": cl,
            "cd": cd,
        }
        (level_dir / "mesh_conv_summary.json").write_text(
            json.dumps(summary), encoding="utf-8",
        )
    return run_dir


@pytest.fixture
def sample_levels() -> list[MeshLevelResult]:
    """Synthetic convergence data (Cl monotonically converging to ~0.86)."""
    return [
        MeshLevelResult(
            level=0, base_size=0.160, cell_count=4000, cl=0.750, cd=0.0125,
        ),
        MeshLevelResult(
            level=1, base_size=0.080, cell_count=32000, cl=0.810, cd=0.0110,
        ),
        MeshLevelResult(
            level=2, base_size=0.040, cell_count=256000, cl=0.845, cd=0.0102,
        ),
        MeshLevelResult(
            level=3, base_size=0.020, cell_count=2048000, cl=0.860, cd=0.0098,
        ),
    ]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtractMeshConvergenceTable:
    def test_extracts_all_levels(self, tmp_conv_run_dir: Path) -> None:
        results = extract_mesh_convergence_table(tmp_conv_run_dir)
        assert len(results) == 4
        assert results[0].base_size == 0.160
        assert results[-1].base_size == 0.020

    def test_returns_none_for_empty_run_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        results = extract_mesh_convergence_table(empty)
        assert results == []

    def test_skips_missing_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        (run_dir / "level_00" / "case").mkdir(parents=True)
        results = extract_mesh_convergence_table(run_dir)
        assert results == []

    def test_sorts_coarse_first(self, tmp_conv_run_dir: Path) -> None:
        results = extract_mesh_convergence_table(tmp_conv_run_dir)
        sizes = [r.base_size for r in results]
        assert sizes == sorted(sizes, reverse=True)


# ---------------------------------------------------------------------------
# GCI computation
# ---------------------------------------------------------------------------


class TestComputeGCI:
    def test_four_levels_cl(self, sample_levels: list[MeshLevelResult]) -> None:
        pairs = compute_gci(sample_levels, qoi="cl")
        assert len(pairs) == 3
        for pair in pairs:
            assert pair.refinement_ratio == pytest.approx(2.0, rel=1e-6)
        assert pairs[0].gci_fine > pairs[2].gci_fine

    def test_gci_decreases_with_refinement(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        pairs = compute_gci(sample_levels, qoi="cl")
        gcis = [p.gci_fine for p in pairs]
        assert gcis[0] > gcis[-1], f"GCI should decrease: {gcis}"

    def test_gci_symmetric_convergence(self) -> None:
        levels = [
            MeshLevelResult(level=0, base_size=0.160, cell_count=1000, cl=0.400),
            MeshLevelResult(level=1, base_size=0.080, cell_count=8000, cl=0.450),
            MeshLevelResult(level=2, base_size=0.040, cell_count=64000, cl=0.475),
            MeshLevelResult(level=3, base_size=0.020, cell_count=512000, cl=0.4875),
        ]
        pairs = compute_gci(levels, qoi="cl")
        for p in pairs:
            assert p.gci_fine > 0
            assert p.gci_fine < 0.5

    def test_two_levels_uses_formal_order(self) -> None:
        levels = [
            MeshLevelResult(level=0, base_size=0.080, cell_count=1000, cl=0.80),
            MeshLevelResult(level=1, base_size=0.040, cell_count=8000, cl=0.84),
        ]
        pairs = compute_gci(levels, qoi="cl", order_of_accuracy=2.0)
        assert len(pairs) == 1
        assert pairs[0].order == 2.0

    def test_skips_missing_values(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        sample_levels[2].cl = None
        pairs = compute_gci(sample_levels, qoi="cl")
        assert len(pairs) == 1

    def test_cd_convergence(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        pairs = compute_gci(sample_levels, qoi="cd")
        assert len(pairs) == 3
        assert all(p.epsilon is not None for p in pairs)


# ---------------------------------------------------------------------------
# Richardson extrapolation
# ---------------------------------------------------------------------------


class TestRichardsonExtrapolate:
    def test_extrapolates_cl(self, sample_levels: list[MeshLevelResult]) -> None:
        result = richardson_extrapolate(sample_levels, qoi="cl")
        assert result is not None
        finest = sample_levels[-1].cl
        assert finest is not None
        assert result > finest

    def test_two_levels(self) -> None:
        levels = [
            MeshLevelResult(level=0, base_size=0.080, cell_count=1000, cl=0.80),
            MeshLevelResult(level=1, base_size=0.040, cell_count=8000, cl=0.84),
        ]
        result = richardson_extrapolate(levels, qoi="cl")
        assert result is not None
        assert result > 0.84

    def test_single_level_returns_none(self) -> None:
        levels = [
            MeshLevelResult(level=0, base_size=0.080, cell_count=1000, cl=0.80),
        ]
        result = richardson_extrapolate(levels, qoi="cl")
        assert result is None

    def test_missing_values_returns_none(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        sample_levels[-1].cl = None
        result = richardson_extrapolate(sample_levels, qoi="cl")
        assert result is None


# ---------------------------------------------------------------------------
# Convergence assessment
# ---------------------------------------------------------------------------


class TestAssessConvergence:
    def test_converged_sequence_passes(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        pairs = compute_gci(sample_levels, qoi="cl")
        report = assess_convergence(pairs, gci_threshold=0.20)
        assert report.is_converged["cl"] is True

    def test_diverged_sequence_fails(self) -> None:
        levels = [
            MeshLevelResult(level=0, base_size=0.160, cell_count=1000, cl=0.30),
            MeshLevelResult(level=1, base_size=0.080, cell_count=8000, cl=0.50),
            MeshLevelResult(level=2, base_size=0.040, cell_count=64000, cl=0.60),
        ]
        pairs = compute_gci(levels, qoi="cl")
        report = assess_convergence(pairs, gci_threshold=0.01)
        assert report.is_converged["cl"] is False

    def test_empty_pairs(self) -> None:
        report = assess_convergence([], gci_threshold=0.05)
        assert report.is_converged == {"cl": False, "cd": False}
        assert report.recommended_level is None

    def test_recommends_finest_when_converged(
        self, sample_levels: list[MeshLevelResult],
    ) -> None:
        pairs = compute_gci(sample_levels, qoi="cl")
        report = assess_convergence(pairs, gci_threshold=0.20)
        assert report.recommended_level is not None


# ---------------------------------------------------------------------------
# Adapter integration — uses conftest fixtures
# ---------------------------------------------------------------------------


class TestPrepareMeshConvergence:
    def _adapter(self) -> StarCCMAdapter:
        return StarCCMAdapter(dry_run=True)

    def test_prepare_creates_macros_at_all_levels(
        self,
        starccm_naca_mesh_conv_case: CaseSpec,
        tmp_path: Path,
    ) -> None:
        adapter = self._adapter()
        run_dir = tmp_path / "run"
        case_dir = tmp_path / "case"
        case_dir.mkdir(parents=True)
        (case_dir / "naca0012.stl").touch()

        with patch.object(adapter, "_is_naca_case", return_value=True):
            macros = adapter.prepare_mesh_convergence(
                starccm_naca_mesh_conv_case, case_dir, run_dir,
            )

        assert len(macros) == 4
        for path in macros:
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "class RunCase extends StarMacro" in content

    def test_prepare_respects_level_subset(
        self,
        starccm_naca_mesh_conv_case: CaseSpec,
        tmp_path: Path,
    ) -> None:
        adapter = self._adapter()
        run_dir = tmp_path / "run"
        case_dir = tmp_path / "case"
        case_dir.mkdir(parents=True)

        with patch.object(adapter, "_is_naca_case", return_value=True):
            macros = adapter.prepare_mesh_convergence(
                starccm_naca_mesh_conv_case, case_dir, run_dir,
                levels=["mesh_160", "mesh_40"],
            )

        assert len(macros) == 2

    def test_template_renders_base_size(
        self,
        starccm_naca_mesh_conv_case: CaseSpec,
        tmp_path: Path,
    ) -> None:
        adapter = self._adapter()
        run_dir = tmp_path / "run"
        case_dir = tmp_path / "case"
        case_dir.mkdir()

        with patch.object(adapter, "_is_naca_case", return_value=True):
            macros = adapter.prepare_mesh_convergence(
                starccm_naca_mesh_conv_case, case_dir, run_dir,
                levels=["mesh_160"],
            )

        content = macros[0].read_text(encoding="utf-8")
        assert "0.16" in content or "BASE" in content
        assert "PRISM" in content.upper()
