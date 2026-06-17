"""P4.2 Web Dashboard tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cfdb.registry import CaseRegistry
from cfdb.schema import MetricsResult, RunManifest, TimingSpec
from cfdb.storage.json_repo import JsonManifestRepository
from cfdb.web import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_manifest() -> RunManifest:
    now = datetime(2025, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 6, 17, 12, 5, 0, tzinfo=timezone.utc)
    return RunManifest(
        run_id="20250617T120000Z_test_case_generic_abc12345",
        case_id="test_case",
        solver="generic",
        backend="local",
        status="success",
        timing=TimingSpec(wall_time_sec=12.345, start_time=now, end_time=end),
        host="testhost",
        git_commit="abc1234",
        cli_args={"case": "test_case", "solver": "generic"},
        cell_count=50000,
        residuals_history={"Ux": [1e-2, 1e-3, 1e-4, 1e-5], "p": [5e-3, 5e-4, 5e-5, 5e-6]},
        final_residuals={"Ux": 1e-5, "p": 5e-6},
        solver_version="1.0.0",
    )


@pytest.fixture
def sample_manifest2() -> RunManifest:
    now = datetime(2025, 6, 17, 12, 10, 0, tzinfo=timezone.utc)
    end = datetime(2025, 6, 17, 12, 17, 0, tzinfo=timezone.utc)
    return RunManifest(
        run_id="20250617T121000Z_test_case_other_xyz98765",
        case_id="test_case",
        solver="other",
        backend="local",
        status="success",
        timing=TimingSpec(wall_time_sec=25.678, start_time=now, end_time=end),
        host="testhost2",
        git_commit="def5678",
        cli_args={"case": "test_case", "solver": "other"},
        residuals_history={"Ux": [1e-2, 5e-4, 1e-5], "p": [1e-2, 1e-4, 1e-6]},
    )


@pytest.fixture
def sample_metrics() -> MetricsResult:
    return MetricsResult(
        qoi_relative_errors={"centerline_umax": 0.005},
        qoi_pass=True,
        overall_status="pass",
        notes=["Test note"],
    )


@pytest.fixture
def sample_metrics2() -> MetricsResult:
    return MetricsResult(
        qoi_relative_errors={"centerline_umax": 0.012},
        qoi_pass=True,
        overall_status="pass",
    )


@pytest.fixture
def tmp_runs_dir(
    tmp_path: Path, sample_manifest, sample_metrics, sample_manifest2, sample_metrics2
) -> Path:
    """Create a runs directory with pre-saved manifest + metrics."""
    repo = JsonManifestRepository(tmp_path / "runs")
    repo.save_run(sample_manifest, sample_metrics)
    repo.save_run(sample_manifest2, sample_metrics2)
    return tmp_path / "runs"


@pytest.fixture
def web_app(tmp_runs_dir, tmp_cases_root):
    """Create a FastAPI app for testing."""
    repo = JsonManifestRepository(tmp_runs_dir)
    registry = CaseRegistry(tmp_cases_root)
    return create_app(repo, registry, tmp_runs_dir, tmp_cases_root)


@pytest.fixture
def client(web_app):
    """TestClient for the web app."""
    return TestClient(web_app)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


def test_root_redirects_to_runs(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/runs"


# ---------------------------------------------------------------------------
# Run listing
# ---------------------------------------------------------------------------


def test_runs_page_renders(client):
    resp = client.get("/runs")
    assert resp.status_code == 200
    html = resp.text
    assert "Runs" in html
    assert "test_case" in html
    assert "generic" in html
    assert "other" in html


def test_runs_partial_htmx(client):
    resp = client.get("/partials/runs")
    assert resp.status_code == 200
    html = resp.text
    assert "test_case" in html
    # Should be just table rows, not a full page
    assert "<html" not in html.lower()


def test_runs_filter_by_case(client):
    resp = client.get("/runs?case_id=test_case")
    assert resp.status_code == 200
    assert "test_case" in resp.text


def test_runs_filter_by_solver(client):
    resp = client.get("/runs?solver=generic")
    assert resp.status_code == 200
    assert "generic" in resp.text
    assert "other" not in resp.text or "other" not in _extract_rows(resp.text)


def test_runs_filter_by_status(client):
    resp = client.get("/runs?status=success")
    assert resp.status_code == 200
    assert "success" in resp.text.lower()


# ---------------------------------------------------------------------------
# Run detail
# ---------------------------------------------------------------------------


def test_run_detail_renders(client, sample_manifest):
    resp = client.get(f"/runs/{sample_manifest.run_id}")
    assert resp.status_code == 200
    html = resp.text
    assert sample_manifest.run_id in html
    assert sample_manifest.case_id in html
    assert sample_manifest.solver in html
    assert "Metrics Results" in html


def test_run_detail_404(client):
    resp = client.get("/runs/nonexistent")
    assert resp.status_code == 404
    assert "does not exist" in resp.text


def test_residuals_svg_endpoint(client, sample_manifest):
    resp = client.get(f"/runs/{sample_manifest.run_id}/residuals.svg")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "svg" in content_type
    assert "<svg" in resp.text


def test_residuals_svg_empty_run(client, sample_manifest2):
    """Run2 has residual history, so it should return SVG."""
    resp = client.get(f"/runs/{sample_manifest2.run_id}/residuals.svg")
    assert resp.status_code == 200
    assert "<svg" in resp.text


# ---------------------------------------------------------------------------
# Case listing
# ---------------------------------------------------------------------------


def test_cases_page_renders(client):
    resp = client.get("/cases")
    assert resp.status_code == 200
    html = resp.text
    assert "Cases" in html
    assert "test_case" in html


def test_case_detail_renders(client):
    resp = client.get("/cases/test_case")
    assert resp.status_code == 200
    html = resp.text
    assert "Test Case" in html
    assert "Reynolds" in html


def test_case_detail_404(client):
    resp = client.get("/cases/nonexistent_case")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def test_compare_form_renders(client):
    resp = client.get("/compare")
    assert resp.status_code == 200
    assert "Compare Runs" in resp.text
    assert '<form' in resp.text.lower()


def test_compare_result_renders(client, sample_manifest, sample_manifest2):
    resp = client.get(
        f"/compare?run1={sample_manifest.run_id}&run2={sample_manifest2.run_id}"
    )
    assert resp.status_code == 200
    html = resp.text
    assert "QoI Differences" in html
    assert sample_manifest.run_id in html
    assert sample_manifest2.run_id in html


def test_compare_missing_run(client):
    resp = client.get("/compare?run1=nonexistent&run2=nonexistent2")
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def test_sweep_form_renders(client):
    resp = client.get("/sweep")
    assert resp.status_code == 200
    assert "Sweep Report" in resp.text


def test_sweep_valid_prefix(client):
    resp = client.get("/sweep/test_case")
    assert resp.status_code == 200
    assert "Run Summary" in resp.text
    assert "test_case" in resp.text


def test_sweep_invalid_prefix(client):
    resp = client.get("/sweep/no_match")
    assert resp.status_code == 200
    assert "No runs matching" in resp.text


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


def test_api_runs(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["case_id"] == "test_case"
    assert "run_id" in data[0]


def test_api_run_detail(client, sample_manifest):
    resp = client.get(f"/api/runs/{sample_manifest.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "manifest" in data
    assert "metrics" in data
    assert data["manifest"]["run_id"] == sample_manifest.run_id


def test_api_cases(client):
    resp = client.get("/api/cases")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    case_ids = [c["id"] for c in data]
    assert "test_case" in case_ids


# ---------------------------------------------------------------------------
# CLI serve command
# ---------------------------------------------------------------------------


def test_serve_command_help():
    """Test that cfdb serve --help works."""


    # Verify the command is registered on the app
    from cfdb.cli import app as cli_app

    # Check that serve is a known command
    cmd_names = [cmd.name for cmd in cli_app.registered_commands]
    assert "serve" in cmd_names


# ---------------------------------------------------------------------------
# Static export (basic check)
# ---------------------------------------------------------------------------


def test_static_export(tmp_path, tmp_runs_dir, tmp_cases_root):
    """Test that --export creates files."""
    from cfdb.cli import _export_static_site

    repo = JsonManifestRepository(tmp_runs_dir)
    registry = CaseRegistry(tmp_cases_root)
    export_dir = tmp_path / "export"

    _export_static_site(repo, registry, tmp_runs_dir, tmp_cases_root, export_dir, "json", None)

    # Check that files were created
    assert (export_dir / "index.html").exists()
    assert (export_dir / "runs").is_dir()
    assert (export_dir / "cases").is_dir()
    assert (export_dir / "api" / "runs.json").exists()
    assert (export_dir / "api" / "cases.json").exists()
    assert (export_dir / "static").is_dir()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_rows(html: str) -> str:
    """Extract text between the first two <tr> tags (approximate)."""
    start = html.find("<tr>")
    if start == -1:
        return ""
    end = html.find("</tr>", start)
    if end == -1:
        return ""
    return html[start : end + 5].lower()
