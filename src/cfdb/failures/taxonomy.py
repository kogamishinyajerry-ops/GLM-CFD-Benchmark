"""Failure mode taxonomy and classification rules (Architecture v4.0 §4,
extended domain-agnostic in v5.0 §2.A4).

Classification is purely mechanical: it derives a :data:`FailureMode` from a
run's ``RunManifest`` + ``MetricsResult`` following a fixed priority chain.
No self-reported verdicts are accepted — a run only escapes classification
(returns ``None``) when the manifest says ``success`` AND the recomputed
metrics say ``pass``. A success run with missing/unreadable metrics is
fail-closed classified as ``UNKNOWN`` (unverified is not a pass).

v5.0 §2.A4 adds five domain-agnostic buckets (BUILD_FAILURE/TEST_FAILURE/
WRONG_ANSWER/RESOURCE_EXCEEDED/CHECKER_ERROR, mapping competitive-judging
CE/TestFail/WA/TLE·MLE/checker-runtime-failure semantics) on top of the
original CFD-specific chain; see :func:`classify` for the merged priority
order and the CFD-detector-order invariant.
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
    # === v5 §2.A4: domain-agnostic buckets (competitive-judging mapping:
    # CE / TestFail / WA / TLE·MLE / trusted-checker-material runtime failure) ===
    "BUILD_FAILURE",
    "TEST_FAILURE",
    "WRONG_ANSWER",
    "RESOURCE_EXCEEDED",
    "CHECKER_ERROR",
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
    "BUILD_FAILURE",
    "TEST_FAILURE",
    "WRONG_ANSWER",
    "RESOURCE_EXCEEDED",
    "CHECKER_ERROR",
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

# === v5 §2.A4: domain-agnostic generic detectors ===
# Step-name keywords (manifest.step_details), mirroring _failed_mesh_step's
# style: substring match on a lowercased step name + nonzero exit code.
_BUILD_STEP_KEYWORDS: tuple[str, ...] = ("build", "compile")
_TEST_STEP_KEYWORDS: tuple[str, ...] = ("test",)

# Notes keywords (metrics.notes), mirroring _matching_notes's style. Kept
# deliberately narrow (compound phrases over bare words) for the same reason
# _ENV_MISSING_PATTERNS is narrow: avoid coincidental substring collisions
# with unrelated CFD note text.
_WRONG_ANSWER_PATTERNS: tuple[str, ...] = ("wrong answer", "qoi mismatch")
_RESOURCE_EXCEEDED_PATTERNS: tuple[str, ...] = (
    "memory limit exceeded",
    "out of memory",
    "oom",
    "rlimit",
)
_CHECKER_ERROR_PATTERNS: tuple[str, ...] = (
    "checker error",
    "checker crashed",
    "invalid checker output",
)


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


def _failed_step_with_keyword(manifest: RunManifest, keywords: tuple[str, ...]) -> dict | None:
    """Return the first step whose name matches a keyword and has a nonzero exit code.

    Generalizes :func:`_failed_mesh_step` to arbitrary keyword sets (v5
    §2.A4 generic detectors: build/compile, test). Same style: case-insensitive
    substring match on the step name, first match wins.
    """
    for step in manifest.step_details or []:
        name = str(step.get("name", "")).lower()
        exit_code = step.get("exit_code")
        if any(kw in name for kw in keywords) and isinstance(exit_code, int) and exit_code != 0:
            return step
    return None


def _matching_notes_any(metrics: MetricsResult | None, patterns: tuple[str, ...]) -> list[str]:
    """Return sorted metrics notes containing any of the given lowercase patterns."""
    if metrics is None:
        return []
    return sorted(
        note for note in metrics.notes if any(pattern in note.lower() for pattern in patterns)
    )


def _env_missing_text(manifest: RunManifest) -> bool:
    """Check whether the manifest error text signals a missing command/binary."""
    error = (manifest.error or "").lower()
    return any(pattern in error for pattern in _ENV_MISSING_PATTERNS)


def classify(manifest: RunManifest, metrics: MetricsResult | None) -> FailureMode | None:
    """Classify a run into a failure mode following the §4/§2.A4 priority chain.

    Priority: TIMEOUT > CHECKER_ERROR > BUILD_FAILURE > TEST_FAILURE >
    MESH_FAILURE > DIVERGENCE > MISSING_ARTIFACT > MISSING_REFERENCE >
    TOLERANCE_EXCEEDED > WRONG_ANSWER > RESOURCE_EXCEEDED > ENV_MISSING >
    SETUP_ERROR > UNKNOWN.

    v5 §2.A4 generic (domain-agnostic) detectors are inserted around the
    CFD-specific chain without moving any CFD detector or changing their
    relative order (MESH_FAILURE > DIVERGENCE > MISSING_ARTIFACT >
    MISSING_REFERENCE > TOLERANCE_EXCEEDED > ENV_MISSING > SETUP_ERROR >
    UNKNOWN is untouched):

    - BUILD_FAILURE / TEST_FAILURE read ``manifest.step_details`` step names
      (build/compile, test keywords), mirroring :func:`_failed_mesh_step`'s
      style, and are checked before MESH_FAILURE.
    - CHECKER_ERROR reads ``metrics.notes`` for judge-material runtime
      failure keywords and is checked right after TIMEOUT — a broken
      checker invalidates every downstream signal, so it must win before
      anything else is inspected.
    - WRONG_ANSWER reads ``metrics.notes`` for wrong-answer keywords but is
      checked *after* TOLERANCE_EXCEEDED, so a genuine numeric-tolerance
      failure always wins when both signals are present (conservative: a
      coincidental notes keyword never overrides an established tolerance
      verdict — "拿不准宁可落既有 TOLERANCE_EXCEEDED").
    - RESOURCE_EXCEEDED reads ``metrics.notes`` for memory/rlimit keywords
      and is checked after WRONG_ANSWER, before ENV_MISSING.

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
    if _matching_notes_any(metrics, _CHECKER_ERROR_PATTERNS):
        return "CHECKER_ERROR"
    if _failed_step_with_keyword(manifest, _BUILD_STEP_KEYWORDS) is not None:
        return "BUILD_FAILURE"
    if _failed_step_with_keyword(manifest, _TEST_STEP_KEYWORDS) is not None:
        return "TEST_FAILURE"
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
    if (
        manifest.status == "success"
        and metrics is not None
        and len(metrics.curves_failed) > 0
    ):
        # v5.0 D1 curve gating (Codex R0 P2): a run can pass every QoI yet
        # fail a curve L2 tolerance (qoi_pass True, curves_failed non-empty,
        # overall_status 'fail') — that is a tolerance failure, not UNKNOWN.
        return "TOLERANCE_EXCEEDED"
    if _matching_notes_any(metrics, _WRONG_ANSWER_PATTERNS):
        return "WRONG_ANSWER"
    if _matching_notes_any(metrics, _RESOURCE_EXCEEDED_PATTERNS):
        return "RESOURCE_EXCEEDED"
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
    if mode == "CHECKER_ERROR":
        return "; ".join(_matching_notes_any(metrics, _CHECKER_ERROR_PATTERNS))
    if mode == "BUILD_FAILURE":
        step = _failed_step_with_keyword(manifest, _BUILD_STEP_KEYWORDS)
        if step is not None:
            return f"step={step.get('name')} exit={step.get('exit_code')}"
        return "step=<unknown-build-step>"
    if mode == "TEST_FAILURE":
        step = _failed_step_with_keyword(manifest, _TEST_STEP_KEYWORDS)
        if step is not None:
            return f"step={step.get('name')} exit={step.get('exit_code')}"
        return "step=<unknown-test-step>"
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
        if metrics is not None and (metrics.qoi_failed or metrics.curves_failed):
            # Stage-A semantics: sign with the QoIs/curves that actually
            # failed their gate, so distinct failure sets over the same
            # measured quantities are not over-deduplicated. Curve-only
            # failures (v5.0 D1) get their own stable signature component.
            parts: list[str] = []
            if metrics.qoi_failed:
                parts.append(f"qoi={','.join(sorted(metrics.qoi_failed))}")
            if metrics.curves_failed:
                parts.append(f"curves={','.join(sorted(metrics.curves_failed))}")
            return " ".join(parts)
        # Legacy metrics without qoi_failed: fall back to the historical
        # all-measured-QoIs signature to keep old fingerprints stable.
        logger.info(
            "TOLERANCE_EXCEEDED signature: qoi_failed empty (legacy metrics), "
            "falling back to qoi_relative_errors keys"
        )
        qoi_names = sorted(metrics.qoi_relative_errors) if metrics is not None else []
        return f"qoi={','.join(qoi_names)}"
    if mode == "WRONG_ANSWER":
        return "; ".join(_matching_notes_any(metrics, _WRONG_ANSWER_PATTERNS))
    if mode == "RESOURCE_EXCEEDED":
        return "; ".join(_matching_notes_any(metrics, _RESOURCE_EXCEEDED_PATTERNS))
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
