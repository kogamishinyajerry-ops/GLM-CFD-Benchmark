"""Tamper witnesses for the Codex R0 governance-review fix batch (v5.0 R1).

Each test follows the house witness form: prove the untampered baseline
passes, apply a single-point tamper, assert the flip to the specified
fail-closed state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_agentbench_coding import StubBackend, _junit_xml, bench  # noqa: F401

from cfdb.agentbench import contract as contract_mod
from cfdb.agentbench.contract import (
    NORMALIZE_SOURCE_KEY,
    init_contract,
    verify_frozen,
)
from cfdb.agentbench.sandbox_scorer import score_coding
from cfdb.agentbench.scorer import score_submission
from cfdb.failures.taxonomy import build_signature, classify
from cfdb.metrics.curves import compute_curve_l2
from cfdb.registry import CaseRegistry
from cfdb.schema import MetricsResult, RunManifest

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _agentic_tmp_registry(tmp_path: Path, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real agentic case into a tmp cases root (safe to tamper)."""
    src = PROJECT_CASES / "agentic_tasks" / case_id
    dst = tmp_path / "cases" / "agentic_tasks" / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


class TestSkippedTestsWitness:
    def test_baseline_all_pass_still_scores(self, bench) -> None:  # noqa: F811
        _, case, case_dir, contract, tmp = bench
        sub = tmp / "sub_ok"
        sub.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3))
        result = score_coding(case, case_dir, sub, contract, backend_factory=lambda *a: stub)
        assert result.valid is True

    def test_skipped_hidden_test_never_passes(self, bench) -> None:  # noqa: F811
        # pytest.skip() from inside a submission-controlled code path keeps
        # the frozen total intact with zero failures — must be invalid.
        _, case, case_dir, contract, tmp = bench
        sub = tmp / "sub_skip"
        sub.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3, skipped=1))
        result = score_coding(case, case_dir, sub, contract, backend_factory=lambda *a: stub)
        assert result.valid is False
        assert result.score is None
        assert result.gates["tests_all_pass"] is False
        assert any("skipped" in n for n in result.notes)


class TestExitCodePolicyWitness:
    @pytest.mark.parametrize("code", [2, 3, 4, 5, 125, -9])
    def test_infra_exit_codes_invalidate(self, bench, code: int) -> None:  # noqa: F811
        # A perfect-looking report left in the writable work zone must not
        # rescue a run whose judge process died an infrastructure death.
        _, case, case_dir, contract, tmp = bench
        sub = tmp / f"sub_exit_{code}"
        sub.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3), exit_code=code)
        result = score_coding(case, case_dir, sub, contract, backend_factory=lambda *a: stub)
        assert result.valid is False
        assert result.score is None
        assert any("abnormally" in n for n in result.notes)

    def test_exit_one_is_ordinary_test_failure(self, bench) -> None:  # noqa: F811
        _, case, case_dir, contract, tmp = bench
        sub = tmp / "sub_exit1"
        sub.mkdir()
        stub = StubBackend(report_xml=_junit_xml(total=3, failures=1), exit_code=1)
        result = score_coding(case, case_dir, sub, contract, backend_factory=lambda *a: stub)
        # Legitimate scoring event: gate failed, not an abnormal exit.
        assert result.gates["tests_all_pass"] is False
        assert not any("abnormally" in n for n in result.notes)


class TestRunnerSandboxConstruction:
    def test_requires_sandbox_docker_builds_sandbox_profile(self, tmp_path: Path) -> None:
        from cfdb.core.runner import Runner
        from cfdb.storage.json_repo import JsonManifestRepository

        registry = CaseRegistry(PROJECT_CASES)
        runner = Runner(registry, JsonManifestRepository(tmp_path), tmp_path)
        backend = runner._build_backend(
            "docker", {"image": "cfdb-judge:py312"}, requires_sandbox=True
        )
        assert backend.is_sandbox is True
        plain = runner._build_backend("docker", {"image": "cfdb-judge:py312"})
        assert getattr(plain, "is_sandbox", False) is False


class TestAgenticVisibleFreezeWitness:
    def test_baseline_then_visible_tamper_drifts(self, tmp_path: Path) -> None:
        registry, case_dir = _agentic_tmp_registry(tmp_path, "dir_organize")
        contract = init_contract("dir_organize", registry)
        assert any(k.startswith("visible/") for k in contract.frozen)
        assert verify_frozen(contract, case_dir) == []
        victim = next((case_dir / "visible").rglob("*"))
        while not victim.is_file():
            victim = next(p for p in (case_dir / "visible").rglob("*") if p.is_file())
        data = bytearray(victim.read_bytes() or b"\x00")
        data[0] ^= 0x01
        victim.write_bytes(bytes(data))
        drifted = verify_frozen(contract, case_dir)
        assert any(k.startswith("visible/") for k in drifted)


class TestNormalizeSourceAnchorWitness:
    def test_baseline_then_normalize_drift(self, tmp_path: Path, monkeypatch) -> None:
        registry, case_dir = _agentic_tmp_registry(tmp_path, "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        assert NORMALIZE_SOURCE_KEY in contract.frozen
        assert verify_frozen(contract, case_dir) == []
        monkeypatch.setattr(
            contract_mod, "_normalize_source_digest", lambda: "0" * 64
        )
        assert NORMALIZE_SOURCE_KEY in verify_frozen(contract, case_dir)


class TestCheckerAdmissionWitness:
    def test_denied_import_refuses_contract(self, tmp_path: Path) -> None:
        registry, case_dir = _agentic_tmp_registry(tmp_path, "csv_field_extract")
        # Baseline: the shipped checker passes admission.
        init_contract("csv_field_extract", registry)
        # Tamper: checker gains a denied import -> init must refuse.
        checker = case_dir / "reference" / "checker.py"
        checker.write_text(
            "import subprocess\n" + checker.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        registry.clear_cache()
        with pytest.raises(ValueError, match="failed admission"):
            init_contract("csv_field_extract", registry)


class TestAgenticGateListFailClosed:
    def test_unknown_gate_fails_closed(self, tmp_path: Path) -> None:
        registry, case_dir = _agentic_tmp_registry(tmp_path, "csv_field_extract")
        contract = init_contract(
            "csv_field_extract",
            registry,
            validity_gates=["checker_ok", "bogus_gate"],
        )
        case = registry.load("csv_field_extract")
        sub = tmp_path / "sub_ok"
        sub.mkdir()
        expected = json.loads(
            (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        )
        (sub / "summary.json").write_text(json.dumps(expected), encoding="utf-8")
        result = score_submission(contract, case, case_dir, sub)
        assert result.gates["checker_ok"] is True
        assert result.gates["bogus_gate"] is False
        assert result.valid is False
        assert result.score is None
        assert any("bogus_gate" in n for n in result.notes)


class TestCurveXGridWitness:
    def test_matching_x_baseline_computes(self) -> None:
        ref = {"c": [(0.0, 1.0), (1.0, 2.0)]}
        comp = {"c": [(0.0, 1.0), (1.0, 2.0)]}
        assert compute_curve_l2(ref, comp)["c"] == pytest.approx(0.0)

    def test_mismatched_x_never_reports_zero(self) -> None:
        # Same y sampled at different abscissas: L2 over y alone would be 0
        # (a fake pass) — the curve must be skipped instead (fail-closed to
        # missing/incomplete at the engine layer).
        ref = {"c": [(0.0, 1.0), (1.0, 2.0)]}
        comp = {"c": [(5.0, 1.0), (6.0, 2.0)]}
        assert "c" not in compute_curve_l2(ref, comp)


class TestTaxonomyCurveToleranceWitness:
    def test_curve_only_failure_is_tolerance_exceeded(self) -> None:
        from datetime import datetime, timezone

        from cfdb.schema import TimingSpec

        now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
        manifest = RunManifest(
            run_id="20260711T120000Z_c1_generic_deadbeef",
            case_id="c1",
            solver="generic",
            backend="local",
            status="success",
            timing=TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now),
        )
        metrics = MetricsResult(
            qoi_pass=True,
            overall_status="fail",
            curves_failed=["cp_distribution"],
        )
        assert classify(manifest, metrics) == "TOLERANCE_EXCEEDED"
        sig = build_signature(manifest, metrics, "TOLERANCE_EXCEEDED")
        assert "curves=cp_distribution" in sig
