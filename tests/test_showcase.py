"""Tests for cfdb.reporting.showcase (P4-F): self-contained showcase HTML.

Hard acceptance items covered here:

- Self-containment: the rendered page carries zero external http(s)
  references, and the render-time gate itself has a tamper witness
  (injecting an external src/href/link/script/@import must raise).
- REAL badge scoping: the evidence-green REAL badge appears only for
  entries with experimental reference AND a citation; a tampered reference
  byte kills the badge (provenance chain bites through the showcase).
- Empty states: sections without data render explicit empty-state copy and
  never fabricate example data; the honesty footer is always present.
- Regression gate tamper witness: flipping a byte in the baseline run's
  metrics.json surfaces TAMPERED on the page after a re-render.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cfdb.agentbench import init_contract, save_contract, score_submission
from cfdb.failures import FailureLibrary
from cfdb.provenance import sha256_file
from cfdb.registry import CaseRegistry
from cfdb.regression import BaselineStore
from cfdb.reporting.showcase import (
    EMPTY_STATE,
    HONESTY_FOOTER,
    NO_CANDIDATE_COPY,
    VERIFICATION_BOUNDARY,
    _collect_agentbench,
    assert_self_contained,
    render_showcase,
)
from cfdb.schema import MetricsResult, RunManifest, TimingSpec

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_BADGE = 'data-honesty="REAL"'
GUARD_TEXT = "pin the solver image digest before rerun"


def _write_case(cases_root: Path, case_id: str, *, citation: str | None) -> Path:
    """Create a minimal validation case with reference data + provenance.yaml."""
    case_dir = cases_root / "validation" / case_id
    (case_dir / "reference").mkdir(parents=True)
    ref_path = case_dir / "reference" / "qoi.json"
    ref_path.write_text(json.dumps({"cd": 0.4}), encoding="utf-8")

    spec = {
        "id": case_id,
        "name": case_id,
        "category": "validation",
        "physics": {"flow": "incompressible", "dimensionality": "2d", "steady": True},
        "conditions": {"reynolds": 100.0},
        "solvers": [{"name": "mock", "command": "true"}],
        "outputs": {"qoi": ["cd"]},
        "reference": {"type": "experimental", "files": {"qoi": "reference/qoi.json"}},
        "metrics": {"qoi_relative_tolerance": {"cd": 0.05}},
        "budget": {"max_runtime_sec": 100},
    }
    (case_dir / "case.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")

    declaration: dict[str, object] = {"file_hashes": {"reference/qoi.json": sha256_file(ref_path)}}
    if citation is not None:
        declaration["citation"] = citation
        declaration["retrieved"] = "1988-01-01"
    (case_dir / "provenance.yaml").write_text(yaml.safe_dump(declaration), encoding="utf-8")
    return case_dir


def _write_run(
    runs_root: Path,
    run_id: str,
    *,
    case_id: str,
    start: datetime,
    solver: str = "mock",
    status: str = "success",
    overall: str = "pass",
    errors: dict[str, float] | None = None,
    values: dict[str, float] | None = None,
    error: str | None = None,
) -> Path:
    """Write a run directory (manifest.json + metrics.json); return run dir."""
    manifest = RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        status=status,  # type: ignore[arg-type]
        timing=TimingSpec(wall_time_sec=2.0, start_time=start, end_time=start),
        error=error,
    )
    metrics = MetricsResult(
        qoi_relative_errors=errors or {},
        qoi_pass=overall == "pass",
        overall_status=overall,
        qoi_computed_values=values,
    )
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    return run_dir


def _build_populated_repo(root: Path) -> dict[str, Path]:
    """Build a minimal repo with real artifacts for every showcase section."""
    cases = root / "cases"
    real_dir = _write_case(cases, "case_real", citation="Ladson, NASA TM-4074, 1988")
    _write_case(cases, "case_uncited", citation=None)

    runs = root / "runs"
    # Failing run is OLDER so the newest candidate for the gate is the pass run.
    _write_run(
        runs,
        "run_bad",
        case_id="case_real",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="failed",
        overall="fail",
        error="solver crashed",
    )
    _write_run(
        runs,
        "run_ok",
        case_id="case_real",
        start=datetime(2026, 1, 2, tzinfo=timezone.utc),
        errors={"cd": 0.02},
        values={"cd": 0.408},
    )
    # Independent candidate run for the regression gate: the showcase never
    # evaluates the baseline run against itself, so a second success run
    # (newest) is required for a genuine PASS row.
    _write_run(
        runs,
        "run_candidate",
        case_id="case_real",
        start=datetime(2026, 1, 3, tzinfo=timezone.utc),
        errors={"cd": 0.021},
        values={"cd": 0.4084},
    )

    library = FailureLibrary(root / "failures" / "library.json")
    summary = library.ingest(runs)
    assert summary.new_records == 1
    fingerprint = library.records()[0].fingerprint
    library.annotate(fingerprint, GUARD_TEXT)

    store = BaselineStore(baselines_path=root / "baselines" / "baselines.json", runs_root=runs)
    store.promote("run_ok", engineer="Zhuanz")

    registry = CaseRegistry(cases)
    contract = init_contract("case_real", registry)
    contract_path = root / "agentbench" / "case_real" / "contract.json"
    save_contract(contract, contract_path)
    submission = root / "submissions" / "sub_a"
    submission.mkdir(parents=True)
    (submission / "qoi.json").write_text(json.dumps({"cd": 0.41}), encoding="utf-8")
    (submission / "manifest.json").write_text(json.dumps({"wall_time_sec": 5.0}), encoding="utf-8")
    score_submission(
        contract,
        registry.load("case_real"),
        real_dir,
        submission,
        ledger_path=root / "agentbench" / "case_real" / "ledger.jsonl",
    )

    return {
        "root": root,
        "reference": real_dir / "reference" / "qoi.json",
        "baseline_metrics": runs / "run_ok" / "metrics.json",
        "contract": contract_path,
    }


@pytest.fixture
def populated(tmp_path: Path) -> dict[str, Path]:
    """Populated repo fixture (all six sections carry real data)."""
    return _build_populated_repo(tmp_path / "repo")


def _render(root: Path, name: str = "showcase.html") -> str:
    """Render the showcase and return the HTML text."""
    out = render_showcase(root, root / name)
    assert out == root / name
    return out.read_text(encoding="utf-8")


class TestSelfContained:
    def test_populated_page_has_no_external_references(self, populated: dict[str, Path]) -> None:
        html = _render(populated["root"])
        assert re.search(r"""(?:src|href)\s*=\s*["']https?://""", html, re.I) is None
        assert re.search(r"<link\b", html, re.I) is None
        assert re.search(r"<script\b", html, re.I) is None
        assert "@import" not in html
        # The gate itself accepts the shipped page.
        assert_self_contained(html)

    @pytest.mark.parametrize(
        "snippet",
        [
            '<img src="http://evil.example/x.png">',
            '<img src="//evil.example/x.png">',
            "<a href='https://evil.example'>x</a>",
            '<link rel="stylesheet" href="style.css">',
            '<script src="https://cdn.example/x.js"></script>',
            "<style>@import url(theme.css);</style>",
            "<style>body{background:url(https://evil.example/p.png)}</style>",
        ],
    )
    def test_gate_bites_on_external_reference(self, snippet: str) -> None:
        """Tamper witness: injecting any external reference must raise."""
        with pytest.raises(ValueError, match="not self-contained"):
            assert_self_contained(f"<html><body>{snippet}</body></html>")

    def test_gate_accepts_inline_svg_namespace(self) -> None:
        clean = '<svg xmlns="http://www.w3.org/2000/svg"></svg><img src="data:image/png;base64,x">'
        assert_self_contained(clean)  # must not raise


class TestProvenanceSection:
    def test_real_badge_only_for_cited_experimental(self, populated: dict[str, Path]) -> None:
        html = _render(populated["root"])
        # Exactly one REAL badge in the provenance table: case_real only.
        assert html.count(REAL_BADGE) == 1
        assert "Ladson, NASA TM-4074, 1988" in html
        # The uncited experimental case fails closed to DECLARED-NOT-VERIFIED.
        assert 'data-honesty="DECLARED-NOT-VERIFIED"' in html
        assert "case_uncited" in html

    def test_tampered_reference_byte_kills_real_badge(self, populated: dict[str, Path]) -> None:
        """Tamper witness: one changed reference byte downgrades REAL on re-render."""
        html_before = _render(populated["root"])
        assert REAL_BADGE in html_before
        assert 'data-frozen="INTACT"' in html_before

        populated["reference"].write_text(json.dumps({"cd": 0.5}), encoding="utf-8")
        html_after = _render(populated["root"], "showcase_after.html")

        assert REAL_BADGE not in html_after
        # The same tamper is also a ruler change: the frozen contract drifts.
        assert 'data-frozen="DRIFTED"' in html_after
        assert 'data-frozen="INTACT"' not in html_after


class TestRegressionSection:
    def test_gate_pass_recomputed_and_shown(self, populated: dict[str, Path]) -> None:
        html = _render(populated["root"])
        assert 'data-verdict="PASS"' in html
        assert "run_ok" in html
        # The evaluated candidate is the independent run, not the baseline.
        assert "run_candidate" in html
        assert "Zhuanz" in html

    def test_baseline_only_repo_renders_empty_state_never_self_pass(self, tmp_path: Path) -> None:
        """Honesty: with only the baseline run itself (plus non-qualifying
        dry-run/failed runs), the gate row shows an explicit empty state and
        never a vacuous run-vs-itself PASS."""
        root = tmp_path / "solo"
        _write_case(root / "cases", "case_solo", citation="Someone, 2020")
        runs = root / "runs"
        _write_run(
            runs,
            "run_base",
            case_id="case_solo",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            errors={"cd": 0.02},
            values={"cd": 0.408},
        )
        # Non-qualifying newer runs: neither may become the gate candidate.
        _write_run(
            runs,
            "run_dry",
            case_id="case_solo",
            start=datetime(2026, 1, 2, tzinfo=timezone.utc),
            status="dry_run",
            overall="unknown",
        )
        _write_run(
            runs,
            "run_failed",
            case_id="case_solo",
            start=datetime(2026, 1, 3, tzinfo=timezone.utc),
            status="failed",
            overall="fail",
            error="solver crashed",
        )
        store = BaselineStore(baselines_path=root / "baselines" / "baselines.json", runs_root=runs)
        store.promote("run_base", engineer="Zhuanz")

        html = _render(root)
        assert NO_CANDIDATE_COPY in html
        assert 'data-verdict="PASS"' not in html
        # The row is explicitly marked not-evaluated, never a real verdict.
        assert 'data-verdict="NOT-EVALUATED"' in html

    def test_tampered_baseline_metrics_shows_tampered(self, populated: dict[str, Path]) -> None:
        """Tamper witness: editing the anchored metrics.json must surface TAMPERED."""
        metrics_path = populated["baseline_metrics"]
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        data["qoi_relative_errors"]["cd"] = 0.0001  # forge a better number
        metrics_path.write_text(json.dumps(data), encoding="utf-8")

        html = _render(populated["root"])
        assert 'data-verdict="TAMPERED"' in html
        assert 'data-verdict="PASS"' not in html


class TestFailuresSection:
    def test_failure_bucket_and_guard_note_shown(self, populated: dict[str, Path]) -> None:
        html = _render(populated["root"])
        assert "case_real / mock" in html
        assert GUARD_TEXT in html
        assert EMPTY_STATE["failures"] not in html


class TestAgentbenchSection:
    def test_ruler_id_and_ledger_summary_shown(self, populated: dict[str, Path]) -> None:
        ruler_id = hashlib.sha256(populated["contract"].read_bytes()).hexdigest()[:8]
        html = _render(populated["root"])
        assert f"#{ruler_id}" in html
        assert 'data-frozen="INTACT"' in html
        assert "sub_a" not in html  # summary table, not per-submission dump
        assert EMPTY_STATE["agentbench"] not in html
        # One scoring event of one unique submission.
        assert 'data-events="1"' in html
        assert 'data-unique="1"' in html

    def test_scoring_events_distinguished_from_unique_submissions(
        self, populated: dict[str, Path]
    ) -> None:
        """Re-scoring the same submission adds an event, not a submission."""
        root = populated["root"]
        registry = CaseRegistry(root / "cases")
        contract = init_contract("case_real", registry)
        score_submission(
            contract,
            registry.load("case_real"),
            registry.get_case_dir("case_real"),
            root / "submissions" / "sub_a",
            ledger_path=root / "agentbench" / "case_real" / "ledger.jsonl",
        )
        html = _render(root)
        assert 'data-events="2"' in html
        assert 'data-unique="1"' in html
        # The ledger-discipline caveat is stated right on the section.
        assert "打分事件数 ≠ 唯一 submission 数" in html

    def test_domain_and_gate_columns_render(self, populated: dict[str, Path]) -> None:
        # R9 enrichment: case_real is a cfd case — the domain badge and the
        # frozen validity-gate tags must render in the agentbench table.
        html = _render(populated["root"])
        assert 'data-domain="cfd"' in html
        assert "data-gate=" in html  # the frozen validity gates are listed

    def test_io_oracle_gate_surfaced_for_shipped_coding_cases(self) -> None:
        # Data-level guard against the REAL shipped cases (mirrors
        # TestShippedIoOracleCases): a coding io_oracle case must carry
        # domain=coding and expose io_oracle_pass beside tests_all_pass in its
        # gate list, so the R9 second signal is visible on the trust surface.
        data = _collect_agentbench(PROJECT_ROOT)
        by_case = {r["case_id"]: r for r in data["contracts"]}
        assert "kth_largest" in by_case, sorted(by_case)
        row = by_case["kth_largest"]
        assert row["domain"] == "coding"
        assert row["has_io_oracle"] is True
        assert "io_oracle_pass" in row["validity_gates"]
        assert "tests_all_pass" in row["validity_gates"]  # both signals AND together

    def test_io_oracle_pass_rendered_emphasized(self, tmp_path: Path) -> None:
        # Integration: rendering the real repo surfaces io_oracle_pass with the
        # clay emphasis class (gate io) and the coding/agentic domain badges.
        out = render_showcase(PROJECT_ROOT, tmp_path / "showcase.html")
        html = out.read_text(encoding="utf-8")
        assert 'data-gate="io_oracle_pass"' in html
        assert 'class="gate io"' in html
        assert 'data-domain="coding"' in html
        assert 'data-domain="agentic"' in html


class TestEmptyStates:
    def test_all_sections_render_explicit_empty_copy(self, tmp_path: Path) -> None:
        root = tmp_path / "empty_repo"
        (root / "cases").mkdir(parents=True)
        html = _render(root)
        for section, copy in EMPTY_STATE.items():
            assert copy in html, f"missing empty-state copy for section '{section}'"
        # No fabricated data: no badges, no verdicts, no ruler ids.
        assert "data-honesty=" not in html
        assert "data-verdict=" not in html
        assert "data-frozen=" not in html
        assert_self_contained(html)

    def test_honesty_footer_always_present(
        self, tmp_path: Path, populated: dict[str, Path]
    ) -> None:
        root = tmp_path / "empty_repo"
        (root / "cases").mkdir(parents=True)
        assert HONESTY_FOOTER in _render(root)
        assert HONESTY_FOOTER in _render(populated["root"])

    def test_verification_boundary_always_present(
        self, tmp_path: Path, populated: dict[str, Path]
    ) -> None:
        """The verification-boundary statement ships on every rendered page."""
        root = tmp_path / "empty_repo"
        (root / "cases").mkdir(parents=True)
        assert VERIFICATION_BOUNDARY in _render(root)
        assert VERIFICATION_BOUNDARY in _render(populated["root"])


class TestTrustSection:
    def test_radar_svg_inlined_for_case_with_runs(self, populated: dict[str, Path]) -> None:
        html = _render(populated["root"])
        assert EMPTY_STATE["trust"] not in html
        assert "case_real / mock" in html
        assert "<svg" in html
        # Honesty banner on the profile is fed by the provenance audit.
        assert 'data-honesty-banner="REAL"' in html
