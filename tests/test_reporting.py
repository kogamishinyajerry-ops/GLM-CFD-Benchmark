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
