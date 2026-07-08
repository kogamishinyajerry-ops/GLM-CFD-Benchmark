"""Tests for P4-A provenance: honesty derivation + sha256 audit (fail-closed).

Includes the mandatory tamper witnesses:
- flip one byte in an anchored reference file -> audit MUST downgrade;
- remove the citation from an experimental case -> MUST grade DNV.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cfdb.provenance import (
    ProvenanceDeclaration,
    ProvenanceRecord,
    audit_all,
    audit_case,
    derive_honesty,
    sha256_file,
)

REPO_ROOT = Path(__file__).parent.parent
REAL_CASES_DIR = REPO_ROOT / "cases"

LADSON_CITATION = "Ladson, NASA TM-4074, 1988"


# ============================================================================
# Helpers
# ============================================================================


def make_case(
    cases_root: Path,
    case_id: str,
    category: str = "validation",
    reference_type: str | None = "experimental",
    reference_files: dict[str, str] | None = None,
    file_contents: dict[str, str] | None = None,
    provenance: dict | None = None,
) -> Path:
    """Create a minimal on-disk case directory and return its path."""
    case_dir = cases_root / category / case_id
    case_dir.mkdir(parents=True)
    spec: dict = {
        "id": case_id,
        "name": case_id,
        "category": category,
        "physics": {"flow": "incompressible"},
        "conditions": {"reynolds": 100.0},
        "solvers": [{"name": "generic", "command": "bash {{ case_dir }}/run.sh"}],
        "outputs": {"qoi": ["cl"]},
        "metrics": {"qoi_relative_tolerance": {"cl": 0.05}},
    }
    if reference_type is not None:
        spec["reference"] = {
            "type": reference_type,
            "files": reference_files or {},
            "qoi_values": {"cl": 1.0},
        }
    (case_dir / "case.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    for rel, content in (file_contents or {}).items():
        target = case_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if provenance is not None:
        (case_dir / "provenance.yaml").write_text(
            yaml.safe_dump(provenance), encoding="utf-8"
        )
    return case_dir


def anchored_experimental_case(cases_root: Path, case_id: str = "case_real") -> Path:
    """Experimental case with citation + correctly anchored reference file."""
    case_dir = make_case(
        cases_root,
        case_id,
        reference_type="experimental",
        reference_files={"cp_curve": "reference/data.csv"},
        file_contents={"reference/data.csv": "x,cp\n0.0,1.0\n"},
    )
    digest = sha256_file(case_dir / "reference/data.csv")
    (case_dir / "provenance.yaml").write_text(
        yaml.safe_dump(
            {
                "citation": LADSON_CITATION,
                "file_hashes": {"reference/data.csv": digest},
            }
        ),
        encoding="utf-8",
    )
    return case_dir


# ============================================================================
# derive_honesty (mechanical, fail-closed)
# ============================================================================


@pytest.mark.parametrize(
    ("reference_type", "citation", "expected"),
    [
        ("experimental", LADSON_CITATION, "REAL"),
        ("dns", "Ghia et al., 1982", "REAL"),
        ("experimental", None, "DECLARED-NOT-VERIFIED"),
        ("experimental", "", "DECLARED-NOT-VERIFIED"),
        ("experimental", "   ", "DECLARED-NOT-VERIFIED"),
        ("dns", None, "DECLARED-NOT-VERIFIED"),
        ("analytical", None, "ANALYTIC"),
        ("analytical", "Blasius 1908", "ANALYTIC"),
        ("manufactured", None, "MANUFACTURED"),
        ("previous_run", None, "PREVIOUS_RUN"),
        (None, None, "SURROGATE"),
        (None, "citation without reference", "SURROGATE"),
        ("wind_tunnel_gossip", "some citation", "DECLARED-NOT-VERIFIED"),
    ],
)
def test_derive_honesty(reference_type: str | None, citation: str | None, expected: str) -> None:
    assert derive_honesty(reference_type, citation) == expected


# ============================================================================
# audit_case: happy path
# ============================================================================


def test_audit_experimental_with_citation_and_valid_anchor_is_real(tmp_path: Path) -> None:
    case_dir = anchored_experimental_case(tmp_path)
    record = audit_case(case_dir)
    assert record.honesty == "REAL"
    assert record.citation == LADSON_CITATION
    assert record.file_status == {"reference/data.csv": "ok"}
    assert record.file_hashes["reference/data.csv"] == sha256_file(
        case_dir / "reference/data.csv"
    )
    # fail-closed default: never verified unless explicitly declared
    assert record.transcription_verified is False


def test_audit_analytical_unanchored_file_keeps_analytic(tmp_path: Path) -> None:
    case_dir = make_case(
        tmp_path,
        "case_analytic",
        category="verification",
        reference_type="analytical",
        reference_files={"blasius": "reference/blasius.csv"},
        file_contents={"reference/blasius.csv": "x,cf\n0.1,0.005\n"},
    )
    record = audit_case(case_dir)
    assert record.honesty == "ANALYTIC"
    assert record.file_status == {"reference/blasius.csv": "unanchored"}


def test_audit_case_without_reference_is_surrogate(tmp_path: Path) -> None:
    case_dir = make_case(tmp_path, "case_surrogate", reference_type=None)
    record = audit_case(case_dir)
    assert record.honesty == "SURROGATE"
    assert record.reference_type == "none"


# ============================================================================
# Tamper witnesses (mandatory: the gate must bite)
# ============================================================================


def test_tamper_witness_one_byte_flip_downgrades(tmp_path: Path) -> None:
    """Flipping a single byte of an anchored reference file MUST downgrade."""
    case_dir = anchored_experimental_case(tmp_path)
    assert audit_case(case_dir).honesty == "REAL"  # pre-tamper baseline

    ref = case_dir / "reference/data.csv"
    data = bytearray(ref.read_bytes())
    data[0] ^= 0x01
    ref.write_bytes(bytes(data))

    record = audit_case(case_dir)
    assert record.honesty == "DECLARED-NOT-VERIFIED"
    assert record.file_status == {"reference/data.csv": "drift"}
    assert any("drift" in n for n in record.notes)


def test_tamper_witness_citation_removal_is_dnv(tmp_path: Path) -> None:
    """Deleting the citation from an experimental case MUST grade DNV."""
    case_dir = anchored_experimental_case(tmp_path)
    assert audit_case(case_dir).honesty == "REAL"  # pre-tamper baseline

    prov = yaml.safe_load((case_dir / "provenance.yaml").read_text(encoding="utf-8"))
    del prov["citation"]
    (case_dir / "provenance.yaml").write_text(yaml.safe_dump(prov), encoding="utf-8")

    assert audit_case(case_dir).honesty == "DECLARED-NOT-VERIFIED"


def test_tamper_witness_hand_filled_honesty_is_rejected(tmp_path: Path) -> None:
    """honesty is derived, never declared: hand-filling it in provenance.yaml
    is an unknown field (extra='forbid') and fails closed to DNV."""
    case_dir = anchored_experimental_case(tmp_path)
    prov = yaml.safe_load((case_dir / "provenance.yaml").read_text(encoding="utf-8"))
    prov["honesty"] = "REAL"
    (case_dir / "provenance.yaml").write_text(yaml.safe_dump(prov), encoding="utf-8")

    record = audit_case(case_dir)
    assert record.honesty == "DECLARED-NOT-VERIFIED"
    assert any("invalid provenance.yaml" in n for n in record.notes)


def test_tamper_witness_shipped_naca0012_anchor_bites(tmp_path: Path) -> None:
    """The shipped naca0012 provenance.yaml anchors must bite on real files."""
    src = REAL_CASES_DIR / "validation" / "naca0012"
    case_dir = tmp_path / "validation" / "naca0012"
    shutil.copytree(src, case_dir)

    assert audit_case(case_dir).honesty == "REAL"  # pre-tamper baseline

    ref = case_dir / "reference" / "ladson1988.csv"
    ref.write_bytes(ref.read_bytes() + b"X")

    record = audit_case(case_dir)
    assert record.honesty == "DECLARED-NOT-VERIFIED"
    assert record.file_status["reference/ladson1988.csv"] == "drift"


# ============================================================================
# Fail-closed on missing/invalid data (report, never crash)
# ============================================================================


def test_missing_anchored_file_reports_missing_and_downgrades(tmp_path: Path) -> None:
    case_dir = anchored_experimental_case(tmp_path)
    (case_dir / "reference/data.csv").unlink()
    record = audit_case(case_dir)
    assert record.honesty == "DECLARED-NOT-VERIFIED"
    assert record.file_status == {"reference/data.csv": "missing"}
    assert "reference/data.csv" not in record.file_hashes


def test_real_mock_missing_reference_reports_missing_without_crash() -> None:
    """cases/smoke/mock_missing_reference points at a nonexistent file on purpose."""
    records = {r.case_id: r for r in audit_all(REAL_CASES_DIR)}
    record = records["mock_missing_reference"]
    assert record.file_status == {"reference/nonexistent.json": "missing"}
    assert record.honesty == "DECLARED-NOT-VERIFIED"


def test_invalid_case_yaml_yields_fail_closed_record(tmp_path: Path) -> None:
    case_dir = tmp_path / "validation" / "broken"
    case_dir.mkdir(parents=True)
    (case_dir / "case.yaml").write_text("id: broken\nname: only-two-fields\n", encoding="utf-8")
    record = audit_case(case_dir)
    assert record.case_id == "broken"
    assert record.reference_type == "invalid"
    assert record.honesty == "DECLARED-NOT-VERIFIED"


def test_invalid_provenance_yaml_syntax_fails_closed(tmp_path: Path) -> None:
    case_dir = anchored_experimental_case(tmp_path)
    (case_dir / "provenance.yaml").write_text("citation: [unclosed", encoding="utf-8")
    record = audit_case(case_dir)
    assert record.honesty == "DECLARED-NOT-VERIFIED"
    assert any("invalid provenance.yaml" in n for n in record.notes)


# ============================================================================
# Path resolution (relative to case dir, '..' allowed)
# ============================================================================


def test_anchor_path_with_dotdot_resolves_relative_to_case_dir(tmp_path: Path) -> None:
    shared = tmp_path / "validation" / "shared_ref.csv"
    shared.parent.mkdir(parents=True)
    shared.write_text("x,cp\n0.0,1.0\n", encoding="utf-8")
    case_dir = make_case(
        tmp_path,
        "case_dotdot",
        reference_type="experimental",
        reference_files={"cp_curve": "../shared_ref.csv"},
        provenance={
            "citation": LADSON_CITATION,
            "file_hashes": {"../shared_ref.csv": sha256_file(shared)},
        },
    )
    record = audit_case(case_dir)
    assert record.honesty == "REAL"
    assert record.file_status == {"../shared_ref.csv": "ok"}


# ============================================================================
# audit_all: scan + real shipped provenance
# ============================================================================


def test_audit_all_nonexistent_dir_returns_empty(tmp_path: Path) -> None:
    assert audit_all(tmp_path / "nope") == []


def test_audit_all_scans_and_sorts(tmp_path: Path) -> None:
    anchored_experimental_case(tmp_path, "zeta")
    anchored_experimental_case(tmp_path, "alpha")
    records = audit_all(tmp_path)
    assert [r.case_id for r in records] == ["alpha", "zeta"]


def test_real_naca_series_is_real_with_honest_transcription_flag() -> None:
    records = {r.case_id: r for r in audit_all(REAL_CASES_DIR)}
    # NOTE: the cases/validation/naca0012 directory declares id 'naca0012_a0'.
    for case_id in ("naca0012_a0", "naca0012_a5", "naca0012_a10", "naca0012_a15"):
        record = records[case_id]
        assert record.honesty == "REAL", f"{case_id}: {record.notes}"
        assert "NASA TM-4074" in (record.citation or "")
        assert record.transcription_verified is False
        assert all(status == "ok" for status in record.file_status.values()), (
            f"{case_id}: {record.file_status}"
        )


def test_real_flat_plate_su2_is_analytic() -> None:
    records = {r.case_id: r for r in audit_all(REAL_CASES_DIR)}
    record = records["flat_plate_su2"]
    assert record.honesty == "ANALYTIC"
    assert record.file_status == {"reference/blasius_cf.csv": "ok"}


# ============================================================================
# Model contracts
# ============================================================================


def test_record_and_declaration_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProvenanceDeclaration.model_validate({"citation": "x", "bogus": 1})
    with pytest.raises(ValidationError):
        ProvenanceRecord.model_validate(
            {"case_id": "c", "reference_type": "none", "honesty": "SURROGATE", "bogus": 1}
        )


def test_record_rejects_invalid_honesty_literal() -> None:
    with pytest.raises(ValidationError):
        ProvenanceRecord.model_validate(
            {"case_id": "c", "reference_type": "none", "honesty": "TOTALLY_LEGIT"}
        )
