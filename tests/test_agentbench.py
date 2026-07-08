"""Tests for the agentbench frozen-ruler scoring module (P4-E).

Covers contract init/round-trip, gate recomputation, score recomputation,
the append-only ledger, and the mandatory tamper witnesses:

1. Flipping one byte of a reference file -> scoring is refused (drift).
2. A forged self-reported ``qoi_error`` in the submission is ignored; the
   recomputed value wins.
3. Invalid samples never enter the valid ranking (even with a forged score).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cfdb.agentbench import (
    EXIT_FROZEN_DRIFT,
    FrozenDriftError,
    ScoringContract,
    SubmissionScore,
    init_contract,
    load_contract,
    ranked,
    read_ledger,
    save_contract,
    score_submission,
    verify_frozen,
)
from cfdb.agentbench.contract import VALIDITY_GATES_KEY, WEIGHTS_KEY
from cfdb.registry import CaseRegistry

CASE_ID = "agent_case"
REF_QOI = {"cl": 0.5, "cd": 0.02}


def _write_case(cases_root: Path, *, max_runtime_sec: int | None = 100) -> Path:
    """Create a minimal validation case with a file-based QoI reference."""
    case_dir = cases_root / "validation" / CASE_ID
    (case_dir / "reference").mkdir(parents=True)
    spec = {
        "id": CASE_ID,
        "name": "Agentbench test case",
        "category": "validation",
        "physics": {"flow": "rans", "turbulence": "rans_sa"},
        "conditions": {"reynolds": 1.0e6},
        "solvers": [{"name": "generic", "command": "true"}],
        "outputs": {"qoi": ["cl", "cd"]},
        "reference": {
            "type": "experimental",
            "files": {"qoi": "reference/qoi.json"},
        },
        "metrics": {"qoi_relative_tolerance": {"cl": 0.2, "cd": 0.2}},
        "budget": {},
    }
    if max_runtime_sec is not None:
        spec["budget"] = {"max_runtime_sec": max_runtime_sec}
    (case_dir / "case.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    (case_dir / "reference" / "qoi.json").write_text(json.dumps(REF_QOI), encoding="utf-8")
    return case_dir


def _write_submission(
    root: Path,
    name: str = "sub_a",
    qoi: dict[str, float] | None = None,
    wall_time_sec: float | None = 50.0,
) -> Path:
    """Create a submission directory with qoi.json (+ optional manifest)."""
    sub_dir = root / name
    sub_dir.mkdir(parents=True)
    if qoi is not None:
        (sub_dir / "qoi.json").write_text(json.dumps(qoi), encoding="utf-8")
    if wall_time_sec is not None:
        manifest = {"timing": {"wall_time_sec": wall_time_sec}}
        (sub_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return sub_dir


@pytest.fixture
def bench(tmp_path: Path):
    """Registry + case + contract + ledger path for one scoring session."""
    cases_root = tmp_path / "cases"
    case_dir = _write_case(cases_root)
    registry = CaseRegistry(cases_root)
    case = registry.load(CASE_ID)
    contract = init_contract(CASE_ID, registry)
    ledger = tmp_path / "agentbench" / CASE_ID / "ledger.jsonl"
    return registry, case, case_dir, contract, ledger, tmp_path


class TestContract:
    def test_frozen_covers_case_reference_and_ruler(self, bench) -> None:
        _, _, _, contract, _, _ = bench
        assert "case.yaml" in contract.frozen
        assert "reference/qoi.json" in contract.frozen
        assert WEIGHTS_KEY in contract.frozen
        assert VALIDITY_GATES_KEY in contract.frozen
        for digest in contract.frozen.values():
            assert len(digest) == 64
            assert all(c in "0123456789abcdef" for c in digest)

    def test_save_load_round_trip(self, bench, tmp_path: Path) -> None:
        _, _, case_dir, contract, _, _ = bench
        path = tmp_path / "agentbench" / CASE_ID / "contract.json"
        save_contract(contract, path)
        loaded = load_contract(path)
        assert loaded == contract
        assert verify_frozen(loaded, case_dir) == []

    def test_empty_frozen_rejected(self) -> None:
        with pytest.raises(ValueError, match="frozen map must not be empty"):
            ScoringContract(case_id=CASE_ID, frozen={})

    def test_missing_declared_reference_fails_init(self, tmp_path: Path) -> None:
        cases_root = tmp_path / "cases"
        case_dir = _write_case(cases_root)
        (case_dir / "reference" / "qoi.json").unlink()
        registry = CaseRegistry(cases_root)
        with pytest.raises(FileNotFoundError, match="cannot freeze"):
            init_contract(CASE_ID, registry)

    def test_exit_code_constant(self) -> None:
        assert EXIT_FROZEN_DRIFT == 3


class TestScoring:
    def test_valid_submission_score_recomputed(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        sub = _write_submission(tmp, qoi={"cl": 0.55, "cd": 0.021}, wall_time_sec=50.0)
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.valid is True
        assert result.gates == {"qoi_complete": True, "within_budget": True}
        # mean(|0.55-0.5|/0.5, |0.021-0.02|/0.02) = mean(0.1, 0.05) = 0.075
        assert result.breakdown["qoi_error"] == pytest.approx(-1.0 * 0.075)
        assert result.breakdown["wall_time_sec"] == pytest.approx(-0.001 * 50.0)
        assert result.score == pytest.approx(-0.075 - 0.05)
        assert result.submission_id == "sub_a"
        assert result.scored_at != ""

    def test_missing_qoi_json_invalid(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        sub = _write_submission(tmp, qoi=None, wall_time_sec=50.0)
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.valid is False
        assert result.score is None
        assert result.gates["qoi_complete"] is False

    def test_budget_exceeded_invalid(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02}, wall_time_sec=150.0)
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.valid is False
        assert result.score is None
        assert result.gates["within_budget"] is False

    def test_missing_wall_time_fails_budget_gate_closed(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02}, wall_time_sec=None)
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.gates["within_budget"] is False
        assert result.valid is False
        assert result.score is None

    def test_unknown_gate_fails_closed(self, bench) -> None:
        registry, case, case_dir, _, ledger, tmp = bench
        contract = init_contract(
            CASE_ID, registry, validity_gates=["qoi_complete", "no_such_gate"]
        )
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.gates["no_such_gate"] is False
        assert result.valid is False
        assert result.score is None

    def test_missing_weighted_metric_never_fabricates_score(self, tmp_path: Path) -> None:
        # No budget configured -> within_budget passes vacuously, but the
        # wall_time_sec weight has no measurement -> score must stay None.
        cases_root = tmp_path / "cases"
        case_dir = _write_case(cases_root, max_runtime_sec=None)
        registry = CaseRegistry(cases_root)
        case = registry.load(CASE_ID)
        contract = init_contract(CASE_ID, registry)
        sub = _write_submission(tmp_path, qoi={"cl": 0.5, "cd": 0.02}, wall_time_sec=None)
        result = score_submission(contract, case, case_dir, sub)
        assert result.valid is True
        assert result.score is None
        assert any("wall_time_sec" in n for n in result.notes)

    def test_case_contract_mismatch_rejected(self, bench) -> None:
        _, case, case_dir, contract, _, tmp = bench
        wrong = contract.model_copy(update={"case_id": "other_case"})
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        with pytest.raises(ValueError, match="does not match contract"):
            score_submission(wrong, case, case_dir, sub)


class TestTamperWitnesses:
    def test_witness_1_reference_byte_flip_refuses_scoring(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        ref = case_dir / "reference" / "qoi.json"
        data = bytearray(ref.read_bytes())
        data[0] ^= 0xFF
        ref.write_bytes(bytes(data))
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        with pytest.raises(FrozenDriftError) as excinfo:
            score_submission(contract, case, case_dir, sub, ledger)
        assert "reference/qoi.json" in excinfo.value.drifted
        assert not ledger.exists()  # nothing was ever scored or ledgered

    def test_witness_1b_case_yaml_edit_refuses_scoring(self, bench) -> None:
        _, case, case_dir, contract, _, tmp = bench
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text(
            case_yaml.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
        )
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        with pytest.raises(FrozenDriftError) as excinfo:
            score_submission(contract, case, case_dir, sub)
        assert "case.yaml" in excinfo.value.drifted

    def test_witness_1c_reference_deletion_refuses_scoring(self, bench) -> None:
        _, case, case_dir, contract, _, tmp = bench
        (case_dir / "reference" / "qoi.json").unlink()
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        with pytest.raises(FrozenDriftError) as excinfo:
            score_submission(contract, case, case_dir, sub)
        assert "reference/qoi.json" in excinfo.value.drifted

    def test_witness_weights_edit_is_ruler_change(self, bench) -> None:
        # Changing the weights without re-anchoring = changing the ruler.
        _, case, case_dir, contract, _, tmp = bench
        tampered = contract.model_copy(update={"weights": {"qoi_error": -100.0}})
        sub = _write_submission(tmp, qoi={"cl": 0.5, "cd": 0.02})
        with pytest.raises(FrozenDriftError) as excinfo:
            score_submission(tampered, case, case_dir, sub)
        assert WEIGHTS_KEY in excinfo.value.drifted

    def test_witness_gates_edit_is_ruler_change(self, bench) -> None:
        _, case, case_dir, contract, _, _ = bench
        tampered = contract.model_copy(update={"validity_gates": []})
        assert VALIDITY_GATES_KEY in verify_frozen(tampered, case_dir)

    def test_witness_2_forged_qoi_error_ignored_recomputed_wins(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        forged = {"cl": 0.55, "cd": 0.021, "qoi_error": 0.0, "score": 999.0}
        sub = _write_submission(tmp, qoi=forged)
        result = score_submission(contract, case, case_dir, sub, ledger)
        assert result.valid is True
        # Recomputed 0.075 wins over the forged 0.0.
        assert result.breakdown["qoi_error"] == pytest.approx(-0.075)
        assert result.score == pytest.approx(-0.125)
        assert result.score != 999.0
        assert any("qoi_error" in n and "ignored" in n for n in result.notes)

    def test_witness_3_invalid_sample_never_ranks(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        good = _write_submission(tmp, "sub_good", qoi={"cl": 0.55, "cd": 0.021})
        bad = _write_submission(tmp, "sub_bad", qoi={"cl": 0.55})  # cd missing
        score_submission(contract, case, case_dir, good, ledger)
        invalid = score_submission(contract, case, case_dir, bad, ledger)
        assert invalid.valid is False
        assert invalid.score is None
        ranking = ranked(read_ledger(ledger))
        assert [e.submission_id for e in ranking] == ["sub_good"]

    def test_witness_3b_forged_score_on_invalid_ledger_line_excluded(self, bench) -> None:
        from cfdb.agentbench import append_ledger

        _, _, _, _, ledger, _ = bench
        forged = SubmissionScore(
            submission_id="forged", valid=False, score=123.0, scored_at="2026-01-01T00:00:00Z"
        )
        append_ledger(ledger, forged)
        assert ranked(read_ledger(ledger)) == []


class TestLedger:
    def test_append_only_preserves_history_and_order(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        first = _write_submission(tmp, "sub_1", qoi={"cl": 0.55, "cd": 0.021})
        second = _write_submission(tmp, "sub_2", qoi={"cl": 0.51, "cd": 0.02})
        score_submission(contract, case, case_dir, first, ledger)
        score_submission(contract, case, case_dir, second, ledger)
        entries = read_ledger(ledger)
        assert [e.submission_id for e in entries] == ["sub_1", "sub_2"]
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2

    def test_ranking_orders_best_score_first(self, bench) -> None:
        _, case, case_dir, contract, ledger, tmp = bench
        worse = _write_submission(tmp, "sub_worse", qoi={"cl": 0.6, "cd": 0.03})
        better = _write_submission(tmp, "sub_better", qoi={"cl": 0.51, "cd": 0.02})
        score_submission(contract, case, case_dir, worse, ledger)
        score_submission(contract, case, case_dir, better, ledger)
        ranking = ranked(read_ledger(ledger))
        assert [e.submission_id for e in ranking] == ["sub_better", "sub_worse"]

    def test_missing_ledger_reads_empty(self, tmp_path: Path) -> None:
        assert read_ledger(tmp_path / "nope.jsonl") == []

    def test_corrupt_ledger_line_raises(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text('{"not": "a score"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt ledger line 1"):
            read_ledger(ledger)
