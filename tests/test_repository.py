"""Tests for cfdb.storage.json_repo.JsonManifestRepository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.schema import MetricsResult, RunManifest, TimingSpec
from cfdb.storage.json_repo import JsonManifestRepository


def make_manifest(
    run_id: str = "test_run",
    case_id: str = "test",
    status: str = "success",
) -> RunManifest:
    """Create a test RunManifest."""
    now = datetime.now(timezone.utc)
    timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
    return RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver="generic",
        status=status,
        timing=timing,
    )


def make_metrics() -> MetricsResult:
    """Create a test MetricsResult."""
    return MetricsResult(
        qoi_relative_errors={"drag": 0.01},
        qoi_pass=True,
        overall_status="pass",
    )


class TestJsonManifestRepository:
    def test_save_and_load_run(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        manifest = make_manifest()
        metrics = make_metrics()
        repo.save_run(manifest, metrics)

        loaded_manifest, loaded_metrics = repo.load_run("test_run")
        assert loaded_manifest.run_id == "test_run"
        assert loaded_metrics.qoi_pass is True

    def test_load_nonexistent_run(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        with pytest.raises(KeyError, match="not found"):
            repo.load_run("nonexistent")

    def test_save_creates_files(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        repo = JsonManifestRepository(runs_root)
        repo.save_run(make_manifest(), make_metrics())

        run_dir = runs_root / "test_run"
        assert (run_dir / "manifest.json").exists()
        assert (run_dir / "metrics.json").exists()

    def test_manifest_json_content(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        repo = JsonManifestRepository(runs_root)
        repo.save_run(make_manifest(), make_metrics())

        manifest_data = json.loads(
            (runs_root / "test_run" / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest_data["run_id"] == "test_run"
        assert manifest_data["status"] == "success"

    def test_list_runs_empty(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        assert repo.list_runs() == []

    def test_list_runs_all(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        repo.save_run(make_manifest("run1", "case_a"), make_metrics())
        repo.save_run(make_manifest("run2", "case_b"), make_metrics())

        runs = repo.list_runs()
        assert len(runs) == 2

    def test_list_runs_filter_by_case(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        repo.save_run(make_manifest("run1", "case_a"), make_metrics())
        repo.save_run(make_manifest("run2", "case_b"), make_metrics())
        repo.save_run(make_manifest("run3", "case_a"), make_metrics())

        runs = repo.list_runs(case_id="case_a")
        assert len(runs) == 2
        assert all(r.case_id == "case_a" for r in runs)

    def test_list_runs_sorted_descending(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        repo.save_run(make_manifest("run1"), make_metrics())
        repo.save_run(make_manifest("run2"), make_metrics())

        runs = repo.list_runs()
        assert len(runs) == 2

    def test_overwrite_existing_run(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "runs")
        repo.save_run(make_manifest(), make_metrics())

        manifest2 = make_manifest()
        manifest2.status = "failed"
        repo.save_run(manifest2, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.status == "failed"

    def test_nonexistent_root_list(self, tmp_path: Path) -> None:
        repo = JsonManifestRepository(tmp_path / "nonexistent")
        assert repo.list_runs() == []
