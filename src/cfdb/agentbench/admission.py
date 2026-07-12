"""Golden-solution admission runs for coding cases (R8 backlog).

Architecture v5.0 §2 requires a coding case's golden solution to pass the
frozen hidden tests in the sandbox three consecutive times BEFORE the case
is admitted (SWE-bench-Verified style flake screening). v5.0 allowed the
case author to run this locally and commit a summary; this module makes
the evidence systematic and machine-written.

The record lands at ``<case_dir>/admission.json`` — deliberately OUTSIDE
the frozen trees (``case.yaml`` / ``reference/`` / ``visible/``), so
writing it never drifts an existing ruler. The record is evidence of a
process, not a scoring input: nothing in the judging path reads it.

A failed admission is still written (``all_passed: false``) — an honest
paper trail beats a silently absent one — but the CLI exits non-zero so
automation cannot mistake it for a pass.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cfdb.agentbench.contract import JUDGE_IMAGE_KEY, init_contract
from cfdb.registry import CaseRegistry

ADMISSION_FILENAME = "admission.json"
DEFAULT_ADMISSION_RUNS = 3


class AdmissionRunVerdict(BaseModel):
    """One golden run's reduced outcome."""

    model_config = ConfigDict(extra="forbid")

    valid: bool | None = None
    score: float | None = None
    scored_at: str = ""
    notes: list[str] = Field(default_factory=list)


class AdmissionRecord(BaseModel):
    """Machine-written evidence of a case's golden admission runs."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    runs: int
    all_passed: bool
    """True only if EVERY run was valid with score == 1.0 (fail-closed:
    a single flaky run fails admission — that is the point)."""

    golden_attempt_id: str
    """Content identity of the golden tree that was admitted (same digest
    scheme as ledger rows) — later edits to the golden are visible."""

    judge_image_id: str | None = None
    verdicts: list[AdmissionRunVerdict] = Field(default_factory=list)
    created_at: str = ""


def run_admission(
    case_id: str,
    registry: CaseRegistry,
    runs: int = DEFAULT_ADMISSION_RUNS,
    backend_factory=None,
) -> AdmissionRecord:
    """Score the case's golden solution ``runs`` times and record evidence.

    Args:
        case_id: Coding case with a ``reference/golden/`` tree.
        registry: Case registry.
        runs: Consecutive sandbox runs required (>= 1).
        backend_factory: Test seam forwarded to
            :func:`~cfdb.agentbench.sandbox_scorer.score_coding`; production
            callers leave None (real sandbox, canary enforced).

    Returns:
        The admission record (also written to ``<case_dir>/admission.json``).

    Raises:
        ValueError: If the case is not coding-domain, has no golden tree,
            or ``runs`` < 1.
    """
    from cfdb.agentbench.sandbox_scorer import score_coding
    from cfdb.agentbench.scorer import _submission_digest

    if runs < 1:
        raise ValueError(f"admission requires at least 1 run (got {runs})")
    case = registry.load(case_id)
    case_dir = registry.get_case_dir(case_id)
    if case.domain != "coding":
        raise ValueError(
            f"admission runs are defined for coding cases only (case '{case_id}' "
            f"has domain '{case.domain}')"
        )
    golden_dir = case_dir / "reference" / "golden"
    if not golden_dir.is_dir() or not any(golden_dir.iterdir()):
        raise ValueError(f"no golden solution at {golden_dir} — nothing to admit")

    # In-memory ruler snapshot: admission judges against the CURRENT case
    # material; it neither reads nor writes the saved contract.
    contract = init_contract(case_id, registry)

    verdicts: list[AdmissionRunVerdict] = []
    for _ in range(runs):
        result = score_coding(case, case_dir, golden_dir, contract, backend_factory=backend_factory)
        verdicts.append(
            AdmissionRunVerdict(
                valid=result.valid,
                score=result.score,
                scored_at=result.scored_at,
                notes=result.notes,
            )
        )

    all_passed = all(v.valid is True and v.score == 1.0 for v in verdicts)
    record = AdmissionRecord(
        case_id=case_id,
        runs=runs,
        all_passed=all_passed,
        golden_attempt_id=_submission_digest(golden_dir),
        judge_image_id=contract.frozen.get(JUDGE_IMAGE_KEY),
        verdicts=verdicts,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # Atomic write (same discipline as save_contract): admission evidence
    # must never exist as a torn file.
    record_path = case_dir / ADMISSION_FILENAME
    tmp_path = record_path.with_name(record_path.name + ".tmp")
    try:
        tmp_path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, record_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return record


def load_admission(case_dir: Path) -> AdmissionRecord | None:
    """Read a case's admission record if one exists.

    Args:
        case_dir: Case directory.

    Returns:
        The record, or None when absent or unreadable (reported by the
        caller, never fabricated).
    """
    path = case_dir / ADMISSION_FILENAME
    if not path.is_file():
        return None
    try:
        return AdmissionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = [
    "ADMISSION_FILENAME",
    "DEFAULT_ADMISSION_RUNS",
    "AdmissionRecord",
    "AdmissionRunVerdict",
    "load_admission",
    "run_admission",
]
