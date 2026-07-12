"""Witnesses for the IO oracle (v5.0 R9): trusted re-execution.

The oracle drives the submission's declared entry function over a held-out
input set in an isolated container (no hidden_tests mounted, expected
outputs never entering the container) and reconciles host-side. These
witnesses cover the four loop-auditor P1 fixes and two deeper hardenings:

- P1-1: empty/tiny oracle refused at admission (an empty oracle gates nothing).
- P1-2: driver imports its deps before inserting the submission path.
- P1-3: the oracle container is built with the verified immutable image ID.
- P1-4: the oracle container never mounts hidden_tests.
- Hardening A: strict-typed reconciliation (True != 1, no JSON coercion).
- Hardening B: the oracle run uses its own work zone (no cross-run channel).
"""

from __future__ import annotations

import builtins
import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest
from test_agentbench_coding import StubBackend, _junit_xml

import cfdb.agentbench.contract as ct
import cfdb.agentbench.sandbox_scorer as ss
from cfdb.adapters.base import RunResult
from cfdb.agentbench.contract import FrozenDriftError, init_contract, verify_frozen
from cfdb.agentbench.sandbox_scorer import (
    IO_RESULTS_CONTAINER,
    IO_RESULTS_FILENAME,
    JUDGE_HIDDEN_TESTS,
    JUDGE_IO,
    JUDGE_IO_INPUTS,
    JUDGE_SUBMISSION,
    _default_io_backend_factory,
    _io_driver_source,
    _reconcile_io,
    _strict_equal,
    score_coding,
)
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _tmp_smoke(tmp_path: Path) -> tuple[CaseRegistry, Path]:
    src = PROJECT_CASES / "coding_tasks" / "smoke_add_two_io"
    dst = tmp_path / "cases" / "coding_tasks" / "smoke_add_two_io"
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


class _IoStub:
    """Stub oracle backend: writes an io_results.json into the work zone.

    ``golden`` computes correct results from the mounted inputs; ``forged``
    writes a caller-supplied payload verbatim (to exercise reconciliation).
    """

    def __init__(self, io_dir: Path, mode: str, fn, forged, exit_code: int, timed_out: bool):
        self._io_dir = io_dir
        self._mode = mode
        self._fn = fn
        self._forged = forged
        self._exit_code = exit_code
        self._timed_out = timed_out
        self.seen_cwd: Path | None = None

    def execute(self, command, cwd, timeout=None, env=None) -> RunResult:
        self.seen_cwd = Path(cwd)
        inputs = json.loads((self._io_dir / "inputs.json").read_text(encoding="utf-8"))
        if self._mode == "golden":
            out = [
                {"index": it["index"], "ok": True, "result": self._fn(*it["args"])} for it in inputs
            ]
            (Path(cwd) / "io_results.json").write_text(json.dumps(out), encoding="utf-8")
        elif self._mode == "forged":
            (Path(cwd) / "io_results.json").write_text(json.dumps(self._forged), encoding="utf-8")
        elif self._mode == "nofile":
            pass  # driver wrote nothing
        return RunResult(
            exit_code=self._exit_code,
            stdout="",
            stderr="",
            wall_time_sec=0.1,
            timed_out=self._timed_out,
        )


def _io_factory(mode="golden", fn=lambda x: x + 2, forged=None, exit_code=0, timed_out=False):
    holder = {}

    def factory(submission_dir: Path, io_dir: Path) -> _IoStub:
        stub = _IoStub(io_dir, mode, fn, forged, exit_code, timed_out)
        holder["stub"] = stub
        return stub

    factory.holder = holder  # type: ignore[attr-defined]
    return factory


def _score(tmp_path, registry, case_dir, io_factory, work_dir=None):
    """Run score_coding on the smoke pilot with a passing pytest stub and
    the given io factory."""
    contract = init_contract("smoke_add_two_io", registry)
    case = registry.load("smoke_add_two_io")
    sub = tmp_path / "sub"
    sub.mkdir(exist_ok=True)
    (sub / "solution.py").write_text(
        "def add_two(x):\n    return x + 2\ndef already_works(x):\n    return x\n",
        encoding="utf-8",
    )
    pytest_stub = StubBackend(report_xml=_junit_xml(total=2))
    return score_coding(
        case,
        case_dir,
        sub,
        contract,
        backend_factory=lambda c, s: pytest_stub,
        work_dir=work_dir or (tmp_path / "work"),
        io_backend_factory=io_factory,
    )


# ============================================================================
# Reconciliation primitives (strict-typed, host-side)
# ============================================================================


class TestStrictEqual:
    def test_bool_int_trap_is_closed(self) -> None:
        assert _strict_equal(1, 1) is True
        assert _strict_equal(True, 1) is False  # would be True under ==
        assert _strict_equal(1, True) is False
        assert _strict_equal([1, True], [1, 1]) is False
        assert _strict_equal({"a": 1}, {"a": True}) is False

    def test_nested_structures(self) -> None:
        assert _strict_equal({"a": [1, 2]}, {"a": [1, 2]}) is True
        assert _strict_equal([{"x": 1}], [{"x": 2}]) is False


class TestReconcile:
    def _write(self, tmp_path: Path, payload) -> Path:
        p = tmp_path / "io_results.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_all_correct_passes(self, tmp_path: Path) -> None:
        payload = [{"index": 0, "ok": True, "result": 9}, {"index": 1, "ok": True, "result": 1}]
        assert _reconcile_io(self._write(tmp_path, payload), [9, 1], []) == 1.0

    def test_wrong_value_fails(self, tmp_path: Path) -> None:
        payload = [{"index": 0, "ok": True, "result": 8}]  # expected 9
        assert _reconcile_io(self._write(tmp_path, payload), [9], []) == 0.0

    def test_missing_count_fails(self, tmp_path: Path) -> None:
        payload = [{"index": 0, "ok": True, "result": 9}]  # only 1 of 2
        assert _reconcile_io(self._write(tmp_path, payload), [9, 1], []) == 0.0

    def test_extra_count_fails(self, tmp_path: Path) -> None:
        payload = [
            {"index": 0, "ok": True, "result": 9},
            {"index": 1, "ok": True, "result": 1},
        ]
        assert _reconcile_io(self._write(tmp_path, payload), [9], []) == 0.0

    def test_misaligned_index_fails(self, tmp_path: Path) -> None:
        payload = [{"index": 5, "ok": True, "result": 9}]
        assert _reconcile_io(self._write(tmp_path, payload), [9], []) == 0.0

    def test_not_run_fails(self, tmp_path: Path) -> None:
        payload = [{"index": 0, "ok": False, "error": "boom"}]
        assert _reconcile_io(self._write(tmp_path, payload), [9], []) == 0.0

    def test_unparseable_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "io_results.json"
        p.write_text("not json{", encoding="utf-8")
        assert _reconcile_io(p, [9], []) == 0.0

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        assert _reconcile_io(tmp_path / "nope.json", [9], []) == 0.0

    def test_bool_int_forgery_fails(self, tmp_path: Path) -> None:
        # A forged True where int 1 is expected must not pass via ==.
        payload = [{"index": 0, "ok": True, "result": True}]
        assert _reconcile_io(self._write(tmp_path, payload), [1], []) == 0.0


# ============================================================================
# Driver source (import-order discipline, P1-2)
# ============================================================================


class TestDriverSource:
    def test_deps_import_before_submission_path_insert(self) -> None:
        src = _io_driver_source("solution", "add_two")
        assert src.index("import sys, json, traceback, importlib") < src.index("sys.path.insert")
        assert "importlib.import_module('solution')" in src
        assert "getattr(_mod, 'add_two')" in src


# ============================================================================
# Mount isolation (P1-4) and image binding (P1-3)
# ============================================================================


class TestBackendMounts:
    def test_io_backend_never_mounts_hidden_tests(self, tmp_path: Path) -> None:
        backend = _default_io_backend_factory(
            tmp_path / "sub", tmp_path / "io", image="sha256:" + "a" * 64
        )
        mounts = [container for _host, container in backend._ro_mounts]
        assert JUDGE_HIDDEN_TESTS not in mounts  # P1-4
        assert JUDGE_SUBMISSION in mounts
        assert JUDGE_IO in mounts

    def test_io_backend_uses_given_image_id(self, tmp_path: Path) -> None:
        img = "sha256:" + "b" * 64
        backend = _default_io_backend_factory(tmp_path / "sub", tmp_path / "io", image=img)
        assert backend._image == img  # P1-3: immutable ID, not a mutable tag


# ============================================================================
# score_coding integration (the two signals combine with AND)
# ============================================================================


class TestScoreCodingWithOracle:
    def test_golden_passes_both_signals(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        result = _score(tmp_path, registry, case_dir, _io_factory(mode="golden"))
        assert result.gates["io_oracle_pass"] is True
        assert result.gates["tests_all_pass"] is True
        assert result.valid is True
        assert result.score == 1.0

    def test_off_by_one_impl_fails_oracle_even_if_tests_stub_passes(self, tmp_path: Path) -> None:
        # The oracle is an INDEPENDENT signal: a wrong implementation the
        # (stubbed) pytest run "passed" is still caught.
        registry, case_dir = _tmp_smoke(tmp_path)
        result = _score(
            tmp_path, registry, case_dir, _io_factory(mode="golden", fn=lambda x: x + 1)
        )
        assert result.gates["io_oracle_pass"] is False
        assert result.valid is False
        assert result.score is None

    def test_forged_correct_answers_pass_forged_wrong_fail(self, tmp_path: Path) -> None:
        # The residual is honest: to pass, the forged file must contain the
        # CORRECT answers (which requires solving). Correct forgery passes;
        # wrong forgery fails.
        registry, case_dir = _tmp_smoke(tmp_path)
        correct = [{"index": i, "ok": True, "result": r} for i, r in enumerate([9, -1, 2, 102, 13])]
        assert (
            _score(tmp_path, registry, case_dir, _io_factory("forged", forged=correct)).valid
            is True
        )
        wrong = [{"index": i, "ok": True, "result": 0} for i in range(5)]
        assert (
            _score(tmp_path, registry, case_dir, _io_factory("forged", forged=wrong)).valid is False
        )

    def test_driver_abnormal_exit_fails_oracle(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        result = _score(tmp_path, registry, case_dir, _io_factory("golden", exit_code=1))
        assert result.gates["io_oracle_pass"] is False

    def test_no_io_factory_on_oracle_case_fails_closed(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        contract = init_contract("smoke_add_two_io", registry)
        case = registry.load("smoke_add_two_io")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "solution.py").write_text("def add_two(x):\n    return x+2\n", encoding="utf-8")
        result = score_coding(
            case,
            case_dir,
            sub,
            contract,
            backend_factory=lambda c, s: StubBackend(report_xml=_junit_xml(total=2)),
            work_dir=tmp_path / "work",
            io_backend_factory=None,  # oracle case but no oracle backend
        )
        assert result.gates["io_oracle_pass"] is False  # fail-closed
        assert result.valid is False

    def test_oracle_uses_separate_work_zone(self, tmp_path: Path) -> None:
        # Hardening B: the oracle run must NOT share the pytest work dir,
        # else a submission could stash a harvested answer across runs.
        registry, case_dir = _tmp_smoke(tmp_path)
        factory = _io_factory(mode="golden")
        pytest_work = tmp_path / "work"
        _score(tmp_path, registry, case_dir, factory, work_dir=pytest_work)
        io_cwd = factory.holder["stub"].seen_cwd
        assert io_cwd is not None
        assert io_cwd.resolve() != pytest_work.resolve()


# ============================================================================
# Admission gate (P1-1 + float rejection + coupling + lint)
# ============================================================================


class TestAdmission:
    def _set_io(self, case_dir: Path, cases: list) -> None:
        (case_dir / "reference" / "held_out_io.json").write_text(
            json.dumps(cases), encoding="utf-8"
        )

    def test_empty_oracle_refused(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_io(case_dir, [])
        with pytest.raises(ValueError, match="an empty/tiny oracle gates nothing"):
            init_contract("smoke_add_two_io", registry)

    def test_tiny_oracle_refused(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_io(case_dir, [{"args": [7], "expected": 9}])  # 1 < MIN
        with pytest.raises(ValueError, match="an empty/tiny oracle gates nothing"):
            init_contract("smoke_add_two_io", registry)

    def test_float_expected_refused(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_io(
            case_dir,
            [
                {"args": [7], "expected": 9.0},
                {"args": [1], "expected": 3},
                {"args": [2], "expected": 4},
            ],
        )
        with pytest.raises(ValueError, match="float in 'expected'"):
            init_contract("smoke_add_two_io", registry)

    def test_bad_entry_format_refused(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text(
            case_yaml.read_text(encoding="utf-8").replace(
                'entry: "solution:add_two"', 'entry: "not-an-identifier"'
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="must be 'module:function'"):
            init_contract("smoke_add_two_io", registry)

    def test_input_overlapping_hidden_tests_refused(self, tmp_path: Path) -> None:
        # Disjointness lint: [2] appears in the hidden test `add_two(2)`.
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_io(
            case_dir,
            [
                {"args": [2], "expected": 4},
                {"args": [7], "expected": 9},
                {"args": [1], "expected": 3},
            ],
        )
        with pytest.raises(ValueError, match="disjoint from hidden-test inputs"):
            init_contract("smoke_add_two_io", registry)

    def test_bare_gate_without_oracle_refused(self, tmp_path: Path) -> None:
        # A hand-authored io_oracle_pass gate with no oracle behind it would
        # fail closed forever — refused at admission.
        registry, case_dir = _tmp_smoke(tmp_path)
        case_yaml = case_dir / "case.yaml"
        text = case_yaml.read_text(encoding="utf-8")
        # strip the io_oracle block, then force the gate via init override
        text = text.split("  io_oracle:")[0]
        case_yaml.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="fail closed on every run"):
            init_contract(
                "smoke_add_two_io", registry, validity_gates=["tests_all_pass", "io_oracle_pass"]
            )


# ============================================================================
# Anchor coverage (held-out expected drift → exit 3)
# ============================================================================


class TestAnchorCoverage:
    def test_held_out_io_byte_drift_is_frozen_drift(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        contract = init_contract("smoke_add_two_io", registry)
        assert verify_frozen(contract, case_dir) == []
        io_path = case_dir / "reference" / "held_out_io.json"
        data = json.loads(io_path.read_text(encoding="utf-8"))
        data[0]["expected"] = 999  # tamper an expected value
        io_path.write_text(json.dumps(data), encoding="utf-8")
        drift = verify_frozen(contract, case_dir)
        assert "reference/held_out_io.json" in drift

    def test_score_submission_refuses_on_held_out_drift(self, tmp_path: Path) -> None:
        from cfdb.agentbench.scorer import score_submission

        registry, case_dir = _tmp_smoke(tmp_path)
        contract = init_contract("smoke_add_two_io", registry)
        case = registry.load("smoke_add_two_io")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "solution.py").write_text("def add_two(x):\n    return x+2\n", encoding="utf-8")
        io_path = case_dir / "reference" / "held_out_io.json"
        io_path.write_text(io_path.read_text(encoding="utf-8").replace("9", "8"), encoding="utf-8")
        with pytest.raises(FrozenDriftError, match="held_out_io.json"):
            score_submission(contract, case, case_dir, sub)


# ============================================================================
# Codex R9-R0 review fixes (3 P1 + 4 P2)
# ============================================================================


class TestResultArtifactGuard:
    """P1: the result file lands in a container-writable dir — a hostile
    submission can plant a FIFO / symlink / oversized file for the host to
    read. The reader requires a bounded regular non-symlink file."""

    def test_fifo_results_file_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / IO_RESULTS_FILENAME
        os.mkfifo(p)  # host read_text() on a FIFO would block forever
        notes: list[str] = []
        assert _reconcile_io(p, [9], notes) == 0.0
        assert any("regular file" in n for n in notes)

    def test_symlink_results_file_rejected(self, tmp_path: Path) -> None:
        # Even a symlink pointing at a VALID payload is rejected (lstat does
        # not follow) — the submission could aim it at /dev/zero or a huge file.
        target = tmp_path / "real.json"
        target.write_text(json.dumps([{"index": 0, "ok": True, "result": 9}]), encoding="utf-8")
        link = tmp_path / IO_RESULTS_FILENAME
        os.symlink(target, link)
        notes: list[str] = []
        assert _reconcile_io(link, [9], notes) == 0.0
        assert any("regular file" in n for n in notes)

    def test_oversized_results_file_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(ss, "IO_RESULTS_MAX_BYTES", 8)  # tiny cap for the test
        p = tmp_path / IO_RESULTS_FILENAME
        p.write_text(json.dumps([{"index": 0, "ok": True, "result": 9}]), encoding="utf-8")
        notes: list[str] = []
        assert _reconcile_io(p, [9], notes) == 0.0
        assert any("exceeds cap" in n for n in notes)

    def test_regular_bounded_file_still_accepted(self, tmp_path: Path) -> None:
        # The guard must not reject a legitimate driver-written file.
        p = tmp_path / IO_RESULTS_FILENAME
        p.write_text(json.dumps([{"index": 0, "ok": True, "result": 9}]), encoding="utf-8")
        assert _reconcile_io(p, [9], []) == 1.0


class TestReconcileNullResult:
    """P2: a forged row that omits ``result`` must fail before equality, else
    ``row.get('result')`` is None and spuriously matches a null expected."""

    def test_missing_result_key_with_null_expected_fails(self, tmp_path: Path) -> None:
        p = tmp_path / IO_RESULTS_FILENAME
        p.write_text(json.dumps([{"index": 0, "ok": True}]), encoding="utf-8")  # no result
        notes: list[str] = []
        assert _reconcile_io(p, [None], notes) == 0.0
        assert any("no 'result' field" in n for n in notes)

    def test_present_null_result_with_null_expected_passes(self, tmp_path: Path) -> None:
        # A genuine null result (key PRESENT) still matches a null expected —
        # the fix rejects a MISSING key, not a null value.
        p = tmp_path / IO_RESULTS_FILENAME
        p.write_text(json.dumps([{"index": 0, "ok": True, "result": None}]), encoding="utf-8")
        assert _reconcile_io(p, [None], []) == 1.0


class TestCasesFileAnchoring:
    """P1: cases_file must resolve inside the frozen, secret reference/ tree,
    or the held-out answers are neither anchored nor kept from the agent."""

    def _set_cases_file(self, case_dir: Path, rel: str) -> None:
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text(
            case_yaml.read_text(encoding="utf-8").replace(
                'cases_file: "reference/held_out_io.json"', f'cases_file: "{rel}"'
            ),
            encoding="utf-8",
        )

    def test_cases_file_at_case_root_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        (case_dir / "held_out_root.json").write_text(
            json.dumps([{"args": [7], "expected": 9}] * 3), encoding="utf-8"
        )
        self._set_cases_file(case_dir, "held_out_root.json")  # outside reference/
        with pytest.raises(ValueError, match="reference/ tree"):
            init_contract("smoke_add_two_io", registry)

    def test_cases_file_dotdot_escape_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_cases_file(case_dir, "../../../etc/passwd")
        with pytest.raises(ValueError, match="reference/ tree"):
            init_contract("smoke_add_two_io", registry)

    def test_symlink_dir_component_rejected(self, tmp_path: Path) -> None:
        # R9-R1 P1: a directory-symlink component would let the target be
        # retargeted outside reference/ without tripping verify_frozen.
        registry, case_dir = _tmp_smoke(tmp_path)
        ref = case_dir / "reference"
        (ref / "real_io").mkdir()
        (ref / "real_io" / "held.json").write_text(
            json.dumps([{"args": [7], "expected": 9}] * 3), encoding="utf-8"
        )
        os.symlink(ref / "real_io", ref / "link")
        self._set_cases_file(case_dir, "reference/link/held.json")
        with pytest.raises(ValueError, match="traverses a symlink"):
            init_contract("smoke_add_two_io", registry)

    def test_symlink_file_component_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        ref = case_dir / "reference"
        (ref / "real_held.json").write_text(
            json.dumps([{"args": [7], "expected": 9}] * 3), encoding="utf-8"
        )
        os.symlink(ref / "real_held.json", ref / "held_link.json")
        self._set_cases_file(case_dir, "reference/held_link.json")
        with pytest.raises(ValueError, match="traverses a symlink"):
            init_contract("smoke_add_two_io", registry)


class TestNonCodingOracleRejected:
    """P2: an io_oracle on a non-coding case would be silently ignored (its
    gate is only auto-appended for coding). Reject the inert declaration."""

    def test_cfd_case_with_io_oracle_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text(
            case_yaml.read_text(encoding="utf-8").replace("domain: coding", "domain: cfd"),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="coding-domain primitive"):
            init_contract("smoke_add_two_io", registry)


class TestMidRunDriftReverify:
    """P1: the oracle is a second container run that extends the scoring
    window past the post-pytest verify_frozen; a host-side tamper DURING it
    must still be caught before the verdict is ledgered."""

    def test_host_tamper_during_oracle_run_is_caught(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        held = case_dir / "reference" / "held_out_io.json"

        def tampering_factory(submission_dir: Path, io_dir: Path):
            class _Tamper:
                def execute(self, command, cwd, timeout=None, env=None) -> RunResult:
                    inputs = json.loads((io_dir / "inputs.json").read_text(encoding="utf-8"))
                    out = [
                        {"index": it["index"], "ok": True, "result": it["args"][0] + 2}
                        for it in inputs
                    ]
                    (Path(cwd) / "io_results.json").write_text(json.dumps(out), encoding="utf-8")
                    # a host actor edits a frozen file mid-oracle-run
                    data = json.loads(held.read_text(encoding="utf-8"))
                    data[0]["expected"] = 4321
                    held.write_text(json.dumps(data), encoding="utf-8")
                    return RunResult(
                        exit_code=0, stdout="", stderr="", wall_time_sec=0.1, timed_out=False
                    )

            return _Tamper()

        contract = init_contract("smoke_add_two_io", registry)
        case = registry.load("smoke_add_two_io")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "solution.py").write_text(
            "def add_two(x):\n    return x + 2\ndef already_works(x):\n    return x\n",
            encoding="utf-8",
        )
        pytest_stub = StubBackend(report_xml=_junit_xml(total=2))
        with pytest.raises(FrozenDriftError, match="held_out_io.json"):
            score_coding(
                case,
                case_dir,
                sub,
                contract,
                backend_factory=lambda c, s: pytest_stub,
                work_dir=tmp_path / "work",
                io_backend_factory=tampering_factory,
            )


class TestDriverNativeTypes:
    """P2: json.dump would silently coerce a tuple to a list (and int keys to
    strings), defeating the type-strict claim. The driver rejects non-native
    returns BEFORE serialization. Exercised by exec-ing the driver source in
    process with the two container paths redirected."""

    def _run_driver(self, tmp_path: Path, monkeypatch, ret) -> list:
        inputs = tmp_path / "inputs.json"
        inputs.write_text(json.dumps([{"index": 0, "args": [1]}]), encoding="utf-8")
        results = tmp_path / "io_results.json"
        fake = types.ModuleType("solmod")
        fake.f = lambda x: ret  # noqa: ARG005
        monkeypatch.setitem(sys.modules, "solmod", fake)
        real_open = builtins.open

        def fake_open(file, *a, **k):
            if file == JUDGE_IO_INPUTS:
                file = str(inputs)
            elif file == IO_RESULTS_CONTAINER:
                file = str(results)
            return real_open(file, *a, **k)

        monkeypatch.setattr(builtins, "open", fake_open)
        exec(compile(_io_driver_source("solmod", "f"), "<driver>", "exec"), {})
        return json.loads(results.read_text(encoding="utf-8"))

    def test_tuple_return_rejected(self, tmp_path: Path, monkeypatch) -> None:
        out = self._run_driver(tmp_path, monkeypatch, (1, 2))
        assert out[0]["ok"] is False
        assert "non-JSON-native" in out[0]["error"]

    def test_non_str_dict_key_rejected(self, tmp_path: Path, monkeypatch) -> None:
        out = self._run_driver(tmp_path, monkeypatch, {1: "a"})  # int key -> would coerce
        assert out[0]["ok"] is False
        assert "non-JSON-native" in out[0]["error"]

    def test_native_list_return_preserved(self, tmp_path: Path, monkeypatch) -> None:
        out = self._run_driver(tmp_path, monkeypatch, [1, 2])
        assert out[0]["ok"] is True
        assert out[0]["result"] == [1, 2]

    def test_int_subclass_rejected(self, tmp_path: Path, monkeypatch) -> None:
        # R9-R1 P2: exact-type identity rejects an int subclass that json.dump
        # would silently collapse to a plain int (isinstance would accept it).
        class _MyInt(int):
            pass

        out = self._run_driver(tmp_path, monkeypatch, _MyInt(9))
        assert out[0]["ok"] is False
        assert "non-JSON-native" in out[0]["error"]

    def test_primitives_captured_before_submission_import(self) -> None:
        # R9-R1 P2: the trusted type primitives must be bound BEFORE the
        # submission is imported, so import-time builtin rebinding cannot reach
        # them. (isinstance is not used at all — exact `is` identity instead.)
        src = _io_driver_source("m", "f")
        assert src.index("_type = type") < src.index("import_module")
        assert "isinstance" not in src

    def test_validator_primitives_are_function_locals(self) -> None:
        # R9-R2 P2: driver.py runs as __main__, so a module-level `_list = list`
        # would be rebindable via `import __main__; __main__._list = tuple`.
        # Wrapping the whole driver in `_drive()` makes every primitive a
        # function LOCAL — nothing but the imports and the _drive() call sits at
        # module level, so there is no submission-reachable name to swap.
        src = _io_driver_source("m", "f")
        assert "def _drive():" in src
        assert "\n    _type = type\n" in src  # captured, indented under _drive
        toplevel = [ln for ln in src.split("\n") if ln and not ln.startswith((" ", "\t"))]
        assert toplevel == [
            "import sys, json, traceback, importlib",
            "def _drive():",
            "_drive()",
        ]


class TestOracleSatisfiability:
    """R9-R1 P2: admission must refuse a held-out set whose correct-solution
    result file would exceed the runtime cap — else init freezes an
    unsatisfiable ruler that fails the golden and every correct submission."""

    def _set_io(self, case_dir: Path, cases: list) -> None:
        (case_dir / "reference" / "held_out_io.json").write_text(
            json.dumps(cases), encoding="utf-8"
        )

    def test_too_many_cases_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        self._set_io(case_dir, [{"args": [i], "expected": i} for i in range(10_001)])
        with pytest.raises(ValueError, match="10000"):
            init_contract("smoke_add_two_io", registry)

    def test_oversized_result_rejected(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_smoke(tmp_path)
        big = "x" * (5 * 1024 * 1024)  # 3 * 5 MiB projected > 8 MiB cap
        self._set_io(
            case_dir,
            [
                {"args": [7], "expected": big},
                {"args": [-3], "expected": big},
                {"args": [0], "expected": big},
            ],
        )
        with pytest.raises(ValueError, match="unsatisfiable|exceed the runtime cap"):
            init_contract("smoke_add_two_io", registry)


class TestResultCapAnchoring:
    """R9-R2 P1: the runtime result-size cap decides accept/reject in
    _reconcile_io, so it is a judging constant and must live on the frozen
    ruler surface (the anchored sandbox_scorer module) — not in the unanchored
    contract.py, where changing it would silently re-decide verdicts while
    verify_frozen stays clean."""

    def test_cap_lives_in_anchored_module_not_contract(self) -> None:
        assert hasattr(ss, "IO_RESULTS_MAX_BYTES")
        assert not hasattr(ct, "IO_RESULTS_MAX_BYTES")  # moved out of unanchored module

    def test_changing_cap_would_drift_the_ruler(self) -> None:
        # judge_source:sandbox_scorer anchors this module's source bytes, so a
        # change to the constant flips the source hash -> new ruler id. Prove
        # the constant is actually in those hashed bytes.
        import hashlib
        import inspect

        src = inspect.getsource(ss)
        assert "IO_RESULTS_MAX_BYTES = 8 * 1024 * 1024" in src
        mutated = src.replace(
            "IO_RESULTS_MAX_BYTES = 8 * 1024 * 1024",
            "IO_RESULTS_MAX_BYTES = 16 * 1024 * 1024",
        )
        assert (
            hashlib.sha256(src.encode()).hexdigest() != hashlib.sha256(mutated.encode()).hexdigest()
        )


class TestShippedIoOracleCases:
    """R9 rollout regression guard: every shipped coding case that declares an
    io_oracle must still init cleanly with the io_oracle_pass gate. This runs
    the REAL admission validation (disjointness / satisfiability / float / path
    checks) against the committed cases, so an author who later breaks a
    held-out set — e.g. edits an input to overlap a hidden test — turns this
    red instead of shipping a silently-degraded oracle."""

    def test_all_shipped_io_cases_admit_with_gate(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        io_cases = sorted(
            c.id for c in registry.list_all() if c.execution.io_oracle is not None
        )
        # the R9 pilot plus the real coding tasks the oracle rolled out to
        assert set(io_cases) >= {
            "smoke_add_two_io",
            "balanced_brackets",
            "csv_field_splitter",
            "roman_to_int",
            "merge_intervals",
        }, io_cases
        for case_id in io_cases:
            contract = init_contract(case_id, registry)  # runs io-oracle admission
            assert "io_oracle_pass" in contract.validity_gates, case_id
