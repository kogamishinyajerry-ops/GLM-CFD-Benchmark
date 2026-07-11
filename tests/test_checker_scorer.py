"""Tests for cfdb.agentbench.checker_scorer (Architecture v5.0 §4).

Covers the mechanical stdout-JSON reduction in score_agentic() and the
admission-time static hygiene scan in validate_checker(), including the
mandatory tamper witnesses (§6):

1. Baseline PASS -> single-byte tamper of checker.py -> CHECKER_ERROR
   (checker exit code flips from 0 to a Python traceback / nonzero exit).
2. checker.py that raises at runtime -> CHECKER_ERROR, never a silent pass.
3. checker.py that prints malformed/non-JSON output -> CHECKER_ERROR.
4. validate_checker() rejects each denied import/call, accepts a clean file.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from cfdb.agentbench.checker_scorer import (
    CheckerValidationResult,
    CheckerVerdict,
    score_agentic,
    validate_checker,
)

CHECKER_OK_TRUE = """\
import json
import sys

def main() -> None:
    submission_dir = sys.argv[1]
    print(json.dumps({"success": True, "evidence": [f"checked {submission_dir}"]}))

if __name__ == "__main__":
    main()
"""

CHECKER_OK_FALSE = """\
import json

def main() -> None:
    print(json.dumps({"success": False, "evidence": ["file missing"]}))

if __name__ == "__main__":
    main()
"""

CHECKER_RAISES = """\
def main() -> None:
    raise RuntimeError("boom")

if __name__ == "__main__":
    main()
"""

CHECKER_PRINTS_GARBAGE = """\
def main() -> None:
    print("not json at all")

if __name__ == "__main__":
    main()
"""

CHECKER_MISSING_SUCCESS_KEY = """\
import json

def main() -> None:
    print(json.dumps({"evidence": ["no success key"]}))

if __name__ == "__main__":
    main()
"""

CHECKER_SUCCESS_NOT_BOOL = """\
import json

def main() -> None:
    print(json.dumps({"success": "yes", "evidence": []}))

if __name__ == "__main__":
    main()
"""

CHECKER_EVIDENCE_NOT_LIST_OF_STR = """\
import json

def main() -> None:
    print(json.dumps({"success": True, "evidence": [1, 2, 3]}))

if __name__ == "__main__":
    main()
"""

CHECKER_SLEEPS_FOREVER = """\
import time

def main() -> None:
    time.sleep(30)

if __name__ == "__main__":
    main()
"""

CHECKER_LOGS_THEN_VERDICT = """\
import json

def main() -> None:
    print("progress: scanning submission")
    print("progress: done")
    print(json.dumps({"success": True, "evidence": ["ok"]}))

if __name__ == "__main__":
    main()
"""


def _write_checker(case_dir: Path, source: str) -> Path:
    reference_dir = case_dir / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    checker_path = reference_dir / "checker.py"
    checker_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return checker_path


@pytest.fixture
def submission_dir(tmp_path: Path) -> Path:
    sub_dir = tmp_path / "submission"
    sub_dir.mkdir()
    (sub_dir / "output.txt").write_text("hello", encoding="utf-8")
    return sub_dir


# ---------------------------------------------------------------------------
# score_agentic: happy paths
# ---------------------------------------------------------------------------


def test_score_agentic_success_true(tmp_path: Path, submission_dir: Path) -> None:
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_OK_TRUE)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_OK"
    assert verdict.success is True
    assert verdict.error is None
    assert len(verdict.evidence) == 1


def test_score_agentic_success_false_is_not_an_error(
    tmp_path: Path, submission_dir: Path
) -> None:
    """A well-formed 'success: false' verdict is CHECKER_OK, not CHECKER_ERROR."""
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_OK_FALSE)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_OK"
    assert verdict.success is False
    assert verdict.error is None


def test_score_agentic_tolerates_leading_progress_lines(
    tmp_path: Path, submission_dir: Path
) -> None:
    """Only the trailing non-blank stdout line is parsed as the verdict."""
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_LOGS_THEN_VERDICT)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_OK"
    assert verdict.success is True


# ---------------------------------------------------------------------------
# score_agentic: CHECKER_ERROR triggers (never pass, never a silent plain fail)
# ---------------------------------------------------------------------------


def test_score_agentic_missing_checker_file(tmp_path: Path, submission_dir: Path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "reference").mkdir(parents=True)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert verdict.error is not None
    assert "not found" in verdict.error


def test_score_agentic_checker_raises_at_runtime(
    tmp_path: Path, submission_dir: Path
) -> None:
    """witness: a checker that raises must land CHECKER_ERROR, never pass/plain-fail."""
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_RAISES)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert verdict.error is not None
    assert "exited with code" in verdict.error


def test_score_agentic_malformed_json_output(tmp_path: Path, submission_dir: Path) -> None:
    """witness: garbage stdout must land CHECKER_ERROR, not be coerced to a verdict."""
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_PRINTS_GARBAGE)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert "not parseable JSON" in verdict.error


def test_score_agentic_missing_success_key(tmp_path: Path, submission_dir: Path) -> None:
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_MISSING_SUCCESS_KEY)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert "not a bool" in verdict.error


def test_score_agentic_success_not_bool(tmp_path: Path, submission_dir: Path) -> None:
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_SUCCESS_NOT_BOOL)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert "not a bool" in verdict.error


def test_score_agentic_evidence_not_list_of_str(
    tmp_path: Path, submission_dir: Path
) -> None:
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_EVIDENCE_NOT_LIST_OF_STR)

    verdict = score_agentic(case_dir, submission_dir)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert "evidence" in verdict.error


def test_score_agentic_timeout(tmp_path: Path, submission_dir: Path) -> None:
    """witness: a hanging checker must land CHECKER_ERROR via timeout, not hang forever."""
    case_dir = tmp_path / "case"
    _write_checker(case_dir, CHECKER_SLEEPS_FOREVER)

    verdict = score_agentic(case_dir, submission_dir, timeout_sec=1)

    assert verdict.mode == "CHECKER_ERROR"
    assert verdict.success is None
    assert "timed out" in verdict.error


# ---------------------------------------------------------------------------
# tamper witness: baseline PASS -> single-byte tamper -> CHECKER_ERROR
# ---------------------------------------------------------------------------


def test_tamper_witness_single_byte_flip_breaks_checker(
    tmp_path: Path, submission_dir: Path
) -> None:
    """Full witness paradigm (§6): baseline PASS -> tamper one byte -> fail-closed flip."""
    case_dir = tmp_path / "case"
    checker_path = _write_checker(case_dir, CHECKER_OK_TRUE)

    # Baseline: untampered checker reports a clean CHECKER_OK/success=True verdict.
    baseline = score_agentic(case_dir, submission_dir)
    assert baseline.mode == "CHECKER_OK"
    assert baseline.success is True

    # Tamper: flip a single byte so the file no longer parses as Python
    # (True -> Tru€, an invalid identifier/syntax error at import time).
    original = checker_path.read_bytes()
    tampered = original.replace(b"True", b"Tru\xc2\xa9")
    assert tampered != original
    checker_path.write_bytes(tampered)

    # Assert: the tamper flips the outcome to fail-closed CHECKER_ERROR —
    # never silently re-interpreted as a pass or a plain fail.
    tampered_verdict = score_agentic(case_dir, submission_dir)
    assert tampered_verdict.mode == "CHECKER_ERROR"
    assert tampered_verdict.success is None
    assert tampered_verdict.error is not None


# ---------------------------------------------------------------------------
# validate_checker: admission-time static hygiene scan
# ---------------------------------------------------------------------------


def test_validate_checker_accepts_clean_file(tmp_path: Path) -> None:
    path = tmp_path / "checker.py"
    path.write_text(CHECKER_OK_TRUE, encoding="utf-8")

    result = validate_checker(path)

    assert isinstance(result, CheckerValidationResult)
    assert result.ok is True
    assert result.violations == []


@pytest.mark.parametrize(
    ("source", "expected_fragment"),
    [
        ("import subprocess\n", "subprocess"),
        ("import subprocess as sp\n", "subprocess"),
        ("from subprocess import run\n", "subprocess"),
        ("import socket\n", "socket"),
        ("from socket import socket\n", "socket"),
        ("import ctypes\n", "ctypes"),
        ("import importlib\n", "importlib"),
        ("from importlib import import_module\n", "importlib"),
        ("eval('1+1')\n", "eval"),
        ("exec('x = 1')\n", "exec"),
    ],
)
def test_validate_checker_rejects_denied_constructs(
    tmp_path: Path, source: str, expected_fragment: str
) -> None:
    path = tmp_path / "checker.py"
    path.write_text(source, encoding="utf-8")

    result = validate_checker(path)

    assert result.ok is False
    assert len(result.violations) >= 1
    assert any(expected_fragment in v for v in result.violations)


def test_validate_checker_rejects_multiple_violations_at_once(tmp_path: Path) -> None:
    path = tmp_path / "checker.py"
    path.write_text("import subprocess\nimport socket\neval('1')\n", encoding="utf-8")

    result = validate_checker(path)

    assert result.ok is False
    assert len(result.violations) == 3


def test_validate_checker_syntax_error_is_a_violation(tmp_path: Path) -> None:
    path = tmp_path / "checker.py"
    path.write_text("def broken(:\n", encoding="utf-8")

    result = validate_checker(path)

    assert result.ok is False
    assert any("syntax error" in v for v in result.violations)


def test_validate_checker_allows_os_path_and_json(tmp_path: Path) -> None:
    """Standard-library modules outside the deny-list are unaffected."""
    path = tmp_path / "checker.py"
    path.write_text(
        "import json\nimport os.path\n\ndef main():\n    return os.path.exists('x')\n",
        encoding="utf-8",
    )

    result = validate_checker(path)

    assert result.ok is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# CheckerVerdict model shape
# ---------------------------------------------------------------------------


def test_checker_verdict_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        CheckerVerdict.model_validate(
            {"success": True, "evidence": [], "mode": "CHECKER_OK", "unexpected": 1}
        )
