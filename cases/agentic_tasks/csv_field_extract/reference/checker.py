"""Checker for csv_field_extract (Architecture v5.0 §4).

Reads ``<submission_dir>/summary.json`` and compares it against
``reference/expected.json``: exact match for ``total_employees`` and
``by_department``, quasi-exact match (case/whitespace-insensitive) for
``highest_paid_name`` via a self-contained copy of the string normalization
rule (checker.py runs standalone under ``sys.executable``, isolated from
the ``cfdb`` package -- see admission notes in this case's directory).

Standard library only, by construction and by ``validate_checker``'s
admission-time scan (no ``subprocess``/``socket``/``ctypes``/``importlib``,
no ``eval``/``exec``).

Outputs ``{"success": bool, "evidence": [...]}`` as the trailing stdout
line, per the score_agentic() contract.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path


def _normalize_string(value: str) -> str:
    """Quasi-exact-match string normalization (mirrors
    cfdb.agentbench.normalize._normalize_string, NORMALIZE_VERSION="1").
    Inlined because this checker must run without importing cfdb."""
    text = unicodedata.normalize("NFKC", value)
    text = text.strip().casefold()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(".,;:!?\"'()[]{}<>")
    return text


def main() -> None:
    submission_dir = Path(sys.argv[1])
    case_dir = Path(__file__).resolve().parent.parent
    expected = json.loads((case_dir / "reference" / "expected.json").read_text(encoding="utf-8"))

    evidence: list[str] = []
    summary_path = submission_dir / "summary.json"
    if not summary_path.is_file():
        print(json.dumps({"success": False, "evidence": ["summary.json not found in submission"]}))
        return

    try:
        actual = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "evidence": [f"summary.json is not valid JSON: {e}"]}))
        return

    if not isinstance(actual, dict):
        print(
            json.dumps(
                {"success": False, "evidence": ["summary.json top level is not an object"]}
            )
        )
        return

    ok = True

    if actual.get("total_employees") != expected["total_employees"]:
        ok = False
        evidence.append(
            f"total_employees mismatch: got {actual.get('total_employees')!r}, "
            f"expected {expected['total_employees']!r}"
        )
    else:
        evidence.append("total_employees matched")

    if actual.get("by_department") != expected["by_department"]:
        ok = False
        evidence.append(
            f"by_department mismatch: got {actual.get('by_department')!r}, "
            f"expected {expected['by_department']!r}"
        )
    else:
        evidence.append("by_department matched")

    got_name = actual.get("highest_paid_name")
    if not isinstance(got_name, str) or _normalize_string(got_name) != _normalize_string(
        expected["highest_paid_name"]
    ):
        ok = False
        evidence.append(
            f"highest_paid_name mismatch: got {got_name!r}, "
            f"expected {expected['highest_paid_name']!r}"
        )
    else:
        evidence.append("highest_paid_name matched (quasi-exact)")

    print(json.dumps({"success": ok, "evidence": evidence}))


if __name__ == "__main__":
    main()
