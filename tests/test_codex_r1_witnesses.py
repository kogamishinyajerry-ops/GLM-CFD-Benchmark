"""Tamper witnesses for the Codex R1 governance-review fix batch (v5.0 R2).

R1 findings and their witnesses here:

- P1 ruler lineage: the coding/agentic judge module source is anchored into
  the frozen map (``judge_source:*``), so hardening the judge changes the
  contract bytes -> new ruler_id -> old and new verdicts never share a
  leaderboard.
- P1 visible-tree inventory: a file *added* to ``reference/`` or
  ``visible/`` after anchoring (which per-file hashes cannot see) drifts
  the :data:`FILE_MANIFEST_KEY` anchor.
- P2 legacy contracts: version-1 contracts (no hardened anchors) are
  refused at load with a re-anchor instruction.
- P2 CLI boundary: checker-admission refusal exits ``cfdb agent-eval init``
  with a structured ``[FAIL]``, not an uncaught traceback.

Each test follows the house witness form: prove the untampered baseline
passes, apply a single-point tamper, assert the flip to the specified
fail-closed state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.agentbench import contract as contract_mod
from cfdb.agentbench.contract import (
    FILE_MANIFEST_KEY,
    JUDGE_SOURCE_PREFIX,
    init_contract,
    load_contract,
    save_contract,
    verify_frozen,
)
from cfdb.cli import app
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


def _combined_output(result: object) -> str:
    """stdout + stderr of a CliRunner result (click >= 8.2 splits them)."""
    output = str(getattr(result, "output", ""))
    try:
        stderr = str(getattr(result, "stderr", ""))
    except ValueError:  # stderr not captured separately
        stderr = ""
    return output + stderr


# ============================================================================
# R1 P1a: judge-source lineage anchor
# ============================================================================


class TestJudgeSourceAnchor:
    def test_coding_contract_anchors_sandbox_scorer(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract("smoke_add_two", registry)
        assert f"{JUDGE_SOURCE_PREFIX}sandbox_scorer" in contract.frozen
        assert f"{JUDGE_SOURCE_PREFIX}checker_scorer" not in contract.frozen

    def test_agentic_contract_anchors_checker_scorer(self) -> None:
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract("csv_field_extract", registry)
        assert f"{JUDGE_SOURCE_PREFIX}checker_scorer" in contract.frozen
        assert f"{JUDGE_SOURCE_PREFIX}sandbox_scorer" not in contract.frozen

    def test_cfd_contract_anchors_shared_scorer_only(self) -> None:
        # Since the R2 batch the shared scorer.py (gate evaluation, score
        # assembly, cfd QoI recomputation) is anchored for every domain;
        # cfd carries no domain-specific judge module beyond it.
        registry = CaseRegistry(PROJECT_CASES)
        contract = init_contract("lid_driven_cavity", registry)
        judge_keys = {k for k in contract.frozen if k.startswith(JUDGE_SOURCE_PREFIX)}
        assert judge_keys == {f"{JUDGE_SOURCE_PREFIX}scorer"}

    def test_baseline_then_judge_source_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert verify_frozen(contract, case_dir) == []
        monkeypatch.setattr(contract_mod, "_judge_source_digest", lambda shortname: "0" * 64)
        assert f"{JUDGE_SOURCE_PREFIX}sandbox_scorer" in verify_frozen(contract, case_dir)

    def test_judge_hardening_changes_contract_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ruler lineage is the sha256 of contract.json; a judge-policy change
        # must change the serialized contract so ruler_id cannot survive it.
        registry = CaseRegistry(PROJECT_CASES)
        before = init_contract("smoke_add_two", registry).model_dump_json()
        monkeypatch.setattr(contract_mod, "_judge_source_digest", lambda shortname: "f" * 64)
        after = init_contract("smoke_add_two", registry).model_dump_json()
        assert before != after

    def test_unknown_judge_anchor_fails_closed(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        contract.frozen[f"{JUDGE_SOURCE_PREFIX}bogus_module"] = "0" * 64
        assert f"{JUDGE_SOURCE_PREFIX}bogus_module" in verify_frozen(contract, case_dir)


# ============================================================================
# R1 P1b: judged-tree file manifest anchor
# ============================================================================


class TestFileManifestWitness:
    def test_agentic_added_visible_file_drifts(self, tmp_path: Path) -> None:
        # The exact R1 attack: visible/messy/new.txt shifts the
        # checker-derived expected layout while every anchored file still
        # hashes clean.
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        contract = init_contract("dir_organize", registry)
        assert FILE_MANIFEST_KEY in contract.frozen
        assert verify_frozen(contract, case_dir) == []
        (case_dir / "visible" / "messy" / "new.txt").write_text(
            "smuggled after anchoring\n", encoding="utf-8"
        )
        assert FILE_MANIFEST_KEY in verify_frozen(contract, case_dir)

    def test_coding_added_hidden_test_drifts(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert verify_frozen(contract, case_dir) == []
        (case_dir / "reference" / "hidden_tests" / "test_extra.py").write_text(
            "def test_extra() -> None:\n    assert True\n", encoding="utf-8"
        )
        assert FILE_MANIFEST_KEY in verify_frozen(contract, case_dir)

    def test_removed_visible_file_drifts(self, tmp_path: Path) -> None:
        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "dir_organize")
        contract = init_contract("dir_organize", registry)
        assert verify_frozen(contract, case_dir) == []
        (case_dir / "visible" / "messy" / "notes.txt").unlink()
        assert FILE_MANIFEST_KEY in verify_frozen(contract, case_dir)

    def test_coding_visible_content_edit_drifts(self, tmp_path: Path) -> None:
        # visible/ is the task surface: editing a coding case's starting
        # solution.py changes what is being measured. Per-file anchoring of
        # visible/ is all-domain since the R1 batch — content edits (which
        # the path manifest cannot see) must drift the per-file anchor.
        registry, case_dir = _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")
        contract = init_contract("smoke_add_two", registry)
        assert "visible/solution.py" in contract.frozen
        assert verify_frozen(contract, case_dir) == []
        target = case_dir / "visible" / "solution.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# nudge the task surface\n",
            encoding="utf-8",
        )
        assert "visible/solution.py" in verify_frozen(contract, case_dir)


# ============================================================================
# R1 P2a: legacy (pre-v2) contracts refused at load
# ============================================================================


class TestLegacyContractRejected:
    def test_v2_contract_roundtrips(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        assert contract.contract_version == "2"
        path = tmp_path / "contract.json"
        save_contract(contract, path)
        assert load_contract(path) == contract

    def test_v1_contract_refused_with_reanchor_instruction(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        raw = init_contract("csv_field_extract", registry).model_dump()
        # Reconstruct what a genuine pre-hardening contract looked like:
        # version 1, none of the hardened anchors.
        raw["contract_version"] = "1"
        for key in list(raw["frozen"]):
            if key == FILE_MANIFEST_KEY or key.startswith(JUDGE_SOURCE_PREFIX):
                del raw["frozen"][key]
        path = tmp_path / "legacy_contract.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="--force"):
            load_contract(path)

    def test_versionless_contract_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "versionless.json"
        path.write_text(
            json.dumps({"case_id": "x", "frozen": {"case.yaml": "0" * 64}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="legacy scoring contract"):
            load_contract(path)


# ============================================================================
# R1 P2b: checker-admission failure is a structured CLI [FAIL]
# ============================================================================


class TestCliAdmissionFailStructured:
    def test_baseline_init_succeeds(self, tmp_path: Path) -> None:
        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        result = CliRunner().invoke(
            app,
            [
                "agent-eval",
                "init",
                "--case",
                "csv_field_extract",
                "--cases-dir",
                str(tmp_path / "cases"),
                "--agentbench-dir",
                str(tmp_path / "agentbench"),
            ],
        )
        assert result.exit_code == 0
        assert "[OK]" in _combined_output(result)

    def test_denied_checker_import_exits_structured_fail(self, tmp_path: Path) -> None:
        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        checker = case_dir / "reference" / "checker.py"
        checker.write_text(
            "import subprocess\n" + checker.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            app,
            [
                "agent-eval",
                "init",
                "--case",
                "csv_field_extract",
                "--cases-dir",
                str(tmp_path / "cases"),
                "--agentbench-dir",
                str(tmp_path / "agentbench"),
            ],
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "[FAIL]" in combined
        assert "failed admission" in combined
        assert result.exception is None or isinstance(result.exception, SystemExit)
