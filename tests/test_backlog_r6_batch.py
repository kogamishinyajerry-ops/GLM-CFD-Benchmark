"""Witnesses for the R6 backlog batch: judge-image anchor, INVALID
disclosure, pass@k.

- Judge image identity (``__judge_image__``): the coding judge environment
  is judging material — a rebuilt image under an unchanged tag must not
  share a ruler ID. Anchored at init (env identity or docker inspect,
  fail-closed when neither resolves), mandatory for coding contracts,
  skipped by verify_frozen (live-daemon comparison happens in
  sandbox_scorer's real path immediately before judging).
- INVALID disclosure: invalid samples are ledgered but never ranked;
  the showcase must show their volume, not just the survivors.
- pass@k: unbiased estimator, current-ruler samples only, never
  extrapolated when n < k.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cfdb.agentbench.contract import (
    JUDGE_IMAGE_KEY,
    init_contract,
    missing_required_anchors,
    verify_frozen,
)
from cfdb.agentbench.scorer import SubmissionScore, pass_at_k
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


# ============================================================================
# Judge image identity anchor
# ============================================================================


class TestJudgeImageAnchor:
    def test_coding_contract_anchors_image_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CFDB_JUDGE_IMAGE_ID", "sha256:" + "a" * 64)
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract("smoke_add_two", registry)
        assert contract.frozen[JUDGE_IMAGE_KEY] == "sha256:" + "a" * 64

    def test_non_coding_domains_do_not_anchor_image(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        assert JUDGE_IMAGE_KEY not in init_contract("csv_field_extract", registry).frozen
        assert JUDGE_IMAGE_KEY not in init_contract("lid_driven_cavity", registry).frozen

    def test_unresolvable_image_refuses_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No explicit identity and no resolvable daemon/image -> init must
        # refuse to anchor a judge environment it cannot identify.
        from cfdb.agentbench import contract as contract_mod

        monkeypatch.delenv("CFDB_JUDGE_IMAGE_ID", raising=False)
        monkeypatch.setattr(
            contract_mod,
            "resolve_judge_image_id",
            lambda ref: (_ for _ in ()).throw(ValueError(f"cannot resolve judge image '{ref}'")),
        )
        registry = CaseRegistry(PROJECT_CASES)
        with pytest.raises(ValueError, match="cannot resolve judge image"):
            init_contract("smoke_add_two", registry)

    def test_image_key_mandatory_for_coding(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        case = registry.load("smoke_add_two")
        assert missing_required_anchors(contract, case, case_dir) == []
        del contract.frozen[JUDGE_IMAGE_KEY]
        assert missing_required_anchors(contract, case, case_dir) == [JUDGE_IMAGE_KEY]

    def test_verify_frozen_skips_image_key(self, tmp_path: Path) -> None:
        # The key is not derivable from the case dir; verify must neither
        # crash nor mark it drifted (the live comparison is the scoring
        # path's job) — and everything else must still verify.
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert JUDGE_IMAGE_KEY in contract.frozen
        assert verify_frozen(contract, case_dir) == []

    def test_live_mismatch_refuses_scoring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Baseline: anchored == live -> judging proceeds (stubbed backend
        # path is exercised elsewhere; here we force the REAL path's image
        # comparison by leaving backend_factory None and stubbing both the
        # resolver and the backend construction).
        from test_agentbench_coding import StubBackend, _junit_xml

        import cfdb.agentbench.sandbox_scorer as sbx
        from cfdb.agentbench.contract import FrozenDriftError

        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        case = registry.load("smoke_add_two")
        sub = tmp_path / "sub"
        sub.mkdir()

        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))
        built_with: list[str | None] = []

        def capturing_factory(c: Path, s: Path, image: str | None = None) -> StubBackend:
            built_with.append(image)
            return stub

        monkeypatch.setattr(sbx, "_default_backend_factory", capturing_factory)

        anchored = contract.frozen[JUDGE_IMAGE_KEY]
        monkeypatch.setattr(sbx, "resolve_judge_image_id", lambda ref: anchored)
        baseline = sbx.score_coding(case, case_dir, sub, contract)
        assert baseline.valid is True
        # TOCTOU closure (Codex R6 P1): the container must be constructed
        # from the verified IMMUTABLE ID, never the mutable tag.
        assert built_with == [anchored]

        # Tamper: daemon now holds a different image under the same tag.
        monkeypatch.setattr(sbx, "resolve_judge_image_id", lambda ref: "sha256:" + "f" * 64)
        with pytest.raises(FrozenDriftError, match=JUDGE_IMAGE_KEY):
            sbx.score_coding(case, case_dir, sub, contract)


# ============================================================================
# pass@k
# ============================================================================


def _entry(
    valid: bool,
    score: float | None,
    ruler: str | None = "r1",
    sid: str | None = None,
) -> SubmissionScore:
    breakdown = {"pass_rate": score} if (valid and score is not None) else {}
    return SubmissionScore(
        submission_id=sid if sid is not None else f"s_{_entry_counter.__next__()}",
        valid=valid,
        score=score if valid else None,
        breakdown=breakdown,
        gates={"tests_all_pass": valid},
        ruler_id=ruler,
    )


_entry_counter = iter(range(10**9))


class TestPassAtK:
    def test_matches_bruteforce_combinatorics(self) -> None:
        from itertools import combinations

        entries = [_entry(True, 1.0) for _ in range(3)] + [_entry(False, None) for _ in range(7)]
        for k in (1, 2, 3, 5):
            result = pass_at_k(entries, k, ruler_id="r1", domain="coding")
            assert result is not None
            value, n, c = result
            assert (n, c) == (10, 3)
            outcomes = [1] * 3 + [0] * 7
            combos = list(combinations(outcomes, k))
            brute = sum(1 for combo in combos if any(combo)) / len(combos)
            assert value == pytest.approx(brute)

    def test_insufficient_samples_never_extrapolated(self) -> None:
        entries = [_entry(True, 1.0), _entry(False, None)]
        assert pass_at_k(entries, 3, ruler_id="r1", domain="coding") is None
        assert pass_at_k(entries, 0, ruler_id="r1", domain="coding") is None
        assert pass_at_k([], 1, domain="agentic") is None

    def test_continuous_domain_refused(self) -> None:
        # Codex R6 P1: for cfd, rankable does not mean correct — a ledger
        # of arbitrarily wrong QoIs must never fabricate pass@1 = 1.
        entries = [_entry(True, 1.0) for _ in range(5)]
        with pytest.raises(ValueError, match="binary success signal"):
            pass_at_k(entries, 1, ruler_id="r1", domain="cfd")

    def test_rescoring_events_collapse_into_one_attempt(self) -> None:
        # Codex R6 P1: rescoring the same submission k times must not
        # manufacture n >= k "independent" samples.
        entries = [_entry(True, 1.0, sid="same_sub") for _ in range(5)]
        assert pass_at_k(entries, 3, ruler_id="r1", domain="coding") is None
        result = pass_at_k(entries, 1, ruler_id="r1", domain="coding")
        assert result is not None
        assert result[1:] == (1, 1)

    def test_disagreeing_rescore_rows_fail_closed(self) -> None:
        # A deterministic judge produces agreeing rows; if rescoring rows
        # for one attempt disagree, the attempt is never counted as a pass.
        entries = [
            _entry(True, 1.0, sid="flaky"),
            _entry(False, None, sid="flaky"),
            _entry(True, 1.0, sid="solid"),
        ]
        result = pass_at_k(entries, 1, ruler_id="r1", domain="coding")
        assert result is not None
        value, n, c = result
        assert (n, c) == (2, 1)

    def test_stale_ruler_rows_are_not_samples(self) -> None:
        entries = [_entry(True, 1.0, ruler="old") for _ in range(5)] + [
            _entry(False, None, ruler="r1") for _ in range(2)
        ]
        result = pass_at_k(entries, 1, ruler_id="r1", domain="coding")
        assert result is not None
        value, n, c = result
        assert (n, c) == (2, 0)
        assert value == 0.0

    def test_forged_score_is_not_a_pass(self) -> None:
        # valid=True but score inconsistent with breakdown -> not rankable,
        # therefore not a pass@k pass either.
        forged = SubmissionScore(
            submission_id="forged",
            valid=True,
            score=1.0,
            breakdown={"pass_rate": 0.0},
            gates={"tests_all_pass": True},
            ruler_id="r1",
        )
        result = pass_at_k([forged], 1, ruler_id="r1", domain="coding")
        assert result is not None
        value, n, c = result
        assert (value, n, c) == (0.0, 1, 0)

    def test_all_pass_short_circuit(self) -> None:
        entries = [_entry(True, 1.0) for _ in range(4)]
        result = pass_at_k(entries, 3, ruler_id="r1", domain="coding")
        assert result is not None
        assert result[0] == 1.0


# ============================================================================
# INVALID disclosure in the showcase collector
# ============================================================================


class TestInvalidDisclosure:
    def test_collector_counts_invalid(self, tmp_path: Path) -> None:
        import json

        from cfdb.agentbench.scorer import append_ledger
        from cfdb.reporting.showcase import _collect_agentbench

        _, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        registry = CaseRegistry(tmp_path / "cases")
        contract = init_contract("smoke_add_two", registry)
        bench_dir = tmp_path / "agentbench" / "smoke_add_two"
        bench_dir.mkdir(parents=True)
        (bench_dir / "contract.json").write_text(
            contract.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        ruler = (
            __import__("hashlib").sha256((bench_dir / "contract.json").read_bytes()).hexdigest()[:8]
        )
        ledger = bench_dir / "ledger.jsonl"
        append_ledger(ledger, _entry(True, 1.0, ruler=ruler))
        append_ledger(ledger, _entry(False, None, ruler=ruler))
        append_ledger(ledger, _entry(False, None, ruler=ruler))

        data = _collect_agentbench(tmp_path)
        row = data["contracts"][0]
        assert row["n_events"] == 3
        assert row["n_valid"] == 1
        assert row["n_invalid"] == 2
        # Sanity: serialize round-trips (template consumes these fields).
        json.dumps({k: v for k, v in row.items() if k != "drifted"})
