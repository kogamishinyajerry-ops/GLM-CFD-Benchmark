"""Web dashboard route handlers — read-only, thin wrappers around existing modules."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cfdb.registry import CaseRegistry
from cfdb.schema import RunManifest
from cfdb.version import __version__

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_repo(request: Request) -> object:
    return request.app.state.repo


def _get_registry(request: Request) -> CaseRegistry:
    return request.app.state.registry


def _get_runs_dir(request: Request) -> Path:
    return request.app.state.runs_dir


def _get_cases_dir(request: Request) -> Path:
    return request.app.state.cases_dir


def _get_web_templates(request: Request):
    return request.app.state.web_templates


def _get_reporting_env(request: Request):
    return request.app.state.reporting_env


def _status_color(status: str) -> str:
    return {
        "success": "#28a745",
        "failed": "#dc3545",
        "timeout": "#ffc107",
        "dry_run": "#17a2b8",
        "pass": "#28a745",
        "fail": "#dc3545",
        "incomplete": "#ffc107",
        "unknown": "#6c757d",
    }.get(status, "#6c757d")


def _render_base(
    request: Request, template_name: str, active_page: str, **kwargs: Any
) -> HTMLResponse:
    """Render a template extending base.html.j2."""
    templates = _get_web_templates(request)
    tmpl = templates.get_template(template_name)
    html = tmpl.render(
        version=__version__,
        active_page=active_page,
        **kwargs,
    )
    return HTMLResponse(html)


def _get_residuals_svg(manifest: RunManifest) -> str | None:
    """Generate residual SVG for a manifest if residuals_history is available."""
    if not manifest.residuals_history:
        return None
    from cfdb.reporting.svg_residuals import render_residual_svg

    return render_residual_svg(
        residuals=manifest.residuals_history,
        title=f"Residual Convergence — {manifest.case_id} ({manifest.solver})",
        log_scale=True,
    )


# ---------------------------------------------------------------------------
# Redirect root -> /runs
# ---------------------------------------------------------------------------


@router.get("/")
def index(request: Request) -> RedirectResponse:
    return RedirectResponse("/runs", status_code=302)


# ---------------------------------------------------------------------------
# Run listing
# ---------------------------------------------------------------------------


@router.get("/runs")
def run_list(
    request: Request,
    case_id: str | None = Query(None),
    solver: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=5, le=100),
) -> HTMLResponse:
    repo = _get_repo(request)

    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        # Fallback: json repo may not support keyword filters
        runs = repo.list_runs()  # type: ignore[union-attr]

    # Client-side filtering for json repo
    filtered: list[RunManifest] = []
    for r in runs:
        if case_id and r.case_id != case_id:
            continue
        if solver and r.solver != solver:
            continue
        if status and r.status != status:
            continue
        filtered.append(r)

    # Pagination
    total = len(filtered)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    page_runs = filtered[start:end]

    # Extract unique filter values
    all_case_ids = sorted(set(r.case_id for r in runs))
    all_solvers = sorted(set(r.solver for r in runs))
    all_statuses = sorted(set(r.status for r in runs))

    return _render_base(
        request,
        "index.html.j2",
        "runs",
        runs=page_runs,
        page=page,
        total_pages=total_pages,
        total_runs=total,
        per_page=per_page,
        case_id=case_id or "",
        solver=solver or "",
        status=status or "",
        all_case_ids=all_case_ids,
        all_solvers=all_solvers,
        all_statuses=all_statuses,
        status_color=_status_color,
    )


# ---------------------------------------------------------------------------
# Partial: reloadable run table (htmx)
# ---------------------------------------------------------------------------


@router.get("/partials/runs")
def partial_runs(
    request: Request,
    case_id: str | None = Query(None),
    solver: str | None = Query(None),
    status: str | None = Query(None),
) -> HTMLResponse:
    repo = _get_repo(request)

    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        runs = repo.list_runs()  # type: ignore[union-attr]

    filtered: list[RunManifest] = []
    for r in runs:
        if case_id and r.case_id != case_id:
            continue
        if solver and r.solver != solver:
            continue
        if status and r.status != status:
            continue
        filtered.append(r)

    templates = _get_web_templates(request)
    tmpl = templates.get_template("partials/run_table.html.j2")
    html = tmpl.render(runs=filtered, status_color=_status_color)
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Single run detail
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> HTMLResponse:
    repo = _get_repo(request)

    try:
        manifest, metrics = repo.load_run(run_id)  # type: ignore[union-attr]
    except KeyError:
        templates = _get_web_templates(request)
        html = templates.get_template("404.html.j2").render(
            version=__version__,
            active_page="runs",
            message=f'Run <code>{run_id}</code> does not exist.',
        )
        return HTMLResponse(html, status_code=404)

    # Generate residual SVG if available (lazy-loaded via htmx)
    has_residuals = manifest.residuals_history is not None

    return _render_base(
        request,
        "run_detail.html.j2",
        "runs",
        manifest=manifest,
        metrics=metrics,
        has_residuals=has_residuals,
        status_color=_status_color,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Lazy-load residual SVG (htmx partial)
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/residuals.svg")
def run_residuals_svg(request: Request, run_id: str) -> Response:
    """Return raw residual SVG for htmx lazy load."""
    repo = _get_repo(request)
    try:
        manifest, _ = repo.load_run(run_id)  # type: ignore[union-attr]
    except KeyError:
        return Response(
            '<svg viewBox="0 0 680 400" xmlns="http://www.w3.org/2000/svg">'
            '<text x="340" y="200" text-anchor="middle" fill="#999" font-size="14">'
            "No residual data available</text></svg>",
            media_type="image/svg+xml",
        )

    svg = _get_residuals_svg(manifest)
    return Response(svg or "", media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Case listing
# ---------------------------------------------------------------------------


@router.get("/cases")
def case_list(request: Request) -> HTMLResponse:
    registry = _get_registry(request)
    repo = _get_repo(request)

    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        runs = repo.list_runs()  # type: ignore[union-attr]

    cases = registry.list_all()

    # Count runs per case
    run_counts: dict[str, int] = {}
    for r in runs:
        run_counts[r.case_id] = run_counts.get(r.case_id, 0) + 1

    return _render_base(
        request,
        "cases.html.j2",
        "cases",
        cases=cases,
        run_counts=run_counts,
    )


# ---------------------------------------------------------------------------
# Case detail
# ---------------------------------------------------------------------------


@router.get("/cases/{case_id}")
def case_detail(request: Request, case_id: str) -> HTMLResponse:
    registry = _get_registry(request)
    repo = _get_repo(request)

    try:
        case = registry.load(case_id)
    except KeyError:
        templates = _get_web_templates(request)
        return HTMLResponse(
            templates.get_template("base.html.j2").render(
                version=__version__,
                active_page="cases",
                content=(
                    f'<div class="card"><h2>Case Not Found</h2>'
                    f'<p>Case <code>{case_id}</code> does not exist.</p></div>'
                ),
            ),
            status_code=404,
        )

    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        runs = repo.list_runs()  # type: ignore[union-attr]

    # Filter runs for this case
    case_runs = [r for r in runs if r.case_id == case_id]

    return _render_base(
        request,
        "cases.html.j2",
        "cases",
        case=case,
        case_runs=case_runs,
        single_case=True,
        status_color=_status_color,
    )


# ---------------------------------------------------------------------------
# Compare form + result
# ---------------------------------------------------------------------------


@router.get("/compare")
def compare_form(
    request: Request,
    run1: str | None = Query(None),
    run2: str | None = Query(None),
) -> HTMLResponse:
    repo = _get_repo(request)
    registry = _get_registry(request)

    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        runs = repo.list_runs()  # type: ignore[union-attr]

    run_ids = [r.run_id for r in runs]

    if not run1 or not run2:
        return _render_base(
            request,
            "compare.html.j2",
            "compare",
            run_ids=run_ids,
            run1=run1 or "",
            run2=run2 or "",
            result=None,
            show_form=True,
        )

    # Execute comparison
    try:
        manifest1, metrics1 = repo.load_run(run1)  # type: ignore[union-attr]
    except KeyError:
        return _render_base(
            request,
            "compare.html.j2",
            "compare",
            run_ids=run_ids,
            run1=run1,
            run2=run2,
            error=f"Run '{run1}' not found.",
            show_form=True,
        )

    try:
        manifest2, metrics2 = repo.load_run(run2)  # type: ignore[union-attr]
    except KeyError:
        return _render_base(
            request,
            "compare.html.j2",
            "compare",
            run_ids=run_ids,
            run1=run1,
            run2=run2,
            error=f"Run '{run2}' not found.",
            show_form=True,
        )

    from cfdb.reporting.compare import compare_runs

    # Load case for tolerance
    case = None
    if manifest1.case_id == manifest2.case_id:
        with contextlib.suppress(Exception):
            case = registry.load(manifest1.case_id)

    comparisons = compare_runs(manifest1, metrics1, manifest2, metrics2, case=case)

    # Count passes for the template
    compare_pass_count = sum(1 for c in comparisons if c.within_tolerance is True)
    compare_total_count = sum(
        1 for c in comparisons if c.within_tolerance is not None
    )

    # Render comparison SVGs
    residual_svg = None
    if manifest1.residuals_history and manifest2.residuals_history:
        from cfdb.reporting.svg_compare import render_residual_comparison_svg

        combined = {
            manifest1.solver: manifest1.residuals_history,
            manifest2.solver: manifest2.residuals_history,
        }
        with contextlib.suppress(Exception):
            residual_svg = render_residual_comparison_svg(combined)

    return _render_base(
        request,
        "compare.html.j2",
        "compare",
        run_ids=run_ids,
        run1=run1,
        run2=run2,
        manifest1=manifest1,
        manifest2=manifest2,
        comparisons=comparisons,
        compare_pass_count=compare_pass_count,
        compare_total_count=compare_total_count,
        residual_svg=residual_svg,
        result=True,
        show_form=False,
        status_color=_status_color,
    )


# ---------------------------------------------------------------------------
# Sweep form + result
# ---------------------------------------------------------------------------


@router.get("/sweep")
def sweep_form(request: Request) -> HTMLResponse:
    return _render_base(
        request,
        "sweep.html.j2",
        "sweep",
        result=None,
        show_form=True,
    )


@router.get("/sweep/{case_prefix}")
def sweep_result(
    request: Request,
    case_prefix: str,
    polar: bool = Query(False),
) -> HTMLResponse:
    repo = _get_repo(request)
    cases_dir = _get_cases_dir(request)

    try:
        all_runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        all_runs = repo.list_runs()  # type: ignore[union-attr]

    matched = [r for r in all_runs if r.case_id.startswith(case_prefix)]

    if not matched:
        return _render_base(
            request,
            "sweep.html.j2",
            "sweep",
            show_form=True,
            error=f"No runs matching case ID prefix '{case_prefix}'.",
        )

    # Load full manifests
    manifests: list[RunManifest] = []
    metrics_list = []
    for run_summary in matched:
        try:
            m, met = repo.load_run(run_summary.run_id)  # type: ignore[union-attr]
            manifests.append(m)
            metrics_list.append(met)
        except Exception as e:
            logger.warning("Failed to load run %s: %s", run_summary.run_id, e)

    # Build polar SVG if requested
    polar_svg = None
    if polar:
        try:
            from cfdb.post.qoi_extractor import load_ladson_polar
            from cfdb.reporting.svg_polar import PolarCurve, PolarPoint, render_polar_svg

            solver_points: dict[str, list[PolarPoint]] = {}
            for m, met in zip(manifests, metrics_list, strict=False):
                alpha_str = m.cli_args.get("alpha") if m.cli_args else None
                if alpha_str is None:
                    continue
                try:
                    alpha = float(alpha_str)
                except (ValueError, TypeError):
                    continue
                cl = met.qoi_computed_values.get("cl") if met.qoi_computed_values else None
                cd = met.qoi_computed_values.get("cd") if met.qoi_computed_values else None
                if cl is not None and cd is not None:
                    solver_points.setdefault(m.solver, []).append(
                        PolarPoint(alpha_deg=alpha, cl=cl, cd=cd)
                    )

            curves = [PolarCurve(solver=s, points=pts) for s, pts in solver_points.items()]
            reference = None
            ref_path = cases_dir / "validation" / case_prefix / "reference" / "ladson_polar.csv"
            if ref_path.exists():
                ref_data = load_ladson_polar(ref_path)
                if ref_data:
                    reference = PolarCurve(
                        solver="Ladson 1988",
                        points=[PolarPoint(a, cl, cd) for a, cl, cd in ref_data],
                        is_reference=True,
                    )
            if curves:
                polar_svg = render_polar_svg(
                    curves=curves, reference=reference, title=f"Polar — {case_prefix}"
                )
        except Exception as e:
            logger.warning("Failed to build polar SVG: %s", e)

    # Collect all QoI names
    qoi_names_set: set[str] = set()
    for met in metrics_list:
        qoi_names_set.update(met.qoi_relative_errors.keys())
    sweep_qoi_names = sorted(qoi_names_set)

    return _render_base(
        request,
        "sweep.html.j2",
        "sweep",
        manifests=manifests,
        metrics_list=metrics_list,
        sweep_qoi_names=sweep_qoi_names,
        polar_svg=polar_svg,
        case_prefix=case_prefix,
        show_form=False,
        status_color=_status_color,
    )


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------


@router.get("/api/runs")
def api_runs(request: Request) -> list[dict[str, Any]]:
    repo = _get_repo(request)
    try:
        runs = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        runs = repo.list_runs()  # type: ignore[union-attr]

    return [
        {
            "run_id": r.run_id,
            "case_id": r.case_id,
            "solver": r.solver,
            "status": r.status,
            "wall_time_sec": r.timing.wall_time_sec,
            "start_time": r.timing.start_time.isoformat(),
            "host": r.host,
            "git_commit": r.git_commit,
        }
        for r in runs
    ]


@router.get("/api/runs/{run_id}")
def api_run_detail(request: Request, run_id: str) -> dict[str, Any]:
    repo = _get_repo(request)
    manifest, metrics = repo.load_run(run_id)  # type: ignore[union-attr]
    return {
        "manifest": manifest.model_dump(),
        "metrics": metrics.model_dump(),
    }


@router.get("/api/cases")
def api_cases(request: Request) -> list[dict[str, Any]]:
    registry = _get_registry(request)
    cases = registry.list_all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "description": c.description,
            "solvers": [s.name for s in c.solvers],
            "qois": c.outputs.qoi,
        }
        for c in cases
    ]
