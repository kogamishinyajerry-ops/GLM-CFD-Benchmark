"""Coding-domain sandbox scoring (v5.0 Wave B/D2, Architecture §3.2-3.3).

Runs the frozen ``hidden_tests`` suite against a submission through an
:class:`~cfdb.execution.base.ExecutionBackend`, reconciles the result
against the frozen contract, and recomputes ``pass_rate`` as an ordinary
gated metric — no self-reported number from the submission is ever trusted.

This module is invoked from :func:`cfdb.agentbench.scorer.score_submission`
for ``case.domain == "coding"``; it does not reuse the CFD ``Runner``
execution pipeline (that pipeline and the scoring pipeline stay independent,
per Architecture v5.0 §3.3).

Failure semantics are two-tier (Architecture v5.0 §3.3):

- **Ruler-level** (the judging material itself is compromised): pre-flight
  ``verify_frozen`` drift (checked by the caller,
  :func:`~cfdb.agentbench.scorer.score_submission`, before this module ever
  runs), or a post-run re-verification here finding the frozen material
  changed on the host during the scoring window. Both raise
  :class:`~cfdb.agentbench.contract.FrozenDriftError` — exit 3, the batch is
  aborted, nothing is ledgered for the current submission.
- **Submission-level** (the ruler is intact, the *submission* is unusable):
  missing/unparseable junitxml, a collected-test-total mismatch against the
  frozen ``expected_test_count`` (collection tampering), or an abnormal
  container exit. These invalidate the one submission (``valid=False,
  score=None``) but are ledgered like any other invalid sample — the ruler
  was never in question.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from cfdb.agentbench.contract import (
    JUDGE_IMAGE_KEY,
    FrozenDriftError,
    ScoringContract,
    resolve_judge_image_id,
    verify_frozen,
)
from cfdb.agentbench.judge_policy import assemble_score, evaluate_gates
from cfdb.agentbench.scorer import SubmissionScore, WallTimeRecord
from cfdb.schema import CaseSpec

if TYPE_CHECKING:
    from cfdb.execution.base import ExecutionBackend

logger = logging.getLogger(__name__)

HIDDEN_TESTS_REL = "reference/hidden_tests"
"""Case-dir-relative path to the frozen hidden-test suite (Architecture §3.1)."""

JUDGE_HIDDEN_TESTS = "/judge/hidden_tests"
JUDGE_SUBMISSION = "/judge/submission"
WORK_REPORT = "/work/report.xml"
WORK_BASETEMP = "/work/pytest-tmp"
WORK_TMPDIR = "/work/tmp"
REPORT_FILENAME = "report.xml"

BackendFactory = Callable[[Path, Path], "ExecutionBackend"]
"""``(case_dir, submission_dir) -> ExecutionBackend``.

Production callers leave this at its default (a real sandboxed
:class:`~cfdb.execution.docker.DockerBackend`); tests inject a stub backend
that never touches Docker."""


def _default_backend_factory(case_dir: Path, submission_dir: Path) -> ExecutionBackend:
    """Build the real sandboxed Docker backend for a coding submission.

    Not exercised by this module's unit tests (they inject a stub) — the
    three-mount sandbox profile (``--network none`` / ``--memory`` /
    ``--pids-limit`` / ``--cap-drop ALL`` / ``ro`` judge mounts) is Wave B
    §3.2 territory in :mod:`cfdb.execution.docker`; end-to-end sandbox
    exercise is the main-controller's real-container acceptance run.

    Args:
        case_dir: Case directory (source of the ro ``hidden_tests`` mount).
        submission_dir: Submission directory (source of the ro
            ``submission`` mount).

    Returns:
        A :class:`~cfdb.execution.docker.DockerBackend` configured with the
        sandbox profile and the two read-only judge mounts.
    """
    from cfdb.execution.docker import DockerBackend

    # The judge image must ship pytest preinstalled: the sandbox runs with
    # --network none, so nothing can be pip-installed at scoring time. A
    # stock python:*-slim image would fail every submission with an abnormal
    # exit. Overridable for site-local judge images; the image reference is
    # recorded in scoring notes (see score_coding) for ledger traceability.
    image = os.environ.get("CFDB_JUDGE_IMAGE", "cfdb-judge:py312")

    return DockerBackend(  # type: ignore[call-arg]
        image=image,
        sandbox=True,
        ro_mounts=[
            (case_dir / HIDDEN_TESTS_REL, JUDGE_HIDDEN_TESTS),
            (submission_dir, JUDGE_SUBMISSION),
        ],
    )


PYTEST_ARGS: list[str] = [
    JUDGE_HIDDEN_TESTS,
    f"--rootdir={JUDGE_HIDDEN_TESTS}",
    f"--confcutdir={JUDGE_HIDDEN_TESTS}",
    "-p",
    "no:cacheprovider",
    f"--basetemp={WORK_BASETEMP}",
    f"--junitxml={WORK_REPORT}",
]
"""Frozen pytest arguments (Architecture §3.3, verbatim)."""


def _pytest_command() -> list[str]:
    """Build the isolated judge bootstrap (Architecture §3.3 + Codex R0 P1).

    ``python -I`` ignores PYTHONPATH, user site-packages and sitecustomize,
    so the interpreter starts and ``pytest`` is imported from the judge
    image ONLY — a submission shipping ``sitecustomize.py`` or a
    ``pytest.py`` shadow module cannot hijack judge startup. The submission
    path is inserted onto ``sys.path`` only after pytest is already
    imported, purely so the hidden tests can import the submission.
    """
    bootstrap = (
        "import sys, pytest; "
        f"sys.path.insert(0, {JUDGE_SUBMISSION!r}); "
        f"raise SystemExit(pytest.main({PYTEST_ARGS!r}))"
    )
    return ["python", "-I", "-c", bootstrap]


def _pytest_env() -> dict[str, str]:
    """Build the frozen pytest environment (Architecture §3.3, verbatim).

    No PYTHONPATH: ``python -I`` would ignore it anyway (by design — the
    submission path travels inside the bootstrap, after pytest import).
    """
    return {
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": WORK_TMPDIR,
    }


def _parse_junit(report_path: Path, notes: list[str]) -> tuple[int, int, int, int] | None:
    """Parse a junitxml report into ``(total, failures, errors, skipped)``.

    Args:
        report_path: Host path to the ``report.xml`` produced by pytest.
        notes: Audit note sink (mutated in place).

    Returns:
        ``(total, failures, errors, skipped)``, or None (fail-closed) if the
        file is missing or cannot be parsed as valid junitxml — this never
        guesses a verdict from stdout. ``skipped`` matters: JUnit's ``tests``
        total INCLUDES skipped items, so ignoring it would let a submission
        call ``pytest.skip()`` from inside a hidden test and match the frozen
        count with zero failures (Codex R0 P1).
    """
    if not report_path.is_file():
        notes.append(f"junitxml missing at {report_path} (submission invalid)")
        return None
    try:
        root = ET.parse(report_path).getroot()
    except ET.ParseError as e:
        notes.append(f"junitxml unparseable: {e} (submission invalid)")
        return None
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        notes.append("junitxml has no <testsuite> element (submission invalid)")
        return None
    try:
        total = int(suite.get("tests", "0"))
        failures = int(suite.get("failures", "0"))
        errors = int(suite.get("errors", "0"))
        skipped = int(suite.get("skipped", "0"))
    except (TypeError, ValueError) as e:
        notes.append(f"junitxml testsuite counts unparseable: {e} (submission invalid)")
        return None
    return total, failures, errors, skipped


def score_coding(
    case: CaseSpec,
    case_dir: Path,
    submission_dir: Path,
    contract: ScoringContract,
    backend_factory: BackendFactory | None = None,
    ruler_id: str | None = None,
    work_dir: Path | None = None,
) -> SubmissionScore:
    """Score one coding-domain submission by running hidden_tests in a sandbox.

    Args:
        case: Case spec (``case.domain == "coding"``).
        case_dir: Case directory; ``case_dir/reference/hidden_tests`` holds
            the frozen judge material.
        submission_dir: Submission source tree (ro-mounted into the sandbox).
        contract: Frozen scoring contract for the case (declares
            ``weights`` — expects a ``pass_rate`` entry — and
            ``validity_gates``).
        backend_factory: Overrides sandbox backend construction; defaults to
            :func:`_default_backend_factory` (real Docker). Tests must pass
            a stub here — this function never starts a real container on
            its own initiative.
        ruler_id: Contract lineage tag recorded on the score.
        work_dir: Host rw scratch directory (bind-mounted to ``/work`` by
            the real backend). Defaults to a fresh temp directory; tests
            should pass ``tmp_path``-derived paths for automatic cleanup.

    Returns:
        The submission score. A ruler-intact-but-unusable submission (bad
        junitxml, test-count mismatch, abnormal container exit) carries
        ``valid=False, score=None`` — this is still returned normally (the
        caller ledgers it), it is not raised.

    Raises:
        FrozenDriftError: If the frozen material (hidden_tests, held-out
            files, weights, gates, ...) drifted during the scoring window
            (checked here as defense-in-depth on top of the caller's
            pre-flight check) — exit 3, nothing is ledgered.
    """
    if backend_factory is None:
        # Real judging path only: the anchored judge image identity must
        # match the LIVE daemon's image before anything runs (backlog item,
        # R6 batch). A rebuilt image under the same tag is a changed judge
        # environment — different verdict semantics must never share a
        # ruler ID. Stub-backend tests never touch Docker; the anchored key
        # itself is still mandatory for coding contracts
        # (missing_required_anchors) and this comparison is E2E-verified.
        anchored_image_id = contract.frozen.get(JUDGE_IMAGE_KEY)
        image_ref = os.environ.get("CFDB_JUDGE_IMAGE", "cfdb-judge:py312")
        live_image_id = resolve_judge_image_id(image_ref)
        if live_image_id != anchored_image_id:
            raise FrozenDriftError(
                [
                    f"{JUDGE_IMAGE_KEY} (live image '{image_ref}' is "
                    f"{live_image_id[:19]}..., contract anchored "
                    f"{str(anchored_image_id)[:19]}...)"
                ]
            )

    factory = backend_factory if backend_factory is not None else _default_backend_factory
    backend = factory(case_dir, submission_dir)

    # Internally-created scratch dirs are cleaned up on success AND
    # exception (Codex R0 P2); caller-owned dirs are left untouched.
    owned_tmp: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is not None:
        effective_work_dir = work_dir
    else:
        owned_tmp = tempfile.TemporaryDirectory(prefix="cfdb-sbx-work-")
        effective_work_dir = Path(owned_tmp.name)
    try:
        effective_work_dir.mkdir(parents=True, exist_ok=True)
        (effective_work_dir / "tmp").mkdir(parents=True, exist_ok=True)

        timeout = case.budget.max_runtime_sec
        run_result = backend.execute(
            _pytest_command(), cwd=effective_work_dir, timeout=timeout, env=_pytest_env()
        )

        # Defense-in-depth: re-verify the ruler was not disturbed while the
        # sandbox ran (the ro judge mount is the primary defense; this catches
        # a host-side race/tamper within the scoring window itself).
        drifted = verify_frozen(contract, case_dir)
        if len(drifted) > 0:
            raise FrozenDriftError(drifted)

        notes: list[str] = []
        judge_image = getattr(backend, "image", None) or getattr(backend, "_image", None)
        if isinstance(judge_image, str):
            notes.append(f"judge image: {judge_image}")
        wall_time = run_result.wall_time_sec if math.isfinite(run_result.wall_time_sec) else None

        computed: dict[str, float] = {"sandbox_used": 1.0}

        # Exit-code policy (Codex R0 P2): only pytest's own outcomes may
        # reach report-based scoring — 0 (all passed) and 1 (ordinary test
        # failures). Timeouts, negative codes and >=2 (pytest infra errors
        # 2-5, docker runtime codes) invalidate the submission regardless
        # of any report file left in the writable work zone.
        if run_result.timed_out is True or run_result.exit_code not in (0, 1):
            notes.append(
                f"container exited abnormally (exit_code={run_result.exit_code}, "
                f"timed_out={run_result.timed_out}): submission invalid"
            )
            computed["tests_all_pass"] = 0.0
        else:
            parsed = _parse_junit(effective_work_dir / REPORT_FILENAME, notes)
            if parsed is None:
                computed["tests_all_pass"] = 0.0
            else:
                total, failures, errors, skipped = parsed
                expected = case.execution.expected_test_count
                if expected is not None and total != expected:
                    notes.append(
                        f"collected test total {total} != frozen expected_test_count "
                        f"{expected} (submission invalid: collection tampering suspected)"
                    )
                    computed["tests_all_pass"] = 0.0
                elif total == 0:
                    notes.append("junitxml reports zero collected tests (submission invalid)")
                    computed["tests_all_pass"] = 0.0
                elif skipped != 0:
                    notes.append(
                        f"hidden tests skipped: {skipped} (submission invalid: "
                        "skipping is never a pass)"
                    )
                    computed["tests_all_pass"] = 0.0
                else:
                    pass_rate = (total - failures - errors) / total
                    all_pass = failures == 0 and errors == 0
                    computed["pass_rate"] = pass_rate
                    computed["tests_all_pass"] = 1.0 if all_pass else 0.0
                    if not all_pass:
                        notes.append(
                            f"tests_all_pass gate failed: {failures} failing, "
                            f"{errors} erroring of {total} collected tests"
                        )

        gates = evaluate_gates(contract, case, computed, wall_time, notes)
        valid = all(gates[g] is True for g in contract.validity_gates)

        metric_values = {k: v for k, v in computed.items() if k == "pass_rate"}
        score, breakdown = assemble_score(contract, valid, metric_values, notes)

        return SubmissionScore(
            submission_id=submission_dir.name,
            valid=valid,
            score=score,
            breakdown=breakdown,
            gates=gates,
            scored_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
            # Backend-measured wall clock, not submission-supplied
            # (Codex R0 P2: mislabelling measured timing as self-reported).
            wall_time=WallTimeRecord(value_sec=wall_time, self_reported=False),
            ruler_id=ruler_id,
        )
    finally:
        if owned_tmp is not None:
            owned_tmp.cleanup()
