"""P4-A provenance: reference data provenance and honesty grading.

Public API:
    - HonestyLevel, FileHashStatus: grading literals.
    - ProvenanceDeclaration: human-written provenance.yaml model.
    - ProvenanceRecord: audited result per case.
    - derive_honesty: mechanical honesty derivation (fail-closed).
    - audit_case / audit_all: scan cases and verify sha256 anchors.
    - sha256_file: streamed sha256 helper.
"""

from cfdb.provenance.audit import (
    PROVENANCE_FILENAME,
    audit_all,
    audit_case,
    sha256_file,
)
from cfdb.provenance.records import (
    FileHashStatus,
    HonestyLevel,
    ProvenanceDeclaration,
    ProvenanceRecord,
    derive_honesty,
)

__all__ = [
    "PROVENANCE_FILENAME",
    "FileHashStatus",
    "HonestyLevel",
    "ProvenanceDeclaration",
    "ProvenanceRecord",
    "audit_all",
    "audit_case",
    "derive_honesty",
    "sha256_file",
]
