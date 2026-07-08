-- CFD-Benchmark SQLite schema v1 -> v2
-- Adds whole-JSON metrics column so MetricsResult round-trips losslessly.
-- Rationale: per-field columns silently reset v4 honesty fields
-- (ungated_qoi / budget_exceeded / qoi_absolute_errors / qoi_failed)
-- to defaults on load. A single serialized column tracks the Pydantic
-- schema without needing a migration per new field.

ALTER TABLE runs ADD COLUMN metrics_json TEXT;
