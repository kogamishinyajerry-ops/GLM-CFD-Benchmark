"""Compare two runs: QoI diff + comparison report rendering.

P2-c feature. Powers `cfdb compare <run_id1> <run_id2>` CLI.

Design:
- compare_runs() returns a list of QoIComparison dataclass, tolerant of
  cross-case runs (skips tolerance column) and missing QoIs (None values).
- render_compare_text() / render_compare_html() format the comparison for
  CLI output or HTML report respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cfdb.schema import CaseSpec, MetricsResult, RunManifest


@dataclass
class QoIComparison:
    """Single QoI comparison between two runs."""

    name: str
    """QoI name (e.g. 'cl', 'cd', 'centerline_umax')."""

    value1: float | None
    """QoI value from run 1. None if QoI not present in run 1."""

    value2: float | None
    """QoI value from run 2. None if QoI not present in run 2."""

    abs_diff: float | None
    """value2 - value1 (absolute). None if either value is None."""

    rel_diff_pct: float | None
    """(value2 - value1) / |value1| * 100. None if value1 is 0 or None."""

    within_tolerance: bool | None
    """True/False if tolerance applies and was checkable, else None.
    None when: (a) cross-case comparison, (b) QoI missing in one run,
    (c) value1 == 0 (relative diff undefined), (d) case has no tolerance for this QoI."""


def _safe_div(num: float, denom: float) -> float | None:
    """Safe division returning None on zero denominator."""
    if abs(denom) < 1e-15:
        return None
    return num / denom


def compare_runs(
    manifest1: RunManifest,
    metrics1: MetricsResult,
    manifest2: RunManifest,
    metrics2: MetricsResult,
    case: CaseSpec | None = None,
) -> list[QoIComparison]:
    """Compare QoIs between two runs.

    Iterates over the union of QoI names present in either run's metrics.
    For each QoI, computes absolute/relative differences and (if applicable)
    tolerance compliance.

    Args:
        manifest1, manifest2: RunManifests for the two runs.
        metrics1, metrics2: Their MetricsResult (contains qoi_relative_errors).
        case: Optional CaseSpec for tolerance lookup. If None, or if the two
            runs come from different cases (manifest1.case_id != manifest2.case_id),
            the tolerance column is skipped.

    Returns:
        List of QoIComparison, one per QoI in the union of both runs.
        Sorted by QoI name for deterministic output.
    """
    # Extract QoI values from metrics.qoi_relative_errors (keys are QoI names).
    # Note: qoi_relative_errors only contains QoIs that were successfully computed.
    qoi_set1 = set(metrics1.qoi_relative_errors.keys())
    qoi_set2 = set(metrics2.qoi_relative_errors.keys())
    all_qois = sorted(qoi_set1 | qoi_set2)

    # Tolerance lookup: only valid if same-case comparison and case provided
    use_tolerance = (
        case is not None
        and manifest1.case_id == manifest2.case_id
        and manifest1.case_id == case.id
    )
    tolerances = case.metrics.qoi_relative_tolerance if use_tolerance else {}

    comparisons: list[QoIComparison] = []
    for qoi_name in all_qois:
        v1_raw = metrics1.qoi_relative_errors.get(qoi_name)
        v2_raw = metrics2.qoi_relative_errors.get(qoi_name)
        # qoi_relative_errors stores RELATIVE ERROR (value - ref)/ref.
        # To get back to absolute QoI value, we need the reference from case.
        # For simplicity, we compare the relative errors directly as the
        # "value" — the user sees "run1 rel_error vs run2 rel_error".
        # If reference is available (case + reference.qoi_values), we can
        # back-calculate absolute values.
        v1 = v1_raw
        v2 = v2_raw

        abs_diff: float | None = None
        rel_diff_pct: float | None = None
        if v1 is not None and v2 is not None:
            abs_diff = v2 - v1
            rel = _safe_div(v2 - v1, abs(v1)) if v1 != 0 else None
            rel_diff_pct = rel * 100.0 if rel is not None else None

        within_tol: bool | None = None
        if (
            use_tolerance
            and qoi_name in tolerances
            and v1 is not None
            and v2 is not None
        ):
            tol = tolerances[qoi_name]
            # "within tolerance" = the absolute difference between the two runs
            # is less than the tolerance threshold. (Tolerance was originally
            # defined as |value - reference| / |reference| < tol; here we adapt
            # it to |v2 - v1| / |v1| < tol.)
            if v1 != 0:
                rel = abs(v2 - v1) / abs(v1)
                within_tol = rel <= tol

        comparisons.append(
            QoIComparison(
                name=qoi_name,
                value1=v1,
                value2=v2,
                abs_diff=abs_diff,
                rel_diff_pct=rel_diff_pct,
                within_tolerance=within_tol,
            )
        )

    return comparisons


def render_compare_text(
    manifest1: RunManifest,
    manifest2: RunManifest,
    comparisons: list[QoIComparison],
) -> str:
    """Render comparison as plain text table.

    Args:
        manifest1, manifest2: The two runs being compared.
        comparisons: Output of compare_runs().

    Returns:
        Multi-line text suitable for CLI stdout.
    """
    lines: list[str] = []
    lines.append(f"Comparing: {manifest1.run_id} vs {manifest2.run_id}")
    lines.append(f"  case1={manifest1.case_id} solver1={manifest1.solver} status1={manifest1.status}")
    lines.append(f"  case2={manifest2.case_id} solver2={manifest2.solver} status2={manifest2.status}")
    lines.append("=" * 80)

    if not comparisons:
        lines.append("(no QoIs to compare)")
        return "\n".join(lines)

    same_case = manifest1.case_id == manifest2.case_id

    # Header
    if same_case:
        header = f"{'QoI':<20} {'run1':>12} {'run2':>12} {'abs_diff':>12} {'rel_diff':>10} {'tolerance':>10}"
    else:
        header = f"{'QoI':<20} {'run1':>12} {'run2':>12} {'abs_diff':>12} {'rel_diff':>10}"
    lines.append(header)
    lines.append("-" * len(header))

    pass_count = 0
    total_with_tol = 0
    for c in comparisons:
        v1_str = f"{c.value1:.4e}" if c.value1 is not None else "N/A"
        v2_str = f"{c.value2:.4e}" if c.value2 is not None else "N/A"
        ad_str = f"{c.abs_diff:+.4e}" if c.abs_diff is not None else "N/A"
        rd_str = f"{c.rel_diff_pct:+.2f}%" if c.rel_diff_pct is not None else "N/A"

        if same_case:
            if c.within_tolerance is None:
                tol_str = "N/A"
            elif c.within_tolerance:
                tol_str = "PASS"
                pass_count += 1
                total_with_tol += 1
            else:
                tol_str = "FAIL"
                total_with_tol += 1
            lines.append(
                f"{c.name:<20} {v1_str:>12} {v2_str:>12} {ad_str:>12} {rd_str:>10} {tol_str:>10}"
            )
        else:
            lines.append(
                f"{c.name:<20} {v1_str:>12} {v2_str:>12} {ad_str:>12} {rd_str:>10}"
            )

    lines.append("=" * 80)
    if same_case and total_with_tol > 0:
        lines.append(f"Overall: {pass_count}/{total_with_tol} QoIs within tolerance")
    elif same_case:
        lines.append("Overall: no tolerances defined for compared QoIs")
    else:
        lines.append("Overall: cross-case comparison (tolerance column skipped)")

    return "\n".join(lines)


def render_compare_html(
    manifest1: RunManifest,
    manifest2: RunManifest,
    comparisons: list[QoIComparison],
    residual_svg: str | None = None,
    cp_svg: str | None = None,
) -> str:
    """Render comparison as a standalone HTML document.

    Args:
        manifest1, manifest2: The two runs being compared.
        comparisons: Output of compare_runs().
        residual_svg: Optional pre-rendered residual comparison SVG (inline).
        cp_svg: Optional pre-rendered Cp comparison SVG (inline).

    Returns:
        HTML string.
    """
    same_case = manifest1.case_id == manifest2.case_id

    # Build QoI table rows
    rows_html: list[str] = []
    pass_count = 0
    total_with_tol = 0
    for c in comparisons:
        v1_str = f"{c.value1:.4e}" if c.value1 is not None else "—"
        v2_str = f"{c.value2:.4e}" if c.value2 is not None else "—"
        ad_str = f"{c.abs_diff:+.4e}" if c.abs_diff is not None else "—"
        rd_str = f"{c.rel_diff_pct:+.2f}%" if c.rel_diff_pct is not None else "—"

        if same_case:
            if c.within_tolerance is None:
                tol_cell = '<td class="na">N/A</td>'
            elif c.within_tolerance:
                tol_cell = '<td class="pass">PASS</td>'
                pass_count += 1
                total_with_tol += 1
            else:
                tol_cell = '<td class="fail">FAIL</td>'
                total_with_tol += 1
        else:
            tol_cell = ""

        rows_html.append(
            f"<tr><td>{_esc(c.name)}</td><td>{v1_str}</td><td>{v2_str}</td>"
            f"<td>{ad_str}</td><td>{rd_str}</td>{tol_cell}</tr>"
        )

    overall_line: str
    if same_case and total_with_tol > 0:
        overall_line = f"{pass_count}/{total_with_tol} QoIs within tolerance"
    elif same_case:
        overall_line = "no tolerances defined"
    else:
        overall_line = "cross-case comparison"

    tolerance_header = "<th>Tolerance</th>" if same_case else ""

    # SVG sections
    svg_sections = ""
    if residual_svg:
        svg_sections += f'<section><h2>Residual Comparison</h2>{residual_svg}</section>'
    if cp_svg:
        svg_sections += f'<section><h2>Cp Distribution Comparison</h2>{cp_svg}</section>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Compare: {manifest1.run_id} vs {manifest2.run_id}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 2em; color: #222; }}
h1, h2 {{ color: #0072B2; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: right; }}
th {{ background: #f4f4f4; font-weight: 600; }}
td:first-child, th:first-child {{ text-align: left; }}
.pass {{ color: #009E73; font-weight: bold; }}
.fail {{ color: #D55E00; font-weight: bold; }}
.na {{ color: #999; }}
.summary {{ margin-top: 1em; font-weight: 600; }}
section {{ margin-top: 2em; }}
svg {{ max-width: 100%; height: auto; border: 1px solid #eee; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 1em; }}
</style>
</head>
<body>
<h1>Run Comparison</h1>
<div class="meta">
  <strong>Run 1:</strong> {manifest1.run_id} (case={manifest1.case_id}, solver={manifest1.solver}, status={manifest1.status})<br>
  <strong>Run 2:</strong> {manifest2.run_id} (case={manifest2.case_id}, solver={manifest2.solver}, status={manifest2.status})
</div>
<section>
<h2>QoI Differences</h2>
<table>
<thead>
<tr><th>QoI</th><th>run1</th><th>run2</th><th>abs_diff</th><th>rel_diff</th>{tolerance_header}</tr>
</thead>
<tbody>
{chr(10).join(rows_html)}
</tbody>
</table>
<p class="summary">Overall: {overall_line}</p>
</section>
{svg_sections}
</body>
</html>
"""
    return html


def _esc(s: Any) -> str:
    """HTML-escape a value."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
