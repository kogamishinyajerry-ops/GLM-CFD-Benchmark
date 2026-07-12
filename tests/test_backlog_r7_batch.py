"""Witnesses for the R7 backlog batch (adjudicated follow-up optimization).

- Atomic contract save: a failed re-anchor must never leave a torn ruler.
- Hash-chained ledger: in-file edits, mid-file deletions and unchained
  forgeries break at a named line; legacy rows tolerated only as a prefix;
  appending to a broken chain is refused.
- Showcase content identity: unique submissions counted by attempt_id,
  legacy rows disclosed separately.
- CSV reference curves: NACA Ladson cp data loads strictly; one malformed
  row rejects the whole file.
- Canary sentinel: a blanket-forged junitxml (fabricated suite counts,
  no real pytest run) cannot name the per-run canary testcase and is
  invalidated. Cost-raiser, not a boundary — the in-process residual
  stays declared.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_agentbench_coding import StubBackend, _junit_xml

from cfdb.agentbench.contract import init_contract, load_contract, save_contract
from cfdb.agentbench.scorer import (
    LEDGER_CHAIN_GENESIS,
    SubmissionScore,
    append_ledger,
    read_ledger,
    verify_ledger_chain,
)
from cfdb.metrics.engine import MetricsEngine
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


def _row(sid: str = "s", score: float = 1.0, attempt: str | None = "a1") -> SubmissionScore:
    return SubmissionScore(
        submission_id=sid,
        valid=True,
        score=score,
        breakdown={"pass_rate": score},
        gates={"tests_all_pass": True},
        ruler_id="r1",
        attempt_id=attempt,
    )


# ============================================================================
# Atomic contract save
# ============================================================================


class TestAtomicContractSave:
    def test_failed_reanchor_leaves_original_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cfdb.agentbench.contract as contract_mod

        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        original = init_contract("csv_field_extract", registry)
        path = tmp_path / "contract.json"
        save_contract(original, path)
        before = path.read_bytes()

        def exploding_replace(src: object, dst: object) -> None:
            raise OSError("simulated crash at replace time")

        monkeypatch.setattr(contract_mod.os, "replace", exploding_replace)
        with pytest.raises(OSError, match="simulated crash"):
            save_contract(original, path, force=True)
        # The old ruler is byte-identical and still loads; no temp litter.
        assert path.read_bytes() == before
        assert load_contract(path).case_id == "csv_field_extract"
        assert list(path.parent.glob("*.tmp")) == []

    def test_success_leaves_no_temp_file(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        path = tmp_path / "contract.json"
        save_contract(contract, path)
        assert load_contract(path).case_id == "csv_field_extract"
        assert list(path.parent.glob("*.tmp")) == []


# ============================================================================
# Hash-chained ledger
# ============================================================================


class TestLedgerHashChain:
    def test_appended_rows_chain_and_verify_clean(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        for i in range(3):
            append_ledger(ledger, _row(sid=f"s{i}", attempt=f"a{i}"))
        report = verify_ledger_chain(ledger)
        assert report.problems == []
        assert (report.unchained_prefix, report.n_chained) == (0, 3)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert report.head == rows[-1]["chain"]
        assert all(isinstance(r["chain"], str) and len(r["chain"]) == 64 for r in rows)
        # read_ledger round-trips the chain field.
        assert [e.chain for e in read_ledger(ledger)] == [r["chain"] for r in rows]

    def test_edited_row_breaks_chain_at_named_line(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        for i in range(3):
            append_ledger(ledger, _row(sid=f"s{i}", attempt=f"a{i}"))
        lines = ledger.read_text().splitlines()
        row = json.loads(lines[1])
        row["score"] = 0.123456  # doctor the middle row, keep its stored chain
        lines[1] = json.dumps(row)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        report = verify_ledger_chain(ledger)
        assert len(report.problems) == 1
        assert report.problems[0].startswith("line 2: chain mismatch")
        # Fresh rows must never bury tampering.
        with pytest.raises(ValueError, match="chain broken"):
            append_ledger(ledger, _row(sid="s3", attempt="a3"))

    def test_mid_file_deletion_breaks_chain(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        for i in range(3):
            append_ledger(ledger, _row(sid=f"s{i}", attempt=f"a{i}"))
        lines = ledger.read_text().splitlines()
        del lines[1]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = verify_ledger_chain(ledger)
        assert any("line 2: chain mismatch" in p for p in report.problems)

    def test_unchained_forgery_after_chain_started_is_named(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        append_ledger(ledger, _row(sid="s0", attempt="a0"))
        forged = json.loads(_row(sid="forged", attempt="af").model_dump_json())
        forged.pop("chain", None)
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(forged) + "\n")
        report = verify_ledger_chain(ledger)
        assert report.problems == ["line 2: unchained row after the chain started"]

    def test_legacy_prefix_is_tolerated_and_disclosed(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        for i in range(2):  # pre-chain rows, as real ledgers have
            legacy = json.loads(_row(sid=f"old{i}", attempt=None).model_dump_json())
            legacy.pop("chain", None)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(legacy) + "\n")
        append_ledger(ledger, _row(sid="new", attempt="a9"))
        report = verify_ledger_chain(ledger)
        assert report.problems == []
        assert (report.unchained_prefix, report.n_chained) == (2, 1)

    def test_first_chained_row_links_from_genesis(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        append_ledger(ledger, _row())
        row = json.loads(ledger.read_text().splitlines()[0])
        from cfdb.agentbench.scorer import _chain_hash

        assert row["chain"] == _chain_hash(LEDGER_CHAIN_GENESIS, row)


# ============================================================================
# Showcase unique submissions by content identity
# ============================================================================


class TestShowcaseAttemptIdentity:
    def test_unique_counts_by_attempt_id_with_legacy_disclosed(self, tmp_path: Path) -> None:
        from cfdb.reporting.showcase import _collect_agentbench

        _, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        registry = CaseRegistry(tmp_path / "cases")
        contract = init_contract("smoke_add_two", registry)
        bench_dir = tmp_path / "agentbench" / "smoke_add_two"
        bench_dir.mkdir(parents=True)
        (bench_dir / "contract.json").write_text(
            contract.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        ledger = bench_dir / "ledger.jsonl"
        # 3 rows of one content, 1 of another (different basenames), 2 legacy.
        for sid in ("team_a", "team_b", "team_c"):
            append_ledger(ledger, _row(sid=sid, attempt="same_content"))
        append_ledger(ledger, _row(sid="team_d", attempt="other_content"))
        for sid in ("old_1", "old_2"):
            append_ledger(ledger, _row(sid=sid, attempt=None))

        data = _collect_agentbench(tmp_path)
        row = next(c for c in data["contracts"] if c["dir_name"] == "smoke_add_two")
        assert row["n_events"] == 6
        assert row["n_unique_submissions"] == 2  # content identities, not basenames
        assert row["n_no_identity"] == 2  # legacy rows disclosed, never merged


# ============================================================================
# CSV reference curves (NACA cp mapping)
# ============================================================================


class TestCsvReferenceCurve:
    def test_ladson_reference_csv_loads(self) -> None:
        engine = MetricsEngine()
        path = PROJECT_CASES / "validation" / "naca0012_a5" / "reference" / "ladson1988_a5.csv"
        curve = engine._load_reference_curve(path)
        assert curve is not None
        assert len(curve) >= 5
        assert curve[0] == (0.0, 1.0)

    def test_key_alignment_resolves_cp_distribution(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        case = registry.load("naca0012_a5")
        case_dir = registry.get_case_dir("naca0012_a5")
        engine = MetricsEngine()
        curves = engine._get_reference_curves(case, case_dir)
        assert "cp_distribution" in curves
        assert len(curves["cp_distribution"]) >= 5

    @pytest.mark.parametrize(
        "content",
        [
            "x/c,Cp\n0.0,1.0\n0.1\n",  # short row
            "x/c,Cp\n0.0,1.0\n0.1,abc\n",  # non-numeric
            "x/c,Cp\n0.0,1.0\n0.1,nan\n",  # non-finite
            "x/c,Cp\n0.0,1.0,9\n",  # three columns
            "x/c,Cp\n",  # header only
            "",  # empty
        ],
    )
    def test_malformed_csv_rejects_whole_file(self, tmp_path: Path, content: str) -> None:
        bad = tmp_path / "ref.csv"
        bad.write_text(content, encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(bad) is None

    def test_headerless_numeric_csv_loads(self, tmp_path: Path) -> None:
        raw = tmp_path / "ref.csv"
        raw.write_text("0.0,1.0\n0.5,-0.3\n", encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(raw) == [(0.0, 1.0), (0.5, -0.3)]

    def test_json_reference_path_unchanged(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.json"
        ref.write_text(json.dumps([[0.0, 1.0], [1.0, 0.5]]), encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(ref) == [(0.0, 1.0), (1.0, 0.5)]


# ============================================================================
# Canary sentinel (coding judge)
# ============================================================================

CANARY = "test_cfdb_canary_deadbeefdeadbeef"


class TestCanarySentinel:
    def _coding_setup(self, tmp_path: Path):
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        case = registry.load("smoke_add_two")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "solution.py").write_text("def add_two(x):\n    return x + 2\n", encoding="utf-8")
        return contract, case, case_dir, sub

    def test_blanket_forged_report_without_canary_is_invalid(self, tmp_path: Path) -> None:
        # A forger who knows only the public expected_test_count fabricates
        # a matching all-pass report — the per-run canary name is missing.
        from cfdb.agentbench.sandbox_scorer import score_coding

        contract, case, case_dir, sub = self._coding_setup(tmp_path)
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))
        result = score_coding(
            case,
            case_dir,
            sub,
            contract,
            backend_factory=lambda c, s: stub,
            work_dir=tmp_path / "work",
            canary_name=CANARY,
        )
        assert result.valid is False
        assert result.score is None
        assert any("canary sentinel" in n for n in result.notes)

    def test_report_with_passing_canary_is_valid(self, tmp_path: Path) -> None:
        from cfdb.agentbench.sandbox_scorer import score_coding

        contract, case, case_dir, sub = self._coding_setup(tmp_path)
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected, extra_case_names=[CANARY]))
        result = score_coding(
            case,
            case_dir,
            sub,
            contract,
            backend_factory=lambda c, s: stub,
            work_dir=tmp_path / "work",
            canary_name=CANARY,
        )
        assert result.valid is True
        assert result.score == 1.0

    def test_failed_canary_is_invalid(self, tmp_path: Path) -> None:
        from cfdb.agentbench.sandbox_scorer import score_coding

        contract, case, case_dir, sub = self._coding_setup(tmp_path)
        expected = case.execution.expected_test_count
        cases_xml = (
            "".join(f'<testcase classname="t" name="t{i}"/>' for i in range(expected))
            + f'<testcase classname="t" name="{CANARY}"><failure message="x"/></testcase>'
        )
        xml = (
            '<?xml version="1.0" encoding="utf-8"?><testsuites>'
            f'<testsuite name="pytest" tests="{expected + 1}" failures="1" '
            f'errors="0" skipped="0">{cases_xml}</testsuite></testsuites>'
        )
        stub = StubBackend(report_xml=xml)
        result = score_coding(
            case,
            case_dir,
            sub,
            contract,
            backend_factory=lambda c, s: stub,
            work_dir=tmp_path / "work",
            canary_name=CANARY,
        )
        assert result.valid is False
        assert any("canary sentinel did not pass" in n for n in result.notes)

    def test_duplicated_canary_is_invalid(self, tmp_path: Path) -> None:
        from cfdb.agentbench.sandbox_scorer import score_coding

        contract, case, case_dir, sub = self._coding_setup(tmp_path)
        expected = case.execution.expected_test_count
        stub = StubBackend(
            report_xml=_junit_xml(total=expected - 1, extra_case_names=[CANARY, CANARY])
        )
        result = score_coding(
            case,
            case_dir,
            sub,
            contract,
            backend_factory=lambda c, s: stub,
            work_dir=tmp_path / "work",
            canary_name=CANARY,
        )
        assert result.valid is False
        assert any("absent or duplicated" in n for n in result.notes)

    def test_real_path_auto_generates_and_enforces_canary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Production path (backend_factory=None): a canary is generated,
        # written into a judge-owned mount dir, wired into the pytest
        # command — and a report that ignores it is invalid.
        import cfdb.agentbench.sandbox_scorer as sbx
        from cfdb.agentbench.contract import JUDGE_IMAGE_KEY

        contract, case, case_dir, sub = self._coding_setup(tmp_path)
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))  # no canary testcase
        staged_files: list[list[str]] = []

        def capturing_factory(
            c: Path, s: Path, image: str | None = None, canary_dir: Path | None = None
        ) -> StubBackend:
            # Capture at factory time — the judge-owned dir is cleaned up
            # after scoring.
            staged_files.append(
                sorted(p.name for p in canary_dir.glob("*.py")) if canary_dir else []
            )
            return stub

        monkeypatch.setattr(sbx, "_default_backend_factory", capturing_factory)
        monkeypatch.setattr(
            sbx, "resolve_judge_image_id", lambda ref: contract.frozen[JUDGE_IMAGE_KEY]
        )
        result = sbx.score_coding(case, case_dir, sub, contract, work_dir=tmp_path / "work")
        assert result.valid is False
        assert any("canary sentinel" in n for n in result.notes)
        # The sentinel file was really staged in a judge-owned dir and the
        # container command collects the canary mount path.
        assert len(staged_files) == 1
        assert len(staged_files[0]) == 1
        assert staged_files[0][0].startswith("test_cfdb_canary_")
        command = stub.calls[0]["command"]
        assert any(sbx.JUDGE_CANARY in part for part in command)

    def test_canary_source_is_a_passing_named_test(self) -> None:
        from cfdb.agentbench.sandbox_scorer import _canary_source, _new_canary_name

        name = _new_canary_name()
        assert name.startswith("test_cfdb_canary_")
        source = _canary_source(name)
        assert f"def {name}():" in source
        namespace: dict = {}
        exec(source, namespace)  # noqa: S102 — judge-authored source, self-check
        namespace[name]()  # does not raise


# ============================================================================
# R7-R1 review fixes (Codex R7-R0: 1P1 + 3P2)
# ============================================================================


class TestR7R1ReviewFixes:
    def test_bundled_contracts_verify_on_committed_bytes(self) -> None:
        # Codex R7 P1 regression guard: every ruler shipped in agentbench/
        # must verify clean against the repo's committed bytes — a stale
        # judge-source anchor (e.g. re-anchor before a later reformat)
        # makes the bundled ruler unusable in a clean checkout (exit 3).
        from cfdb.agentbench.contract import missing_required_anchors, verify_frozen

        repo_root = Path(__file__).resolve().parent.parent
        registry = CaseRegistry(repo_root / "cases")
        contract_paths = sorted((repo_root / "agentbench").glob("*/contract.json"))
        assert len(contract_paths) >= 3  # smoke / csv / cavity ship today
        for contract_path in contract_paths:
            contract = load_contract(contract_path)
            case = registry.load(contract.case_id)
            case_dir = registry.get_case_dir(contract.case_id)
            assert verify_frozen(contract, case_dir) == [], contract_path
            assert missing_required_anchors(contract, case, case_dir) == [], contract_path

    def test_non_string_chain_is_named_violation_not_crash(self, tmp_path: Path) -> None:
        # Codex R7 P2: a corrupt line storing chain as a JSON number must
        # be a line-named problem, never a TypeError traceback.
        ledger = tmp_path / "ledger.jsonl"
        append_ledger(ledger, _row(sid="s0", attempt="a0"))
        good = json.loads(_row(sid="s1", attempt="a1").model_dump_json())
        good["chain"] = 123
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(good) + "\n")
        report = verify_ledger_chain(ledger)
        assert report.problems == ["line 2: chain value is not a 64-char string"]
        with pytest.raises(ValueError, match="chain broken"):
            append_ledger(ledger, _row(sid="s2", attempt="a2"))

    def test_malformed_first_data_row_rejects_headerless_file(self, tmp_path: Path) -> None:
        # Codex R7 P2: '0.0,abc' is a bad DATA row (one numeric cell), not
        # a header — silently skipping it would truncate the curve and
        # shift the L2 verdict.
        bad = tmp_path / "ref.csv"
        bad.write_text("0.0,abc\n0.5,1.0\n", encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(bad) is None
        # A positively validated header (neither cell numeric) still skips.
        good = tmp_path / "ok.csv"
        good.write_text("x/c,Cp\n0.5,1.0\n", encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(good) == [(0.5, 1.0)]

    def test_oversized_csv_field_is_contained(self, tmp_path: Path) -> None:
        # Codex R7 P2 (reviewer repro): a field beyond csv.field_size_limit
        # raises csv.Error during iteration — must be None, never a crash.
        big = tmp_path / "big.csv"
        big.write_text("x/c,Cp\n" + "1" * 200000 + ",2\n", encoding="utf-8")
        assert MetricsEngine()._load_reference_curve(big) is None

    def test_undecodable_csv_is_contained(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_bytes(b"x/c,Cp\n0.0,\xff\xfe\n")
        assert MetricsEngine()._load_reference_curve(bad) is None
