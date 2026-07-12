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
  frozen ``expected_test_count`` (collection tampering), a missing or
  non-passing per-run canary sentinel (R7 — blanket report forgery), or an
  abnormal container exit. These invalidate the one submission
  (``valid=False, score=None``) but are ledgered like any other invalid
  sample — the ruler was never in question.
"""

from __future__ import annotations

import json
import logging
import math
import os
import secrets
import stat
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
JUDGE_CANARY = "/judge/canary"
JUDGE_IO = "/judge/io"
JUDGE_IO_INPUTS = "/judge/io/inputs.json"
JUDGE_IO_DRIVER = "/judge/io/driver.py"
IO_RESULTS_CONTAINER = "/work/io_results.json"
IO_INPUTS_FILENAME = "inputs.json"
IO_DRIVER_FILENAME = "driver.py"
IO_RESULTS_FILENAME = "io_results.json"
# The oracle result file lands in a container-WRITABLE work zone, so the
# submission can plant a FIFO / symlink / oversized file there (Codex R9-R0
# P1). The host reader requires a bounded regular non-symlink file; 8 MiB is
# far above any legitimate result set. This is a RUNTIME judging constant, so
# it lives in this anchored module (judge_source:sandbox_scorer) — changing it
# changes the ruler id (Codex R9-R2 P1); admission imports it locally to prove
# the golden result fits under it.
IO_RESULTS_MAX_BYTES = 8 * 1024 * 1024
WORK_REPORT = "/work/report.xml"
WORK_BASETEMP = "/work/pytest-tmp"
WORK_TMPDIR = "/work/tmp"
REPORT_FILENAME = "report.xml"

BackendFactory = Callable[[Path, Path], "ExecutionBackend"]
"""``(case_dir, submission_dir) -> ExecutionBackend``.

Production callers leave this at its default (a real sandboxed
:class:`~cfdb.execution.docker.DockerBackend`); tests inject a stub backend
that never touches Docker."""

IoBackendFactory = Callable[[Path, Path], "ExecutionBackend"]
"""``(submission_dir, io_dir) -> ExecutionBackend`` for the IO-oracle run
(R9). Distinct from :data:`BackendFactory`: its backend mounts the
submission and the judge-owned io dir but NEVER hidden_tests — the oracle
container must not carry the expected answers embedded in the test source
(Codex/loop-auditor R9 P1-4)."""


def _default_backend_factory(
    case_dir: Path,
    submission_dir: Path,
    image: str | None = None,
    canary_dir: Path | None = None,
) -> ExecutionBackend:
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
        image: Image reference for the container. The verified scoring path
            passes the anchor-verified IMMUTABLE image ID here (Codex R6
            P1): a mutable tag could be retagged between the anchor
            comparison and ``docker run``, silently swapping the judge —
            running by content-addressed ID closes that window. None falls
            back to the configured tag (non-scoring/diagnostic use only).
        canary_dir: Judge-owned host directory holding the per-run canary
            sentinel test (R7); ro-mounted at :data:`JUDGE_CANARY` so
            submission-side code cannot delete or edit it. None mounts no
            canary (non-scoring/diagnostic use).

    Returns:
        A :class:`~cfdb.execution.docker.DockerBackend` configured with the
        sandbox profile and the two read-only judge mounts.
    """
    from cfdb.execution.docker import DockerBackend

    # The judge image must ship pytest preinstalled: the sandbox runs with
    # --network none, so nothing can be pip-installed at scoring time. A
    # stock python:*-slim image would fail every submission with an abnormal
    # exit. Overridable for site-local judge images; the image identity is
    # recorded in scoring notes (see score_coding) for ledger traceability.
    if image is None:
        image = os.environ.get("CFDB_JUDGE_IMAGE", "cfdb-judge:py312")

    ro_mounts = [
        (case_dir / HIDDEN_TESTS_REL, JUDGE_HIDDEN_TESTS),
        (submission_dir, JUDGE_SUBMISSION),
    ]
    if canary_dir is not None:
        ro_mounts.append((canary_dir, JUDGE_CANARY))
    return DockerBackend(  # type: ignore[call-arg]
        image=image,
        sandbox=True,
        ro_mounts=ro_mounts,
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
"""Frozen pytest arguments (Architecture §3.3, verbatim). The per-run canary
path (R7 backlog) is APPENDED at runtime by :func:`_pytest_command` — the
canary scheme is anchored via this module's source, its nonce is per-run by
design and therefore can never itself be a frozen anchor."""


def _pytest_command(extra_paths: list[str] | None = None) -> list[str]:
    """Build the isolated judge bootstrap (Architecture §3.3 + Codex R0 P1).

    ``python -I`` ignores PYTHONPATH, user site-packages and sitecustomize,
    so the interpreter starts and ``pytest`` is imported from the judge
    image ONLY — a submission shipping ``sitecustomize.py`` or a
    ``pytest.py`` shadow module cannot hijack judge startup. The submission
    path is inserted onto ``sys.path`` only after pytest is already
    imported, purely so the hidden tests can import the submission.

    Args:
        extra_paths: Additional collection paths appended to the frozen
            args (the judge-owned canary mount).
    """
    args = PYTEST_ARGS + list(extra_paths or [])
    bootstrap = (
        "import sys, pytest; "
        f"sys.path.insert(0, {JUDGE_SUBMISSION!r}); "
        f"raise SystemExit(pytest.main({args!r}))"
    )
    return ["python", "-I", "-c", bootstrap]


def _new_canary_name() -> str:
    """Per-run canary test name: unforgeable without observing the run.

    The 16-hex-char nonce comes from ``secrets``; a forged report written
    from knowledge of the public case metadata (``expected_test_count``)
    alone cannot contain it.
    """
    return f"test_cfdb_canary_{secrets.token_hex(8)}"


def _canary_source(canary_name: str) -> str:
    """Source of the canary sentinel test module (trivially passing)."""
    return (
        '"""cfdb judge canary (R7): per-run sentinel, judge-owned ro mount."""\n'
        f"def {canary_name}():\n"
        f"    assert {canary_name!r} == {canary_name!r}\n"
    )


def _verify_canary(report_path: Path, canary_name: str, notes: list[str]) -> bool:
    """Check the junitxml for exactly one PASSING canary testcase.

    A blanket-forged report (fabricated suite counts, no real pytest run)
    cannot name the per-run canary, so it fails here (R7 backlog). This is
    a COST-RAISER, not a boundary: in-process hostile code that observes
    the live pytest session can read the canary name and forge around it —
    that residual stays declared (§8, README verification boundary).

    Args:
        report_path: Host path to the junitxml report.
        canary_name: The per-run sentinel test name to require.
        notes: Audit note sink (mutated in place).

    Returns:
        True only if exactly one canary testcase exists and it passed.
    """
    try:
        root = ET.parse(report_path).getroot()
    except (ET.ParseError, OSError) as e:
        notes.append(f"canary check could not re-read junitxml: {e} (submission invalid)")
        return False
    matches = [tc for tc in root.iter("testcase") if tc.get("name") == canary_name]
    if len(matches) != 1:
        notes.append(
            f"canary sentinel testcase absent or duplicated ({len(matches)} match(es)): "
            "submission invalid (blanket report forgery suspected)"
        )
        return False
    bad_children = [
        child.tag for child in matches[0] if child.tag in ("failure", "error", "skipped")
    ]
    if len(bad_children) > 0:
        notes.append(
            f"canary sentinel did not pass ({', '.join(bad_children)}): submission invalid"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# IO oracle (R9): trusted re-execution — a second, independent judging signal
# that narrows forgery from "write a passing report" to "actually compute the
# correct answers". The driver source below is judging material and is
# anchored via judge_source:sandbox_scorer (this module).
# ---------------------------------------------------------------------------


def _io_driver_source(entry_module: str, entry_func: str) -> str:
    """Source of the judge-owned IO driver (R9).

    The driver imports its OWN dependencies (sys, json, traceback,
    importlib) BEFORE inserting the submission path onto ``sys.path`` —
    identical to the pytest bootstrap's import-order discipline (Codex R0
    P1 / loop-auditor R9 P1-2): a submission shipping ``json.py`` (or any
    driver-dependency shadow) at its tree root cannot hijack the result
    writer, because those modules are already resolved from the stdlib
    before the submission path exists on ``sys.path``. ``python -I``
    additionally ignores PYTHONPATH/site so the driver's own imports come
    from the judge image only.

    The driver drives ``entry_module:entry_func`` over the inputs-only
    projection and writes one row per input to the work zone. It NEVER
    sees the expected outputs (reconciliation is host-side).

    Type-strictness is enforced BEFORE serialization (Codex R9-R0 P2): the
    driver rejects any return value that is not strictly JSON-native
    (tuple/set, or a dict with non-string keys), because ``json.dump`` would
    silently coerce a tuple to a list and an integer key to a string — the
    host-side ``_strict_equal`` could then never tell them apart, defeating
    the advertised strict typing. Values that round-trip JSON losslessly
    (None/bool/int/float/str/list/str-keyed dict) are preserved and left for
    the host to compare exactly.

    The whole driver runs inside a function ``_drive`` (Codex R9-R2 P2): the
    validator captures EXACT type primitives (and open/json/traceback) as
    FUNCTION LOCALS before importing the submission. driver.py executes as
    ``__main__``, so module-level captures would be reachable — and rebindable
    — via ``import __main__; __main__._list = tuple``; locals are not in any
    submission-visible namespace. Combined with exact ``is`` identity (never
    ``isinstance``), a submission can neither swap the primitives nor sneak an
    int/list/dict SUBCLASS past the coercion check.
    """
    return (
        "import sys, json, traceback, importlib\n"
        "def _drive():\n"
        "    _type = type\n"
        "    _open = open\n"
        "    _json_load, _json_dump = json.load, json.dump\n"
        "    _fmt_exc = traceback.format_exc\n"
        "    _bool, _int, _float, _str, _list, _dict = bool, int, float, str, list, dict\n"
        f"    with _open({JUDGE_IO_INPUTS!r}, encoding='utf-8') as _f:\n"
        "        _inputs = _json_load(_f)\n"
        f"    sys.path.insert(0, {JUDGE_SUBMISSION!r})\n"
        f"    _mod = importlib.import_module({entry_module!r})\n"
        f"    _fn = getattr(_mod, {entry_func!r})\n"
        "    def _native(_v):\n"
        "        _t = _type(_v)\n"
        "        if _v is None or _t is _bool or _t is _int or _t is _float or _t is _str:\n"
        "            return True\n"
        "        if _t is _list:\n"
        "            return all(_native(_x) for _x in _v)\n"
        "        if _t is _dict:\n"
        "            return all(_type(_k) is _str and _native(_x) for _k, _x in _v.items())\n"
        "        return False\n"
        "    _out = []\n"
        "    for _item in _inputs:\n"
        "        try:\n"
        "            _r = _fn(*_item['args'])\n"
        "            if not _native(_r):\n"
        "                _out.append({'index': _item['index'], 'ok': False, "
        "'error': 'non-JSON-native return type (would coerce): ' + _type(_r).__name__})\n"
        "            else:\n"
        "                _out.append({'index': _item['index'], 'ok': True, 'result': _r})\n"
        "        except Exception:\n"
        "            _out.append({'index': _item['index'], 'ok': False, "
        "'error': _fmt_exc()[-400:]})\n"
        f"    with _open({IO_RESULTS_CONTAINER!r}, 'w', encoding='utf-8') as _f:\n"
        "        _json_dump(_out, _f)\n"
        "_drive()\n"
    )


def _io_command() -> list[str]:
    """Container command for the IO driver: isolated interpreter, judge-owned
    driver script (Codex R0 P1 discipline reused)."""
    return ["python", "-I", JUDGE_IO_DRIVER]


def _default_io_backend_factory(
    submission_dir: Path, io_dir: Path, image: str | None = None
) -> ExecutionBackend:
    """Build the real sandboxed backend for the IO-oracle run (R9).

    Mounts ONLY the submission (ro) and the judge-owned io dir (ro) —
    crucially NOT hidden_tests (loop-auditor R9 P1-4): the oracle container
    must not carry the expected answers embedded in the test source. Same
    sandbox profile and immutable-image-ID discipline as the pytest backend.
    """
    from cfdb.execution.docker import DockerBackend

    if image is None:
        image = os.environ.get("CFDB_JUDGE_IMAGE", "cfdb-judge:py312")
    return DockerBackend(  # type: ignore[call-arg]
        image=image,
        sandbox=True,
        ro_mounts=[
            (submission_dir, JUDGE_SUBMISSION),
            (io_dir, JUDGE_IO),
        ],
    )


def _strict_equal(a: object, b: object) -> bool:
    """Type-strict recursive equality for IO reconciliation (R9).

    Plain ``==`` would let ``True == 1`` and ``1 == 1.0`` pass — Python's
    numeric/bool coercion is a forgery surface for an oracle. Every level
    must match in type AND value; floats are already rejected at admission,
    so this only ever compares int/str/bool/None/list/dict.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_strict_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_strict_equal(a[k], b[k]) for k in a)
    return a == b


def _reconcile_io(results_path: Path, expected: list, notes: list[str]) -> float:
    """Reconcile the driver's results against held-out expected (host-side).

    Runs entirely outside the container, on data the submission-occupied
    process can never reach. Any deviation — unreadable/unparseable file,
    wrong count, misaligned index, a row that did not run, or a value that
    is not strictly equal to expected — fails closed to 0.0.

    Args:
        results_path: Host path to the driver-written ``io_results.json``.
        expected: Held-out expected values, in input order.
        notes: Audit note sink (mutated in place).

    Returns:
        1.0 only if every input produced the strictly-equal expected value.
    """
    # The result file sits in a container-writable dir, so a hostile
    # submission may pre-create it as a FIFO (a plain open would block
    # forever, past the container timeout), a symlink (aimed at /dev/zero or
    # a huge file), or an oversized regular file. Open it ONCE with
    # O_NOFOLLOW (a symlink at the path is refused at open, ELOOP) and
    # O_NONBLOCK (a FIFO open returns a fd instead of blocking on a writer),
    # then decide S_ISREG + size on the SAME inode via fstat and read that
    # exact descriptor — so the guard and the read cannot straddle a swapped
    # inode. (Codex R9-R0 P1 hardened per the R9 loop-auditor: the prior
    # lstat-then-read_text split was the textbook TOCTOU shape — not
    # exploitable here because the oracle container is already gone by this
    # point, but the single-open idiom is the correct primitive for a
    # checked read and removes the shape entirely.)
    # O_NOFOLLOW / O_NONBLOCK are POSIX-only; getattr-fallback to 0 (no-op) on
    # a non-Unix host so the reader degrades to a plain bounded open+fstat
    # rather than raising AttributeError before os.open (Codex R9-harden-R0
    # P2). The judge platform is Linux/Docker, where both are present and the
    # symlink/FIFO guards are fully active.
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(results_path, open_flags)
    except OSError as e:
        notes.append(
            f"io oracle: results file not present or not a regular non-symlink file: {e} (invalid)"
        )
        return 0.0
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            notes.append("io oracle: results file is not a regular file (FIFO/device?) (invalid)")
            return 0.0
        if st.st_size > IO_RESULTS_MAX_BYTES:
            notes.append(
                f"io oracle: results file {st.st_size} B exceeds "
                f"cap {IO_RESULTS_MAX_BYTES} B (invalid)"
            )
            return 0.0
        with os.fdopen(fd, "rb") as f:
            fd = -1  # fdopen owns the descriptor now; its context manager closes it
            raw = f.read(IO_RESULTS_MAX_BYTES + 1)
        # Enforce the cap on the BYTES ACTUALLY READ, not just fstat's size
        # (Codex R9-harden-R0 P2): if fstat under-reported (stale metadata),
        # read(MAX+1) returns MAX+1 bytes — reject before a valid-JSON prefix
        # of an oversized artifact is parsed as a complete result.
        if len(raw) > IO_RESULTS_MAX_BYTES:
            notes.append(f"io oracle: results read exceeds cap {IO_RESULTS_MAX_BYTES} B (invalid)")
            return 0.0
        results = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        notes.append(f"io oracle: results unreadable/unparseable: {e} (invalid)")
        return 0.0
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(results, list) or len(results) != len(expected):
        got = len(results) if isinstance(results, list) else "not-a-list"
        notes.append(f"io oracle: result count {got} != expected {len(expected)} (invalid)")
        return 0.0
    for i, (row, exp) in enumerate(zip(results, expected, strict=True)):
        if not isinstance(row, dict) or row.get("index") != i:
            notes.append(f"io oracle: result {i} missing/misaligned index (invalid)")
            return 0.0
        if row.get("ok") is not True:
            notes.append(f"io oracle: input {i} raised inside the submission (invalid)")
            return 0.0
        # The result file is submission-influenced, so a forged/malformed row
        # that omits "result" must fail BEFORE equality (Codex R9-R0 P2):
        # otherwise `row.get("result")` is None and would spuriously match a
        # held-out expected value of JSON null.
        if "result" not in row:
            notes.append(f"io oracle: result {i} has no 'result' field (invalid)")
            return 0.0
        if not _strict_equal(row["result"], exp):
            notes.append(f"io oracle: input {i} produced the wrong output (invalid)")
            return 0.0
    return 1.0


def _run_io_oracle(
    case: CaseSpec,
    case_dir: Path,
    submission_dir: Path,
    io_factory: IoBackendFactory | None,
    notes: list[str],
) -> float:
    """Run the trusted re-execution oracle and return its 1.0/0.0 verdict (R9).

    The oracle run uses its OWN fresh work zone (loop-auditor R9, deeper
    than P2-1): the pytest run can read the ro-mounted hidden_tests, so if
    the two runs shared ``/work`` a submission could stash a harvested
    expected value during the pytest run and replay it here. A separate
    work dir closes that cross-container channel structurally; the
    admission-time input-disjointness lint is only belt-and-suspenders.

    Expected values are loaded HOST-SIDE and never written into any mount.

    Args:
        case: Coding case with ``execution.io_oracle`` set.
        case_dir: Case directory (holds the held-out cases file).
        submission_dir: Submission tree (ro-mounted into the oracle run).
        io_factory: Builds the oracle backend. None on a stub path with no
            oracle wiring -> fail-closed 0.0 (the gate then invalidates).
        notes: Audit note sink.

    Returns:
        1.0 if every held-out input reproduced its expected output, else 0.0.
    """
    io = case.execution.io_oracle
    assert io is not None  # caller guards
    if io_factory is None:
        notes.append("io oracle: no backend available for the oracle run (fail-closed)")
        return 0.0

    cases_path = case_dir / io.cases_file
    try:
        data = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        notes.append(f"io oracle: cannot read cases file {io.cases_file}: {e} (invalid)")
        return 0.0
    if not isinstance(data, list) or len(data) == 0:
        notes.append("io oracle: cases file empty or not a list (invalid)")
        return 0.0

    expected = [item["expected"] for item in data]
    inputs_projection = [{"index": i, "args": item["args"]} for i, item in enumerate(data)]
    entry_module, entry_func = io.entry.split(":", 1)

    # Judge-owned io dir (inputs-only + driver) and a SEPARATE oracle work
    # zone (never the pytest work dir) — both cleaned up on exit.
    with (
        tempfile.TemporaryDirectory(prefix="cfdb-io-") as io_tmp,
        tempfile.TemporaryDirectory(prefix="cfdb-io-work-") as io_work_tmp,
    ):
        io_dir = Path(io_tmp)
        (io_dir / IO_INPUTS_FILENAME).write_text(json.dumps(inputs_projection), encoding="utf-8")
        (io_dir / IO_DRIVER_FILENAME).write_text(
            _io_driver_source(entry_module, entry_func), encoding="utf-8"
        )
        io_work_dir = Path(io_work_tmp)
        (io_work_dir / "tmp").mkdir(parents=True, exist_ok=True)

        backend = io_factory(submission_dir, io_dir)
        run = backend.execute(
            _io_command(),
            cwd=io_work_dir,
            timeout=case.budget.max_runtime_sec,
            env=_pytest_env(),
        )
        # The oracle driver exits 0 on a normal completion; ANY non-zero or
        # timeout invalidates (stricter than pytest's {0,1} — the driver has
        # no legitimate non-zero outcome).
        if run.timed_out is True or run.exit_code != 0:
            notes.append(
                f"io oracle: driver exited abnormally (exit_code={run.exit_code}, "
                f"timed_out={run.timed_out}) (invalid)"
            )
            return 0.0
        return _reconcile_io(io_work_dir / IO_RESULTS_FILENAME, expected, notes)


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
    canary_name: str | None = None,
    io_backend_factory: IoBackendFactory | None = None,
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
        canary_name: Per-run canary sentinel override (R7). Production
            callers leave this None: on the real judging path a fresh
            ``secrets`` nonce name is generated every run, the sentinel is
            ro-mounted and its junitxml testcase is required to exist and
            pass — a blanket-forged report cannot name it. On stub-backend
            paths the canary is enforced only when a name is passed here
            (unit tests exercise the verification with a known name).
        io_backend_factory: Overrides IO-oracle backend construction (R9,
            only used when ``case.execution.io_oracle`` is set). Production
            callers leave this None to get the real Docker backend built
            with the same verified immutable image ID as the pytest run;
            tests inject a stub. A case with an oracle but no factory
            available fails the oracle closed (the gate then invalidates).

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
        # Canary sentinel (R7): mandatory on the real judging path. The
        # per-run nonce name lives in a judge-owned ro mount so
        # submission-side code cannot delete or edit the file; its junitxml
        # testcase is required to exist and pass below. (Created outside
        # the try like owned_tmp; the shared finally cleans both.)
        if canary_name is None:
            canary_name = _new_canary_name()
        canary_tmp = tempfile.TemporaryDirectory(prefix="cfdb-canary-")
        canary_dir = Path(canary_tmp.name)
        (canary_dir / f"{canary_name}.py").write_text(_canary_source(canary_name), encoding="utf-8")
        # TOCTOU closure (Codex R6 P1): the container is started by the
        # verified IMMUTABLE image ID, never the mutable tag — a retag
        # between the comparison above and `docker run` can no longer swap
        # the judge.
        factory: BackendFactory = lambda c, s: _default_backend_factory(  # noqa: E731
            c, s, image=live_image_id, canary_dir=canary_dir
        )
        # IO oracle backend (R9): same verified immutable image ID (P1-3),
        # its own no-hidden_tests mount profile (P1-4). Built only when the
        # case actually declares an oracle.
        if case.execution.io_oracle is not None and io_backend_factory is None:
            io_backend_factory = lambda s, d: _default_io_backend_factory(  # noqa: E731
                s, d, image=live_image_id
            )
    else:
        canary_tmp = None
        factory = backend_factory
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

        canary_paths = [JUDGE_CANARY] if canary_tmp is not None else []
        timeout = case.budget.max_runtime_sec
        run_result = backend.execute(
            _pytest_command(canary_paths),
            cwd=effective_work_dir,
            timeout=timeout,
            env=_pytest_env(),
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
            report_path = effective_work_dir / REPORT_FILENAME
            parsed = _parse_junit(report_path, notes)
            if parsed is None:
                computed["tests_all_pass"] = 0.0
            else:
                total, failures, errors, skipped = parsed
                # Canary sentinel check (R7): the per-run testcase must
                # exist exactly once and pass; its single passing entry is
                # then removed from the totals so the hidden-suite counts
                # below stay exactly what the frozen case expects.
                canary_ok = True
                if canary_name is not None:
                    canary_ok = _verify_canary(report_path, canary_name, notes)
                    if canary_ok is True:
                        total -= 1
                expected = case.execution.expected_test_count
                if canary_ok is False:
                    computed["tests_all_pass"] = 0.0
                elif expected is not None and total != expected:
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

        # IO oracle (R9): a second, independent judging signal. Computed
        # before gate evaluation so `io_oracle_pass` reaches evaluate_gates
        # as a 1.0/0.0 sentinel like the other coding gates. Runs only when
        # the case opts in; the two signals combine with AND (any failure
        # invalidates) because contract.validity_gates lists both.
        if case.execution.io_oracle is not None:
            computed["io_oracle_pass"] = _run_io_oracle(
                case, case_dir, submission_dir, io_backend_factory, notes
            )
            # The oracle is a SECOND container run — it extends the scoring
            # window past the post-pytest verify_frozen above. Re-verify the
            # ruler once more so a host-side edit of the held-out answers (or
            # any frozen material) DURING the oracle run cannot slip an
            # already-ledgered verdict past drift detection (Codex R9-R0 P1).
            drifted_after_oracle = verify_frozen(contract, case_dir)
            if len(drifted_after_oracle) > 0:
                raise FrozenDriftError(drifted_after_oracle)

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
        if canary_tmp is not None:
            canary_tmp.cleanup()
