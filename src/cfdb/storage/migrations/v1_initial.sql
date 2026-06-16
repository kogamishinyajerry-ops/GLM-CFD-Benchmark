-- CFD-Benchmark SQLite schema v1
-- Created: P2-a (Architecture v2.0)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    case_id              TEXT NOT NULL,
    solver               TEXT NOT NULL,
    backend              TEXT NOT NULL,
    status               TEXT NOT NULL,
    solver_version       TEXT,
    timing_wall_time_sec REAL,
    timing_start         TEXT,
    timing_end           TEXT,
    host                 TEXT,
    git_commit           TEXT,
    container_digest     TEXT,
    error                TEXT,
    cli_args_json        TEXT,
    cell_count           INTEGER,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_case_id    ON runs(case_id);
CREATE INDEX IF NOT EXISTS idx_runs_solver     ON runs(solver);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id        TEXT NOT NULL,
    metric_name   TEXT NOT NULL,
    metric_value  REAL,
    tolerance     REAL,
    pass          INTEGER,
    PRIMARY KEY (run_id, metric_name),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_residuals (
    run_id       TEXT NOT NULL,
    field_name   TEXT NOT NULL,
    final_value  REAL,
    PRIMARY KEY (run_id, field_name),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_steps (
    run_id        TEXT NOT NULL,
    step_index    INTEGER NOT NULL,
    step_name     TEXT NOT NULL,
    exit_code     INTEGER NOT NULL,
    wall_time_sec REAL,
    status        TEXT NOT NULL,
    PRIMARY KEY (run_id, step_index),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
