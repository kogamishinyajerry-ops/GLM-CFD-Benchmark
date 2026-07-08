"""Append-only failure library persisted to ``failures/library.json`` (§4).

Red lines enforced here:

- The library only ever grows: records are never deleted, ``count`` never
  decreases, and saving a state that drops any fingerprint already on disk
  raises instead of silently rewriting history (lessons are assets). The
  guard compares fingerprint sets, so equal-count replacement also bites.
- Ingest is incremental and idempotent per run: each run_id is only counted
  once, so re-ingesting the same runs directory never inflates counts, while
  a *new* run reproducing a known fingerprint increments ``count`` and
  updates ``last_seen`` without creating a duplicate entry.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cfdb.failures.taxonomy import (
    FailureMode,
    build_signature,
    classify,
    compute_fingerprint,
)
from cfdb.schema import MetricsResult, RunManifest

logger = logging.getLogger(__name__)


class FailureRecord(BaseModel):
    """A single deduplicated failure mode occurrence."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    """sha256(case_id|solver|mode|signature)[:16] — deduplication key."""

    case_id: str
    """Associated CaseSpec.id."""

    solver: str
    """Solver name used by the failing run."""

    mode: FailureMode
    """Classified failure mode."""

    signature: str
    """Stable failure summary (e.g. "step=snappy_mesh exit=1")."""

    first_seen: str
    """run_id of the first run exhibiting this failure."""

    last_seen: str
    """run_id of the most recent run exhibiting this failure."""

    count: int = 1
    """Number of distinct runs that exhibited this failure."""

    evidence: list[str] = Field(default_factory=list)
    """Paths to run artifacts, relative to the ingested runs directory."""

    guard: str | None = None
    """Human-written guard note: how to prevent this failure next time."""


class IngestSummary(BaseModel):
    """Result summary of a single :meth:`FailureLibrary.ingest` pass."""

    model_config = ConfigDict(extra="forbid")

    scanned: int = 0
    """Run directories inspected."""

    passed: int = 0
    """Runs skipped because they are verified passes (manifest success +
    recomputed metrics pass). Dry runs are never counted here — a dry run
    verified nothing."""

    dry_run_skipped: int = 0
    """Runs skipped because they are dry runs (nothing executed, nothing
    verified); excluded from pass statistics."""

    new_records: int = 0
    """New failure fingerprints added to the library."""

    updated_records: int = 0
    """Existing fingerprints whose count was incremented."""

    already_ingested: int = 0
    """Runs skipped because their run_id was ingested previously."""

    errors: list[str] = Field(default_factory=list)
    """Per-run problems (missing/corrupt manifest.json) — reported, not hidden."""


class _LibraryFile(BaseModel):
    """On-disk schema of ``library.json``."""

    model_config = ConfigDict(extra="forbid")

    records: list[FailureRecord] = Field(default_factory=list)
    ingested_run_ids: list[str] = Field(default_factory=list)


class FailureLibrary:
    """Append-only, fingerprint-deduplicated failure library.

    Storage: a single JSON file (canonically ``failures/library.json``)
    holding all :class:`FailureRecord` entries plus the set of already
    ingested run_ids (idempotency ledger).
    """

    def __init__(self, library_path: Path) -> None:
        """Initialize the library and load existing state if present.

        Args:
            library_path: Path to library.json (created on first save).
        """
        self._path: Path = library_path
        self._records: dict[str, FailureRecord] = {}
        self._ingested_run_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        """Load library state from disk (empty state when file is absent)."""
        if not self._path.exists():
            return
        data = _LibraryFile.model_validate_json(self._path.read_text(encoding="utf-8"))
        self._records = {record.fingerprint: record for record in data.records}
        self._ingested_run_ids = set(data.ingested_run_ids)

    def _save(self) -> None:
        """Persist library state, refusing to drop any existing record.

        The guard is a fingerprint superset check, not a count comparison:
        every fingerprint already on disk must still be present in the state
        about to be written. This also bites on equal-count replacement
        (swapping one record for another), which a size check would miss.

        Raises:
            RuntimeError: If any on-disk fingerprint is absent from the
                          in-memory state (append-only red line).
        """
        if self._path.exists():
            on_disk = _LibraryFile.model_validate_json(self._path.read_text(encoding="utf-8"))
            dropped = {record.fingerprint for record in on_disk.records} - set(self._records)
            if dropped:
                raise RuntimeError(
                    f"refusing to overwrite failure library {self._path}: "
                    f"{len(dropped)} on-disk record(s) missing from the state "
                    f"about to be written (library is append-only): "
                    f"{sorted(dropped)}"
                )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = _LibraryFile(
            records=[self._records[fp] for fp in sorted(self._records)],
            ingested_run_ids=sorted(self._ingested_run_ids),
        )
        self._path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("saved failure library with %d records to %s", len(self._records), self._path)

    def records(self, mode: FailureMode | None = None) -> list[FailureRecord]:
        """Return library records, optionally filtered by failure mode.

        Args:
            mode: If provided, only return records with this mode.

        Returns:
            Records sorted by count descending, then fingerprint.
        """
        selected = [r for r in self._records.values() if mode is None or r.mode == mode]
        selected.sort(key=lambda r: (-r.count, r.fingerprint))
        return selected

    def get(self, fingerprint: str) -> FailureRecord:
        """Return the record for a fingerprint.

        Args:
            fingerprint: The failure fingerprint.

        Returns:
            The matching FailureRecord.

        Raises:
            KeyError: If the fingerprint is not in the library.
        """
        if fingerprint not in self._records:
            raise KeyError(f"fingerprint '{fingerprint}' not found in failure library")
        return self._records[fingerprint]

    def ingest(self, runs_dir: Path) -> IngestSummary:
        """Incrementally ingest failures from a runs directory.

        Scans ``runs_dir/<run_id>/manifest.json`` (+ ``metrics.json``),
        classifies each not-yet-ingested run, and updates the library:
        new fingerprint -> new record; known fingerprint -> count++ and
        last_seen update. Runs with missing/corrupt manifest.json are
        reported in the summary errors instead of being silently skipped.
        Dry runs are skipped without entering any statistic other than
        ``dry_run_skipped`` (a dry run verified nothing).

        Args:
            runs_dir: Root runs directory (json repo layout).

        Returns:
            IngestSummary with per-category counts and errors.
        """
        summary = IngestSummary()
        if not runs_dir.exists():
            summary.errors.append(f"runs directory not found: {runs_dir}")
            return summary

        changed = False
        for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            run_id = run_dir.name
            summary.scanned += 1
            if run_id in self._ingested_run_ids:
                summary.already_ingested += 1
                continue

            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                summary.errors.append(f"{run_id}: manifest.json missing")
                continue
            try:
                manifest = RunManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                summary.errors.append(f"{run_id}: unreadable manifest.json ({exc})")
                continue

            if manifest.status == "dry_run":
                # A dry run executed nothing and verified nothing: it is
                # neither a failure nor a verified pass, so it stays out of
                # both statistics and the idempotency ledger.
                summary.dry_run_skipped += 1
                continue

            metrics: MetricsResult | None = None
            metrics_path = run_dir / "metrics.json"
            if metrics_path.exists():
                try:
                    metrics = MetricsResult.model_validate_json(
                        metrics_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    # Fail-closed: unreadable metrics != pass; classify with None.
                    summary.errors.append(f"{run_id}: unreadable metrics.json ({exc})")

            self._ingested_run_ids.add(run_id)
            changed = True

            mode = classify(manifest, metrics)
            if mode is None:
                summary.passed += 1
                continue

            signature = build_signature(manifest, metrics, mode)
            fingerprint = compute_fingerprint(manifest.case_id, manifest.solver, mode, signature)
            evidence = [f"{run_id}/manifest.json"]
            if metrics_path.exists():
                evidence.append(f"{run_id}/metrics.json")

            existing = self._records.get(fingerprint)
            if existing is None:
                self._records[fingerprint] = FailureRecord(
                    fingerprint=fingerprint,
                    case_id=manifest.case_id,
                    solver=manifest.solver,
                    mode=mode,
                    signature=signature,
                    first_seen=run_id,
                    last_seen=run_id,
                    count=1,
                    evidence=evidence,
                )
                summary.new_records += 1
            else:
                existing.count += 1
                existing.last_seen = run_id
                for path in evidence:
                    if path not in existing.evidence:
                        existing.evidence.append(path)
                summary.updated_records += 1

        if changed:
            self._save()
        return summary

    def annotate(self, fingerprint: str, guard: str) -> FailureRecord:
        """Attach a human-written guard note to an existing failure record.

        Args:
            fingerprint: Fingerprint of the record to annotate.
            guard: Non-empty guard text (how to prevent this failure).

        Returns:
            The updated FailureRecord.

        Raises:
            KeyError: If the fingerprint is not in the library.
            ValueError: If guard is empty/whitespace.
        """
        if not guard.strip():
            raise ValueError("guard note must be a non-empty string")
        record = self.get(fingerprint)
        record.guard = guard.strip()
        self._save()
        return record
