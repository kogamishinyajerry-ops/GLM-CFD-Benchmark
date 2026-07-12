"""Witnesses for the R8 backlog batch.

- cp-distribution collection: strict surface-raw parsing, Cp conversion,
  upper-surface extraction, resampling onto the reference x/c grid with
  NO extrapolation — activates the curve_l2 gate for NACA cases.
- Platform guards: oversized submission trees refused before hashing;
  oversized checker stdout is CHECKER_ERROR, never truncated-and-parsed.
- Golden admission runs: systematic 3x sandbox evidence written OUTSIDE
  the frozen trees (no ruler drift), fail-closed on any flaky run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_agentbench_coding import StubBackend, _junit_xml

from cfdb.agentbench.admission import load_admission, run_admission
from cfdb.agentbench.contract import init_contract, verify_frozen
from cfdb.post.cp_curve import extract_cp_distribution, parse_surface_raw
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


def _tmp_case_registry(tmp_path: Path, family: str, case_id: str) -> tuple[CaseRegistry, Path]:
    """Copy a real case into an isolated cases root (safe to tamper)."""
    src = PROJECT_CASES / family / case_id
    dst = tmp_path / "cases" / family / case_id
    shutil.copytree(src, dst)
    return CaseRegistry(tmp_path / "cases"), dst


def _raw_lines(points: list[tuple[float, float, float, float]]) -> str:
    body = "\n".join(f"{x} {y} {z} {p}" for x, y, z, p in points)
    return f"# sampled surface\n# x y z p\n{body}\n"


def _synthetic_surface(n: int = 25, q: float = 5000.0) -> list[tuple[float, float, float, float]]:
    """Upper+lower surface samples spanning x/c 0..1, kinematic p = q*Cp."""
    pts: list[tuple[float, float, float, float]] = []
    for i in range(n):
        x = i / (n - 1)
        cp_upper = 1.0 - 4.0 * x * (1.0 - x)  # stagnation-ish shape
        pts.append((x, 0.06, 0.05, q * cp_upper))  # upper (y > 0)
        pts.append((x, -0.06, 0.05, q * 0.5))  # lower (y < 0) — must be ignored
    return pts


# ============================================================================
# cp extraction primitives
# ============================================================================


class TestSurfaceRawParsing:
    def test_valid_raw_parses(self, tmp_path: Path) -> None:
        raw = tmp_path / "p_airfoilSurface.raw"
        raw.write_text(_raw_lines([(0.1, 0.05, 0.05, 42.0)]), encoding="utf-8")
        assert parse_surface_raw(raw) == [(0.1, 0.05, 0.05, 42.0)]

    @pytest.mark.parametrize(
        "line",
        [
            "0.1 0.05 42.0",  # 3 columns
            "0.1 0.05 0.05 42.0 9.9",  # 5 columns
            "0.1 0.05 0.05 abc",  # non-numeric
            "0.1 0.05 0.05 nan",  # non-finite
        ],
    )
    def test_malformed_row_rejects_whole_file(self, tmp_path: Path, line: str) -> None:
        raw = tmp_path / "p.raw"
        raw.write_text(f"# header\n0.0 0.05 0.05 1.0\n{line}\n", encoding="utf-8")
        assert parse_surface_raw(raw) is None

    def test_empty_or_missing_is_none(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.raw"
        empty.write_text("# only comments\n", encoding="utf-8")
        assert parse_surface_raw(empty) is None
        assert parse_surface_raw(tmp_path / "missing.raw") is None


class TestCpExtraction:
    U_INF = 100.0
    Q = 0.5 * U_INF * U_INF

    def test_cp_conversion_and_reference_grid(self, tmp_path: Path) -> None:
        raw = tmp_path / "p.raw"
        raw.write_text(_raw_lines(_synthetic_surface(q=self.Q)), encoding="utf-8")
        ref_x = [0.0, 0.25, 0.5, 0.75, 1.0]
        curve = extract_cp_distribution(raw, u_inf=self.U_INF, l_ref=1.0, reference_x=ref_x)
        assert curve is not None
        assert [x for x, _ in curve] == ref_x  # EXACTLY the reference grid
        by_x = dict(curve)
        # p = q * Cp with Cp = 1 - 4x(1-x): stagnation Cp=1 at x=0, -0 at mid.
        assert by_x[0.0] == pytest.approx(1.0, abs=1e-9)
        assert by_x[0.5] == pytest.approx(0.0, abs=1e-9)
        # Lower-surface constant Cp=0.5 never leaks into the upper curve.
        assert by_x[0.25] != pytest.approx(0.5, abs=1e-3)

    def test_reference_beyond_sampled_range_is_refused(self, tmp_path: Path) -> None:
        # No extrapolation: a reference station outside the sampled x/c
        # range BY MORE THAN the local sample spacing rejects the whole
        # curve rather than inventing a flat tail (gap 0.1 > spacing 0.042).
        pts = [(0.1 + 0.8 * i / 19, 0.06, 0.05, 100.0) for i in range(20)]
        raw = tmp_path / "p.raw"
        raw.write_text(_raw_lines(pts), encoding="utf-8")
        assert (
            extract_cp_distribution(raw, u_inf=self.U_INF, l_ref=1.0, reference_x=[0.0, 0.5])
            is None
        )

    def test_sub_resolution_endpoint_gap_is_clamped(self, tmp_path: Path) -> None:
        # Real-run evidence (R8): cell-centre sampling never lands exactly
        # on the leading/trailing edge — an endpoint overhang SMALLER than
        # the local sample spacing is clamped (np.interp edge value), not
        # rejected. The mid-curve values stay pure interpolation.
        pts = [(0.001 + 0.994 * i / 24, 0.06, 0.05, 100.0 * (i / 24)) for i in range(25)]
        raw = tmp_path / "p.raw"
        raw.write_text(_raw_lines(pts), encoding="utf-8")
        # gap_lo = 0.001, gap_hi = 0.005 — both < spacing (0.994/24 ≈ 0.0414)
        curve = extract_cp_distribution(
            raw, u_inf=self.U_INF, l_ref=1.0, reference_x=[0.0, 0.5, 1.0]
        )
        assert curve is not None
        by_x = dict(curve)
        # Endpoints clamp to nearest sampled Cp (0 at x≈0.001, 100/Q at end).
        assert by_x[0.0] == pytest.approx(0.0, abs=1e-9)
        assert by_x[1.0] == pytest.approx(100.0 / self.Q, rel=1e-9)

    def test_too_few_upper_points_is_refused(self, tmp_path: Path) -> None:
        pts = [(i / 4, 0.06, 0.05, 10.0) for i in range(5)]  # 5 < MIN_SURFACE_POINTS
        raw = tmp_path / "p.raw"
        raw.write_text(_raw_lines(pts), encoding="utf-8")
        assert extract_cp_distribution(raw, u_inf=self.U_INF, l_ref=1.0, reference_x=[0.5]) is None

    def test_invalid_freestream_is_refused(self, tmp_path: Path) -> None:
        raw = tmp_path / "p.raw"
        raw.write_text(_raw_lines(_synthetic_surface()), encoding="utf-8")
        assert extract_cp_distribution(raw, u_inf=0.0, l_ref=1.0, reference_x=[0.5]) is None
        assert extract_cp_distribution(raw, u_inf=100.0, l_ref=0.0, reference_x=[0.5]) is None


# ============================================================================
# adapter integration (real naca case + reference)
# ============================================================================


class TestAdapterCpCollection:
    def _adapter_setup(self, tmp_path: Path):
        from cfdb.adapters.openfoam import OpenFOAMAdapter

        registry = CaseRegistry(PROJECT_CASES)
        case = registry.load("naca0012_a5")
        case_dir = registry.get_case_dir("naca0012_a5")
        adapter = OpenFOAMAdapter(dry_run=False)
        adapter._source_case_dir = case_dir  # what prepare() records
        run_dir = tmp_path / "run"
        (run_dir / "case").mkdir(parents=True)
        return adapter, case, run_dir

    def test_collects_cp_on_reference_grid(self, tmp_path: Path) -> None:
        from cfdb.metrics.curves import load_reference_curve

        adapter, case, run_dir = self._adapter_setup(tmp_path)
        sample_dir = run_dir / "case" / "postProcessing" / "cpSurface" / "3000"
        sample_dir.mkdir(parents=True)
        (sample_dir / "p_airfoilSurface.raw").write_text(
            _raw_lines(_synthetic_surface(q=5000.0)), encoding="utf-8"
        )
        manifest = adapter.collect_outputs(case, run_dir)
        assert manifest.curves is not None
        assert "cp_distribution" in manifest.curves
        reference = load_reference_curve(
            PROJECT_CASES / "validation" / "naca0012_a5" / "reference" / "ladson1988_a5.csv"
        )
        assert reference is not None
        assert [x for x, _ in manifest.curves["cp_distribution"]] == [x for x, _ in reference]

    def test_missing_sample_reports_attempted_but_failed(self, tmp_path: Path) -> None:
        # Codex R8 P1: a DECLARED curve whose collection fails must return
        # an EMPTY mapping — None would leave the engine's curve gate
        # dormant and silently exempt a configured tolerance.
        adapter, case, run_dir = self._adapter_setup(tmp_path)
        manifest = adapter.collect_outputs(case, run_dir)
        assert manifest.curves == {}

    def test_corrupt_sample_reports_attempted_but_failed(self, tmp_path: Path) -> None:
        adapter, case, run_dir = self._adapter_setup(tmp_path)
        sample_dir = run_dir / "case" / "postProcessing" / "cpSurface" / "3000"
        sample_dir.mkdir(parents=True)
        (sample_dir / "p_airfoilSurface.raw").write_text(
            "0.0 0.06 0.05 not_a_number\n", encoding="utf-8"
        )
        manifest = adapter.collect_outputs(case, run_dir)
        assert manifest.curves == {}

    def test_undeclared_curve_stays_none(self, tmp_path: Path) -> None:
        # None is reserved for "never attempted": a case that declares no
        # cp curve keeps the engine's backward-compat guard dormant.
        adapter, case, run_dir = self._adapter_setup(tmp_path)
        no_curves = case.model_copy(deep=True)
        no_curves.outputs.curves = []
        manifest = adapter.collect_outputs(no_curves, run_dir)
        assert manifest.curves is None

    def test_failed_collection_makes_run_incomplete_not_pass(self) -> None:
        # Codex R8 P1 end-to-end: naca0012 (base) configures a
        # cp_distribution tolerance; perfect QoI + failed cp collection
        # ({}) must be 'incomplete', never 'pass'.
        from cfdb.adapters.base import ArtifactManifest, RunResult
        from cfdb.metrics.engine import MetricsEngine

        registry = CaseRegistry(PROJECT_CASES)
        case = registry.load("naca0012_a0")
        case_dir = registry.get_case_dir("naca0012_a0")
        ref_qoi = case.reference.qoi_values
        artifacts = ArtifactManifest(files={}, qoi_values=dict(ref_qoi), curves={})
        run_result = RunResult(
            exit_code=0, stdout="", stderr="", wall_time_sec=1.0, timed_out=False
        )
        metrics = MetricsEngine().compute(case, artifacts, run_result, case_dir=case_dir)
        assert metrics.qoi_pass is True  # QoI alone would have passed
        assert metrics.overall_status == "incomplete"  # the gate is NOT exempt


# ============================================================================
# platform guards
# ============================================================================


class TestPlatformGuards:
    def test_oversized_submission_tree_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cfdb.agentbench.scorer as scorer_mod
        from cfdb.agentbench.scorer import score_submission

        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        expected = (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text(expected, encoding="utf-8")
        (sub / "padding.bin").write_bytes(b"x" * 4096)

        monkeypatch.setattr(scorer_mod, "MAX_SUBMISSION_BYTES", 1024)
        ledger = tmp_path / "ledger.jsonl"
        with pytest.raises(ValueError, match="the platform cap"):
            score_submission(contract, case, case_dir, sub, ledger_path=ledger)
        assert ledger.exists() is False  # refused before judging/ledgering

        monkeypatch.setattr(scorer_mod, "MAX_SUBMISSION_BYTES", 64 * 1024 * 1024)
        result = score_submission(contract, case, case_dir, sub)
        assert result.valid is True  # same tree passes under the real cap

    def test_oversized_checker_stdout_is_checker_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8 P1: the cap is enforced WHILE reading — the child is
        # killed on overflow, never fully buffered first.
        import cfdb.agentbench.checker_scorer as checker_mod
        from cfdb.agentbench.checker_scorer import score_agentic

        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        (case_dir / "reference" / "checker.py").write_text(
            "import sys\nfor _ in range(100):\n    print('x' * 10000)\n",
            encoding="utf-8",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(checker_mod, "MAX_CHECKER_STDOUT_CHARS", 50_000)
        verdict = score_agentic(case_dir, sub)
        assert verdict.mode == "CHECKER_ERROR"
        assert verdict.success is None  # could not judge — never a pass/fail
        assert "exceeded its output cap" in (verdict.error or "")

    def test_oversized_checker_stderr_is_checker_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # stderr is attacker-influencable too (Codex R8 P1) — same bound.
        import cfdb.agentbench.checker_scorer as checker_mod
        from cfdb.agentbench.checker_scorer import score_agentic

        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        (case_dir / "reference" / "checker.py").write_text(
            "import sys\n"
            "for _ in range(100):\n"
            "    print('e' * 10000, file=sys.stderr)\n"
            'print(\'{"success": true, "evidence": []}\')\n',
            encoding="utf-8",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(checker_mod, "MAX_CHECKER_STDERR_CHARS", 50_000)
        verdict = score_agentic(case_dir, sub)
        assert verdict.mode == "CHECKER_ERROR"
        assert "exceeded its output cap" in (verdict.error or "")

    def test_submission_entry_count_is_bounded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8 P2: a tree of empty files stays at zero bytes but must
        # still hit the entry-count bound in the same pre-judging walk.
        import cfdb.agentbench.scorer as scorer_mod
        from cfdb.agentbench.scorer import score_submission

        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        expected = (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text(expected, encoding="utf-8")
        for i in range(6):
            (sub / f"empty_{i}").touch()

        monkeypatch.setattr(scorer_mod, "MAX_SUBMISSION_ENTRIES", 5)
        with pytest.raises(ValueError, match="more than 5 entries"):
            score_submission(contract, case, case_dir, sub)


# ============================================================================
# golden admission runs
# ============================================================================


class TestGoldenAdmission:
    def _registry(self, tmp_path: Path) -> tuple[CaseRegistry, Path]:
        return _tmp_case_registry(tmp_path, "coding_tasks", "smoke_add_two")

    def test_all_green_admission_writes_record(self, tmp_path: Path) -> None:
        registry, case_dir = self._registry(tmp_path)
        case = registry.load("smoke_add_two")
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))
        record = run_admission("smoke_add_two", registry, runs=3, backend_factory=lambda c, s: stub)
        assert record.all_passed is True
        assert record.runs == 3
        assert len(record.golden_attempt_id) == 64
        on_disk = load_admission(case_dir)
        assert on_disk is not None
        assert on_disk.all_passed is True
        assert list(case_dir.glob("*.tmp")) == []  # atomic write, no litter

    def test_single_flaky_run_fails_admission_but_still_records(self, tmp_path: Path) -> None:
        registry, case_dir = self._registry(tmp_path)
        case = registry.load("smoke_add_two")
        expected = case.execution.expected_test_count
        calls = {"n": 0}

        def flaky_factory(c: Path, s: Path) -> StubBackend:
            calls["n"] += 1
            failures = 1 if calls["n"] == 2 else 0
            return StubBackend(report_xml=_junit_xml(total=expected, failures=failures))

        record = run_admission("smoke_add_two", registry, runs=3, backend_factory=flaky_factory)
        assert record.all_passed is False  # one flaky run fails admission
        on_disk = load_admission(case_dir)
        assert on_disk is not None and on_disk.all_passed is False  # honest paper trail

    def test_admission_record_does_not_drift_ruler(self, tmp_path: Path) -> None:
        registry, case_dir = self._registry(tmp_path)
        contract = init_contract("smoke_add_two", registry)
        case = registry.load("smoke_add_two")
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))
        run_admission("smoke_add_two", registry, runs=1, backend_factory=lambda c, s: stub)
        assert (case_dir / "admission.json").is_file()
        # admission.json lives OUTSIDE the frozen trees: the ruler anchored
        # BEFORE admission still verifies clean after the record is written.
        assert verify_frozen(contract, case_dir) == []

    def test_non_coding_case_is_refused(self, tmp_path: Path) -> None:
        registry, _ = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        with pytest.raises(ValueError, match="coding cases only"):
            run_admission("csv_field_extract", registry, runs=1)

    def test_missing_golden_is_refused(self, tmp_path: Path) -> None:
        registry, case_dir = self._registry(tmp_path)
        shutil.rmtree(case_dir / "reference" / "golden")
        with pytest.raises(ValueError, match="no golden solution"):
            run_admission("smoke_add_two", registry, runs=1)

    def test_admission_json_is_valid_json(self, tmp_path: Path) -> None:
        registry, case_dir = self._registry(tmp_path)
        case = registry.load("smoke_add_two")
        expected = case.execution.expected_test_count
        stub = StubBackend(report_xml=_junit_xml(total=expected))
        run_admission("smoke_add_two", registry, runs=1, backend_factory=lambda c, s: stub)
        payload = json.loads((case_dir / "admission.json").read_text(encoding="utf-8"))
        assert payload["case_id"] == "smoke_add_two"


# ============================================================================
# R8-R2 review fixes (Codex R8-R1: 1P1 + 2P2 + 1P3)
# ============================================================================


class TestR8R2ReviewFixes:
    def test_caps_enforced_inside_digest_traversal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8-R1 P2: the caps bind to the hashed snapshot — the
        # digest traversal itself raises, not a separate pre-scan.
        import cfdb.agentbench.scorer as scorer_mod
        from cfdb.agentbench.scorer import _submission_digest

        sub = tmp_path / "sub"
        sub.mkdir()
        for i in range(6):
            (sub / f"f{i}").touch()
        monkeypatch.setattr(scorer_mod, "MAX_SUBMISSION_ENTRIES", 5)
        with pytest.raises(ValueError, match="more than 5 entries"):
            _submission_digest(sub)

    def test_streaming_digest_matches_legacy_rglob_scheme(self, tmp_path: Path) -> None:
        # Codex R8-R1 P1: the streaming os.scandir walk must reproduce the
        # original sorted(rglob) identity byte-for-byte — including the
        # ordering edge between siblings like 'a.b' and 'a' subtrees.
        from cfdb.agentbench.contract import canonical_digest, sha256_file
        from cfdb.agentbench.scorer import _submission_digest

        sub = tmp_path / "sub"
        (sub / "a").mkdir(parents=True)
        (sub / "a.b").mkdir()
        (sub / "a" / "x.txt").write_text("ax", encoding="utf-8")
        (sub / "a.b" / "x.txt").write_text("abx", encoding="utf-8")
        (sub / "empty_dir").mkdir()
        (sub / "top.txt").write_text("top", encoding="utf-8")

        legacy_pairs: list[list[str]] = []
        for path in sorted(sub.rglob("*")):  # the original scheme, inlined
            rel = path.relative_to(sub).as_posix()
            if path.is_dir():
                legacy_pairs.append([rel + "/", "dir"])
            elif path.is_file():
                legacy_pairs.append([rel, sha256_file(path)])
        assert _submission_digest(sub) == canonical_digest(legacy_pairs)

    def test_mutation_past_caps_during_judging_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8-R1 P2: the post-judging re-digest re-enforces the caps,
        # so a tree that grows past the limits DURING judging is refused.
        import cfdb.agentbench.checker_scorer as checker_scorer
        import cfdb.agentbench.scorer as scorer_mod
        from cfdb.agentbench.scorer import score_submission

        registry, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        contract = init_contract("csv_field_extract", registry)
        case = registry.load("csv_field_extract")
        expected = (case_dir / "reference" / "expected.json").read_text(encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text(expected, encoding="utf-8")

        real_score_agentic = checker_scorer.score_agentic

        def growing(case_dir_: Path, submission_dir_: Path):
            verdict = real_score_agentic(case_dir_, submission_dir_)
            for i in range(8):
                (submission_dir_ / f"late_{i}").touch()
            return verdict

        monkeypatch.setattr(checker_scorer, "score_agentic", growing)
        monkeypatch.setattr(scorer_mod, "MAX_SUBMISSION_ENTRIES", 5)
        ledger = tmp_path / "ledger.jsonl"
        with pytest.raises(ValueError, match="more than 5 entries"):
            score_submission(contract, case, case_dir, sub, ledger_path=ledger)
        assert ledger.exists() is False

    def test_just_over_limit_overflow_kills_promptly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8-R1 P2: a checker that writes cap+1 chars then hangs must
        # be killed by the boundary-sized read, not carried to the 60s
        # timeout by a read() waiting to fill a full 64 KiB chunk.
        import time

        import cfdb.agentbench.checker_scorer as checker_mod
        from cfdb.agentbench.checker_scorer import score_agentic

        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        (case_dir / "reference" / "checker.py").write_text(
            "import sys, time\nsys.stdout.write('x' * 10001)\nsys.stdout.flush()\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(checker_mod, "MAX_CHECKER_STDOUT_CHARS", 10_000)
        started = time.monotonic()
        verdict = score_agentic(case_dir, sub)
        elapsed = time.monotonic() - started
        assert verdict.mode == "CHECKER_ERROR"
        assert "exceeded its output cap" in (verdict.error or "")
        assert elapsed < 10.0  # killed at the boundary, not at the timeout

    def test_overflow_message_names_the_offending_stream_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex R8-R1 P3: audit output must cite the cap that was enforced.
        import cfdb.agentbench.checker_scorer as checker_mod
        from cfdb.agentbench.checker_scorer import score_agentic

        _, case_dir = _tmp_case_registry(tmp_path, "agentic_tasks", "csv_field_extract")
        (case_dir / "reference" / "checker.py").write_text(
            "import sys\n"
            "print('e' * 60000, file=sys.stderr)\n"
            'print(\'{"success": true, "evidence": []}\')\n',
            encoding="utf-8",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "summary.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(checker_mod, "MAX_CHECKER_STDERR_CHARS", 50_000)
        verdict = score_agentic(case_dir, sub)
        assert verdict.mode == "CHECKER_ERROR"
        assert "stderr" in (verdict.error or "")
        assert "50000" in (verdict.error or "")  # the stderr cap, not stdout's
