"""HTML report generator using Jinja2."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cfdb.schema import MetricsResult, RunManifest
from cfdb.version import __version__

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def generate_html_report(
    manifest: RunManifest,
    metrics: MetricsResult,
    run_dir: Path,
    residuals_svg: str | None = None,
) -> Path:
    """Generate a single-file HTML report.

    Args:
        manifest: The run manifest.
        metrics: The metrics result.
        run_dir: Run directory where report.html will be written.
        residuals_svg: Optional SVG string for residual convergence plot.
                       If provided, embedded into the report as an inline SVG section.

    Returns:
        Path to the generated report.html.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")

    status_color = {
        "success": "#28a745",
        "failed": "#dc3545",
        "timeout": "#ffc107",
        "dry_run": "#17a2b8",
        "pass": "#28a745",
        "fail": "#dc3545",
        "incomplete": "#ffc107",
        "unknown": "#6c757d",
    }

    html = template.render(
        manifest=manifest,
        metrics=metrics,
        version=__version__,
        status_color=status_color,
        residuals_svg=residuals_svg,
    )

    report_path = run_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("HTML report written to %s", report_path)
    return report_path


# === P2-c: Multi-solver report ===

def generate_multi_solver_report(
    manifests: list[RunManifest],
    metrics_list: list[MetricsResult],
    output_path: Path,
    cp_svg: str | None = None,
    polar_svg: str | None = None,
    title: str | None = None,
) -> Path:
    """Generate an HTML report comparing multiple runs of related cases.

    Used by `cfdb report-sweep` to summarize an alpha sweep (multiple α ×
    multiple solvers). Sections:

    1. Run summary table (run_id, case, solver, status, wall_time, alpha)
    2. QoI table (solver × case, each cell shows cl/cd with reference error)
    3. Optional Cp comparison SVG (cp_svg)
    4. Optional polar curve SVG (polar_svg)

    Args:
        manifests: List of RunManifests from related runs (e.g. all naca0012*
            cases). Must be non-empty.
        metrics_list: Corresponding MetricsResult for each manifest.
        output_path: Full path of the output HTML file (parent dir must exist).
        cp_svg: Pre-rendered Cp comparison SVG (inline).
        polar_svg: Pre-rendered polar SVG (inline).
        title: Report title. Defaults to "Multi-Solver Comparison Report".

    Returns:
        Path to the generated HTML.

    Raises:
        ValueError: If manifests list is empty or length mismatch with metrics_list.
    """
    if not manifests:
        raise ValueError("manifests list must be non-empty")
    if len(manifests) != len(metrics_list):
        raise ValueError(
            f"manifests ({len(manifests)}) and metrics_list ({len(metrics_list)}) "
            "must have the same length"
        )

    report_title = title or "Multi-Solver Comparison Report"

    # Build summary table rows
    summary_rows: list[dict[str, Any]] = []
    for m, met in zip(manifests, metrics_list, strict=False):
        alpha = None
        if m.cli_args and "alpha" in m.cli_args:
            with contextlib.suppress(ValueError):
                alpha = float(m.cli_args["alpha"])
        summary_rows.append(
            {
                "run_id": m.run_id,
                "case_id": m.case_id,
                "solver": m.solver,
                "status": m.status,
                "wall_time_sec": m.timing.wall_time_sec,
                "alpha_deg": alpha,
                "overall": met.overall_status,
            }
        )

    # Build QoI table: for each (case_id, solver), gather qoi_relative_errors
    qoi_names: set[str] = set()
    for met in metrics_list:
        qoi_names.update(met.qoi_relative_errors.keys())
    qoi_names_sorted = sorted(qoi_names)

    qoi_rows: list[dict[str, Any]] = []
    for row, met in zip(summary_rows, metrics_list, strict=False):
        cells: dict[str, Any] = {"case_id": row["case_id"], "solver": row["solver"]}
        for qn in qoi_names_sorted:
            cells[qn] = met.qoi_relative_errors.get(qn)
        qoi_rows.append(cells)

    # Build HTML (simple inline-CSS, no Jinja template for this one)
    summary_html = _build_summary_table(summary_rows)
    qoi_html = _build_qoi_table(qoi_rows, qoi_names_sorted)

    svg_sections = ""
    if cp_svg:
        svg_sections += f'<section><h2>Cp Distribution Comparison</h2>{cp_svg}</section>'
    if polar_svg:
        svg_sections += f'<section><h2>Polar Curves (Cl-α / Cd-α)</h2>{polar_svg}</section>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(report_title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 2em; color: #222; background: #fff; }}
h1, h2 {{ color: #0072B2; }}
h1 {{ border-bottom: 2px solid #0072B2; padding-bottom: 0.3em; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: right; }}
th {{ background: #f4f4f4; font-weight: 600; }}
td:first-child, th:first-child {{ text-align: left; }}
section {{ margin-top: 2em; }}
svg {{ max-width: 100%; height: auto; border: 1px solid #eee; }}
.pass {{ color: #009E73; }}
.fail {{ color: #D55E00; }}
.na {{ color: #999; }}
</style>
</head>
<body>
<h1>{_esc(report_title)}</h1>
<p>Generated by cfdb v{__version__} · {len(manifests)} run(s) included</p>

<section>
<h2>Run Summary</h2>
{summary_html}
</section>

<section>
<h2>QoI Relative Errors</h2>
{qoi_html}
</section>

{svg_sections}

</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")
    logger.info("Multi-solver report written to %s", output_path)
    return output_path


def _build_summary_table(rows: list[dict[str, Any]]) -> str:
    """Build HTML table for run summary."""
    if not rows:
        return "<p>(no runs)</p>"
    parts = ["<table><thead><tr>"]
    headers = ["Run ID", "Case", "Solver", "Status", "Wall (s)", "α (deg)", "Overall"]
    for h in headers:
        parts.append(f"<th>{_esc(h)}</th>")
    parts.append("</tr></thead><tbody>")
    for r in rows:
        alpha_str = f"{r['alpha_deg']:.1f}" if r.get("alpha_deg") is not None else "—"
        parts.append(
            "<tr>"
            f"<td><code>{_esc(r['run_id'])}</code></td>"
            f"<td>{_esc(r['case_id'])}</td>"
            f"<td>{_esc(r['solver'])}</td>"
            f"<td>{_esc(r['status'])}</td>"
            f"<td>{r['wall_time_sec']:.3f}</td>"
            f"<td>{alpha_str}</td>"
            f"<td>{_esc(r['overall'])}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _build_qoi_table(rows: list[dict[str, Any]], qoi_names: list[str]) -> str:
    """Build HTML table for QoI values per (case, solver)."""
    if not rows:
        return "<p>(no QoIs)</p>"
    parts = ["<table><thead><tr><th>Case</th><th>Solver</th>"]
    for qn in qoi_names:
        parts.append(f"<th>{_esc(qn)}</th>")
    parts.append("</tr></thead><tbody>")
    for r in rows:
        parts.append(f"<tr><td>{_esc(r['case_id'])}</td><td>{_esc(r['solver'])}</td>")
        for qn in qoi_names:
            val = r.get(qn)
            if val is None:
                parts.append('<td class="na">—</td>')
            else:
                parts.append(f"<td>{val:.4e}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _esc(s: Any) -> str:
    """HTML-escape a value."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
