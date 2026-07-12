"""Tamper witnesses for the Codex R2 governance-review fix batch (v5.0 R3).

R2 findings and their witnesses here:

- P1 scorer-side policy unanchored: gate evaluation / score assembly /
  cfd QoI recomputation live in the shared scorer.py, which the
  ``judge_source:*`` anchors did not cover — now anchored for every domain
  (``judge_source:scorer``), so any policy edit drifts every contract.
- P2 version label is not migration proof: a payload relabeled to v2 (or
  truncated) without the mandatory anchors is refused — universal anchors
  at load, domain-specific anchors at the scoring seam (exit-3 family).
- P2 bytecode-cache false drift: a checker importing a sibling helper must
  not drift its own ruler via a generated ``__pycache__``; caches are
  excluded from anchoring/manifest and the checker runs under ``-B``.

Each test follows the house witness form: prove the untampered baseline
passes, apply a single-point tamper, assert the flip to the specified
fail-closed state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cfdb.agentbench import contract as contract_mod
from cfdb.agentbench.contract import (
    FILE_MANIFEST_KEY,
    JUDGE_SOURCE_PREFIX,
    NORMALIZE_SOURCE_KEY,
    FrozenDriftError,
    init_contract,
    load_contract,
    missing_required_anchors,
    verify_frozen,
)
from cfdb.agentbench.scorer import score_submission
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"

SCORER_KEY = f"{JUDGE_SOURCE_PREFIX}scorer"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


# ============================================================================
# R2 P1: shared scorer.py policy is anchored for every domain
# ============================================================================


class TestSharedScorerAnchor:
    @pytest.mark.parametrize(
        ("family", "case_id"),
        [
            ("coding_tasks", "smoke_add_two"),
            ("agentic_tasks", "csv_field_extract"),
            ("validation", "lid_driven_cavity"),
        ],
    )
    def test_every_domain_anchors_scorer(self, family: str, case_id: str) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract(case_id, registry)
        assert SCORER_KEY in contract.frozen

    def test_baseline_then_scorer_policy_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A policy edit in scorer.py (simulated via its source digest
        # changing) must drift every contract, coding and cfd alike.
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert verify_frozen(contract, case_dir) == []
        real_digest = contract_mod._judge_source_digest
        monkeypatch.setattr(
            contract_mod,
            "_judge_source_digest",
            lambda name: "0" * 64 if name == "scorer" else real_digest(name),
        )
        drifted = verify_frozen(contract, case_dir)
        assert SCORER_KEY in drifted
        assert f"{JUDGE_SOURCE_PREFIX}sandbox_scorer" not in drifted


# ============================================================================
# R2 P2a: mandatory anchors are enforced, not inferred from the label
# ============================================================================


class TestMandatoryAnchorEnforcement:
    def test_relabeled_v1_payload_refused_at_load(self, tmp_path: Path) -> None:
        # The exact R2 attack: take a genuine pre-hardening payload and only
        # flip its version label to "2" — no hardened anchor exists, so the
        # label must not be honored.
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        raw = init_contract("csv_field_extract", registry).model_dump()
        raw["contract_version"] = "2"
        for key in list(raw["frozen"]):
            if key in (FILE_MANIFEST_KEY, NORMALIZE_SOURCE_KEY) or key.startswith(
                JUDGE_SOURCE_PREFIX
            ):
                del raw["frozen"][key]
        path = tmp_path / "relabeled.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="missing mandatory anchors"):
            load_contract(path)

    def test_missing_required_anchors_per_domain(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        case = registry.load("smoke_add_two")
        assert missing_required_anchors(contract, case, case_dir) == []
        del contract.frozen[f"{JUDGE_SOURCE_PREFIX}sandbox_scorer"]
        assert missing_required_anchors(contract, case, case_dir) == [
            f"{JUDGE_SOURCE_PREFIX}sandbox_scorer"
        ]

    def test_stripped_domain_anchor_refuses_scoring(self, tmp_path: Path) -> None:
        # verify_frozen alone cannot catch a *removed* key; the scoring seam
        # must refuse an incomplete ruler with the exit-3 error family and
        # write nothing to any ledger.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        sub = tmp_path / "sub"
        sub.mkdir()
        expected = json.loads(
            (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        )
        (sub / "summary.json").write_text(json.dumps(expected), encoding="utf-8")
        # Baseline: complete ruler scores normally.
        baseline = score_submission(contract, case, case_dir, sub)
        assert baseline.valid is True
        # Tamper: strip the normalize anchor -> refuse, exit-3 family.
        del contract.frozen[NORMALIZE_SOURCE_KEY]
        ledger = tmp_path / "ledger.jsonl"
        with pytest.raises(FrozenDriftError, match="mandatory anchor missing"):
            score_submission(contract, case, case_dir, sub, ledger_path=ledger)
        assert not ledger.exists()


# ============================================================================
# R2 P2b: bytecode caches never drift (or enter) the ruler
# ============================================================================


class TestBytecodeCacheImmunity:
    def test_helper_importing_checker_does_not_drift_own_ruler(self, tmp_path: Path) -> None:
        # A checker that legitimately imports a sibling helper module used
        # to generate reference/__pycache__/ mid-scoring, tripping the
        # post-run manifest re-verification as a false drift.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        helper = case_dir / "reference" / "helper.py"
        helper.write_text("MARKER = 'ok'\n", encoding="utf-8")
        checker = case_dir / "reference" / "checker.py"
        checker.write_text(
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent))\n"
            "import helper\n"
            "print(json.dumps({'success': helper.MARKER == 'ok', 'evidence': []}))\n",
            encoding="utf-8",
        )
        registry.clear_cache()
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "anything.txt").write_text("x\n", encoding="utf-8")
        result = score_submission(contract, case, case_dir, sub)
        assert result.gates["checker_ok"] is True
        assert not (case_dir / "reference" / "__pycache__").exists()
        assert verify_frozen(contract, case_dir) == []

    def test_preexisting_cache_refuses_anchor(self, tmp_path: Path) -> None:
        # R3 semantics (supersedes the R2 exclusion approach): bytecode is
        # loadable judging material even under -B, so init refuses to bless
        # a ruler over it instead of hiding it from the anchors.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        pycache = case_dir / "reference" / "__pycache__"
        pycache.mkdir()
        (pycache / "stale.cpython-312.pyc").write_bytes(b"\x00\x01")
        registry.clear_cache()
        with pytest.raises(ValueError, match="importable bytecode"):
            init_contract("csv_field_extract", registry)
        # Deleting the cache unblocks anchoring.
        shutil.rmtree(pycache)
        contract = init_contract("csv_field_extract", registry)
        assert verify_frozen(contract, case_dir) == []

    def test_real_file_addition_still_drifts(self, tmp_path: Path) -> None:
        # Guard the guard: the cache exclusion must not widen into ignoring
        # real judging-material additions.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        assert verify_frozen(contract, case_dir) == []
        (case_dir / "reference" / "extra.json").write_text("{}", encoding="utf-8")
        assert FILE_MANIFEST_KEY in verify_frozen(contract, case_dir)
