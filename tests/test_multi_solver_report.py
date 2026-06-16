"""Tests for cfdb.reporting.html multi-solver report (P2-c)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.reporting.html import generate_multi_solver_report
from cfdb.schema import (
    MetricsResult,
    RunManifest,
    TimingSpec,
)


def _make_manifest(
    run_id: str,
    case_id: str,
    solver: str,
    status: str = "success",
    cli_alpha: str | None = None,
) -> RunManifest:
    cli_args = {"alpha": cli_alpha} if cli_alpha else None
    return RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        backend="local",
        status=status,  # type: ignore[arg-type]
        timing=TimingSpec(
            wall_time_sec=1.5,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
        cli_args=cli_args,
    )


class TestGenerateMultiSolverReport:
    """Tests for generate_multi_solver_report."""

    def test_basic_report_with_one_run(self, tmp_path: Path) -> None:
        manifests = [_make_manifest("r1", "naca0012_a0", "openfoam", cli_alpha="0.0")]
        metrics = [MetricsResult(qoi_relative_errors={"cl": 0.01, "cd": 0.02})]
        out = tmp_path / "report.html"
        result = generate_multi_solver_report(manifests, metrics, out)
        assert result == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "Multi-Solver Comparison Report" in content
        assert "r1" in content
        assert "naca0012_a0" in content
        assert "openfoam" in content

    def test_multiple_runs_multiple_solvers(self, tmp_path: Path) -> None:
        manifests = [
            _make_manifest("r1", "naca0012_a0", "openfoam", cli_alpha="0.0"),
            _make_manifest("r2", "naca0012_a0", "su2", cli_alpha="0.0"),
            _make_manifest("r3", "naca0012_a5", "openfoam", cli_alpha="5.0"),
        ]
        metrics = [
            MetricsResult(qoi_relative_errors={"cl": 0.01}),
            MetricsResult(qoi_relative_errors={"cl": 0.02}),
            MetricsResult(qoi_relative_errors={"cl": 0.03}),
        ]
        out = tmp_path / "report.html"
        generate_multi_solver_report(manifests, metrics, out)
        content = out.read_text(encoding="utf-8")
        assert "3 run(s)" in content
        assert "r1" in content and "r2" in content and "r3" in content
        # QoI table has cl column
        assert "<th>cl</th>" in content

    def test_polar_svg_embedded(self, tmp_path: Path) -> None:
        manifests = [_make_manifest("r1", "naca0012_a0", "openfoam")]
        metrics = [MetricsResult()]
        out = tmp_path / "report.html"
        generate_multi_solver_report(
            manifests, metrics, out, polar_svg="<svg>fake polar</svg>"
        )
        content = out.read_text(encoding="utf-8")
        assert "Polar Curves" in content
        assert "fake polar" in content

    def test_cp_svg_embedded(self, tmp_path: Path) -> None:
        manifests = [_make_manifest("r1", "naca0012_a0", "openfoam")]
        metrics = [MetricsResult()]
        out = tmp_path / "report.html"
        generate_multi_solver_report(
            manifests, metrics, out, cp_svg="<svg>fake cp</svg>"
        )
        content = out.read_text(encoding="utf-8")
        assert "Cp Distribution Comparison" in content

    def test_empty_manifests_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            generate_multi_solver_report([], [], tmp_path / "x.html")

    def test_length_mismatch_raises(self, tmp_path: Path) -> None:
        manifests = [_make_manifest("r1", "x", "openfoam")]
        with pytest.raises(ValueError, match="same length"):
            generate_multi_solver_report(manifests, [], tmp_path / "x.html")

    def test_custom_title(self, tmp_path: Path) -> None:
        manifests = [_make_manifest("r1", "x", "openfoam")]
        metrics = [MetricsResult()]
        out = tmp_path / "report.html"
        generate_multi_solver_report(
            manifests, metrics, out, title="Custom Title Here"
        )
        content = out.read_text(encoding="utf-8")
        assert "Custom Title Here" in content
