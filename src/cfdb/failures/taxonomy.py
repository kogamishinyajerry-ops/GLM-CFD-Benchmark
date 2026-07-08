"""Failure mode taxonomy and classification rules (Architecture v4.0 §4).

Classification is purely mechanical: it derives a :data:`FailureMode` from a
run's ``RunManifest`` + ``MetricsResult`` following a fixed priority chain.
No self-reported verdicts are accepted — a run only escapes classification
(returns ``None``) when the manifest says ``success`` AND the recomputed
metrics say ``pass``. A success run with missing/unreadable metrics is
fail-closed classified as ``UNKNOWN`` (unverified is not a pass).
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Literal

from cfdb.schema import MetricsResult, RunManifest

logger = logging.getLogger(__name__)

FailureMode = Literal[
    "MESH_FAILURE",
    "DIVERGENCE",
    "TIMEOUT",
    "MISSING_ARTIFACT",
    "MISSING_REFERENCE",
    "TOLERANCE_EXCEEDED",
    "SETUP_ERROR",
    "ENV_MISSING",
    "UNKNOWN",
]

FAILURE_MODES: tuple[FailureMode, ...] = (
    "MESH_FAILURE",
    "DIVERGENCE",
    "TIMEOUT",
    "MISSING_ARTIFACT",
    "MISSING_REFERENCE",
    "TOLERANCE_EXCEEDED",
    "SETUP_ERROR",
    "ENV_MISSING",
    "UNKNOWN",
)

DIVERGENCE_THRESHOLD: float = 1.0e3
"""Final residual magnitude above which a run is considered diverged.

Converged residuals are normally orders of magnitude below 1; non-finite
values (NaN/inf) always count as diverged regardless of this threshold.
"""

# Deliberately narrow: solver-internal dictionary errors like OpenFOAM's
# "Entry 'pFinal' not found in dictionary" are SETUP_ERROR, not a missing
# binary. A bare "not found" substring misclassified those on real runs.
_ENV_MISSING_PATTERNS: tuple[str, ...] = (
    "command not found",
    "no such file or directory",
    "executable not found",
    "enoent",
)
_MISSING_COMPUTED_PATTERN = "missing computed"
_MISSING_REFERENCE_PATTERN = "missing reference"


def _failed_mesh_step(manifest: RunManifest) -> dict | None:
    """Return the first mesh-like step with a nonzero exit code, if any."""
    for step in manifest.step_details or []:
        name = str(step.get("name", ""))
        exit_code = step.get("exit_code")
        if "mesh" in name.lower() and isinstance(exit_code, int) and exit_code != 0:
            return step
    return None


def _failed_step(manifest: RunManifest) -> dict | None:
    """Return the first step with a nonzero exit code, if any."""
    for step in manifest.step_details or []:
        exit_code = step.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return step
    return None


def _diverged_fields(manifest: RunManifest) -> list[str]:
    """Return sorted residual field names that exceed the divergence threshold."""
    diverged: list[str] = []
    for field, value in (manifest.final_residuals or {}).items():
        if not math.isfinite(value) or abs(value) > DIVERGENCE_THRESHOLD:
            diverged.append(field)
    return sorted(diverged)


def _matching_notes(metrics: MetricsResult | None, pattern: str) -> list[str]:
    """Return sorted metrics notes containing the given lowercase pattern."""
    if metrics is None:
        return []
    return sorted(note for note in metrics.notes if pattern in note.lower())


def _env_missing_text(manifest: RunManifest) -> bool:
    """Check whether the manifest error text signals a missing command/binary."""
    error = (manifest.error or "").lower()
    return any(pattern in error for pattern in _ENV_MISSING_PATTERNS)


def classify(manifest: RunManifest, metrics: MetricsResult | None) -> FailureMode | None:
    """Classify a run into a failure mode following the §4 priority chain.

    Priority: TIMEOUT > MESH_FAILURE > DIVERGENCE > MISSING_ARTIFACT >
    MISSING_REFERENCE > TOLERANCE_EXCEEDED > ENV_MISSING > SETUP_ERROR >
    UNKNOWN.

    Args:
        manifest: The run manifest (execution-side truth).
        metrics: The recomputed metrics result, or None when metrics.json is
                 missing/unreadable. Fail-closed: a success run without
                 verifiable metrics is classified UNKNOWN, never skipped.

    Returns:
        The failure mode, or None when the run is a verified pass
        (manifest success + metrics pass) or a dry run (nothing executed).
    """
    if manifest.status == "dry_run":
        return None
    if (
        manifest.status == "success"
        and metrics is not None
        and metrics.overall_status == "pass"
    ):
        return None

    if manifest.status == "timeout":
        return "TIMEOUT"
    if _failed_mesh_step(manifest) is not None:
        return "MESH_FAILURE"
    if _diverged_fields(manifest):
        return "DIVERGENCE"
    if _matching_notes(metrics, _MISSING_COMPUTED_PATTERN):
        return "MISSING_ARTIFACT"
    if _matching_notes(metrics, _MISSING_REFERENCE_PATTERN):
        return "MISSING_REFERENCE"
    if (
        manifest.status == "success"
        and metrics is not None
        and metrics.qoi_pass is False
        and (metrics.qoi_failed or metrics.qoi_relative_errors)
    ):
        # qoi_failed (Stage-A) covers both relative-tolerance failures and
        # zero-reference absolute-tolerance failures, so the latter no longer
        # fall through to UNKNOWN. Legacy metrics (empty qoi_failed) keep the
        # old qoi_relative_errors trigger.
        return "TOLERANCE_EXCEEDED"
    if _env_missing_text(manifest):
        return "ENV_MISSING"
    if manifest.status == "failed" or _failed_step(manifest) is not None:
        return "SETUP_ERROR"
    return "UNKNOWN"


def build_signature(
    manifest: RunManifest, metrics: MetricsResult | None, mode: FailureMode
) -> str:
    """Build a stable, human-readable signature for a classified failure.

    The signature deliberately excludes volatile values (run ids, wall times,
    raw residual magnitudes) so that recurrences of the same failure produce
    the same fingerprint.

    Args:
        manifest: The run manifest.
        metrics: The metrics result, or None when unavailable.
        mode: The failure mode returned by :func:`classify`.

    Returns:
        A stable signature string, e.g. ``"step=snappy_mesh exit=1"``.
    """
    if mode == "TIMEOUT":
        return "status=timeout"
    if mode == "MESH_FAILURE":
        step = _failed_mesh_step(manifest)
        if step is not None:
            return f"step={step.get('name')} exit={step.get('exit_code')}"
        return "step=<unknown-mesh-step>"
    if mode == "DIVERGENCE":
        return f"residuals={','.join(_diverged_fields(manifest))}"
    if mode == "MISSING_ARTIFACT":
        return "; ".join(_matching_notes(metrics, _MISSING_COMPUTED_PATTERN))
    if mode == "MISSING_REFERENCE":
        return "; ".join(_matching_notes(metrics, _MISSING_REFERENCE_PATTERN))
    if mode == "TOLERANCE_EXCEEDED":
        if metrics is not None and metrics.qoi_failed:
            # Stage-A semantics: sign with the QoIs that actually failed
            # their gate, so distinct failure sets over the same measured
            # QoIs are not over-deduplicated into one fingerprint.
            return f"qoi={','.join(sorted(metrics.qoi_failed))}"
        # Legacy metrics without qoi_failed: fall back to the historical
        # all-measured-QoIs signature to keep old fingerprints stable.
        logger.info(
            "TOLERANCE_EXCEEDED signature: qoi_failed empty (legacy metrics), "
            "falling back to qoi_relative_errors keys"
        )
        qoi_names = sorted(metrics.qoi_relative_errors) if metrics is not None else []
        return f"qoi={','.join(qoi_names)}"
    if mode == "ENV_MISSING":
        return "error=command_not_found"
    if mode == "SETUP_ERROR":
        step = _failed_step(manifest)
        if step is not None:
            return f"step={step.get('name')} exit={step.get('exit_code')}"
        return f"status={manifest.status}"
    # UNKNOWN: distinguish the fail-closed "unverifiable metrics" case.
    if metrics is None:
        return "metrics=missing"
    return f"status={manifest.status}"


def compute_fingerprint(case_id: str, solver: str, mode: FailureMode, signature: str) -> str:
    """Compute the deduplication fingerprint for a failure.

    Args:
        case_id: The case identifier.
        solver: The solver name.
        mode: The failure mode.
        signature: The stable signature from :func:`build_signature`.

    Returns:
        First 16 hex chars of sha256 over ``case_id|solver|mode|signature``.
    """
    payload = f"{case_id}|{solver}|{mode}|{signature}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
