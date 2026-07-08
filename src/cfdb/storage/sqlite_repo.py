"""SQLite implementation of ResultRepository."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cfdb.schema import MetricsResult, RunManifest, TimingSpec

logger = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 2
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class SqliteRepository:
    """SQLite implementation of ResultRepository.

    Uses 5 relational tables (runs, run_metrics, run_residuals, run_steps,
    schema_version) for efficient cross-run querying.

    Satisfies the ResultRepository Protocol via structural subtyping
    (duck typing) — does not inherit the Protocol class explicitly.

    Storage:
        Single SQLite file at db_path (default: runs/cfdb.db).

    Migration:
        Auto-runs on __init__. Versioned SQL scripts in migrations/.

    Dual-write:
        When ``runs_root`` is provided, also writes JSON manifest to
        ``runs_root/<run_id>/manifest.json`` (architecture §10.1 decision A).
    """

    def __init__(
        self,
        db_path: Path,
        runs_root: Path | None = None,
    ) -> None:
        """Initialize SQLite repository, running migrations if needed.

        Args:
            db_path: Path to the SQLite database file.
            runs_root: Root directory for JSON dual-write (optional).
                       If provided, manifest.json is also written per run.
        """
        self._db_path = db_path
        self._runs_root = runs_root
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        """Run pending migrations to bring schema to _CURRENT_SCHEMA_VERSION."""
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_version'"
        )
        if cursor.fetchone() is None:
            self._execute_sql_file(_MIGRATIONS_DIR / "v1_initial.sql")
            logger.info("SQLite schema v1 initialized at %s", self._db_path)
            # Fall through: fresh DBs must still apply v1 -> vN migrations.

        cursor = self._conn.execute("SELECT MAX(version) as v FROM schema_version")
        row = cursor.fetchone()
        current_version = row["v"] if row and row["v"] else 0

        while current_version < _CURRENT_SCHEMA_VERSION:
            next_version = current_version + 1
            migration_file = (
                _MIGRATIONS_DIR / f"migrate_v{current_version}_to_v{next_version}.sql"
            )
            if not migration_file.exists():
                logger.warning(
                    "migration script not found: %s (staying at v%d)",
                    migration_file,
                    current_version,
                )
                break
            self._execute_sql_file(migration_file)
            self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (next_version, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
            logger.info("SQLite migrated v%d -> v%d", current_version, next_version)
            current_version = next_version

    def _execute_sql_file(self, path: Path) -> None:
        """Execute all SQL statements in a file.

        Args:
            path: Path to the .sql file.
        """
        sql = path.read_text(encoding="utf-8")
        self._conn.executescript(sql)
        self._conn.commit()

    # ========== ResultRepository Protocol methods ==========

    def save_run(self, manifest: RunManifest, metrics: MetricsResult) -> None:
        """Save a run's manifest + metrics to SQLite (and dual-write JSON).

        Args:
            manifest: The run manifest.
            metrics: The metrics result.
        """
        now = datetime.now(timezone.utc).isoformat()
        cli_args_json = json.dumps(manifest.cli_args) if manifest.cli_args else None

        self._conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, case_id, solver, backend, status, solver_version,
                timing_wall_time_sec, timing_start, timing_end, host,
                git_commit, container_digest, error, cli_args_json,
                cell_count, created_at, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.run_id,
                manifest.case_id,
                manifest.solver,
                manifest.backend,
                manifest.status,
                manifest.solver_version,
                manifest.timing.wall_time_sec,
                manifest.timing.start_time.isoformat(),
                manifest.timing.end_time.isoformat(),
                manifest.host,
                manifest.git_commit,
                manifest.container_digest,
                manifest.error,
                cli_args_json,
                manifest.cell_count,
                now,
                # v2: whole-model JSON so every MetricsResult field (incl.
                # honesty fields like ungated_qoi / budget_exceeded) survives
                # the SQLite round-trip without a column per field.
                metrics.model_dump_json(),
            ),
        )

        # Save metrics
        self._conn.execute(
            "DELETE FROM run_metrics WHERE run_id = ?", (manifest.run_id,)
        )
        for qoi_name, error_val in metrics.qoi_relative_errors.items():
            self._conn.execute(
                """
                INSERT INTO run_metrics (run_id, metric_name, metric_value, tolerance, pass)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    manifest.run_id,
                    qoi_name,
                    error_val,
                    None,
                    1 if metrics.qoi_pass else 0,
                ),
            )

        # Save final residuals
        self._conn.execute(
            "DELETE FROM run_residuals WHERE run_id = ?", (manifest.run_id,)
        )
        if manifest.final_residuals:
            for field_name, value in manifest.final_residuals.items():
                self._conn.execute(
                    """
                    INSERT INTO run_residuals (run_id, field_name, final_value)
                    VALUES (?, ?, ?)
                    """,
                    (manifest.run_id, field_name, value),
                )

        # Save step details
        self._conn.execute(
            "DELETE FROM run_steps WHERE run_id = ?", (manifest.run_id,)
        )
        if manifest.step_details:
            for idx, step in enumerate(manifest.step_details):
                self._conn.execute(
                    """
                    INSERT INTO run_steps
                        (run_id, step_index, step_name, exit_code, wall_time_sec, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.run_id,
                        idx,
                        step.get("name", ""),
                        step.get("exit_code", -1),
                        step.get("wall_time_sec"),
                        step.get("status", "unknown"),
                    ),
                )

        self._conn.commit()
        logger.debug("saved run %s to SQLite", manifest.run_id)

        # P2-a dual-write: also write JSON manifest if runs_root is set
        if self._runs_root is not None:
            run_dir = self._runs_root / manifest.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            metrics_path = run_dir / "metrics.json"
            metrics_path.write_text(
                metrics.model_dump_json(indent=2), encoding="utf-8"
            )
            logger.debug("dual-write JSON manifest to %s", run_dir)

    def load_run(self, run_id: str) -> tuple[RunManifest, MetricsResult]:
        """Load a run's manifest + metrics by run_id.

        Args:
            run_id: The run identifier.

        Returns:
            Tuple of (RunManifest, MetricsResult).

        Raises:
            KeyError: If run_id does not exist.
        """
        cursor = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"run '{run_id}' not found in SQLite database")

        manifest = self._row_to_manifest(row)
        metrics = self._load_metrics(run_id)
        return manifest, metrics

    def list_runs(
        self,
        case_id: str | None = None,
        solver: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[RunManifest]:
        """List runs with optional filtering.

        Args:
            case_id: Filter by case ID (exact match).
            solver: Filter by solver name (exact match).
            status: Filter by run status (exact match).
            since: ISO 8601 datetime string, only runs at or after this time.
            until: ISO 8601 datetime string, only runs at or before this time.
            limit: Maximum number of runs to return (default 100).

        Returns:
            List of RunManifest, sorted by created_at descending (newest first).
        """
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []

        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        if solver:
            query += " AND solver = ?"
            params.append(solver)
        if status:
            query += " AND status = ?"
            params.append(status)
        if since:
            query += " AND timing_start >= ?"
            params.append(since)
        if until:
            query += " AND timing_end <= ?"
            params.append(until)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()
        return [self._row_to_manifest(row) for row in rows]

    def query_metrics(
        self,
        metric_name: str,
        tolerance_pass: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Query runs by metric name, optionally filtering by pass/fail.

        Args:
            metric_name: The QoI metric name to query (e.g. 'centerline_umax').
            tolerance_pass: If True, only return passing runs.
                           If False, only return failing runs.
                           If None, return all.

        Returns:
            List of dicts, each with keys:
            run_id, case_id, solver, metric_value, pass.
        """
        query = """
            SELECT r.run_id, r.case_id, r.solver, m.metric_value, m.pass
            FROM run_metrics m
            JOIN runs r ON m.run_id = r.run_id
            WHERE m.metric_name = ?
        """
        params: list[Any] = [metric_name]

        if tolerance_pass is not None:
            query += " AND m.pass = ?"
            params.append(1 if tolerance_pass else 0)

        query += " ORDER BY m.metric_value ASC"
        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ========== Private helpers ==========

    def _row_to_manifest(self, row: sqlite3.Row) -> RunManifest:
        """Convert a runs table row to a RunManifest.

        Note: full residuals_history is not stored in SQLite columns.
        It remains None when loaded from SQLite (use JSON for that).
        step_details and final_residuals are loaded from child tables.

        Args:
            row: A sqlite3.Row from the runs table.

        Returns:
            A RunManifest reconstructed from the database row.
        """
        cli_args = json.loads(row["cli_args_json"]) if row["cli_args_json"] else None

        # Load final residuals from run_residuals table
        cursor = self._conn.execute(
            "SELECT field_name, final_value FROM run_residuals WHERE run_id = ?",
            (row["run_id"],),
        )
        final_residuals = (
            {r["field_name"]: r["final_value"] for r in cursor.fetchall()} or None
        )

        # Load step details from run_steps table
        cursor = self._conn.execute(
            "SELECT step_name, exit_code, wall_time_sec, status "
            "FROM run_steps WHERE run_id = ? ORDER BY step_index",
            (row["run_id"],),
        )
        step_details = (
            [
                {
                    "name": r["step_name"],
                    "exit_code": r["exit_code"],
                    "wall_time_sec": r["wall_time_sec"],
                    "status": r["status"],
                }
                for r in cursor.fetchall()
            ]
            or None
        )

        return RunManifest(
            run_id=row["run_id"],
            case_id=row["case_id"],
            solver=row["solver"],
            backend=row["backend"],
            status=row["status"],
            timing=TimingSpec(
                wall_time_sec=row["timing_wall_time_sec"] or 0.0,
                start_time=datetime.fromisoformat(row["timing_start"]),
                end_time=datetime.fromisoformat(row["timing_end"]),
            ),
            host=row["host"],
            artifacts={},  # Not stored in SQLite (use JSON for full artifacts)
            git_commit=row["git_commit"],
            container_digest=row["container_digest"],
            error=row["error"],
            cli_args=cli_args,
            dry_run_skipped_commands=None,
            solver_version=row["solver_version"],
            final_residuals=final_residuals,
            cell_count=row["cell_count"],
            step_details=step_details,
            residuals_history=None,  # Not stored in SQLite (use JSON)
        )

    def _load_metrics(self, run_id: str) -> MetricsResult:
        """Load metrics for a run.

        Schema v2: the full MetricsResult is persisted as JSON in
        runs.metrics_json, so every field (qoi_relative_errors,
        qoi_computed_values, ungated_qoi, budget_exceeded, and any future
        field with a default) round-trips losslessly. If metrics_json is
        present but invalid, we raise instead of silently returning
        defaults (fail-closed: corrupt honesty data must bite).

        Legacy rows (saved before v2, metrics_json is NULL) fall back to
        the old per-field reconstruction from run_metrics plus the
        P3-hotfix dual-write JSON read for qoi_computed_values.

        Args:
            run_id: The run identifier.

        Returns:
            MetricsResult reconstructed from metrics_json (v2) or from the
            run_metrics table (legacy fallback).

        Raises:
            ValueError: If metrics_json exists but cannot be parsed into a
                valid MetricsResult (corruption / tampering).
        """
        cursor = self._conn.execute(
            "SELECT metrics_json FROM runs WHERE run_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        if row is not None and row["metrics_json"]:
            try:
                return MetricsResult.model_validate_json(row["metrics_json"])
            except ValidationError as e:
                raise ValueError(
                    f"corrupt metrics_json for run '{run_id}': "
                    "refusing to substitute default metrics"
                ) from e

        # Legacy fallback (pre-v2 rows): reconstruct from run_metrics table.
        cursor = self._conn.execute(
            "SELECT metric_name, metric_value, pass FROM run_metrics WHERE run_id = ?",
            (run_id,),
        )
        rows = cursor.fetchall()
        qoi_errors = {r["metric_name"]: r["metric_value"] for r in rows}
        all_pass = all(r["pass"] for r in rows) if rows else False

        # P3-hotfix: read qoi_computed_values from dual-write JSON
        qoi_computed: dict[str, float] | None = None
        if self._runs_root is not None:
            metrics_json_path = self._runs_root / run_id / "metrics.json"
            if metrics_json_path.exists():
                try:
                    raw = metrics_json_path.read_text(encoding="utf-8")
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed.get("qoi_computed_values"):
                        qoi_computed = dict(parsed["qoi_computed_values"])
                except (json.JSONDecodeError, OSError, TypeError) as e:
                    logger.debug(
                        "could not read qoi_computed_values from %s: %s",
                        metrics_json_path,
                        e,
                    )

        return MetricsResult(
            qoi_relative_errors=qoi_errors,
            qoi_pass=all_pass,
            overall_status="pass" if all_pass else ("fail" if rows else "unknown"),
            notes=[],
            qoi_computed_values=qoi_computed,
        )


# Protocol compliance marker (structural subtyping — no explicit inheritance)
_ResultRepository = SqliteRepository  # type: ignore[assignment]
