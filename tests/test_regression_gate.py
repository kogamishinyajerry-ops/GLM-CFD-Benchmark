"""Tests for cfdb.regression (P4-D): baseline governance + regression gate.

Includes the mandatory tamper witnesses:
  1. Flip one byte in the baseline run's metrics.json -> TAMPERED.
  2. Edit the anchored QoI values inside baselines.json (run file untouched,
     hash still matches) -> cross-check against the re-read file bites.
  3. With no baseline, evaluate() never returns PASS.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.regression import (
    BaselineFile,
    BaselineStore,
    baseline_key,
    evaluate,
    sha256_of_file,
)
from cfdb.schema import MetricsResult, RunManifest, TimingSpec


def write_run(
    runs_root: Path,
    run_id: str,
    *,
    case_id: str = "naca0012",
    solver: str = "openfoam",
    status: str = "success",
    overall_status: str = "pass",
    qoi_relative_errors: dict[str, float] | None = None,
    qoi_computed_values: dict[str, float] | None = None,
) -> Path:
    """Write a run directory with manifest.json + metrics.json; return run dir."""
    now = datetime.now(timezone.utc)
    manifest = RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        status=status,
        timing=TimingSpec(wall_time_sec=1.0, start_time=now, end_time=now),
    )
    errors = {"cd": 0.02, "cl": 0.01} if qoi_relative_errors is None else qoi_relative_errors
    metrics = MetricsResult(
        qoi_relative_errors=errors,
        qoi_pass=overall_status == "pass",
        overall_status=overall_status,
        qoi_computed_values=qoi_computed_values,
    )
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    return run_dir


@pytest.fixture
def store(tmp_path: Path) -> BaselineStore:
    """BaselineStore with isolated baselines.json and runs root."""
    return BaselineStore(
        baselines_path=tmp_path / "baselines" / "baselines.json",
        runs_root=tmp_path / "runs",
    )


class TestPromote:
    def test_promote_pass_run_anchors_values_and_hash(self, store: BaselineStore) -> None:
        write_run(
            store.runs_root,
            "run_base",
            qoi_relative_errors={"cd": 0.02},
            qoi_computed_values={"cd": 0.0102},
        )
        entry = store.promote("run_base", engineer="Zhuanz")

        assert entry.promoted_by == "Zhuanz"
        assert entry.case_id == "naca0012"
        assert entry.solver == "openfoam"
        assert entry.qoi_relative_errors == {"cd": 0.02}
        assert entry.qoi_values == {"cd": 0.0102}
        assert entry.metrics_sha256 == sha256_of_file(store.run_metrics_path("run_base"))
        # Persisted round-trip.
        reloaded = store.get("naca0012", "openfoam")
        assert reloaded is not None
        assert reloaded.run_id == "run_base"

    def test_promote_rejects_failed_run(self, store: BaselineStore) -> None:
        """A failed run is structurally unable to become a baseline."""
        write_run(store.runs_root, "run_fail", status="failed", overall_status="fail")
        with pytest.raises(ValueError, match="only 'pass' runs"):
            store.promote("run_fail", engineer="Zhuanz")
        assert store.get("naca0012", "openfoam") is None

    def test_promote_rejects_incomplete_run(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_inc", overall_status="incomplete")
        with pytest.raises(ValueError, match="only 'pass' runs"):
            store.promote("run_inc", engineer="Zhuanz")

    def test_promote_requires_engineer_name(self, store: BaselineStore) -> None:
        """Human signature is mandatory; blank name is not a signature."""
        write_run(store.runs_root, "run_base")
        with pytest.raises(ValueError, match="engineer"):
            store.promote("run_base", engineer="   ")

    def test_promote_missing_run_raises(self, store: BaselineStore) -> None:
        with pytest.raises(FileNotFoundError):
            store.promote("no_such_run", engineer="Zhuanz")

    def test_entry_promoted_by_has_no_default(self) -> None:
        """BaselineEntry cannot be constructed without promoted_by."""
        from pydantic import ValidationError

        from cfdb.regression import BaselineEntry

        with pytest.raises(ValidationError):
            BaselineEntry(  # type: ignore[call-arg]
                case_id="c",
                solver="s",
                run_id="r",
                promoted_at="2026-01-01T00:00:00+00:00",
            )


class TestEvaluate:
    def test_no_baseline_is_never_pass(self, store: BaselineStore) -> None:
        """Tamper witness 3: absent baseline -> NO_BASELINE, never PASS."""
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.0})
        verdict = evaluate("run_new", store)
        assert verdict.verdict == "NO_BASELINE"
        assert verdict.verdict != "PASS"

    def test_pass_when_errors_do_not_worsen(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_base", qoi_relative_errors={"cd": 0.02, "cl": 0.01})
        store.promote("run_base", engineer="Zhuanz")
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.019, "cl": 0.01})

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "PASS"
        assert verdict.deltas["cd"] == pytest.approx(-0.001)
        assert verdict.deltas["cl"] == pytest.approx(0.0)

    def test_regression_when_error_exceeds_band(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_base", qoi_relative_errors={"cd": 0.02})
        store.promote("run_base", engineer="Zhuanz")
        # band = max(0.005, 0.1 * 0.02) = 0.005 -> threshold 0.025.
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.03})

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "REGRESSION"
        assert verdict.deltas["cd"] == pytest.approx(0.01)
        assert any("cd" in r for r in verdict.reasons)

    def test_pass_within_default_tolerance_band(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_base", qoi_relative_errors={"cd": 0.02})
        store.promote("run_base", engineer="Zhuanz")
        # 0.024 <= 0.02 + max(0.005, 0.002) = 0.025 -> inside the band.
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.024})

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "PASS"

    def test_regression_margin_is_publicly_configurable(self, store: BaselineStore) -> None:
        """Tightening the margin in baselines.json flips PASS to REGRESSION."""
        write_run(store.runs_root, "run_base", qoi_relative_errors={"cd": 0.02})
        store.promote("run_base", engineer="Zhuanz")
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.024})
        assert evaluate("run_new", store).verdict == "PASS"

        data = store.load()
        data.regression_margin.absolute = 0.001
        data.regression_margin.relative = 0.0
        store.save(data)
        assert evaluate("run_new", store).verdict == "REGRESSION"

    def test_missing_baseline_qoi_in_candidate_is_regression(
        self, store: BaselineStore
    ) -> None:
        """Fail-closed: dropping an anchored QoI never passes."""
        write_run(store.runs_root, "run_base", qoi_relative_errors={"cd": 0.02, "cl": 0.01})
        store.promote("run_base", engineer="Zhuanz")
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.02})

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "REGRESSION"
        assert any("cl" in r and "missing" in r for r in verdict.reasons)

    def test_invalid_run_on_failed_status(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_base")
        store.promote("run_base", engineer="Zhuanz")
        write_run(store.runs_root, "run_new", status="failed", overall_status="fail")
        assert evaluate("run_new", store).verdict == "INVALID_RUN"

    def test_invalid_run_on_incomplete_metrics(self, store: BaselineStore) -> None:
        write_run(store.runs_root, "run_new", overall_status="incomplete")
        assert evaluate("run_new", store).verdict == "INVALID_RUN"

    def test_invalid_run_on_missing_metrics_file(self, store: BaselineStore) -> None:
        run_dir = write_run(store.runs_root, "run_new")
        (run_dir / "metrics.json").unlink()
        assert evaluate("run_new", store).verdict == "INVALID_RUN"

    def test_invalid_run_on_missing_run_dir(self, store: BaselineStore) -> None:
        assert evaluate("ghost_run", store).verdict == "INVALID_RUN"


class TestTamperWitnesses:
    def _promote_pair(self, store: BaselineStore) -> None:
        write_run(
            store.runs_root,
            "run_base",
            qoi_relative_errors={"cd": 0.02},
            qoi_computed_values={"cd": 0.0102},
        )
        store.promote("run_base", engineer="Zhuanz")
        write_run(store.runs_root, "run_new", qoi_relative_errors={"cd": 0.02})
        # Sanity: the untampered setup passes, so the witnesses below prove
        # that the tamper alone flips the verdict.
        assert evaluate("run_new", store).verdict == "PASS"

    def test_witness_1_flip_one_byte_in_run_metrics(self, store: BaselineStore) -> None:
        """Editing the baseline run's metrics.json by one byte -> TAMPERED."""
        self._promote_pair(store)
        metrics_path = store.run_metrics_path("run_base")
        raw = metrics_path.read_text(encoding="utf-8")
        # Change the anchored error 0.02 -> 0.03 (one digit) in the run file.
        assert "0.02" in raw
        metrics_path.write_text(raw.replace("0.02", "0.03", 1), encoding="utf-8")

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "TAMPERED"
        assert any("hash mismatch" in r for r in verdict.reasons)

    def test_witness_1b_deleted_baseline_metrics_file(self, store: BaselineStore) -> None:
        """Removing the baseline run's metrics.json is also TAMPERED, not pass."""
        self._promote_pair(store)
        store.run_metrics_path("run_base").unlink()
        verdict = evaluate("run_new", store)
        assert verdict.verdict == "TAMPERED"
        assert any("missing" in r for r in verdict.reasons)

    def test_witness_2_edit_anchored_qoi_error_in_baselines_json(
        self, store: BaselineStore
    ) -> None:
        """Editing baselines.json anchored errors (run file intact) must bite."""
        self._promote_pair(store)
        data = json.loads(store.path.read_text(encoding="utf-8"))
        key = baseline_key("naca0012", "openfoam")
        # Inflate the anchored baseline error so a regressed run would pass —
        # the hash still matches the untouched run file.
        data["baselines"][key]["qoi_relative_errors"]["cd"] = 99.0
        store.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "TAMPERED"
        assert any("qoi_relative_errors" in r for r in verdict.reasons)

    def test_witness_2b_edit_anchored_qoi_value_in_baselines_json(
        self, store: BaselineStore
    ) -> None:
        self._promote_pair(store)
        data = json.loads(store.path.read_text(encoding="utf-8"))
        key = baseline_key("naca0012", "openfoam")
        data["baselines"][key]["qoi_values"]["cd"] = 123.456
        store.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        verdict = evaluate("run_new", store)
        assert verdict.verdict == "TAMPERED"
        assert any("qoi_values" in r for r in verdict.reasons)

    def test_witness_3_perfect_run_without_baseline_is_not_pass(
        self, store: BaselineStore
    ) -> None:
        """Even a zero-error passing run cannot PASS without a baseline."""
        write_run(store.runs_root, "run_perfect", qoi_relative_errors={"cd": 0.0, "cl": 0.0})
        verdict = evaluate("run_perfect", store)
        assert verdict.verdict == "NO_BASELINE"
        assert verdict.verdict != "PASS"


class TestBaselineFileSchema:
    def test_load_missing_file_returns_empty_document(self, store: BaselineStore) -> None:
        data = store.load()
        assert data.baselines == {}
        assert data.regression_margin.absolute == pytest.approx(0.005)
        assert data.regression_margin.relative == pytest.approx(0.1)

    def test_extra_fields_are_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BaselineFile.model_validate({"baselines": {}, "unexpected": 1})
