"""ResultRepository Protocol — storage abstraction layer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cfdb.schema import MetricsResult, RunManifest


@runtime_checkable
class ResultRepository(Protocol):
    """Result storage abstraction.

    P0: JsonManifestRepository (JSON files).
    P2: SqliteRepository (SQLite database).
    Switching implementations requires zero business code changes.
    """

    def save_run(self, manifest: RunManifest, metrics: MetricsResult) -> None:
        """Save a run's manifest + metrics.

        Args:
            manifest: The run manifest.
            metrics: The metrics result.
        """
        ...

    def load_run(self, run_id: str) -> tuple[RunManifest, MetricsResult]:
        """Load a run's manifest + metrics by run_id.

        Args:
            run_id: The run identifier.

        Returns:
            Tuple of (RunManifest, MetricsResult).

        Raises:
            KeyError: If run_id does not exist.
        """
        ...

    def list_runs(self, case_id: str | None = None) -> list[RunManifest]:
        """List all runs, optionally filtered by case_id.

        Args:
            case_id: If provided, only return runs for this case.

        Returns:
            List of RunManifest, sorted by start_time descending (newest first).
        """
        ...
