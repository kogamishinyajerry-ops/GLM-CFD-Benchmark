"""Witnesses for the judge-policy extraction (v5.0 R5 batch, backlog item).

The refactor's contract: every shared verdict-affecting rule moved from the
orchestration module (scorer.py) into the dedicated, anchored
``judge_policy`` module. Two properties must hold and stay held:

1. The anchor surface is exact — ``judge_source:judge_policy`` is frozen
   into contracts of every domain, and editing the policy module drifts
   them (exit-3 family).
2. The orchestration/ledger module is NOT anchored — no contract carries a
   ``judge_source:scorer`` key anymore, so ledger and ranking improvements
   no longer force a fleet-wide re-anchor. Rulers anchored before the
   extraction are refused at load with a re-anchor instruction, never
   silently accepted under a moved policy.

Behavior invariance of the move itself is pinned by the full pre-existing
suite (verdict/gate/score semantics unchanged, byte-for-byte function
bodies).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cfdb.agentbench.contract import (
    JUDGE_SOURCE_PREFIX,
    init_contract,
    load_contract,
    verify_frozen,
)
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"

POLICY_KEY = f"{JUDGE_SOURCE_PREFIX}judge_policy"
LEGACY_SCORER_KEY = f"{JUDGE_SOURCE_PREFIX}scorer"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


class TestPolicyAnchorExact:
    @pytest.mark.parametrize(
        ("family", "case_id"),
        [
            ("coding_tasks", "smoke_add_two"),
            ("agentic_tasks", "csv_field_extract"),
            ("validation", "lid_driven_cavity"),
        ],
    )
    def test_policy_anchored_scorer_not(self, family: str, case_id: str) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract(case_id, registry)
        assert POLICY_KEY in contract.frozen
        assert LEGACY_SCORER_KEY not in contract.frozen

    def test_policy_edit_drifts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from cfdb.agentbench import contract as contract_mod

        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert verify_frozen(contract, case_dir) == []
        real_digest = contract_mod._judge_source_digest
        monkeypatch.setattr(
            contract_mod,
            "_judge_source_digest",
            lambda name: "0" * 64 if name == "judge_policy" else real_digest(name),
        )
        assert POLICY_KEY in verify_frozen(contract, case_dir)


class TestPreExtractionRulersRefused:
    def test_scorer_anchored_ruler_refused_at_load(self, tmp_path: Path) -> None:
        # Reconstruct an R3/R4-era contract: shared policy anchored as
        # judge_source:scorer, no judge_policy key. Accepting it would let
        # verdicts run under a moved (= changed) policy with a stale lineage.
        registry, _ = _tmp_case_registry(tmp_path, "validation", "lid_driven_cavity")
        raw = init_contract("lid_driven_cavity", registry).model_dump()
        raw["frozen"][LEGACY_SCORER_KEY] = raw["frozen"].pop(POLICY_KEY)
        path = tmp_path / "pre_extraction.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="missing mandatory anchors"):
            load_contract(path)

    def test_unknown_scorer_anchor_fails_closed_at_verify(self, tmp_path: Path) -> None:
        # Even if such a ruler reached verify_frozen (e.g. via a caller that
        # skips load_contract), the retired shortname is no longer in the
        # whitelist and must read as drift, never crash or pass.
        registry, case_dir = _tmp_case_registry(tmp_path, "validation", "lid_driven_cavity")
        contract = init_contract("lid_driven_cavity", registry)
        contract.frozen[LEGACY_SCORER_KEY] = "0" * 64
        assert LEGACY_SCORER_KEY in verify_frozen(contract, case_dir)
