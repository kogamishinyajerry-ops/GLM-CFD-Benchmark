"""P4-C failure mode library — turn failures into reusable assets.

This package implements the failure-mode taxonomy and the append-only
failure library described in Architecture v4.0 §4. Modules in the v4 wave
do not import each other; composition happens at the CLI layer.
"""

from __future__ import annotations

from cfdb.failures.library import FailureLibrary, FailureRecord, IngestSummary
from cfdb.failures.taxonomy import (
    DIVERGENCE_THRESHOLD,
    FAILURE_MODES,
    FailureMode,
    build_signature,
    classify,
    compute_fingerprint,
)

__all__ = [
    "DIVERGENCE_THRESHOLD",
    "FAILURE_MODES",
    "FailureLibrary",
    "FailureMode",
    "FailureRecord",
    "IngestSummary",
    "build_signature",
    "classify",
    "compute_fingerprint",
]
