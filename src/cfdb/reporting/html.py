"""HTML report generator using Jinja2."""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cfdb.schema import MetricsResult, RunManifest
from cfdb.version import __version__

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def generate_html_report(
    manifest: RunManifest,
    metrics: MetricsResult,
    run_dir: Path,
) -> Path:
    """Generate a single-file HTML report.

    Args:
        manifest: The run manifest.
        metrics: The metrics result.
        run_dir: Run directory where report.html will be written.

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
    )

    report_path = run_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("HTML report written to %s", report_path)
    return report_path
