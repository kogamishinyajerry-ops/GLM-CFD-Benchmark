"""Tests for cfdb.storage.sqlite_repo.SqliteRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.schema import MetricsResult, RunManifest, TimingSpec
from cfdb.storage.sqlite_repo import SqliteRepository


def make_manifest(
    run_id: str = "test_run",
    case_id: str = "test",
    solver: str = "generic",
    status: str = "success",
    cell_count: int | None = None,
    step_details: list[dict] | None = None,
    final_residuals: dict[str, float] | None = None,
    residuals_history: dict[str, list[float]] | None = None,
) -> RunManifest:
    """Create a test RunManifest."""
    now = datetime.now(timezone.utc)
    timing = TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now)
    return RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        status=status,
        timing=timing,
        host="test-host",
        git_commit="abc1234",
        solver_version="TestSolver v1.0",
        final_residuals=final_residuals,
        cell_count=cell_count,
        step_details=step_details,
        residuals_history=residuals_history,
    )


def make_metrics(pass_val: bool = True) -> MetricsResult:
    """Create a test MetricsResult."""
    return MetricsResult(
        qoi_relative_errors={"centerline_umax": 0.01},
        qoi_pass=pass_val,
        overall_status="pass" if pass_val else "fail",
    )


class TestSqliteRepositoryInit:
    def test_init_creates_db_file(self, tmp_path: Path) -> None:
        """__init__ creates the SQLite file."""
        db_path = tmp_path / "test.db"
        repo = SqliteRepository(db_path)
        assert db_path.exists()
        repo.close()

    def test_init_creates_parent_dir(self, tmp_path: Path) -> None:
        """Parent directory is created automatically."""
        db_path = tmp_path / "nested" / "deep" / "test.db"
        repo = SqliteRepository(db_path)
        assert db_path.exists()
        repo.close()

    def test_migration_fresh_db(self, tmp_path: Path) -> None:
        """Fresh DB gets all tables created."""
        import sqlite3

        db_path = tmp_path / "test.db"
        repo = SqliteRepository(db_path)
        repo.close()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "schema_version" in tables
        assert "runs" in tables
        assert "run_metrics" in tables
        assert "run_residuals" in tables
        assert "run_steps" in tables

    def test_migration_version_recorded(self, tmp_path: Path) -> None:
        """schema_version table records the current version (2)."""
        import sqlite3

        db_path = tmp_path / "test.db"
        repo = SqliteRepository(db_path)
        repo.close()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT MAX(version) FROM schema_version")
        version = cursor.fetchone()[0]
        conn.close()

        assert version == 2

    def test_reopen_existing_db(self, tmp_path: Path) -> None:
        """Reopening an existing DB does not error."""
        db_path = tmp_path / "test.db"
        repo1 = SqliteRepository(db_path)
        repo1.save_run(make_manifest(), make_metrics())
        repo1.close()

        repo2 = SqliteRepository(db_path)
        manifest, _ = repo2.load_run("test_run")
        assert manifest.run_id == "test_run"
        repo2.close()


class TestSaveLoad:
    def test_save_and_load_basic(self, tmp_path: Path) -> None:
        """save_run -> load_run round-trip."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest(cell_count=400)
        metrics = make_metrics()
        repo.save_run(manifest, metrics)

        loaded_manifest, loaded_metrics = repo.load_run("test_run")
        assert loaded_manifest.run_id == "test_run"
        assert loaded_manifest.case_id == "test"
        assert loaded_manifest.solver == "generic"
        assert loaded_manifest.status == "success"
        assert loaded_manifest.cell_count == 400
        repo.close()

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        """load_run raises KeyError for non-existent run."""
        repo = SqliteRepository(tmp_path / "test.db")
        with pytest.raises(KeyError, match="not found"):
            repo.load_run("nonexistent")
        repo.close()

    def test_overwrite_run(self, tmp_path: Path) -> None:
        """Re-saving a run overwrites the old data."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest(status="success")
        repo.save_run(manifest, make_metrics())

        manifest2 = make_manifest(status="failed")
        repo.save_run(manifest2, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.status == "failed"
        repo.close()

    def test_save_with_metrics(self, tmp_path: Path) -> None:
        """Metrics are saved and loaded correctly."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest()
        metrics = MetricsResult(
            qoi_relative_errors={"qoi_a": 0.01, "qoi_b": 0.05},
            qoi_pass=True,
            overall_status="pass",
        )
        repo.save_run(manifest, metrics)

        _, loaded_metrics = repo.load_run("test_run")
        assert "qoi_a" in loaded_metrics.qoi_relative_errors
        assert "qoi_b" in loaded_metrics.qoi_relative_errors
        assert loaded_metrics.qoi_pass is True
        repo.close()

    def test_save_with_final_residuals(self, tmp_path: Path) -> None:
        """Final residuals are saved to run_residuals table."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest(
            final_residuals={"Ux": 1.2e-6, "Uy": 2.1e-6, "p": 3.4e-5}
        )
        repo.save_run(manifest, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.final_residuals is not None
        assert loaded.final_residuals["Ux"] == pytest.approx(1.2e-6)
        assert loaded.final_residuals["p"] == pytest.approx(3.4e-5)
        repo.close()

    def test_save_with_step_details(self, tmp_path: Path) -> None:
        """Step details are saved to run_steps table."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest(
            step_details=[
                {"name": "block_mesh", "exit_code": 0, "wall_time_sec": 1.5, "status": "success"},
                {"name": "solve", "exit_code": 0, "wall_time_sec": 10.0, "status": "success"},
            ]
        )
        repo.save_run(manifest, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.step_details is not None
        assert len(loaded.step_details) == 2
        assert loaded.step_details[0]["name"] == "block_mesh"
        assert loaded.step_details[1]["name"] == "solve"
        repo.close()

    def test_residuals_history_not_stored(self, tmp_path: Path) -> None:
        """residuals_history is None from SQLite (only in JSON)."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest(
            residuals_history={"Ux": [1e-1, 1e-2, 1e-3]}
        )
        repo.save_run(manifest, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.residuals_history is None  # Not stored in SQLite
        repo.close()

    def test_solver_version_stored(self, tmp_path: Path) -> None:
        """solver_version is stored in the runs table."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest()
        manifest.solver_version = "OpenFOAM v2406"
        repo.save_run(manifest, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.solver_version == "OpenFOAM v2406"
        repo.close()

    def test_cli_args_stored(self, tmp_path: Path) -> None:
        """cli_args are stored as JSON and recovered."""
        repo = SqliteRepository(tmp_path / "test.db")
        manifest = make_manifest()
        manifest.cli_args = {"case": "test", "solver": "generic"}
        repo.save_run(manifest, make_metrics())

        loaded, _ = repo.load_run("test_run")
        assert loaded.cli_args is not None
        assert loaded.cli_args["case"] == "test"
        repo.close()


class TestListRuns:
    def test_list_empty(self, tmp_path: Path) -> None:
        """Empty DB returns empty list."""
        repo = SqliteRepository(tmp_path / "test.db")
        assert repo.list_runs() == []
        repo.close()

    def test_list_all(self, tmp_path: Path) -> None:
        """List all runs without filter."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1", "case_a"), make_metrics())
        repo.save_run(make_manifest("run2", "case_b"), make_metrics())

        runs = repo.list_runs()
        assert len(runs) == 2
        repo.close()

    def test_filter_by_case_id(self, tmp_path: Path) -> None:
        """Filter by case_id."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1", "case_a"), make_metrics())
        repo.save_run(make_manifest("run2", "case_b"), make_metrics())
        repo.save_run(make_manifest("run3", "case_a"), make_metrics())

        runs = repo.list_runs(case_id="case_a")
        assert len(runs) == 2
        assert all(r.case_id == "case_a" for r in runs)
        repo.close()

    def test_filter_by_solver(self, tmp_path: Path) -> None:
        """Filter by solver."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1", solver="openfoam"), make_metrics())
        repo.save_run(make_manifest("run2", solver="su2"), make_metrics())

        runs = repo.list_runs(solver="openfoam")
        assert len(runs) == 1
        assert runs[0].solver == "openfoam"
        repo.close()

    def test_filter_by_status(self, tmp_path: Path) -> None:
        """Filter by status."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1", status="success"), make_metrics())
        repo.save_run(make_manifest("run2", status="failed"), make_metrics())

        runs = repo.list_runs(status="success")
        assert len(runs) == 1
        assert runs[0].status == "success"
        repo.close()

    def test_limit(self, tmp_path: Path) -> None:
        """Limit parameter controls result count."""
        repo = SqliteRepository(tmp_path / "test.db")
        for i in range(5):
            repo.save_run(make_manifest(f"run{i}"), make_metrics())

        runs = repo.list_runs(limit=3)
        assert len(runs) == 3
        repo.close()


class TestQueryMetrics:
    def test_query_by_metric_name(self, tmp_path: Path) -> None:
        """Query runs by metric name."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1", "case_a"), make_metrics())
        repo.save_run(make_manifest("run2", "case_b"), make_metrics())

        results = repo.query_metrics("centerline_umax")
        assert len(results) == 2
        assert "metric_value" in results[0]
        assert "run_id" in results[0]
        repo.close()

    def test_query_pass_only(self, tmp_path: Path) -> None:
        """Query only passing runs."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1"), make_metrics(pass_val=True))
        repo.save_run(make_manifest("run2"), make_metrics(pass_val=False))

        passing = repo.query_metrics("centerline_umax", tolerance_pass=True)
        assert len(passing) == 1
        assert passing[0]["pass"] == 1
        repo.close()

    def test_query_fail_only(self, tmp_path: Path) -> None:
        """Query only failing runs."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest("run1"), make_metrics(pass_val=True))
        repo.save_run(make_manifest("run2"), make_metrics(pass_val=False))

        failing = repo.query_metrics("centerline_umax", tolerance_pass=False)
        assert len(failing) == 1
        assert failing[0]["pass"] == 0
        repo.close()

    def test_query_nonexistent_metric(self, tmp_path: Path) -> None:
        """Query for a metric that doesn't exist returns empty."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest(), make_metrics())

        results = repo.query_metrics("nonexistent_qoi")
        assert results == []
        repo.close()


class TestMetricsJsonRoundTrip:
    """v2 fix: MetricsResult must round-trip losslessly through SQLite.

    Regression for the P3 integration finding where the read path silently
    reset v4 honesty fields (ungated_qoi / budget_exceeded / Stage-A
    qoi_absolute_errors / qoi_failed) to their defaults.
    """

    def _make_full_metrics(self) -> MetricsResult:
        """Build a MetricsResult with every field set to a non-default value.

        Stage-A fields (qoi_absolute_errors / qoi_failed) are included
        dynamically if the schema has grown them, so this test keeps biting
        as the model evolves.
        """
        kwargs: dict = {
            "qoi_relative_errors": {"cl": 0.02, "cd": 0.07},
            "qoi_pass": False,
            "overall_status": "fail",
            "notes": ["budget exceeded: wall time", "qoi 'cm' has no tolerance"],
            "qoi_computed_values": {"cl": 0.315, "cd": 0.0092},
            "ungated_qoi": ["cm"],
            "budget_exceeded": True,
        }
        if "qoi_absolute_errors" in MetricsResult.model_fields:
            kwargs["qoi_absolute_errors"] = {"cl": 0.005}
        if "qoi_failed" in MetricsResult.model_fields:
            kwargs["qoi_failed"] = ["cd"]
        return MetricsResult(**kwargs)

    def test_all_fields_round_trip(self, tmp_path: Path) -> None:
        """save_run -> load_run preserves every MetricsResult field."""
        repo = SqliteRepository(tmp_path / "test.db")
        original = self._make_full_metrics()
        repo.save_run(make_manifest(), original)

        _, loaded = repo.load_run("test_run")
        assert loaded.model_dump() == original.model_dump()
        repo.close()

    def test_honesty_fields_explicit(self, tmp_path: Path) -> None:
        """v4 honesty fields survive individually (not reset to defaults)."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest(), self._make_full_metrics())

        _, loaded = repo.load_run("test_run")
        assert loaded.ungated_qoi == ["cm"]
        assert loaded.budget_exceeded is True
        assert loaded.qoi_computed_values == {"cl": 0.315, "cd": 0.0092}
        assert loaded.notes == [
            "budget exceeded: wall time",
            "qoi 'cm' has no tolerance",
        ]
        repo.close()

    def test_round_trip_survives_reopen(self, tmp_path: Path) -> None:
        """Fields survive a full close/reopen cycle (persisted, not cached)."""
        db_path = tmp_path / "test.db"
        repo1 = SqliteRepository(db_path)
        original = self._make_full_metrics()
        repo1.save_run(make_manifest(), original)
        repo1.close()

        repo2 = SqliteRepository(db_path)
        _, loaded = repo2.load_run("test_run")
        assert loaded.model_dump() == original.model_dump()
        repo2.close()

    def test_computed_values_no_runs_root(self, tmp_path: Path) -> None:
        """qoi_computed_values round-trips via SQLite alone (no dual-write)."""
        repo = SqliteRepository(tmp_path / "test.db")  # no runs_root
        metrics = MetricsResult(
            qoi_relative_errors={"cl": 0.01},
            qoi_pass=True,
            overall_status="pass",
            qoi_computed_values={"cl": 0.32},
        )
        repo.save_run(make_manifest(), metrics)

        _, loaded = repo.load_run("test_run")
        assert loaded.qoi_computed_values == {"cl": 0.32}
        repo.close()

    def test_legacy_row_falls_back_to_run_metrics(self, tmp_path: Path) -> None:
        """Pre-v2 rows (metrics_json NULL) load via legacy reconstruction."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest(), make_metrics(pass_val=True))
        # Simulate a row written by pre-v2 code.
        repo._conn.execute(
            "UPDATE runs SET metrics_json = NULL WHERE run_id = ?", ("test_run",)
        )
        repo._conn.commit()

        _, loaded = repo.load_run("test_run")
        assert loaded.qoi_relative_errors == {"centerline_umax": 0.01}
        assert loaded.qoi_pass is True
        # Legacy rows honestly carry defaults (data was never stored).
        assert loaded.ungated_qoi == []
        assert loaded.budget_exceeded is False
        repo.close()

    def test_tamper_invalid_json_bites(self, tmp_path: Path) -> None:
        """Tamper witness: corrupt metrics_json must raise, never default."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest(), self._make_full_metrics())
        repo._conn.execute(
            "UPDATE runs SET metrics_json = ? WHERE run_id = ?",
            ('{"qoi_pass": tru', "test_run"),  # truncated / invalid JSON
        )
        repo._conn.commit()

        with pytest.raises(ValueError, match="corrupt metrics_json"):
            repo.load_run("test_run")
        repo.close()

    def test_tamper_unknown_field_bites(self, tmp_path: Path) -> None:
        """Tamper witness: injected unknown field is rejected (extra=forbid)."""
        repo = SqliteRepository(tmp_path / "test.db")
        repo.save_run(make_manifest(), self._make_full_metrics())
        repo._conn.execute(
            "UPDATE runs SET metrics_json = ? WHERE run_id = ?",
            ('{"qoi_pass": true, "injected_field": 1}', "test_run"),
        )
        repo._conn.commit()

        with pytest.raises(ValueError, match="corrupt metrics_json"):
            repo.load_run("test_run")
        repo.close()


class TestV1ToV2Migration:
    def _create_v1_db(self, db_path: Path) -> None:
        """Create a bare v1 database exactly as pre-v2 code would."""
        import sqlite3

        migrations_dir = (
            Path(__file__).parent.parent
            / "src"
            / "cfdb"
            / "storage"
            / "migrations"
        )
        sql = (migrations_dir / "v1_initial.sql").read_text(encoding="utf-8")
        conn = sqlite3.connect(str(db_path))
        conn.executescript(sql)
        conn.commit()
        conn.close()

    def test_v1_db_migrates_to_v2(self, tmp_path: Path) -> None:
        """Opening a v1 DB applies the v1->v2 migration."""
        import sqlite3

        db_path = tmp_path / "old.db"
        self._create_v1_db(db_path)

        repo = SqliteRepository(db_path)
        repo.close()

        conn = sqlite3.connect(str(db_path))
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        conn.close()

        assert version == 2
        assert "metrics_json" in columns

    def test_v1_db_save_load_after_migration(self, tmp_path: Path) -> None:
        """A migrated v1 DB supports the full v2 round-trip."""
        db_path = tmp_path / "old.db"
        self._create_v1_db(db_path)

        repo = SqliteRepository(db_path)
        metrics = MetricsResult(
            qoi_relative_errors={"cl": 0.01},
            qoi_pass=True,
            overall_status="pass",
            ungated_qoi=["cm"],
            budget_exceeded=True,
        )
        repo.save_run(make_manifest(), metrics)

        _, loaded = repo.load_run("test_run")
        assert loaded.ungated_qoi == ["cm"]
        assert loaded.budget_exceeded is True
        repo.close()


class TestDualWrite:
    def test_dual_write_json(self, tmp_path: Path) -> None:
        """When runs_root is provided, JSON manifest is also written."""
        runs_root = tmp_path / "runs"
        db_path = tmp_path / "test.db"
        repo = SqliteRepository(db_path, runs_root=runs_root)
        repo.save_run(make_manifest(), make_metrics())

        manifest_path = runs_root / "test_run" / "manifest.json"
        assert manifest_path.exists()

        metrics_path = runs_root / "test_run" / "metrics.json"
        assert metrics_path.exists()
        repo.close()

    def test_no_dual_write_without_runs_root(self, tmp_path: Path) -> None:
        """Without runs_root, no JSON is written."""
        db_path = tmp_path / "test.db"
        repo = SqliteRepository(db_path)
        repo.save_run(make_manifest(), make_metrics())

        # No runs directory created
        assert not (tmp_path / "test_run").exists()
        repo.close()
