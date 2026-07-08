"""Provenance data models and mechanical honesty derivation (P4-A).

Honesty levels are derived mechanically from the reference type and the
presence of a citation. They can never be hand-assigned: claiming
experimental/DNS data without a verifiable citation fails closed to
DECLARED-NOT-VERIFIED.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

HonestyLevel = Literal[
    "REAL",
    "ANALYTIC",
    "MANUFACTURED",
    "PREVIOUS_RUN",
    "SURROGATE",
    "DECLARED-NOT-VERIFIED",
]
"""Honesty grading of a case's reference data (see Architecture v4.0 §0/§2)."""

FileHashStatus = Literal["ok", "drift", "missing", "unanchored", "unreadable"]
"""Per-file sha256 verification outcome.

- ok: file exists and its sha256 matches the anchored value.
- drift: file exists but its sha256 differs from the anchored value.
- missing: file is anchored or declared in case.yaml but absent on disk.
- unanchored: file has no anchored sha256 -- either declared in case.yaml
  without an anchor, or present under the case's ``reference/`` directory
  without being anchored or declared.

The REAL honesty level requires every file in the audit scope to be "ok";
any other status downgrades experimental/dns cases to DECLARED-NOT-VERIFIED
(see :func:`cfdb.provenance.audit.audit_case`).
"""


class ProvenanceDeclaration(BaseModel):
    """Human-written provenance declaration (``cases/<cat>/<id>/provenance.yaml``).

    Contains only declared facts: citation, source URL, retrieval date, and
    sha256 anchors for reference files. It never contains an honesty level;
    honesty is derived mechanically by :func:`derive_honesty`.
    """

    model_config = ConfigDict(extra="forbid")

    citation: str | None = None
    """Bibliographic citation, e.g. 'Ladson, NASA TM-4074, 1988'."""

    source_url: str | None = None
    """URL of the original data source (optional)."""

    retrieved: str | None = None
    """ISO date when the original data was retrieved (optional)."""

    transcription_verified: bool = False
    """True only if the transcribed values were checked against the original
    source tables. Defaults to False (fail-closed honesty boundary)."""

    file_hashes: dict[str, str] = Field(default_factory=dict)
    """Anchored sha256 hex digests: path relative to the case directory -> sha256."""

    notes: list[str] = Field(default_factory=list)
    """Free-form human notes on provenance limits (optional)."""


class ProvenanceRecord(BaseModel):
    """Audit result for one case: declared provenance + verified hash state.

    Produced only by :func:`cfdb.provenance.audit.audit_case`; ``honesty`` is
    derived mechanically and downgraded on any hash drift or missing file.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    """Case identifier (directory name if case.yaml failed to load)."""

    reference_type: str
    """Mirrors ReferenceSpec.type; 'none' if the case declares no reference,
    'invalid' if case.yaml could not be parsed/validated."""

    citation: str | None = None
    """Citation copied from the provenance declaration (None if undeclared)."""

    source_url: str | None = None
    """Source URL copied from the provenance declaration."""

    retrieved: str | None = None
    """Retrieval date copied from the provenance declaration."""

    file_hashes: dict[str, str] = Field(default_factory=dict)
    """Recomputed sha256 of reference files that exist on disk (relative path
    -> sha256). Never copied from the declaration: always re-hashed."""

    honesty: HonestyLevel = "DECLARED-NOT-VERIFIED"
    """Mechanically derived honesty level (never hand-filled)."""

    transcription_verified: bool = False
    """Copied from the declaration; False when undeclared (fail-closed)."""

    file_status: dict[str, FileHashStatus] = Field(default_factory=dict)
    """Per-file verification outcome (relative path -> status)."""

    notes: list[str] = Field(default_factory=list)
    """Audit notes: drift/missing explanations plus declaration notes."""


def derive_honesty(reference_type: str | None, citation: str | None) -> HonestyLevel:
    """Derive the honesty level mechanically from reference type and citation.

    Fail-closed: experimental/dns data without a non-empty citation is not
    verifiable and grades as DECLARED-NOT-VERIFIED; unknown reference types
    also grade as DECLARED-NOT-VERIFIED.

    Note: a REAL result here is a necessary but not sufficient condition.
    :func:`cfdb.provenance.audit.audit_case` further requires every reference
    file to be anchored and hash-verified ("ok") before the REAL badge holds.

    Args:
        reference_type: ReferenceSpec.type value, or None if the case has no
            reference block.
        citation: Declared citation string (None or blank counts as absent).

    Returns:
        The derived HonestyLevel.
    """
    if reference_type is None:
        return "SURROGATE"
    has_citation = citation is not None and citation.strip() != ""
    if reference_type in ("experimental", "dns"):
        if has_citation:
            return "REAL"
        logger.warning(
            "reference_type '%s' claims measured data but has no citation "
            "-> DECLARED-NOT-VERIFIED",
            reference_type,
        )
        return "DECLARED-NOT-VERIFIED"
    if reference_type == "analytical":
        return "ANALYTIC"
    if reference_type == "manufactured":
        return "MANUFACTURED"
    if reference_type == "previous_run":
        return "PREVIOUS_RUN"
    logger.warning(
        "unknown reference_type '%s' -> DECLARED-NOT-VERIFIED (fail-closed)",
        reference_type,
    )
    return "DECLARED-NOT-VERIFIED"
