"""Tests for cfdb.reporting.compare (P2-c)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfdb.reporting.compare import (
    QoIComparison,
    compare_runs,
    render_compare_html,
    render_compare_text,
)
from cfdb.schema import (
    CaseSpec,
    ConditionsSpec,
    MetricSpec,
    MetricsResult,
    OutputSpec,
    PhysicsSpec,
    RunManifest,
    SolverConfig,
    TimingSpec,
)


def _make_manifest(
    run_id: str = "r1",
    case_id: str = "naca0012_a0",
    solver: str = "openfoam",
    status: str = "success",
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        backend="local",
        status=status,  # type: ignore[arg-type]
        timing=TimingSpec(
            wall_time_sec=1.0,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        ),
    )


def _make_metrics(qoi_errors: dict[str, float]) -> MetricsResult:
    return MetricsResult(qoi_relative_errors=qoi_errors)


def _make_case(
    case_id: str = "naca0012_a0",
    tolerances: dict[str, float] | None = None,
) -> CaseSpec:
    return CaseSpec(
        id=case_id,
        name="test",
        category="validation",
        physics=PhysicsSpec(flow="rans"),
        conditions=ConditionsSpec(reynolds=1.0),
        solvers=[SolverConfig(name="openfoam", command="x")],
        outputs=OutputSpec(),
        metrics=MetricSpec(qoi_relative_tolerance=tolerances or {}),
    )


class TestCompareRuns:
    """Tests for compare_runs()."""

    def test_same_case_qoi_overlap(self) -> None:
        m1 = _make_manifest("r1", "naca0012_a0", "openfoam")
        m2 = _make_manifest("r2", "naca0012_a0", "su2")
        met1 = _make_metrics({"cl": 0.01, "cd": 0.02})
        met2 = _make_metrics({"cl": 0.02, "cd": 0.025})
        case = _make_case(tolerances={"cl": 0.5, "cd": 0.5})
        comps = compare_runs(m1, met1, m2, met2, case=case)
        # 2 QoIs
        assert len(comps) == 2
        names = {c.name for c in comps}
        assert names == {"cl", "cd"}
        # Check cl: abs_diff = 0.02 - 0.01 = 0.01
        cl_comp = next(c for c in comps if c.name == "cl")
        assert cl_comp.abs_diff == pytest.approx(0.01)
        assert cl_comp.rel_diff_pct == pytest.approx(100.0)  # (0.01)/0.01 * 100
        # rel = 0.01/0.01 = 1.0, tol = 0.5 → 1.0 > 0.5 → FAIL
        assert cl_comp.within_tolerance is False

    def test_cross_case_skips_tolerance(self) -> None:
        m1 = _make_manifest("r1", "case_a", "openfoam")
        m2 = _make_manifest("r2", "case_b", "openfoam")
        met1 = _make_metrics({"cl": 0.01})
        met2 = _make_metrics({"cl": 0.02})
        case = _make_case(case_id="case_a", tolerances={"cl": 0.5})
        comps = compare_runs(m1, met1, m2, met2, case=case)
        assert len(comps) == 1
        assert comps[0].within_tolerance is None

    def test_no_case_skips_tolerance(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        met1 = _make_metrics({"cl": 0.01})
        met2 = _make_metrics({"cl": 0.02})
        comps = compare_runs(m1, met1, m2, met2, case=None)
        assert comps[0].within_tolerance is None

    def test_missing_qoi_in_one_run(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        met1 = _make_metrics({"cl": 0.01})
        met2 = _make_metrics({"cd": 0.02})  # different QoI
        comps = compare_runs(m1, met1, m2, met2, case=None)
        names = {c.name for c in comps}
        assert names == {"cl", "cd"}
        cl_comp = next(c for c in comps if c.name == "cl")
        assert cl_comp.value1 == 0.01
        assert cl_comp.value2 is None
        assert cl_comp.abs_diff is None
        cd_comp = next(c for c in comps if c.name == "cd")
        assert cd_comp.value1 is None
        assert cd_comp.value2 == 0.02

    def test_value1_zero_rel_diff_none(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        met1 = _make_metrics({"cl": 0.0})
        met2 = _make_metrics({"cl": 0.5})
        comps = compare_runs(m1, met1, m2, met2, case=None)
        cl_comp = comps[0]
        assert cl_comp.rel_diff_pct is None  # div by zero
        assert cl_comp.abs_diff == pytest.approx(0.5)

    def test_tolerance_pass(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        met1 = _make_metrics({"cl": 0.456})
        met2 = _make_metrics({"cl": 0.460})  # diff = 0.004, rel = 0.877%
        case = _make_case(tolerances={"cl": 0.05})  # 5% tol
        comps = compare_runs(m1, met1, m2, met2, case=case)
        assert comps[0].within_tolerance is True


class TestRenderCompareText:
    """Tests for render_compare_text()."""

    def test_same_case_text_output(self) -> None:
        m1 = _make_manifest("run1", "naca0012_a0", "openfoam")
        m2 = _make_manifest("run2", "naca0012_a0", "su2")
        met1 = _make_metrics({"cl": 0.456})
        met2 = _make_metrics({"cl": 0.460})
        case = _make_case(tolerances={"cl": 0.05})
        comps = compare_runs(m1, met1, m2, met2, case=case)
        text = render_compare_text(m1, m2, comps)
        assert "run1" in text
        assert "run2" in text
        assert "cl" in text
        assert "PASS" in text or "FAIL" in text  # tolerance column
        assert "Overall" in text

    def test_cross_case_text_output(self) -> None:
        m1 = _make_manifest("run1", "case_a")
        m2 = _make_manifest("run2", "case_b")
        met1 = _make_metrics({"cl": 0.456})
        met2 = _make_metrics({"cl": 0.460})
        comps = compare_runs(m1, met1, m2, met2, case=None)
        text = render_compare_text(m1, m2, comps)
        assert "cross-case" in text

    def test_empty_comparisons(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        text = render_compare_text(m1, m2, [])
        assert "no QoIs" in text


class TestRenderCompareHtml:
    """Tests for render_compare_html()."""

    def test_html_basic_structure(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        comps = [QoIComparison(name="cl", value1=0.456, value2=0.460,
                               abs_diff=0.004, rel_diff_pct=0.88,
                               within_tolerance=True)]
        html = render_compare_html(m1, m2, comps)
        assert "<!DOCTYPE html>" in html
        assert "<table>" in html
        assert "cl" in html
        assert "PASS" in html

    def test_html_with_svg_sections(self) -> None:
        m1 = _make_manifest()
        m2 = _make_manifest()
        comps: list[QoIComparison] = []
        html = render_compare_html(
            m1, m2, comps,
            residual_svg="<svg>fake residual</svg>",
            cp_svg="<svg>fake cp</svg>",
        )
        assert "Residual Comparison" in html
        assert "Cp Distribution Comparison" in html
        assert "fake residual" in html

    def test_html_cross_case_skips_tolerance_header(self) -> None:
        m1 = _make_manifest("r1", "case_a")
        m2 = _make_manifest("r2", "case_b")
        comps = [QoIComparison(name="cl", value1=0.1, value2=0.2,
                               abs_diff=0.1, rel_diff_pct=100.0,
                               within_tolerance=None)]
        html = render_compare_html(m1, m2, comps)
        assert "<th>Tolerance</th>" not in html
