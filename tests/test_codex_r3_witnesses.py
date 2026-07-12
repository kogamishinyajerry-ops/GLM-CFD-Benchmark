"""Tamper witnesses for the Codex R3 governance-review fix batch (v5.0 R4).

R3 findings and their witnesses here:

- P1 importable bytecode invisible to the ruler: ``-B`` only stops Python
  *writing* caches, not *loading* them (a bare ``helper.pyc`` beside a
  checker imports fine — verified live on this machine). The R2 batch's
  cache *exclusion* therefore hid executable judging material from both
  the per-file anchors and the manifest. R3 semantics: init REFUSES to
  anchor while any cache artifact exists in a judged tree, and the
  manifest sees caches — one appearing post-anchor is real drift.
- P2 judged-file anchors not mandatory: stripping ``reference/checker.py``
  or a ``held_out:*`` key from the frozen map used to verify clean. The
  required set is now the full expected key set re-derived from the case.
- P2 the universal load check omitted ``judge_source:scorer``: a contract
  the scoring path would refuse could still load and display INTACT.

Each test follows the house witness form: prove the untampered baseline
passes, apply a single-point tamper, assert the flip to the specified
fail-closed state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cfdb.agentbench.contract import (
    FILE_MANIFEST_KEY,
    JUDGE_SOURCE_PREFIX,
    REQUIRED_UNIVERSAL_ANCHORS,
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
# R3 P1: importable bytecode is refused at anchor time and visible as drift
# ============================================================================


class TestBytecodeRefusedNotHidden:
    def test_baseline_clean_case_anchors(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        contract = init_contract("dir_organize", registry)
        assert FILE_MANIFEST_KEY in contract.frozen

    def test_pycache_dir_refuses_anchor(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        pycache = case_dir / "reference" / "__pycache__"
        pycache.mkdir()
        (pycache / "helper.cpython-312.pyc").write_bytes(b"\x00")
        registry.clear_cache()
        with pytest.raises(ValueError, match="importable bytecode"):
            init_contract("dir_organize", registry)

    def test_bare_pyc_refuses_anchor(self, tmp_path: Path) -> None:
        # The sharpest form verified live: a sourceless helper.pyc adjacent
        # to the checker is importable even under -B.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        (case_dir / "reference" / "helper.pyc").write_bytes(b"\x00")
        registry.clear_cache()
        with pytest.raises(ValueError, match="importable bytecode"):
            init_contract("dir_organize", registry)

    def test_post_anchor_cache_appearance_drifts(self, tmp_path: Path) -> None:
        # -B keeps legitimate runs cache-free, so a cache appearing after
        # anchoring is real drift and must bite via the manifest.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        contract = init_contract("dir_organize", registry)
        assert verify_frozen(contract, case_dir) == []
        pycache = case_dir / "reference" / "__pycache__"
        pycache.mkdir()
        (pycache / "smuggled.cpython-312.pyc").write_bytes(b"\x00")
        assert FILE_MANIFEST_KEY in verify_frozen(contract, case_dir)


# ============================================================================
# R3 P2a: every judged file key is mandatory, not just the special keys
# ============================================================================


class TestJudgedFileAnchorsMandatory:
    def test_stripped_checker_key_refuses_scoring(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        sub = tmp_path / "sub"
        sub.mkdir()
        expected = json.loads(
            (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        )
        (sub / "summary.json").write_text(json.dumps(expected), encoding="utf-8")
        baseline = score_submission(contract, case, case_dir, sub)
        assert baseline.valid is True
        del contract.frozen["reference/checker.py"]
        assert verify_frozen(contract, case_dir) == []  # the R3 attack premise
        ledger = tmp_path / "ledger.jsonl"
        with pytest.raises(FrozenDriftError, match="reference/checker.py"):
            score_submission(contract, case, case_dir, sub, ledger_path=ledger)
        assert not ledger.exists()

    def test_stripped_held_out_key_flagged(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "validation", "lid_driven_cavity")
        contract = init_contract("lid_driven_cavity", registry)
        case = registry.load("lid_driven_cavity")
        held_out_keys = [k for k in contract.frozen if k.startswith("held_out:")]
        assert held_out_keys != []  # cavity pilot must carry one
        assert missing_required_anchors(contract, case, case_dir) == []
        del contract.frozen[held_out_keys[0]]
        assert verify_frozen(contract, case_dir) == []  # invisible to re-hashing
        assert missing_required_anchors(contract, case, case_dir) == [held_out_keys[0]]

    def test_vanished_declared_reference_fails_closed(self, tmp_path: Path) -> None:
        # If the expected key set cannot even be enumerated, that failure
        # is the finding — never a silent downgrade to special-keys-only.
        registry, case_dir = _tmp_case_registry(tmp_path, "validation", "lid_driven_cavity")
        contract = init_contract("lid_driven_cavity", registry)
        case = registry.load("lid_driven_cavity")
        (case_dir / "held_out" / "qoi.json").unlink()
        missing = missing_required_anchors(contract, case, case_dir)
        assert len(missing) == 1
        assert "enumeration failed" in missing[0]


# ============================================================================
# R3 P2b: judge_source:scorer is a universal load-time requirement
# ============================================================================


class TestScorerAnchorUniversalAtLoad:
    def test_scorer_key_in_universal_set(self) -> None:
        assert SCORER_KEY in REQUIRED_UNIVERSAL_ANCHORS

    def test_stripped_scorer_key_refused_at_load(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "validation", "lid_driven_cavity")
        raw = init_contract("lid_driven_cavity", registry).model_dump()
        path = tmp_path / "contract.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert load_contract(path).case_id == "lid_driven_cavity"  # baseline
        del raw["frozen"][SCORER_KEY]
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="missing mandatory anchors"):
            load_contract(path)
