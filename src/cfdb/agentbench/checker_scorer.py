"""Agentic-domain judging primitive: state-based checker execution
(Architecture v5.0 §4).

:func:`score_agentic` runs a case's own ``reference/checker.py`` as a
subprocess against a submission directory and reduces its stdout to a
:class:`CheckerVerdict` (WorkArena/OSWorld/tau-bench-style state-based
judging). The checker is executed by the cfdb process itself — the
submission under evaluation never touches it.

Threat model (§4): the checker is case-author-written, human-reviewed at
case admission, and sha256-anchored into the scoring contract's frozen map
— it is **trusted judging material**, and the risk this module defends
against is carelessness (an accidental crash, a stray ``print`` that
corrupts the JSON payload), not malice. :func:`validate_checker` is
consequently a supply-chain *hygiene* check at admission time, not a
security boundary — see its docstring.

Any of the following collapses :func:`score_agentic` to
``mode="CHECKER_ERROR"``, never to a pass and never to a plain fail — a
checker that misbehaves is a broken ruler, not a failed submission:

- the checker process cannot be started, crashes (non-zero exit), or times out;
- its stdout tail is not parseable JSON, or is not a JSON object;
- the parsed payload's ``"success"`` field is missing or is not a bool;
- the parsed payload's ``"evidence"`` field (when present) is not a
  ``list[str]``.
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

CheckerMode = Literal["CHECKER_OK", "CHECKER_ERROR"]

_DENIED_MODULES: frozenset[str] = frozenset({"subprocess", "socket", "ctypes", "importlib"})
"""Module roots whose import trips :func:`validate_checker` (accidental
outbound calls / dynamic loading in judging material)."""

_DENIED_CALLS: frozenset[str] = frozenset({"eval", "exec"})
"""Bare function names whose call trips :func:`validate_checker`."""


class CheckerVerdict(BaseModel):
    """Reduced outcome of running one case's ``checker.py`` against a submission."""

    model_config = ConfigDict(extra="forbid")

    success: bool | None = None
    """The checker's judgment. Always ``None`` when ``mode ==
    "CHECKER_ERROR"`` — a broken ruler reports "could not judge", never a
    pass and never a plain fail."""

    evidence: list[str] = Field(default_factory=list)
    """Human-readable evidence strings from the checker's JSON payload.
    Empty when the payload omitted ``"evidence"`` or when ``mode ==
    "CHECKER_ERROR"``."""

    mode: CheckerMode = "CHECKER_ERROR"
    """``"CHECKER_OK"``: the checker ran to completion and reported a
    well-formed bool verdict. ``"CHECKER_ERROR"``: the checker itself is
    unusable for this run (see module docstring for the full list of
    triggers)."""

    error: str | None = None
    """Diagnostic message when ``mode == "CHECKER_ERROR"``; ``None`` otherwise."""


class CheckerValidationResult(BaseModel):
    """Result of the admission-time static hygiene scan (:func:`validate_checker`)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    """True iff the AST scan found no denied import or call."""

    violations: list[str] = Field(default_factory=list)
    """Sorted, deduplicated human-readable violation descriptions. Empty
    iff ``ok is True``."""


def _error_verdict(message: str) -> CheckerVerdict:
    """Build a CHECKER_ERROR verdict (fail-closed: never a pass, never a plain fail)."""
    logger.error("checker error: %s", message)
    return CheckerVerdict(success=None, evidence=[], mode="CHECKER_ERROR", error=message)


def _extract_json_tail(stdout: str) -> dict | None:
    """Parse the last non-blank stdout line as a JSON object.

    Checkers may print progress/debug lines before the final verdict; only
    the trailing non-blank line is treated as the machine-readable payload.

    Args:
        stdout: Full captured stdout of the checker subprocess.

    Returns:
        The parsed JSON object, or ``None`` when stdout has no non-blank
        line, the trailing line is not valid JSON, or it does not decode
        to a JSON object (dict).
    """
    lines = [line for line in stdout.splitlines() if line.strip() != ""]
    if len(lines) == 0:
        return None
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def score_agentic(
    case_dir: Path,
    submission_dir: Path,
    timeout_sec: int = 60,
) -> CheckerVerdict:
    """Run a case's ``reference/checker.py`` against a submission directory.

    Args:
        case_dir: Case directory; the checker at
            ``case_dir/reference/checker.py`` is executed with the current
            interpreter (``sys.executable``), never with an implicit
            ``python``/shell resolution.
        submission_dir: Directory holding the agent's produced artifacts,
            passed to the checker as its sole positional argument.
        timeout_sec: Wall-clock timeout for the checker subprocess.

    Returns:
        A :class:`CheckerVerdict`. See the module docstring for the exact
        set of conditions that force ``mode="CHECKER_ERROR"``.
    """
    checker_path = case_dir / "reference" / "checker.py"
    if not checker_path.is_file():
        return _error_verdict(f"checker not found: {checker_path}")

    try:
        # -B: a checker legitimately importing a sibling module (e.g.
        # reference/helper.py) must not generate __pycache__ inside the
        # anchored reference/ tree — a bytecode cache appearing mid-scoring
        # would trip the post-run manifest re-verification as a false ruler
        # drift (Codex R2 P2).
        proc = subprocess.run(
            [sys.executable, "-B", str(checker_path), str(submission_dir)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return _error_verdict(f"checker timed out after {timeout_sec}s: {checker_path}")
    except OSError as e:
        return _error_verdict(f"checker process could not be started: {e}")

    if proc.returncode != 0:
        stderr_tail = proc.stderr.strip()[-2000:]
        return _error_verdict(
            f"checker exited with code {proc.returncode}: {checker_path} "
            f"(stderr tail: {stderr_tail!r})"
        )

    payload = _extract_json_tail(proc.stdout)
    if payload is None:
        return _error_verdict(f"checker stdout is not parseable JSON: {checker_path}")

    success = payload.get("success")
    if isinstance(success, bool) is False:
        return _error_verdict(
            f"checker payload 'success' is not a bool (got {success!r}): {checker_path}"
        )

    evidence_raw = payload.get("evidence", [])
    is_valid_evidence = isinstance(evidence_raw, list) and all(
        isinstance(item, str) for item in evidence_raw
    )
    if is_valid_evidence is False:
        return _error_verdict(
            f"checker payload 'evidence' is not a list[str] (got {evidence_raw!r}): {checker_path}"
        )

    return CheckerVerdict(success=success, evidence=evidence_raw, mode="CHECKER_OK", error=None)


def validate_checker(path: Path) -> CheckerValidationResult:
    """Static supply-chain hygiene scan for a ``checker.py`` at case admission.

    This is **not** a security boundary — it is a defense against
    carelessness, not malice (see module docstring). The AST scan flags:

    - ``import``/``from ... import`` of :data:`_DENIED_MODULES`
      (``subprocess``, ``socket``, ``ctypes``, ``importlib``);
    - a bare call to :data:`_DENIED_CALLS` (``eval``, ``exec``).

    A determined author can trivially evade this scan (e.g. via
    ``__import__("os")`` or string-built attribute access); that is an
    explicitly accepted gap for this v5.0 checker trust model, where
    vetting a deliberately adversarial checker is a human-admission-review
    concern, not a static-analysis one.

    Args:
        path: ``checker.py`` file to scan.

    Returns:
        ``CheckerValidationResult(ok=False, violations=[...])`` when the
        scan finds a denied construct or the file has a syntax error;
        ``ok=True`` with an empty violation list otherwise.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return CheckerValidationResult(ok=False, violations=[f"syntax error: {e}"])

    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _DENIED_MODULES:
                    violations.add(f"import '{alias.name}' at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _DENIED_MODULES:
                violations.add(f"from '{node.module}' import at line {node.lineno}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _DENIED_CALLS
        ):
            violations.add(f"call to '{node.func.id}' at line {node.lineno}")

    sorted_violations = sorted(violations)
    return CheckerValidationResult(ok=len(sorted_violations) == 0, violations=sorted_violations)
