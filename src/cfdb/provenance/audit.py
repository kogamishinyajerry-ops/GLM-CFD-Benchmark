"""Provenance audit: scan cases, verify reference file sha256 anchors (P4-A).

Fail-closed rules enforced here:

- experimental/dns without citation -> DECLARED-NOT-VERIFIED (via derive_honesty).
- Anchored file whose sha256 drifted -> honesty downgraded to DECLARED-NOT-VERIFIED.
- Anchored or case.yaml-declared file missing on disk -> reported as ``missing``
  (never a crash) and honesty downgraded to DECLARED-NOT-VERIFIED.
- Unreadable/invalid case.yaml or provenance.yaml -> record emitted with
  DECLARED-NOT-VERIFIED instead of silently skipping the case.
- REAL anchoring gate: REAL (experimental/dns) additionally requires every
  reference file in scope -- anchored hashes, case.yaml-declared files, and
  any file found under the case's ``reference/`` directory -- to verify as
  ``ok``. Any ``unanchored``/``missing``/``drift`` entry downgrades to
  DECLARED-NOT-VERIFIED. Non-REAL levels (ANALYTIC/MANUFACTURED/...) are not
  affected by unanchored files.
- Directory-level scan: files present under ``reference/`` but neither
  anchored nor declared are reported as ``unanchored`` (defends against
  swapping data files without updating the declaration).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from cfdb.provenance.records import (
    FileHashStatus,
    ProvenanceDeclaration,
    ProvenanceRecord,
    derive_honesty,
)
from cfdb.schema import CaseSpec

logger = logging.getLogger(__name__)

PROVENANCE_FILENAME = "provenance.yaml"

_CHUNK_SIZE = 65536


def sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file (streamed, stdlib only).

    Args:
        path: File to hash.

    Returns:
        Lowercase sha256 hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _load_declaration(case_dir: Path) -> tuple[ProvenanceDeclaration | None, list[str]]:
    """Load provenance.yaml from a case directory, fail-closed on errors.

    Args:
        case_dir: Case directory (contains case.yaml).

    Returns:
        (declaration, error_notes). declaration is None if the file is absent
        or invalid; invalid files additionally produce an error note so the
        audit downgrades instead of crashing.
    """
    prov_path = case_dir / PROVENANCE_FILENAME
    if not prov_path.exists():
        return None, []
    try:
        with prov_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if raw is None:
            raw = {}
        return ProvenanceDeclaration.model_validate(raw), []
    except (yaml.YAMLError, ValidationError) as e:
        logger.error("invalid %s in %s: %s", PROVENANCE_FILENAME, case_dir, e)
        return None, [f"invalid {PROVENANCE_FILENAME}: {type(e).__name__}"]


def _check_files(
    case_dir: Path,
    anchored: dict[str, str],
    declared_files: list[str],
) -> tuple[dict[str, str], dict[str, FileHashStatus], list[str], bool]:
    """Verify anchored hashes and existence of declared reference files.

    Paths are resolved relative to the case directory; ``..`` segments are
    allowed (shared reference data may live outside the case directory).

    Args:
        case_dir: Case directory used as the resolution base.
        anchored: Anchored hashes from provenance.yaml (rel path -> sha256).
        declared_files: Reference file paths declared in case.yaml (relative
            to the case directory).

    Returns:
        (computed_hashes, file_status, notes, downgrade) where downgrade is
        True if any anchored hash drifted or any file is missing.
    """
    computed: dict[str, str] = {}
    status: dict[str, FileHashStatus] = {}
    notes: list[str] = []
    downgrade = False

    all_paths = dict.fromkeys(list(anchored) + declared_files)
    for rel in all_paths:
        target = case_dir / rel
        if not target.is_file():
            status[rel] = "missing"
            notes.append(f"reference file missing: {rel}")
            downgrade = True
            continue
        actual = sha256_file(target)
        computed[rel] = actual
        if rel not in anchored:
            status[rel] = "unanchored"
            notes.append(f"reference file not anchored in {PROVENANCE_FILENAME}: {rel}")
        elif actual == anchored[rel]:
            status[rel] = "ok"
        else:
            status[rel] = "drift"
            notes.append(
                f"sha256 drift for {rel}: anchored {anchored[rel][:12]}..., "
                f"actual {actual[:12]}..."
            )
            downgrade = True

    return computed, status, notes, downgrade


REFERENCE_DIRNAME = "reference"


def _scan_stray_reference_files(
    case_dir: Path,
    known: set[str],
) -> tuple[dict[str, str], dict[str, FileHashStatus], list[str]]:
    """Report files under ``reference/`` that are neither anchored nor declared.

    Defends against "swap the data, keep the declaration": a file placed in
    the case's ``reference/`` directory without an anchor is reported as
    ``unanchored`` so the REAL anchoring gate bites.

    Dotfiles (OS metadata such as ``.DS_Store``) are skipped: they cannot be
    consumed as reference data without being declared in case.yaml, where the
    declared-file check already catches them.

    Args:
        case_dir: Case directory used as the resolution base.
        known: Relative paths already covered by anchors or case.yaml
            declarations (i.e. already present in file_status).

    Returns:
        (computed_hashes, file_status, notes) for the stray files only.
    """
    computed: dict[str, str] = {}
    status: dict[str, FileHashStatus] = {}
    notes: list[str] = []

    ref_dir = case_dir / REFERENCE_DIRNAME
    if not ref_dir.is_dir():
        return computed, status, notes

    for target in sorted(ref_dir.rglob("*")):
        if not target.is_file() or target.name.startswith("."):
            continue
        rel = target.relative_to(case_dir).as_posix()
        if rel in known:
            continue
        # Never raise (module contract): an unreadable stray file is itself
        # a fail-closed downgrade condition, not a crash.
        try:
            computed[rel] = sha256_file(target)
            status[rel] = "unanchored"
            notes.append(
                f"undeclared file present in {REFERENCE_DIRNAME}/: {rel} "
                f"(not anchored in {PROVENANCE_FILENAME}, not declared in case.yaml)"
            )
        except OSError as exc:
            status[rel] = "unreadable"
            notes.append(
                f"stray file in {REFERENCE_DIRNAME}/ could not be read: "
                f"{rel} ({exc}) — treated as unverifiable (fail-closed)"
            )

    return computed, status, notes


def audit_case(case_dir: Path) -> ProvenanceRecord:
    """Audit provenance for a single case directory.

    Never raises on bad/missing data: every failure path is reported inside
    the returned record (fail-closed honesty downgrade + note).

    Args:
        case_dir: Case directory containing case.yaml.

    Returns:
        The audited ProvenanceRecord.
    """
    case_id = case_dir.name
    reference_type: str | None = None
    declared_files: list[str] = []
    notes: list[str] = []
    invalid_spec = False

    try:
        with (case_dir / "case.yaml").open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        spec = CaseSpec.model_validate(raw)
        case_id = spec.id
        if spec.reference is not None:
            reference_type = spec.reference.type
            declared_files = [str(p) for p in spec.reference.files.values()]
    except (OSError, yaml.YAMLError, ValidationError) as e:
        logger.error("failed to load case.yaml in %s: %s", case_dir, e)
        notes.append(f"case.yaml unreadable or invalid: {type(e).__name__}")
        invalid_spec = True

    declaration, decl_errors = _load_declaration(case_dir)
    notes.extend(decl_errors)
    citation = declaration.citation if declaration else None
    anchored = declaration.file_hashes if declaration else {}

    computed, file_status, check_notes, downgrade = _check_files(
        case_dir, anchored, declared_files
    )
    notes.extend(check_notes)

    stray_hashes, stray_status, stray_notes = _scan_stray_reference_files(
        case_dir, set(file_status)
    )
    computed.update(stray_hashes)
    file_status.update(stray_status)
    notes.extend(stray_notes)

    honesty = derive_honesty(reference_type, citation)
    if invalid_spec or decl_errors:
        honesty = "DECLARED-NOT-VERIFIED"
    if downgrade and honesty != "DECLARED-NOT-VERIFIED":
        notes.append(f"honesty downgraded from {honesty} to DECLARED-NOT-VERIFIED")
        honesty = "DECLARED-NOT-VERIFIED"
    # REAL anchoring gate (P1 fix): REAL additionally requires every reference
    # file in scope (anchored + declared + found under reference/) to verify
    # as "ok". Unanchored files never carry a REAL badge; non-REAL levels are
    # unaffected by this gate.
    if honesty == "REAL":
        not_ok = sorted(rel for rel, st in file_status.items() if st != "ok")
        if not_ok:
            notes.append(
                "honesty downgraded from REAL to DECLARED-NOT-VERIFIED: "
                f"reference files not fully anchored/verified: {', '.join(not_ok)}"
            )
            honesty = "DECLARED-NOT-VERIFIED"

    if reference_type is None:
        ref_type_str = "invalid" if invalid_spec else "none"
    else:
        ref_type_str = reference_type

    return ProvenanceRecord(
        case_id=case_id,
        reference_type=ref_type_str,
        citation=citation,
        source_url=declaration.source_url if declaration else None,
        retrieved=declaration.retrieved if declaration else None,
        file_hashes=computed,
        honesty=honesty,
        transcription_verified=(declaration.transcription_verified if declaration else False),
        file_status=file_status,
        notes=notes + (declaration.notes if declaration else []),
    )


def audit_all(cases_dir: Path) -> list[ProvenanceRecord]:
    """Audit provenance for every case under ``cases_dir``.

    Scans the ``cases/<category>/<case_id>/case.yaml`` layout. Directories
    whose case.yaml fails to parse still produce a fail-closed record so no
    case silently disappears from the provenance table.

    Args:
        cases_dir: Root cases directory.

    Returns:
        Records sorted by case_id.
    """
    records: list[ProvenanceRecord] = []
    if not cases_dir.is_dir():
        logger.warning("cases_dir does not exist: %s", cases_dir)
        return records

    for category_dir in sorted(cases_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for case_dir in sorted(category_dir.iterdir()):
            if not case_dir.is_dir() or not (case_dir / "case.yaml").exists():
                continue
            records.append(audit_case(case_dir))

    return sorted(records, key=lambda r: r.case_id)
