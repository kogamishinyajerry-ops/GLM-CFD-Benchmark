"""Tests for coding-domain sandbox scoring (v5.0 Wave B/D2).

Covers the pytest command/env construction, junitxml reconciliation, the
two-tier failure semantics (ruler-level exit 3 vs. submission-level
INVALID), and the mandatory tamper witnesses:

1. A hidden_tests byte flip is caught by the post-run defense-in-depth
   re-verification -> FrozenDriftError (exit 3, never ledgered).
2. A junitxml collected-test-total that disagrees with the frozen
   ``expected_test_count`` -> INVALID (score=None), never a pass.
3. An abnormal container exit -> INVALID, but still ledgered (score=None,
   never ranked).

All tests use a stub :class:`~cfdb.execution.base.ExecutionBackend` — no
real Docker container is ever started.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from cfdb.adapters.base import RunResult
from cfdb.agentbench import init_contract, ranked, read_ledger, score_submission
from cfdb.agentbench.contract import FrozenDriftError
from cfdb.agentbench.sandbox_scorer import (
    JUDGE_HIDDEN_TESTS,
    JUDGE_SUBMISSION,
    WORK_BASETEMP,
    WORK_REPORT,
    WORK_TMPDIR,
    _pytest_command,
    _pytest_env,
    score_coding,
)
from cfdb.registry import CaseRegistry

CASE_ID = "coding_case"


def _junit_xml(
    total: int,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
    extra_case_names: list[str] | None = None,
) -> str:
    """Build a minimal well-formed junitxml report.

    ``extra_case_names`` appends named passing testcases (e.g. the R7
    canary sentinel) on top of ``total`` generic ones; the suite's
    ``tests`` attribute counts both.
    """
    extras = extra_case_names or []
    cases = "".join(f'<testcase classname="t" name="t{i}"/>' for i in range(total))
    cases += "".join(f'<testcase classname="t" name="{name}"/>' for name in extras)
    suite_total = total + len(extras)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<testsuites>"
        f'<testsuite name="pytest" tests="{suite_total}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}">'
        f"{cases}"
        "</testsuite>"
        "</testsuites>"
    )


def _write_case(
    cases_root: Path,
    *,
    expected_test_count: int | None = 3,
    max_runtime_sec: int | None = 100,
) -> Path:
    """Create a minimal coding-domain case with a frozen hidden_tests suite."""
    case_dir = cases_root / "verification" / CASE_ID
    hidden_tests = case_dir / "reference" / "hidden_tests"
    hidden_tests.mkdir(parents=True)
    (hidden_tests / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8"
    )
    spec: dict = {
        "id": CASE_ID,
        "name": "Coding sandbox test case",
        "category": "verification",
        "domain": "coding",
        "solvers": [{"name": "generic", "command": "true"}],
        "outputs": {"qoi": []},
        "metrics": {},
    }
    execution: dict = {}
    if expected_test_count is not None:
        execution["expected_test_count"] = expected_test_count
    if execution:
        spec["execution"] = execution
    if max_runtime_sec is not None:
        spec["budget"] = {"max_runtime_sec": max_runtime_sec}
    (case_dir / "case.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return case_dir


@dataclass
class StubBackend:
    """Stub ExecutionBackend: simulates the container writing report.xml."""

    name: str = "stub"
    report_xml: str | None = None
    exit_code: int = 0
    timed_out: bool = False
    wall_time_sec: float = 1.5
    calls: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout, "env": env})
        if self.report_xml is not None:
            (cwd / "report.xml").write_text(self.report_xml, encoding="utf-8")
        return RunResult(
            exit_code=self.exit_code,
            stdout="",
            stderr="",
            wall_time_sec=self.wall_time_sec,
            timed_out=self.timed_out,
        )


@pytest.fixture
def bench(tmp_path: Path):
    """Registry + coding case + contract for one sandbox scoring session."""
    cases_root = tmp_path / "cases"
    case_dir = _write_case(cases_root)
    registry = CaseRegistry(cases_root)
    case = registry.load(CASE_ID)
    contract = init_contract(
        CASE_ID,
        registry,
        weights={"pass_rate": 1.0},
        validity_gates=["tests_all_pass", "sandbox_used"],
    )
    return registry, case, case_dir, contract, tmp_path


class TestPytestInvocation:
    """The pytest command/env are frozen verbatim by Architecture v5.0 §3.3."""

    def test_command_is_isolated_bootstrap(self) -> None:
        # Codex R0 P1: python -I ignores PYTHONPATH/sitecustomize so pytest
        # resolves from the judge image only; the submission path is added
        # inside the bootstrap AFTER pytest is imported.
        cmd = _pytest_command()
        assert cmd[:3] == ["python", "-I", "-c"]
        bootstrap = cmd[3]
        assert bootstrap.startswith("import sys, pytest; ")
        assert f"sys.path.insert(0, {JUDGE_SUBMISSION!r})" in bootstrap
        assert "pytest.main(" in bootstrap
        for frozen_arg in (
            JUDGE_HIDDEN_TESTS,
            f"--rootdir={JUDGE_HIDDEN_TESTS}",
            f"--confcutdir={JUDGE_HIDDEN_TESTS}",
            "no:cacheprovider",
            f"--basetemp={WORK_BASETEMP}",
            f"--junitxml={WORK_REPORT}",
        ):
            assert frozen_arg in bootstrap
        # sys.path insertion must come after the pytest import.
        assert bootstrap.index("pytest") < bootstrap.index("sys.path.insert")

    def test_env_matches_blueprint(self) -> None:
        # No PYTHONPATH: -I would ignore it, and its absence is the point
        # (submission path travels inside the bootstrap instead).
        env = _pytest_env()
        assert env == {
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": WORK_TMPDIR,
        }
        assert "PYTHONPATH" not in env


class TestScoreCodingBaseline:
    def test_all_pass_scores_and_ranks(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_a"
        sub_dir.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3, failures=0, errors=0))
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is True
        assert result.gates == {"sandbox_used": True, "tests_all_pass": True}
        assert result.breakdown == pytest.approx({"pass_rate": 1.0})
        assert result.score == pytest.approx(1.0)
        assert result.submission_id == "sub_a"
        assert result.wall_time is not None
        assert result.wall_time.value_sec == pytest.approx(1.5)
        # Backend-measured wall clock is recomputed evidence, not a
        # submission-supplied number (Codex R0 P2).
        assert result.wall_time.self_reported is False
        # The stub actually received the frozen command/env (dispatch wiring works).
        assert len(stub.calls) == 1
        assert stub.calls[0]["command"][:3] == ["python", "-I", "-c"]
        assert "PYTHONPATH" not in stub.calls[0]["env"]

    def test_partial_fail_valid_submission_no_score(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_b"
        sub_dir.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3, failures=1, errors=0))
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        # Ruler intact, submission legitimately ran and failed one test:
        # this is NOT the two-tier "invalid" bucket, just an ordinary
        # failed gate -> no score, no fabricated partial credit.
        assert result.gates["tests_all_pass"] is False
        assert result.valid is False
        assert result.score is None
        assert any("tests_all_pass gate failed" in n for n in result.notes)

    def test_dispatch_through_score_submission(self, bench, tmp_path: Path) -> None:
        """score_submission's domain dispatch reaches score_coding correctly."""
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_dispatch"
        sub_dir.mkdir()
        ledger = tmp / "ledger.jsonl"
        stub = StubBackend(report_xml=_junit_xml(total=3, failures=0, errors=0))
        result = score_submission(
            contract,
            case,
            case_dir,
            sub_dir,
            ledger_path=ledger,
            backend_factory=lambda cd, sd: stub,
        )
        assert result.valid is True
        assert result.score == pytest.approx(1.0)
        assert [e.submission_id for e in ranked(read_ledger(ledger))] == ["sub_dispatch"]


class TestTamperWitnesses:
    def test_witness_1_hidden_test_byte_flip_exit3_never_ledgered(
        self, bench, tmp_path: Path
    ) -> None:
        """Pre-flight: tampering before score_submission is even called."""
        _, case, case_dir, contract, tmp = bench
        target = case_dir / "reference" / "hidden_tests" / "test_smoke.py"
        data = bytearray(target.read_bytes())
        data[0] ^= 0xFF
        target.write_bytes(bytes(data))

        sub_dir = tmp / "sub_tampered"
        sub_dir.mkdir()
        ledger = tmp / "ledger.jsonl"
        stub = StubBackend(report_xml=_junit_xml(total=3))
        with pytest.raises(FrozenDriftError) as excinfo:
            score_submission(
                contract,
                case,
                case_dir,
                sub_dir,
                ledger_path=ledger,
                backend_factory=lambda cd, sd: stub,
            )
        assert "reference/hidden_tests/test_smoke.py" in excinfo.value.drifted
        assert not ledger.exists()
        # The tampered hidden_tests were never even executed.
        assert stub.calls == []

    def test_witness_1b_post_run_reverify_catches_race_tamper(self, bench, tmp_path: Path) -> None:
        """Defense-in-depth: score_coding's own post-run re-verify catches
        drift that happened during the scoring window (simulated here by
        calling score_coding directly, bypassing score_submission's
        pre-flight check, and having the stub backend tamper the file as
        a side effect of "running")."""
        _, case, case_dir, contract, tmp = bench
        target = case_dir / "reference" / "hidden_tests" / "test_smoke.py"

        class TamperingBackend:
            name = "tampering-stub"

            def execute(self, command, cwd, timeout=None, env=None) -> RunResult:
                data = bytearray(target.read_bytes())
                data[0] ^= 0xFF
                target.write_bytes(bytes(data))
                (cwd / "report.xml").write_text(_junit_xml(total=3), encoding="utf-8")
                return RunResult(
                    exit_code=0, stdout="", stderr="", wall_time_sec=1.0, timed_out=False
                )

        sub_dir = tmp / "sub_race"
        sub_dir.mkdir()
        with pytest.raises(FrozenDriftError):
            score_coding(
                case,
                case_dir,
                sub_dir,
                contract,
                backend_factory=lambda cd, sd: TamperingBackend(),
                work_dir=tmp_path / "work",
            )

    def test_witness_2_test_count_mismatch_invalid_not_pass(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_undercollect"
        sub_dir.mkdir()
        # expected_test_count=3 but collection only reports 2 -> collection
        # tampering suspected (e.g. a conftest.py filtering hidden tests).
        stub = StubBackend(report_xml=_junit_xml(total=2, failures=0, errors=0))
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is False
        assert result.score is None
        assert result.gates["tests_all_pass"] is False
        assert any("expected_test_count" in n for n in result.notes)

    def test_witness_3_container_abnormal_exit_invalid_but_ledgered(
        self, bench, tmp_path: Path
    ) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_crash"
        sub_dir.mkdir()
        ledger = tmp / "ledger.jsonl"
        # No report.xml written: simulates the container dying before pytest
        # could produce one.
        stub = StubBackend(report_xml=None, exit_code=-1, timed_out=False)
        result = score_submission(
            contract,
            case,
            case_dir,
            sub_dir,
            ledger_path=ledger,
            backend_factory=lambda cd, sd: stub,
        )
        assert result.valid is False
        assert result.score is None
        assert any("abnormally" in n for n in result.notes)
        # Ledgered for audit, but never ranks.
        entries = read_ledger(ledger)
        assert [e.submission_id for e in entries] == ["sub_crash"]
        assert ranked(entries) == []

    def test_witness_3b_timeout_invalid_but_ledgered(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_timeout"
        sub_dir.mkdir()
        stub = StubBackend(report_xml=None, exit_code=-1, timed_out=True)
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is False
        assert result.score is None
        assert any("timed_out=True" in n for n in result.notes)

    def test_missing_junitxml_invalid(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_no_report"
        sub_dir.mkdir()
        # Exit code 0 but no report.xml: e.g. pytest crashed internally
        # before writing the report, or a wrong --junitxml path.
        stub = StubBackend(report_xml=None, exit_code=0, timed_out=False)
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is False
        assert result.score is None
        assert any("junitxml missing" in n for n in result.notes)

    def test_unparseable_junitxml_invalid(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_garbage_report"
        sub_dir.mkdir()
        stub = StubBackend(report_xml="not xml at all <<<", exit_code=0, timed_out=False)
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is False
        assert result.score is None
        assert any("junitxml unparseable" in n for n in result.notes)

    def test_zero_collected_tests_invalid(self, bench, tmp_path: Path) -> None:
        _, case, case_dir, contract, tmp = bench
        sub_dir = tmp / "sub_zero"
        sub_dir.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=0), exit_code=0, timed_out=False)
        # expected_test_count=3 in the fixture case, so this also trips the
        # mismatch path; use a case without an expected_test_count instead
        # to isolate the "zero collected" fail-closed branch.
        alt_cases_root = tmp / "cases_noexpect"
        alt_case_dir = _write_case(alt_cases_root, expected_test_count=None)
        registry = CaseRegistry(alt_cases_root)
        alt_case = registry.load(CASE_ID)
        alt_contract = init_contract(
            CASE_ID,
            registry,
            weights={"pass_rate": 1.0},
            validity_gates=["tests_all_pass", "sandbox_used"],
        )
        result = score_coding(
            alt_case,
            alt_case_dir,
            sub_dir,
            alt_contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.valid is False
        assert result.score is None
        assert any("zero collected tests" in n for n in result.notes)


class TestSandboxGateFailClosed:
    def test_unknown_gate_still_fails_closed_for_coding_contract(
        self, bench, tmp_path: Path
    ) -> None:
        registry, case, case_dir, _, tmp = bench
        contract = init_contract(
            CASE_ID,
            registry,
            weights={"pass_rate": 1.0},
            validity_gates=["tests_all_pass", "sandbox_used", "no_such_gate"],
        )
        sub_dir = tmp / "sub_unknown_gate"
        sub_dir.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3, failures=0, errors=0))
        result = score_coding(
            case,
            case_dir,
            sub_dir,
            contract,
            backend_factory=lambda cd, sd: stub,
            work_dir=tmp_path / "work",
        )
        assert result.gates["no_such_gate"] is False
        assert result.valid is False
        assert result.score is None
