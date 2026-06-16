"""Tests for `cfdb report-sweep` CLI command (P2-c).

Architecture doc §9.1: ~3 tests (扫描 run / 渲染 / 空结果).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.cli import app
from cfdb.schema import (
    MetricsResult,
    RunManifest,
    TimingSpec,
)
from cfdb.storage.json_repo import JsonManifestRepository


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_sweep_manifest(
    run_id: str,
    case_id: str,
    solver: str,
    alpha: float,
    cl: float,
    cd: float,
) -> tuple[RunManifest, MetricsResult]:
    """Build a sweep run with cli_args.alpha and cl/cd QoIs."""
    m = RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        backend="local",
        status="success",
        timing=TimingSpec(
            wall_time_sec=10.0,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
        cli_args={"alpha": str(alpha)},
    )
    met = MetricsResult(
        qoi_relative_errors={"cl": 0.0, "cd": 0.0},
        qoi_computed_values={"cl": cl, "cd": cd},
    )
    return m, met


def _seed_sweep(
    runs_dir: Path,
    solver: str = "openfoam",
) -> list[str]:
    """Seed 4 NACA0012 sweep runs (alpha 0/5/10/15); return run_ids."""
    repo = JsonManifestRepository(runs_dir)
    runs = [
        ("20260101T000000Z_naca0012_a0_" + solver + "_aaaa0000", "naca0012_a0", 0.0, 0.001, 0.0120),
        ("20260101T000001Z_naca0012_a5_" + solver + "_bbbb1111", "naca0012_a5", 5.0, 0.543, 0.0125),
        ("20260101T000002Z_naca0012_a10_" + solver + "_cccc2222", "naca0012_a10", 10.0, 1.022, 0.0140),
        ("20260101T000003Z_naca0012_a15_" + solver + "_dddd3333", "naca0012_a15", 15.0, 1.473, 0.0190),
    ]
    run_ids: list[str] = []
    for run_id, case_id, alpha, cl, cd in runs:
        m, met = _make_sweep_manifest(run_id, case_id, solver, alpha, cl, cd)
        repo.save_run(m, met)
        run_ids.append(run_id)
    return run_ids


class TestReportSweepCommand:
    """P2-c: `cfdb report-sweep --case-id <prefix>`."""

    def test_report_sweep_renders(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Default invocation matches NACA0012 runs and writes an HTML report."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        _seed_sweep(runs_dir)

        out_path = tmp_path / "sweep.html"
        result = runner.invoke(
            app,
            [
                "report-sweep",
                "--case-id", "naca0012",
                "--runs-dir", str(runs_dir),
                "--out", str(out_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[OK]" in result.output
        assert "4 runs" in result.output
        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "naca0012" in html.lower() or "Sweep" in html

    def test_report_sweep_with_polar(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--polar flag adds a polar curve SVG to the HTML report."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        _seed_sweep(runs_dir, solver="openfoam")

        out_path = tmp_path / "sweep_polar.html"
        result = runner.invoke(
            app,
            [
                "report-sweep",
                "--case-id", "naca0012",
                "--runs-dir", str(runs_dir),
                "--out", str(out_path),
                "--polar",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8")
        # Polar SVG section present (renderer emits an <svg> with viewBox)
        assert "<svg" in html

    def test_report_sweep_empty(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """No matching runs exits with code 1 and a clear error message."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Seed unrelated runs
        repo = JsonManifestRepository(runs_dir)
        m = RunManifest(
            run_id="20260101T000000Z_other_case_openfoam_xyz",
            case_id="other_case",
            solver="openfoam",
            backend="local",
            status="success",
            timing=TimingSpec(
                wall_time_sec=1.0,
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            ),
        )
        repo.save_run(m, MetricsResult())

        result = runner.invoke(
            app,
            [
                "report-sweep",
                "--case-id", "naca0012",
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1
        assert "[FAIL]" in result.output
        assert "No runs matching" in result.output
