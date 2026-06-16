"""Tests for cfdb.reporting.html."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cfdb.reporting.html import generate_html_report
from cfdb.schema import MetricsResult, RunManifest, TimingSpec


def make_test_manifest(status: str = "success") -> RunManifest:
    """Create a test RunManifest."""
    now = datetime.now(timezone.utc)
    return RunManifest(
        run_id="20260616T120000Z_test_generic_abcd1234",
        case_id="mock_success",
        solver="generic",
        backend="local",
        status=status,
        timing=TimingSpec(wall_time_sec=0.42, start_time=now, end_time=now),
        host="test-host",
        git_commit="abc1234",
        error=None if status == "success" else "Some error",
    )


def make_test_metrics() -> MetricsResult:
    """Create a test MetricsResult."""
    return MetricsResult(
        qoi_relative_errors={"centerline_umax": 0.005},
        qoi_pass=True,
        overall_status="pass",
        notes=["all good"],
    )


class TestGenerateHtmlReport:
    def test_generates_file(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        path = generate_html_report(manifest, metrics, tmp_path)
        assert path.exists()
        assert path.name == "report.html"

    def test_contains_run_id(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert manifest.run_id in content

    def test_contains_status(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "SUCCESS" in content

    def test_contains_case_id(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "mock_success" in content

    def test_contains_qoi_errors(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "centerline_umax" in content

    def test_contains_notes(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "all good" in content

    def test_contains_version(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "CFD-Benchmark v" in content

    def test_contains_inline_css(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "<style>" in content

    def test_failed_status(self, tmp_path: Path) -> None:
        manifest = make_test_manifest(status="failed")
        metrics = MetricsResult(overall_status="fail", qoi_pass=False)
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "FAILED" in content
        assert "Some error" in content

    def test_no_external_dependencies(self, tmp_path: Path) -> None:
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "cdn" not in content.lower()
        assert "http://" not in content
        assert "https://" not in content

    def test_embeds_residuals_svg(self, tmp_path: Path) -> None:
        """P2-a: residuals_svg is embedded into the HTML report."""
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        svg = '<svg viewBox="0 0 680 400"><polyline points="1,2 3,4"/></svg>'
        generate_html_report(manifest, metrics, tmp_path, residuals_svg=svg)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "<svg" in content
        assert "polyline" in content
        assert "Residual Convergence" in content

    def test_no_svg_section_without_data(self, tmp_path: Path) -> None:
        """P2-a: No SVG section when residuals_svg is None."""
        manifest = make_test_manifest()
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path, residuals_svg=None)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "<svg" not in content

    def test_solver_details_with_cell_count(self, tmp_path: Path) -> None:
        """P2-a: cell_count appears in Solver Details section."""
        manifest = make_test_manifest()
        manifest.cell_count = 400
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "Cell Count" in content
        assert "400" in content

    def test_solver_details_with_step_details(self, tmp_path: Path) -> None:
        """P2-a: step_details table renders."""
        manifest = make_test_manifest()
        manifest.step_details = [
            {"name": "block_mesh", "exit_code": 0, "wall_time_sec": 1.5, "status": "success"},
        ]
        metrics = make_test_metrics()
        generate_html_report(manifest, metrics, tmp_path)
        content = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "Step Details" in content
        assert "block_mesh" in content
