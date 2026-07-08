"""Baseline governance for the regression gate (P4-D).

A baseline is a human-promoted anchor of a passing run's QoI results.
Promotion is structurally restricted: only runs whose recomputed
``overall_status == "pass"`` may become baselines, and the promoting
engineer's name is a required field with no default (no automatic
promotion path exists).

Storage layout::

    baselines/baselines.json    <- BaselineFile serialized (single JSON doc)

Each entry anchors the SHA-256 of the promoted run's ``metrics.json`` so
that any post-hoc edit of the run artifacts is detected by the gate
(fail-closed: hash mismatch -> TAMPERED, never silently re-trusted).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cfdb.schema import MetricsResult, RunManifest
from cfdb.utils import utc_now_iso

logger = logging.getLogger(__name__)


class BaselineFileError(RuntimeError):
    """baselines.json itself is corrupt, unreadable, or fails validation.

    Raised by :meth:`BaselineStore.load` so callers can fail closed with a
    dedicated exit path (the CLI maps this to exit code 3, same as TAMPERED)
    instead of crashing with an undifferentiated parse error.
    """


def sha256_of_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file's bytes.

    Args:
        path: File to hash.

    Returns:
        Lowercase hex digest string.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RegressionMargin(BaseModel):
    """Publicly configurable tolerance band for the regression gate.

    A QoI regresses when::

        new_err > base_err + max(absolute, relative * base_err)

    Both knobs live at the top level of ``baselines.json`` so the band is
    visible and auditable next to the baselines it governs.
    """

    model_config = ConfigDict(extra="forbid")

    absolute: float = Field(default=0.005, ge=0)
    """Absolute floor of the tolerance band."""

    relative: float = Field(default=0.1, ge=0)
    """Relative fraction of the baseline error added to the band."""


class BaselineEntry(BaseModel):
    """One promoted baseline anchor for a (case_id, solver) pair."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    """Case this baseline anchors."""

    solver: str
    """Solver this baseline anchors."""

    run_id: str
    """Run that was promoted."""

    promoted_by: str
    """Engineer who promoted this baseline. Required, no default:
    automatic promotion is structurally impossible."""

    promoted_at: str
    """Promotion timestamp (ISO 8601 UTC)."""

    qoi_values: dict[str, float] = Field(default_factory=dict)
    """Computed QoI values copied from the run's metrics.json at promote time."""

    qoi_relative_errors: dict[str, float] = Field(default_factory=dict)
    """Per-QoI relative errors copied from the run's metrics.json at promote time."""

    qoi_absolute_errors: dict[str, float] = Field(default_factory=dict)
    """Per-QoI absolute errors (zero-reference channel) copied from the run's
    metrics.json at promote time. Defaults to empty so baselines promoted
    before this field existed remain readable (legacy entries)."""

    metrics_sha256: str = ""
    """SHA-256 of the promoted run's metrics.json file (tamper anchor)."""


class BaselineFile(BaseModel):
    """On-disk schema of ``baselines/baselines.json``."""

    model_config = ConfigDict(extra="forbid")

    regression_margin: RegressionMargin = Field(default_factory=RegressionMargin)
    """Public tolerance band configuration (top-level, auditable)."""

    baselines: dict[str, BaselineEntry] = Field(default_factory=dict)
    """Baseline entries keyed by ``<case_id>::<solver>``."""


def baseline_key(case_id: str, solver: str) -> str:
    """Build the dictionary key for a (case_id, solver) baseline.

    Args:
        case_id: Case identifier.
        solver: Solver name.

    Returns:
        Stable key string ``<case_id>::<solver>``.
    """
    return f"{case_id}::{solver}"


class BaselineStore:
    """Load/save baselines and perform human-signed promotion.

    Args:
        baselines_path: Path to ``baselines/baselines.json``.
        runs_root: Root of the runs directory (``runs/``), used to read the
            promoted run's ``manifest.json`` / ``metrics.json`` directly from
            disk (the gate never trusts in-memory or self-reported values).
    """

    def __init__(self, baselines_path: Path, runs_root: Path) -> None:
        self._path: Path = baselines_path
        self._runs_root: Path = runs_root

    @property
    def path(self) -> Path:
        """Path of the baselines.json file."""
        return self._path

    @property
    def runs_root(self) -> Path:
        """Root of the runs directory this store reads from."""
        return self._runs_root

    def load(self) -> BaselineFile:
        """Load the baseline file, returning an empty document if absent.

        Returns:
            Parsed BaselineFile (empty defaults when the file does not exist).

        Raises:
            BaselineFileError: If baselines.json exists but cannot be read or
                does not validate (fail-closed: a corrupt anchor store must
                never degrade into "no baselines").
        """
        if not self._path.exists():
            return BaselineFile()
        try:
            # ValueError covers pydantic ValidationError, json.JSONDecodeError
            # and UnicodeDecodeError (all ValueError subclasses).
            return BaselineFile.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise BaselineFileError(
                f"baselines file {self._path} is corrupt or unreadable "
                f"(fail-closed): {exc}"
            ) from exc

    def save(self, data: BaselineFile) -> None:
        """Persist the baseline file (creates parent directories).

        Args:
            data: Document to write.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("saved baselines to %s", self._path)

    def get(self, case_id: str, solver: str) -> BaselineEntry | None:
        """Look up the baseline for a (case_id, solver) pair.

        Args:
            case_id: Case identifier.
            solver: Solver name.

        Returns:
            The BaselineEntry, or None when no baseline exists.
        """
        return self.load().baselines.get(baseline_key(case_id, solver))

    def run_metrics_path(self, run_id: str) -> Path:
        """Return the on-disk metrics.json path for a run.

        Args:
            run_id: Run identifier.

        Returns:
            Path to ``runs/<run_id>/metrics.json`` (may not exist).
        """
        return self._runs_root / run_id / "metrics.json"

    def read_run(self, run_id: str) -> tuple[RunManifest, MetricsResult]:
        """Read a run's manifest and metrics directly from disk.

        Args:
            run_id: Run identifier.

        Returns:
            Tuple of (RunManifest, MetricsResult) parsed from the run directory.

        Raises:
            FileNotFoundError: If manifest.json or metrics.json is missing.
        """
        run_dir = self._runs_root / run_id
        manifest_path = run_dir / "manifest.json"
        metrics_path = run_dir / "metrics.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"run '{run_id}': missing {manifest_path}")
        if not metrics_path.exists():
            raise FileNotFoundError(f"run '{run_id}': missing {metrics_path}")
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        metrics = MetricsResult.model_validate_json(metrics_path.read_text(encoding="utf-8"))
        return manifest, metrics

    def promote(self, run_id: str, engineer: str) -> BaselineEntry:
        """Promote a passing run to baseline, signed by an engineer.

        Fail-closed rules:
          - ``engineer`` must be a non-empty name (human signature required).
          - The run's metrics.json is re-read from disk; only
            ``overall_status == "pass"`` is promotable. A failed or
            incomplete run can never become a baseline.
          - The run's manifest.json is re-read from disk; only
            ``status == "success"`` is promotable (symmetric with the gate's
            candidate-side check).
          - The run must anchor at least one QoI error (relative or
            absolute). A run with no measurable quantities cannot become a
            baseline: it would make every future candidate PASS vacuously.

        Args:
            run_id: Run to promote.
            engineer: Name of the engineer signing the promotion.

        Returns:
            The stored BaselineEntry.

        Raises:
            ValueError: If engineer is empty, the run is not a passing
                successful run, or the run anchors no QoI errors at all.
            FileNotFoundError: If the run directory lacks manifest/metrics.
            BaselineFileError: If the existing baselines.json is corrupt.
        """
        engineer_name = engineer.strip()
        if len(engineer_name) == 0:
            raise ValueError("promotion requires a non-empty engineer name (--engineer)")

        # Verify the anchor store is readable BEFORE anything else: a corrupt
        # baselines.json must surface as BaselineFileError (fail-closed,
        # CLI exit 3), not be masked by run-lookup errors (Codex R1 P2).
        data = self.load()

        manifest, metrics = self.read_run(run_id)
        if metrics.overall_status != "pass":
            raise ValueError(
                f"run '{run_id}' has overall_status="
                f"'{metrics.overall_status}'; only 'pass' runs can be promoted"
            )
        if manifest.status != "success":
            raise ValueError(
                f"run '{run_id}' has status='{manifest.status}'; "
                "only 'success' runs can be promoted"
            )
        has_relative = len(metrics.qoi_relative_errors) > 0
        has_absolute = len(metrics.qoi_absolute_errors) > 0
        if (has_relative is False) and (has_absolute is False):
            raise ValueError(
                f"run '{run_id}' has no QoI errors (relative or absolute) to "
                "anchor; a run with nothing measurable cannot become a baseline"
            )

        metrics_path = self.run_metrics_path(run_id)
        entry = BaselineEntry(
            case_id=manifest.case_id,
            solver=manifest.solver,
            run_id=run_id,
            promoted_by=engineer_name,
            promoted_at=utc_now_iso(),
            qoi_values=dict(metrics.qoi_computed_values or {}),
            qoi_relative_errors=dict(metrics.qoi_relative_errors),
            qoi_absolute_errors=dict(metrics.qoi_absolute_errors),
            metrics_sha256=sha256_of_file(metrics_path),
        )

        # store already loaded above (fail-closed early check)
        data.baselines[baseline_key(manifest.case_id, manifest.solver)] = entry
        self.save(data)
        logger.info(
            "promoted run %s as baseline for %s/%s (by %s)",
            run_id,
            manifest.case_id,
            manifest.solver,
            engineer_name,
        )
        return entry
