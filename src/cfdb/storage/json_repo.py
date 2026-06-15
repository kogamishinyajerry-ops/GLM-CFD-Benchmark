"""JSON file implementation of ResultRepository."""

from __future__ import annotations

import logging
from pathlib import Path

from cfdb.schema import MetricsResult, RunManifest

logger = logging.getLogger(__name__)


class JsonManifestRepository:
    """JSON file implementation of ResultRepository.

    Storage structure:
        runs/<run_id>/manifest.json    <- RunManifest serialized
        runs/<run_id>/metrics.json     <- MetricsResult serialized

    When switching to SqliteRepository in P2, only a new implementation class
    is needed; Runner / CLI code remains unchanged.
    """

    def __init__(self, runs_root: Path) -> None:
        """Initialize the repository.

        Args:
            runs_root: Root path of the runs directory (e.g. repository root's runs/).
        """
        self._root: Path = runs_root

    def save_run(self, manifest: RunManifest, metrics: MetricsResult) -> None:
        """Save a run's manifest + metrics to JSON files.

        Args:
            manifest: The run manifest.
            metrics: The metrics result.
        """
        run_dir = self._root / manifest.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        metrics_path = run_dir / "metrics.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        metrics_path.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("saved run %s to %s", manifest.run_id, run_dir)

    def load_run(self, run_id: str) -> tuple[RunManifest, MetricsResult]:
        """Load a run's manifest + metrics by run_id.

        Args:
            run_id: The run identifier.

        Returns:
            Tuple of (RunManifest, MetricsResult).

        Raises:
            KeyError: If run_id does not exist.
        """
        run_dir = self._root / run_id
        manifest_path = run_dir / "manifest.json"
        metrics_path = run_dir / "metrics.json"
        if not manifest_path.exists():
            raise KeyError(f"run '{run_id}' not found at {run_dir}")
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        metrics = MetricsResult.model_validate_json(metrics_path.read_text(encoding="utf-8"))
        return manifest, metrics

    def list_runs(self, case_id: str | None = None) -> list[RunManifest]:
        """List all runs, optionally filtered by case_id.

        Args:
            case_id: If provided, only return runs for this case.

        Returns:
            List of RunManifest, sorted by start_time descending (newest first).
        """
        if not self._root.exists():
            return []

        results: list[RunManifest] = []
        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = RunManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
                if case_id is None or manifest.case_id == case_id:
                    results.append(manifest)
            except Exception as e:
                logger.error("failed to load manifest from %s: %s", manifest_path, e)

        results.sort(key=lambda m: m.timing.start_time, reverse=True)
        return results
