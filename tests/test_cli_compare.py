"""Tests for `cfdb compare` CLI command (P2-c).

Architecture doc §9.1: ~4 tests (HTML 输出 / text 输出 / run 不存在 / 跨 case).
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


def _make_manifest(
    run_id: str,
    case_id: str = "naca0012_a0",
    solver: str = "openfoam",
    status: str = "success",
    cli_args: dict[str, str] | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        backend="local",
        status=status,  # type: ignore[arg-type]
        timing=TimingSpec(
            wall_time_sec=1.0,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
        cli_args=cli_args,
    )


def _seed_two_runs(
    runs_dir: Path,
    *,
    case_id1: str = "naca0012_a0",
    case_id2: str = "naca0012_a0",
) -> tuple[str, str]:
    """Seed runs_dir with two runs; return (run_id1, run_id2)."""
    repo = JsonManifestRepository(runs_dir)
    m1 = _make_manifest("20260101T000000Z_naca0012_a0_openfoam_abcd1234", case_id=case_id1, solver="openfoam")
    m2 = _make_manifest("20260101T000001Z_naca0012_a0_su2_efgh5678", case_id=case_id2, solver="su2")
    met1 = MetricsResult(qoi_relative_errors={"cl": 0.456, "cd": 0.012})
    met2 = MetricsResult(qoi_relative_errors={"cl": 0.460, "cd": 0.013})
    repo.save_run(m1, met1)
    repo.save_run(m2, met2)
    return m1.run_id, m2.run_id


class TestCompareCommand:
    """P2-c: `cfdb compare <run_id1> <run_id2>`."""

    def test_compare_html_output(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Default format (html) writes a .html file with comparison table."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        rid1, rid2 = _seed_two_runs(runs_dir)

        out_path = tmp_path / "compare.html"
        result = runner.invoke(
            app,
            [
                "compare", rid1, rid2,
                "--runs-dir", str(runs_dir),
                "--format", "html",
                "--out", str(out_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[OK]" in result.output
        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "cl" in html  # QoI name present
        assert rid1 in html or rid1[:20] in html  # run id referenced

    def test_compare_text_output(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--format text prints the comparison table to stdout."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        rid1, rid2 = _seed_two_runs(runs_dir)

        result = runner.invoke(
            app,
            [
                "compare", rid1, rid2,
                "--runs-dir", str(runs_dir),
                "--format", "text",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Comparing:" in result.output
        assert "cl" in result.output
        assert "cd" in result.output
        assert "Overall" in result.output

    def test_compare_run_not_found(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Missing run_id exits with code 1 and error message."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        rid1, _ = _seed_two_runs(runs_dir)

        result = runner.invoke(
            app,
            [
                "compare", rid1, "NONEXISTENT_RUN",
                "--runs-dir", str(runs_dir),
                "--format", "text",
            ],
        )
        assert result.exit_code == 1
        assert "[FAIL]" in result.output
        assert "NONEXISTENT_RUN" in result.output

    def test_compare_cross_case(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Cross-case comparison skips tolerance column gracefully."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        rid1, rid2 = _seed_two_runs(
            runs_dir, case_id1="naca0012_a0", case_id2="naca0012_a5"
        )

        result = runner.invoke(
            app,
            [
                "compare", rid1, rid2,
                "--runs-dir", str(runs_dir),
                "--format", "text",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "cross-case" in result.output
