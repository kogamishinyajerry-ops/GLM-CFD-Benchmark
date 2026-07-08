"""E2E smoke tests for the v4 trust-platform CLI commands.

Covers Architecture v4.0 §9 wiring: provenance / trust / failures /
baseline / gate / agent-eval / showcase. Every command gets at least one
end-to-end smoke test on tmp data, and the two hard exit-code contracts are
pinned with tamper witnesses:

- ``cfdb gate``: 0=PASS, 1=REGRESSION/INVALID_RUN, 2=NO_BASELINE, 3=TAMPERED
  (tamper witness: flipping one byte of the baseline run's metrics.json must
  bite with exit 3).
- ``cfdb agent-eval score``: exit 3 when frozen material drifted (tamper
  witness: changing one byte of a frozen reference file must refuse scoring).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cfdb.cli import app
from cfdb.schema import MetricsResult, RunManifest, TimingSpec

CASE_ID = "mock_success"
SOLVER = "generic"


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner fixture."""
    return CliRunner()


@pytest.fixture
def tmp_cases(tmp_path: Path) -> Path:
    """Copy the real mock_success case into an isolated cases tree."""
    src = Path(__file__).parent.parent / "cases" / "smoke" / CASE_ID
    dst = tmp_path / "cases" / "smoke" / CASE_ID
    shutil.copytree(src, dst)
    return tmp_path / "cases"


def combined_output(result: object) -> str:
    """Return stdout + stderr of a CliRunner result (click >= 8.2 splits them)."""
    output = str(getattr(result, "output", ""))
    try:
        stderr = str(getattr(result, "stderr", ""))
    except ValueError:  # stderr not captured separately
        stderr = ""
    return output + stderr


def write_run(
    runs_dir: Path,
    run_id: str,
    *,
    case_id: str = CASE_ID,
    solver: str = SOLVER,
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
    errors = (
        {"centerline_umax": 0.01} if qoi_relative_errors is None else qoi_relative_errors
    )
    metrics = MetricsResult(
        qoi_relative_errors=errors,
        qoi_pass=overall_status == "pass",
        overall_status=overall_status,
        qoi_computed_values=qoi_computed_values,
    )
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    return run_dir


class TestProvenanceCommand:
    def test_provenance_table_smoke(
        self, runner: CliRunner, tmp_cases: Path
    ) -> None:
        result = runner.invoke(app, ["provenance", "--cases-dir", str(tmp_cases)])
        assert result.exit_code == 0
        assert CASE_ID in result.output
        # analytical reference without citation -> ANALYTIC (never REAL)
        assert "ANALYTIC" in result.output
        assert "Total: 1 case(s)" in result.output

    def test_provenance_hash_drift_downgrades(
        self, runner: CliRunner, tmp_cases: Path
    ) -> None:
        """Tamper witness: a wrong anchored sha256 must downgrade honesty."""
        case_dir = tmp_cases / "smoke" / CASE_ID
        (case_dir / "provenance.yaml").write_text(
            "citation: null\nfile_hashes:\n  reference/qoi.json: '" + "0" * 64 + "'\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["provenance", "--cases-dir", str(tmp_cases)])
        assert result.exit_code == 0
        assert "DECLARED-NOT-VERIFIED" in result.output
        assert "drift" in result.output

    def test_provenance_empty_dir(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(app, ["provenance", "--cases-dir", str(tmp_path / "none")])
        assert result.exit_code == 0
        assert "No cases found" in result.output


class TestTrustCommand:
    def test_trust_table_smoke(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_a", qoi_computed_values={"centerline_umax": 0.37})
        write_run(runs_dir, "run_b", qoi_computed_values={"centerline_umax": 0.372})
        result = runner.invoke(
            app,
            [
                "trust", "-c", CASE_ID, "-s", SOLVER,
                "--cases-dir", str(tmp_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert f"Trust Profile — {CASE_ID} / {SOLVER}" in result.output
        assert "robustness" in result.output
        assert "Honesty: ANALYTIC" in result.output
        assert "Runs:    2" in result.output

    def test_trust_json_output(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_a")
        result = runner.invoke(
            app,
            [
                "trust", "-c", CASE_ID, "-s", SOLVER, "--json",
                "--cases-dir", str(tmp_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["case_id"] == CASE_ID
        assert payload["solver"] == SOLVER
        assert payload["n_runs"] == 1

    def test_trust_svg_output(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_a")
        svg_path = tmp_path / "out" / "radar.svg"
        result = runner.invoke(
            app,
            [
                "trust", "-c", CASE_ID, "-s", SOLVER,
                "--svg", str(svg_path),
                "--cases-dir", str(tmp_cases),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert svg_path.exists()
        assert "<svg" in svg_path.read_text(encoding="utf-8")

    def test_trust_unknown_case_fails(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "trust", "-c", "no_such_case", "-s", SOLVER,
                "--cases-dir", str(tmp_cases),
                "--runs-dir", str(tmp_path / "runs"),
            ],
        )
        assert result.exit_code == 1


class TestFailuresCommands:
    def test_ingest_list_annotate_e2e(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        library = tmp_path / "failures" / "library.json"
        write_run(runs_dir, "run_fail", status="failed", overall_status="fail")
        write_run(runs_dir, "run_pass")

        result = runner.invoke(
            app,
            ["failures", "ingest", "--runs-dir", str(runs_dir), "--library", str(library)],
        )
        assert result.exit_code == 0
        assert "1 new failure(s)" in result.output
        assert "1 verified pass" in result.output
        assert library.exists()

        data = json.loads(library.read_text(encoding="utf-8"))
        assert len(data["records"]) == 1
        fingerprint = data["records"][0]["fingerprint"]

        result = runner.invoke(app, ["failures", "list", "--library", str(library)])
        assert result.exit_code == 0
        assert fingerprint in result.output
        assert "SETUP_ERROR" in result.output

        result = runner.invoke(
            app,
            [
                "failures", "annotate", fingerprint,
                "--guard", "check solver step exit codes before metrics",
                "--library", str(library),
            ],
        )
        assert result.exit_code == 0
        assert "[OK]" in result.output

        result = runner.invoke(app, ["failures", "list", "--library", str(library)])
        assert result.exit_code == 0
        assert "check solver step exit codes" in result.output

    def test_list_mode_filter(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        library = tmp_path / "failures" / "library.json"
        write_run(runs_dir, "run_fail", status="failed", overall_status="fail")
        runner.invoke(
            app,
            ["failures", "ingest", "--runs-dir", str(runs_dir), "--library", str(library)],
        )

        result = runner.invoke(
            app,
            ["failures", "list", "--mode", "TIMEOUT", "--library", str(library)],
        )
        assert result.exit_code == 0
        assert "empty" in result.output

        result = runner.invoke(
            app,
            ["failures", "list", "--mode", "SETUP_ERROR", "--library", str(library)],
        )
        assert result.exit_code == 0
        assert "SETUP_ERROR" in result.output

    def test_ingest_missing_runs_dir_exits_1(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A nonexistent runs directory is an error line + nonzero exit."""
        result = runner.invoke(
            app,
            [
                "failures", "ingest",
                "--runs-dir", str(tmp_path / "no_such_runs"),
                "--library", str(tmp_path / "library.json"),
            ],
        )
        assert result.exit_code == 1
        assert "runs directory not found" in combined_output(result)

    def test_ingest_corrupt_manifest_partial_success_exits_1(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Corrupt manifest: error line + exit 1, but good runs still persist."""
        runs_dir = tmp_path / "runs"
        library = tmp_path / "failures" / "library.json"
        write_run(runs_dir, "run_fail", status="failed", overall_status="fail")
        corrupt_dir = runs_dir / "run_corrupt"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "manifest.json").write_text("{not json", encoding="utf-8")

        result = runner.invoke(
            app,
            ["failures", "ingest", "--runs-dir", str(runs_dir), "--library", str(library)],
        )
        assert result.exit_code == 1
        output = combined_output(result)
        assert "run_corrupt" in output
        assert "1 new failure(s)" in output  # partial success summarized
        # Partial results were persisted despite the nonzero exit.
        data = json.loads(library.read_text(encoding="utf-8"))
        assert len(data["records"]) == 1

    def test_ingest_dry_run_not_counted_as_verified_pass(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A dry run is reported as dry-run skipped, never as a verified pass."""
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_dry", status="dry_run", overall_status="unknown")
        result = runner.invoke(
            app,
            [
                "failures", "ingest",
                "--runs-dir", str(runs_dir),
                "--library", str(tmp_path / "library.json"),
            ],
        )
        assert result.exit_code == 0
        assert "0 verified pass" in result.output
        assert "1 dry-run skipped" in result.output

    def test_list_rejects_unknown_mode(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "failures", "list", "--mode", "NOT_A_MODE",
                "--library", str(tmp_path / "library.json"),
            ],
        )
        assert result.exit_code == 1

    def test_annotate_unknown_fingerprint_fails(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "failures", "annotate", "deadbeefdeadbeef",
                "--guard", "anything",
                "--library", str(tmp_path / "library.json"),
            ],
        )
        assert result.exit_code == 1


class TestBaselineCommands:
    def test_promote_and_list_e2e(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        baselines = tmp_path / "baselines" / "baselines.json"
        write_run(runs_dir, "run_base")

        result = runner.invoke(
            app,
            [
                "baseline", "promote", "run_base",
                "--engineer", "Zhuanz",
                "--baselines", str(baselines),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "[OK] Promoted run 'run_base'" in result.output
        assert baselines.exists()

        result = runner.invoke(
            app,
            [
                "baseline", "list",
                "--baselines", str(baselines),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 0
        assert "run_base" in result.output
        assert "Zhuanz" in result.output
        assert "Regression margin" in result.output

    def test_promote_requires_engineer_option(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--engineer is required with no default: omission is a usage error."""
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_base")
        result = runner.invoke(
            app,
            [
                "baseline", "promote", "run_base",
                "--baselines", str(tmp_path / "baselines.json"),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 2
        assert "--engineer" in combined_output(result)

    def test_promote_rejects_failed_run(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_fail", status="failed", overall_status="fail")
        result = runner.invoke(
            app,
            [
                "baseline", "promote", "run_fail",
                "--engineer", "Zhuanz",
                "--baselines", str(tmp_path / "baselines.json"),
                "--runs-dir", str(runs_dir),
            ],
        )
        assert result.exit_code == 1
        assert "only 'pass' runs" in combined_output(result)

    def test_list_empty_store(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "baseline", "list",
                "--baselines", str(tmp_path / "baselines.json"),
                "--runs-dir", str(tmp_path / "runs"),
            ],
        )
        assert result.exit_code == 0
        assert "No baselines promoted yet" in result.output


class TestGateExitCodes:
    """Pin the four-way exit-code contract of ``cfdb gate``."""

    def _gate(self, runner: CliRunner, tmp_path: Path, run_id: str) -> object:
        return runner.invoke(
            app,
            [
                "gate", run_id,
                "--baselines", str(tmp_path / "baselines" / "baselines.json"),
                "--runs-dir", str(tmp_path / "runs"),
            ],
        )

    def _promote(self, runner: CliRunner, tmp_path: Path, run_id: str) -> None:
        result = runner.invoke(
            app,
            [
                "baseline", "promote", run_id,
                "--engineer", "Zhuanz",
                "--baselines", str(tmp_path / "baselines" / "baselines.json"),
                "--runs-dir", str(tmp_path / "runs"),
            ],
        )
        assert result.exit_code == 0

    def test_no_baseline_exit_2(self, runner: CliRunner, tmp_path: Path) -> None:
        write_run(tmp_path / "runs", "run_new")
        result = self._gate(runner, tmp_path, "run_new")
        assert result.exit_code == 2
        assert "NO_BASELINE" in result.output

    def test_pass_exit_0(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_base", qoi_relative_errors={"centerline_umax": 0.01})
        self._promote(runner, tmp_path, "run_base")
        write_run(runs_dir, "run_new", qoi_relative_errors={"centerline_umax": 0.011})
        result = self._gate(runner, tmp_path, "run_new")
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_regression_exit_1(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_base", qoi_relative_errors={"centerline_umax": 0.01})
        self._promote(runner, tmp_path, "run_base")
        write_run(runs_dir, "run_new", qoi_relative_errors={"centerline_umax": 0.05})
        result = self._gate(runner, tmp_path, "run_new")
        assert result.exit_code == 1
        assert "REGRESSION" in result.output

    def test_invalid_run_exit_1(self, runner: CliRunner, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_bad", status="failed", overall_status="fail")
        result = self._gate(runner, tmp_path, "run_bad")
        assert result.exit_code == 1
        assert "INVALID_RUN" in result.output

    def test_tampered_exit_3(self, runner: CliRunner, tmp_path: Path) -> None:
        """Tamper witness: edit the baseline run's metrics.json -> exit 3."""
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_base", qoi_relative_errors={"centerline_umax": 0.01})
        self._promote(runner, tmp_path, "run_base")
        write_run(runs_dir, "run_new", qoi_relative_errors={"centerline_umax": 0.01})
        # Sanity: gate passes before tampering.
        assert self._gate(runner, tmp_path, "run_new").exit_code == 0

        metrics_path = runs_dir / "run_base" / "metrics.json"
        metrics_path.write_text(
            metrics_path.read_text(encoding="utf-8").replace("0.01", "0.001"),
            encoding="utf-8",
        )
        result = self._gate(runner, tmp_path, "run_new")
        assert result.exit_code == 3
        assert "TAMPERED" in result.output


    def test_corrupt_baselines_file_exit_3(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Tamper witness: a corrupt baselines.json must exit 3, never crash
        and never degrade into NO_BASELINE (exit 2)."""
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_new")
        baselines = tmp_path / "baselines" / "baselines.json"
        baselines.parent.mkdir(parents=True)
        baselines.write_text("{definitely not valid json", encoding="utf-8")

        result = self._gate(runner, tmp_path, "run_new")
        assert result.exit_code == 3
        assert "corrupt or unreadable" in combined_output(result)


class TestAgentEvalCommands:
    def _init(self, runner: CliRunner, tmp_cases: Path, tmp_path: Path) -> Path:
        agentbench_dir = tmp_path / "agentbench"
        result = runner.invoke(
            app,
            [
                "agent-eval", "init", "-c", CASE_ID,
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 0
        assert (agentbench_dir / CASE_ID / "contract.json").exists()
        return agentbench_dir

    def _write_submission(self, tmp_path: Path, name: str = "sub1") -> Path:
        submission = tmp_path / name
        submission.mkdir(parents=True, exist_ok=True)
        (submission / "qoi.json").write_text(
            json.dumps({"centerline_umax": 0.371}), encoding="utf-8"
        )
        (submission / "manifest.json").write_text(
            json.dumps({"wall_time_sec": 2.0}), encoding="utf-8"
        )
        return submission

    def test_init_score_ledger_e2e(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        submission = self._write_submission(tmp_path)

        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(submission),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Valid:      True" in result.output
        assert (agentbench_dir / CASE_ID / "ledger.jsonl").exists()

        result = runner.invoke(
            app,
            [
                "agent-eval", "ledger", "-c", CASE_ID,
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 0
        assert "sub1" in result.output
        assert "Total: 1 submission(s)" in result.output

    def test_score_frozen_drift_exit_3(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """Tamper witness: one changed reference byte must refuse scoring."""
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        submission = self._write_submission(tmp_path)

        ref_path = tmp_cases / "smoke" / CASE_ID / "reference" / "qoi.json"
        ref_path.write_text(
            ref_path.read_text(encoding="utf-8").replace("0.371", "0.372"),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(submission),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 3
        assert "reference/qoi.json" in combined_output(result)
        # No score is ever ledgered against a drifted ruler.
        assert not (agentbench_dir / CASE_ID / "ledger.jsonl").exists()

    def test_init_refuses_existing_contract_without_force(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """Re-running init on an existing contract must refuse and hint --force."""
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        contract_path = agentbench_dir / CASE_ID / "contract.json"
        before = contract_path.read_bytes()

        result = runner.invoke(
            app,
            [
                "agent-eval", "init", "-c", CASE_ID,
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 1
        assert "--force" in combined_output(result)
        # The frozen ruler is untouched.
        assert contract_path.read_bytes() == before

    def test_init_force_reanchors_with_loud_warning(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """--force re-anchors and prints old -> new ruler ids loudly."""
        import hashlib

        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        contract_path = agentbench_dir / CASE_ID / "contract.json"
        old_ruler_id = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]

        # Change the ruler material so the re-anchored contract differs.
        ref_path = tmp_cases / "smoke" / CASE_ID / "reference" / "qoi.json"
        ref_path.write_text(
            ref_path.read_text(encoding="utf-8").replace("0.371", "0.372"),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "agent-eval", "init", "-c", CASE_ID, "--force",
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 0
        new_ruler_id = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]
        assert new_ruler_id != old_ruler_id
        output = combined_output(result)
        assert "RULER RE-ANCHORED" in output
        assert f"#{old_ruler_id}" in output
        assert f"#{new_ruler_id}" in output

    def test_score_missing_submission_dir_exit_1_no_ledger(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """Void input: a missing submission directory never enters the ledger."""
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(tmp_path / "no_such_submission"),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 1
        assert "not found" in combined_output(result)
        assert not (agentbench_dir / CASE_ID / "ledger.jsonl").exists()

    @pytest.mark.parametrize("payload", ["{not json", "[1, 2, 3]"])
    def test_score_unparsable_qoi_exit_1_no_ledger(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path, payload: str
    ) -> None:
        """Void input: corrupt or non-object qoi.json never enters the ledger."""
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        submission = tmp_path / "sub_bad"
        submission.mkdir()
        (submission / "qoi.json").write_text(payload, encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(submission),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 1
        assert "ledger" in combined_output(result)
        assert not (agentbench_dir / CASE_ID / "ledger.jsonl").exists()

    def test_score_gate_failing_submission_is_still_ledgered(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """Boundary witness: a readable submission that fails a validity gate
        is an invalid SAMPLE (score=None) and DOES enter the ledger — only
        void inputs stay out."""
        agentbench_dir = self._init(runner, tmp_cases, tmp_path)
        submission = tmp_path / "sub_gatefail"
        submission.mkdir()
        # Parseable JSON object, but missing the expected QoI -> qoi_complete fails.
        (submission / "qoi.json").write_text(json.dumps({"other": 1.0}), encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(submission),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(agentbench_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Valid:      False" in result.output
        ledger_path = agentbench_dir / CASE_ID / "ledger.jsonl"
        assert ledger_path.exists()
        entry = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["valid"] is False
        assert entry["score"] is None

    def test_score_without_contract_fails(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        submission = self._write_submission(tmp_path)
        result = runner.invoke(
            app,
            [
                "agent-eval", "score", "-c", CASE_ID,
                "--submission", str(submission),
                "--cases-dir", str(tmp_cases),
                "--agentbench-dir", str(tmp_path / "agentbench"),
            ],
        )
        assert result.exit_code == 1
        assert "init" in combined_output(result)

    def test_ledger_empty(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "agent-eval", "ledger", "-c", CASE_ID,
                "--agentbench-dir", str(tmp_path / "agentbench"),
            ],
        )
        assert result.exit_code == 0
        assert "empty" in result.output


class TestShowcaseCommand:
    def test_showcase_smoke(
        self, runner: CliRunner, tmp_cases: Path, tmp_path: Path
    ) -> None:
        """E2E: tmp repo root (cases/ + runs/) renders a self-contained HTML."""
        runs_dir = tmp_path / "runs"
        write_run(runs_dir, "run_a", qoi_computed_values={"centerline_umax": 0.37})
        out_path = tmp_path / "showcase.html"
        result = runner.invoke(
            app,
            [
                "showcase",
                "--out", str(out_path),
                "--repo-root", str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8").lower()
        assert "html" in html
        assert CASE_ID in html
