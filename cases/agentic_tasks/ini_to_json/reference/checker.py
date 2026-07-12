"""Checker for ini_to_json (Architecture v5.0 §4).

Reads ``<submission_dir>/config.json`` and compares it against
``reference/expected.json`` by EXACT structural equality: the same sections,
the same keys per section, and string-typed values (so a submission that
coerces ``8080`` to the integer ``8080`` fails — ``8080 != "8080"``).

Standard library only, by construction and by ``validate_checker``'s
admission-time scan (no ``subprocess``/``socket``/``ctypes``/``importlib``,
no ``eval``/``exec``).

Outputs ``{"success": bool, "evidence": [...]}`` as the trailing stdout
line, per the score_agentic() contract. Exit code is always 0 on a
well-formed verdict; the verdict lives in the payload, not the exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    submission_dir = Path(sys.argv[1])
    case_dir = Path(__file__).resolve().parent.parent
    expected = json.loads((case_dir / "reference" / "expected.json").read_text(encoding="utf-8"))

    evidence: list[str] = []
    config_path = submission_dir / "config.json"
    if not config_path.is_file():
        print(json.dumps({"success": False, "evidence": ["config.json not found in submission"]}))
        return

    try:
        actual = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "evidence": [f"config.json is not valid JSON: {e}"]}))
        return

    if not isinstance(actual, dict):
        print(
            json.dumps({"success": False, "evidence": ["config.json top level is not an object"]})
        )
        return

    ok = True

    expected_sections = set(expected)
    actual_sections = set(actual)
    if actual_sections != expected_sections:
        ok = False
        missing = sorted(expected_sections - actual_sections)
        extra = sorted(actual_sections - expected_sections)
        evidence.append(f"section set mismatch: missing={missing}, unexpected={extra}")
    else:
        evidence.append("section set matched")

    # Per-section exact key/value comparison, only for sections present in both.
    for section in sorted(expected_sections & actual_sections):
        exp_body = expected[section]
        got_body = actual[section]
        if got_body == exp_body:
            evidence.append(f"section '{section}' matched")
        else:
            ok = False
            evidence.append(
                f"section '{section}' mismatch: got {got_body!r}, expected {exp_body!r}"
            )

    print(json.dumps({"success": ok, "evidence": evidence}))


if __name__ == "__main__":
    main()
