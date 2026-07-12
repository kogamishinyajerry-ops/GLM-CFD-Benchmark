"""Typer CLI entry point for the cfdb platform.

Command surface (grown well beyond the original 4 commands):

- Core pipeline: ``list-cases`` / ``validate-case`` / ``run`` / ``report``
- Data & aggregation: ``data`` (DVC wrapper) / ``compare`` / ``report-sweep``
  / ``serve``
- Trust platform (v4): ``provenance`` / ``trust`` / ``failures`` /
  ``baseline`` / ``gate`` / ``agent-eval`` / ``showcase``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

import typer

from cfdb.registry import CaseRegistry
from cfdb.storage.json_repo import JsonManifestRepository
from cfdb.version import __version__

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="cfdb",
    help="CFD-Benchmark: Open-source CFD solver V&V and multi-solver comparison platform.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# P2-b: DVC management sub-app
data_app = typer.Typer(
    name="data",
    help="DVC large file management (meshes, reference datasets).",
    no_args_is_help=True,
)
app.add_typer(data_app, name="data")


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", help="Show version and exit."),
    ] = None,
) -> None:
    """CFD-Benchmark CLI."""
    if version:
        typer.echo(f"cfdb {__version__}")
        raise typer.Exit(code=0)


@app.command("list-cases")
def list_cases(
    cases_dir: Annotated[
        Path,
        typer.Option("--cases-dir", help="Directory containing case categories."),
    ] = Path("cases"),
) -> None:
    """List all registered cases."""
    registry = CaseRegistry(cases_dir)
    cases = registry.list_all()

    if not cases:
        typer.echo("No cases found.")
    else:
        typer.echo(f"{'ID':<25} {'Category':<15} {'Solvers':<20} {'Name'}")
        typer.echo("-" * 80)
        for case in cases:
            solvers = ", ".join(s.name for s in case.solvers)
            typer.echo(f"{case.id:<25} {case.category:<15} {solvers:<20} {case.name}")

        typer.echo(f"\nTotal: {len(cases)} case(s)")

    # A2: registry fail-open scanning never hides invalid cases — surface them
    # on stderr while keeping exit code 0 (list-cases is a read-only report).
    skipped = registry.skipped
    if skipped:
        typer.echo(f"\n{len(skipped)} case(s) skipped (invalid):", err=True)
        for rel_path, reason in skipped:
            typer.echo(f"  {rel_path}: {reason}", err=True)


@app.command("validate-case")
def validate_case(
    yaml_path: Annotated[
        Path,
        typer.Argument(help="Path to case.yaml file."),
    ],
) -> None:
    """Validate a single case.yaml file against the CaseSpec schema."""
    registry = CaseRegistry(yaml_path.parent.parent.parent)
    try:
        spec = registry.validate(yaml_path)
        typer.echo(f"[OK] CaseSpec '{spec.id}' validation passed.")
        typer.echo(f"  Name: {spec.name}")
        typer.echo(f"  Category: {spec.category}")
        typer.echo(f"  Solvers: {', '.join(s.name for s in spec.solvers)}")
        typer.echo(f"  QoIs: {', '.join(spec.outputs.qoi) if spec.outputs.qoi else 'none'}")
    except Exception as e:
        typer.echo(f"[FAIL] Validation failed: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("run")
def run(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID to run.")],
    solver: Annotated[
        str,
        typer.Option("--solver", "-s", help="Solver/adapter name."),
    ] = "generic",
    backend: Annotated[
        str,
        typer.Option("--backend", "-b", help="Execution backend: 'local' or 'docker'."),
    ] = "local",
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            help="Docker image (name:tag). Required when --backend docker is used.",
        ),
    ] = None,
    pull: Annotated[
        str,
        typer.Option(
            "--pull",
            help="Docker image pull policy: 'always' | 'missing' | 'never'.",
        ),
    ] = "missing",
    cases_dir: Annotated[
        Path,
        typer.Option("--cases-dir", help="Directory containing case categories."),
    ] = Path("cases"),
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory for run outputs."),
    ] = Path("runs"),
    report: Annotated[
        bool,
        typer.Option("--report", help="Generate HTML report after run."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render templates and generate case dir, but do not execute solver.",
        ),
    ] = False,
    storage: Annotated[
        str,
        typer.Option(
            "--storage",
            help="Storage backend: 'json' (default) or 'sqlite'.",
        ),
    ] = "json",
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="SQLite database path (only used with --storage sqlite). "
            "Default: <runs-dir>/cfdb.db",
        ),
    ] = None,
) -> None:
    """Run a specified case with a given solver and backend."""
    from cfdb.core.runner import Runner

    # P2-b: validate docker backend options
    if backend == "docker" and not image:
        typer.echo(
            "[FAIL] --backend docker requires --image (e.g. --image openfoam/openfoam:v2406)",
            err=True,
        )
        raise typer.Exit(code=1)
    if pull not in ("always", "missing", "never"):
        typer.echo(
            f"[FAIL] --pull must be one of: always, missing, never (got '{pull}')",
            err=True,
        )
        raise typer.Exit(code=1)

    registry = CaseRegistry(cases_dir)

    # P2-a: Select storage backend
    if storage == "sqlite":
        from cfdb.storage.sqlite_repo import SqliteRepository

        actual_db_path = db_path if db_path is not None else (runs_dir / "cfdb.db")
        repo = SqliteRepository(actual_db_path, runs_root=runs_dir)
    else:
        repo = JsonManifestRepository(runs_dir)

    runner = Runner(registry, repo, runs_dir)

    cli_args: dict[str, str] = {
        "case": case,
        "solver": solver,
        "backend": backend,
        "storage": storage,
    }
    if dry_run:
        cli_args["dry_run"] = "true"
    if db_path is not None:
        cli_args["db_path"] = str(db_path)
    if image is not None:
        cli_args["image"] = image
    cli_args["pull"] = pull

    # P2-b: build backend_options for Docker
    backend_options: dict[str, Any] | None = None
    if backend == "docker":
        backend_options = {"image": image, "pull_policy": pull}

    manifest = runner.execute(
        case_id=case,
        solver=solver,
        backend=backend,
        backend_options=backend_options,
        generate_report=report,
        cli_args=cli_args,
        dry_run=dry_run,
    )

    typer.echo("=" * 60)
    typer.echo(f"Run ID:    {manifest.run_id}")
    typer.echo(f"Case:      {manifest.case_id}")
    typer.echo(f"Solver:    {manifest.solver}", nl=False)
    if manifest.solver_version:
        typer.echo(f" ({manifest.solver_version})")
    else:
        typer.echo("")
    typer.echo(f"Backend:   {manifest.backend}")
    # P2-b: print image + digest when Docker backend
    if manifest.backend == "docker" and manifest.backend_options:
        opts = manifest.backend_options
        typer.echo(f"Image:     {opts.get('image', '?')} (pull: {opts.get('pull_policy', '?')})")
    typer.echo(f"Status:    {manifest.status}")
    typer.echo(f"Wall Time: {manifest.timing.wall_time_sec:.3f}s")

    # P1-b: Print final residuals
    if manifest.final_residuals:
        res_parts = ", ".join(f"{k}={v:.2e}" for k, v in manifest.final_residuals.items())
        typer.echo(f"Residuals: {res_parts}")

    # P2-b: Print container_digest if available
    if manifest.container_digest:
        typer.echo(f"Digest:    {manifest.container_digest}")

    if manifest.dry_run_skipped_commands:
        typer.echo(f"[DRY-RUN] Skipped {len(manifest.dry_run_skipped_commands)} command(s):")
        for i, cmd in enumerate(manifest.dry_run_skipped_commands, 1):
            typer.echo(f"  [{i}] {cmd}")
    if manifest.error:
        typer.echo(f"Error:     {manifest.error}", err=True)
    typer.echo("=" * 60)

    raise typer.Exit(code=0 if manifest.status in ("success", "dry_run") else 1)


@app.command("report")
def report_cmd(
    run_dir: Annotated[
        Path,
        typer.Option("--run-dir", help="Run directory path containing manifest.json."),
    ],
) -> None:
    """Generate an HTML report for a completed run."""
    from cfdb.reporting.html import generate_html_report

    repo = JsonManifestRepository(run_dir.parent)
    run_id = run_dir.name
    try:
        manifest, metrics = repo.load_run(run_id)
    except KeyError:
        typer.echo(f"[FAIL] Run '{run_id}' not found in {run_dir.parent}", err=True)
        raise typer.Exit(code=1) from None

    # P2-a: Generate residual SVG if residuals_history available
    residuals_svg: str | None = None
    if manifest.residuals_history:
        from cfdb.reporting.svg_residuals import render_residual_svg

        residuals_svg = render_residual_svg(
            residuals=manifest.residuals_history,
            title=f"Residual Convergence — {manifest.case_id} ({manifest.solver})",
            log_scale=True,
        )

    html_path = generate_html_report(manifest, metrics, run_dir, residuals_svg=residuals_svg)
    typer.echo(f"[OK] Report generated: {html_path}")


# ============================================================================
# P2-b: cfdb data subcommands (DVC wrapper)
# ============================================================================


@data_app.command("status")
def data_status_cmd(
    cwd: Annotated[
        Path,
        typer.Option("--cwd", help="Working directory (defaults to current dir)."),
    ] = Path("."),
) -> None:
    """Show DVC status (which tracked files are missing or changed)."""
    from cfdb.data import DVCError, dvc_available, dvc_status

    if not dvc_available():
        typer.echo(
            "[WARN] DVC not installed. Install with: pip install dvc\n"
            "       See https://dvc.org/doc/install for details."
        )
        return

    try:
        status = dvc_status(cwd=cwd)
    except DVCError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    if not status:
        typer.echo("[OK] DVC workspace up to date — all tracked files present.")
    else:
        typer.echo(f"DVC status ({len(status)} item(s)):")
        for path, info in status.items():
            typer.echo(f"  {path}: {info}")


@data_app.command("pull")
def data_pull_cmd(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Specific .dvc targets (relative paths). If empty, pulls all."),
    ] = None,
    cwd: Annotated[
        Path,
        typer.Option("--cwd", help="Working directory (defaults to current dir)."),
    ] = Path("."),
) -> None:
    """Pull DVC-tracked data from remote (meshes, reference datasets)."""
    from cfdb.data import DVCError, dvc_available, dvc_pull

    if not dvc_available():
        typer.echo(
            "[FAIL] DVC not installed. Install with: pip install dvc",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        output = dvc_pull(targets=targets, cwd=cwd)
    except DVCError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo("[OK] DVC pull complete.")
    if output.strip():
        typer.echo(output.rstrip())


# ============================================================================
# P2-c: cfdb compare + cfdb report-sweep commands
# ============================================================================


@app.command("compare")
def compare_cmd(
    run_id1: Annotated[str, typer.Argument(help="First run ID to compare.")],
    run_id2: Annotated[str, typer.Argument(help="Second run ID to compare.")],
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory containing run outputs."),
    ] = Path("runs"),
    storage: Annotated[
        str,
        typer.Option("--storage", help="Storage backend: 'json' or 'sqlite'."),
    ] = "json",
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite database path (if --storage sqlite)."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", help="Output format: 'html' or 'text'."),
    ] = "html",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output path. Default: <runs-dir>/compare_<id1>_<id2>.html"),
    ] = None,
    cases_dir: Annotated[
        Path,
        typer.Option("--cases-dir", help="Cases directory (for tolerance lookup)."),
    ] = Path("cases"),
) -> None:
    """Compare two runs: QoI diff table + comparison SVG."""
    from cfdb.registry import CaseRegistry
    from cfdb.reporting.compare import (
        compare_runs,
        render_compare_html,
        render_compare_text,
    )

    if fmt not in ("html", "text"):
        typer.echo(f"[FAIL] --format must be 'html' or 'text' (got '{fmt}')", err=True)
        raise typer.Exit(code=1)

    # Load runs from repository
    if storage == "sqlite":
        from cfdb.storage.sqlite_repo import SqliteRepository

        actual_db_path = db_path if db_path is not None else (runs_dir / "cfdb.db")
        repo = SqliteRepository(actual_db_path, runs_root=runs_dir)
    else:
        repo = JsonManifestRepository(runs_dir)

    try:
        manifest1, metrics1 = repo.load_run(run_id1)
    except KeyError:
        typer.echo(f"[FAIL] Run '{run_id1}' not found in {runs_dir}", err=True)
        raise typer.Exit(code=1) from None
    try:
        manifest2, metrics2 = repo.load_run(run_id2)
    except KeyError:
        typer.echo(f"[FAIL] Run '{run_id2}' not found in {runs_dir}", err=True)
        raise typer.Exit(code=1) from None

    # Try to load case for tolerance lookup (only if both runs are same case)
    case = None
    if manifest1.case_id == manifest2.case_id:
        try:
            registry = CaseRegistry(cases_dir)
            case = registry.load(manifest1.case_id)
        except Exception:
            # If case can't be loaded, skip tolerance column gracefully
            pass

    comparisons = compare_runs(manifest1, metrics1, manifest2, metrics2, case=case)

    if fmt == "text":
        text_output = render_compare_text(manifest1, manifest2, comparisons)
        typer.echo(text_output)
        return

    # HTML format: build SVGs from residuals_history if available
    residual_svg = None
    if manifest1.residuals_history and manifest2.residuals_history:
        from cfdb.reporting.svg_compare import render_residual_comparison_svg

        combined = {
            manifest1.solver: manifest1.residuals_history,
            manifest2.solver: manifest2.residuals_history,
        }
        residual_svg = render_residual_comparison_svg(combined)

    html = render_compare_html(manifest1, manifest2, comparisons, residual_svg=residual_svg)

    out_path = out or (runs_dir / f"compare_{run_id1}_{run_id2}.html")
    out_path.write_text(html, encoding="utf-8")
    typer.echo(f"[OK] Comparison report: {out_path}")


@app.command("report-sweep")
def report_sweep_cmd(
    case_id: Annotated[
        str,
        typer.Option("--case-id", help="Case ID prefix to match (e.g. 'naca0012')."),
    ],
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory containing run outputs."),
    ] = Path("runs"),
    storage: Annotated[
        str,
        typer.Option("--storage", help="Storage backend: 'json' or 'sqlite'."),
    ] = "json",
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite database path (if --storage sqlite)."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output HTML path. Default: <runs-dir>/sweep_<case_id>.html"),
    ] = None,
    polar: Annotated[
        bool,
        typer.Option("--polar", help="Also render polar curve SVG (requires cl/cd QoIs)."),
    ] = False,
) -> None:
    """Generate a multi-solver HTML report aggregating all runs of a case family."""
    if storage == "sqlite":
        from cfdb.storage.sqlite_repo import SqliteRepository

        actual_db_path = db_path if db_path is not None else (runs_dir / "cfdb.db")
        repo = SqliteRepository(actual_db_path, runs_root=runs_dir)
    else:
        repo = JsonManifestRepository(runs_dir)

    # Find all runs whose case_id starts with the given prefix
    all_runs = repo.list_runs()
    matched = [r for r in all_runs if r.case_id.startswith(case_id)]

    if not matched:
        typer.echo(
            f"[FAIL] No runs matching case_id prefix '{case_id}' in {runs_dir}",
            err=True,
        )
        raise typer.Exit(code=1)

    # Load full manifest + metrics for each
    manifests = []
    metrics_list = []
    for run_manifest_summary in matched:
        m, met = repo.load_run(run_manifest_summary.run_id)
        manifests.append(m)
        metrics_list.append(met)

    # Build polar SVG if requested
    polar_svg = None
    if polar:
        from cfdb.post.qoi_extractor import load_ladson_polar
        from cfdb.reporting.svg_polar import PolarCurve, PolarPoint, render_polar_svg

        solver_points: dict[str, list[PolarPoint]] = {}
        for m, met in zip(manifests, metrics_list, strict=False):
            alpha_str = m.cli_args.get("alpha") if m.cli_args else None
            if alpha_str is None:
                continue
            try:
                alpha = float(alpha_str)
            except ValueError:
                continue
            # P3-hotfix: read computed values (real Cl/Cd), not relative errors.
            # Fallback: if qoi_computed_values is None (old data), skip the point.
            cl = met.qoi_computed_values.get("cl") if met.qoi_computed_values else None
            cd = met.qoi_computed_values.get("cd") if met.qoi_computed_values else None
            if cl is not None and cd is not None:
                solver_points.setdefault(m.solver, []).append(
                    PolarPoint(alpha_deg=alpha, cl=cl, cd=cd)
                )

        curves = [PolarCurve(solver=s, points=pts) for s, pts in solver_points.items()]

        reference = None
        ref_path = Path("cases") / "validation" / case_id / "reference" / "ladson_polar.csv"
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
                curves=curves,
                reference=reference,
                title=f"Polar — {case_id}",
            )

    from cfdb.reporting.html import generate_multi_solver_report

    out_path = out or (runs_dir / f"sweep_{case_id}.html")
    generate_multi_solver_report(
        manifests=manifests,
        metrics_list=metrics_list,
        output_path=out_path,
        polar_svg=polar_svg,
        title=f"Sweep Report — {case_id}",
    )
    typer.echo(f"[OK] Sweep report ({len(manifests)} runs): {out_path}")


# ============================================================================
# P4.2: cfdb serve — Web dashboard
# ============================================================================


@app.command("serve")
def serve_cmd(
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory containing run outputs."),
    ] = Path("runs"),
    cases_dir: Annotated[
        Path,
        typer.Option("--cases-dir", help="Directory containing case categories."),
    ] = Path("cases"),
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind the server to."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Port to listen on."),
    ] = 8080,
    storage: Annotated[
        str,
        typer.Option(
            "--storage",
            help="Storage backend: 'json' (default) or 'sqlite'.",
        ),
    ] = "json",
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="SQLite database path (only used with --storage sqlite). "
            "Default: <runs-dir>/cfdb.db",
        ),
    ] = None,
    export_dir: Annotated[
        Path | None,
        typer.Option(
            "--export",
            help="Export static site to this directory instead of starting the server.",
        ),
    ] = None,
) -> None:
    """Start the CFD-Benchmark web dashboard or export a static site."""
    from cfdb.registry import CaseRegistry
    from cfdb.web import create_app

    # Validate storage option
    if storage not in ("json", "sqlite"):
        typer.echo(f"[FAIL] --storage must be 'json' or 'sqlite' (got '{storage}')", err=True)
        raise typer.Exit(code=1)

    # Select repository
    if storage == "sqlite":
        from cfdb.storage.sqlite_repo import SqliteRepository

        actual_db_path = db_path if db_path is not None else (runs_dir / "cfdb.db")
        repo = SqliteRepository(actual_db_path, runs_root=runs_dir)
    else:
        repo = JsonManifestRepository(runs_dir)

    registry = CaseRegistry(cases_dir)

    if export_dir is not None:
        # Static export mode
        _export_static_site(repo, registry, runs_dir, cases_dir, export_dir, storage, db_path)
        return

    # Server mode
    typer.echo(f"Starting CFD-Benchmark Dashboard at http://{host}:{port}")
    typer.echo(f"  Runs: {runs_dir.absolute()}")
    typer.echo(f"  Cases: {cases_dir.absolute()}")
    typer.echo(f"  Storage: {storage}")
    typer.echo("Press Ctrl+C to stop.")

    import uvicorn

    app_instance = create_app(repo, registry, runs_dir, cases_dir)
    uvicorn.run(app_instance, host=host, port=port, log_level="info")


def _export_static_site(
    repo: object,
    registry: CaseRegistry,
    runs_dir: Path,
    cases_dir: Path,
    export_dir: Path,
    storage: str,
    db_path: Path | None,
) -> None:
    """Export a full static site from the dashboard.

    Strategy A: Pre-built HTML reports using existing reporting functions.
    Strategy B: httpx TestClient crawl for dashboard pages.
    Both strategies run; results are deduplicated by path.
    """
    import json

    from cfdb.reporting.html import generate_html_report
    from cfdb.reporting.svg_residuals import render_residual_svg

    export_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Exporting static site to {export_dir.absolute()}...")

    # --- Strategy A: Pre-built reports ---

    # Ensure all runs use the same list_runs call
    try:
        run_list = repo.list_runs()  # type: ignore[union-attr]
    except TypeError:
        run_list = repo.list_runs()  # type: ignore[union-attr]

    # A1: Per-run reports
    runs_export_dir = export_dir / "runs"
    runs_export_dir.mkdir(exist_ok=True)
    for manifest_summary in run_list:
        try:
            manifest, metrics = repo.load_run(manifest_summary.run_id)  # type: ignore[union-attr]
        except Exception:
            continue
        run_dir_path = runs_export_dir / manifest.run_id
        run_dir_path.mkdir(exist_ok=True)
        residuals_svg = None
        if manifest.residuals_history:
            residuals_svg = render_residual_svg(
                residuals=manifest.residuals_history,
                title=f"Residual Convergence — {manifest.case_id} ({manifest.solver})",
                log_scale=True,
            )
        generate_html_report(manifest, metrics, run_dir_path, residuals_svg=residuals_svg)

    # A2: Case listing
    cases_export_dir = export_dir / "cases"
    cases_export_dir.mkdir(exist_ok=True)
    cases = registry.list_all()
    run_counts: dict[str, int] = {}
    for r in run_list:
        run_counts[r.case_id] = run_counts.get(r.case_id, 0) + 1

    # A3: Sweep reports for unique case prefixes
    case_prefixes = set()
    for r in run_list:
        # Take the base case family (everything before the last '_alpha' or '_mesh' etc)
        # Simple heuristic: first underscore_not_numeric group
        parts = r.case_id.split("_")
        prefix_parts = []
        for part in parts:
            if part.lstrip("-").isdigit():
                break
            prefix_parts.append(part)
        if prefix_parts:
            case_prefixes.add("_".join(prefix_parts))
    # Also include full case_ids as prefixes
    case_prefixes |= set(c.id for c in cases)

    for prefix in sorted(case_prefixes):
        matched = [r for r in run_list if r.case_id.startswith(prefix)]
        if len(matched) >= 2:
            try:
                from cfdb.reporting.html import generate_multi_solver_report

                manifests = []
                metrics_list = []
                for m in matched:
                    try:
                        man, met = repo.load_run(m.run_id)  # type: ignore[union-attr]
                        manifests.append(man)
                        metrics_list.append(met)
                    except Exception:
                        continue
                if manifests:
                    sweep_dir = export_dir / "sweep" / prefix
                    sweep_dir.mkdir(parents=True, exist_ok=True)
                    generate_multi_solver_report(
                        manifests,
                        metrics_list,
                        sweep_dir / "index.html",
                        title=f"Sweep Report — {prefix}",
                    )
            except Exception as e:
                logger.warning("Failed to generate sweep for %s: %s", prefix, e)

    # --- Strategy B: Starlette TestClient crawl ---
    try:
        from starlette.testclient import TestClient

        from cfdb.web import create_app

        app = create_app(repo, registry, runs_dir, cases_dir)

        with TestClient(app) as client:
            # Pages to crawl
            routes_to_crawl = [
                "/runs",
                "/cases",
                "/compare",
                "/sweep",
            ]

            for route in routes_to_crawl:
                try:
                    resp = client.get(route)
                    if resp.status_code == 200:
                        route_path = route.lstrip("/")
                        out_dir = export_dir / route_path
                        out_dir.mkdir(parents=True, exist_ok=True)
                        (out_dir / "index.html").write_text(resp.text, encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to crawl %s: %s", route, e)

            # Crawl individual run pages
            for manifest_summary in run_list:
                try:
                    resp = client.get(f"/runs/{manifest_summary.run_id}")
                    if resp.status_code == 200:
                        out_dir = runs_export_dir / manifest_summary.run_id
                        out_dir.mkdir(parents=True, exist_ok=True)
                        (out_dir / "index.html").write_text(resp.text, encoding="utf-8")
                except Exception:
                    continue

            # Crawl individual case pages
            for case in cases:
                try:
                    resp = client.get(f"/cases/{case.id}")
                    if resp.status_code == 200:
                        out_dir = cases_export_dir / case.id
                        out_dir.mkdir(parents=True, exist_ok=True)
                        (out_dir / "index.html").write_text(resp.text, encoding="utf-8")
                except Exception:
                    continue
    except ImportError:
        typer.echo("  [WARN] httpx not installed — Strategy B (dashboard crawl) skipped.")

    # Copy static files
    static_src = Path(__file__).parent / "web" / "static"
    static_dst = export_dir / "static"
    if static_src.exists():
        _copy_tree(static_src, static_dst)

    # Write JSON API data files
    api_dir = export_dir / "api"
    api_dir.mkdir(exist_ok=True)

    runs_data = []
    for r in run_list:
        runs_data.append(
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
        )
    (api_dir / "runs.json").write_text(json.dumps(runs_data, indent=2), encoding="utf-8")

    cases_data = [
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
    (api_dir / "cases.json").write_text(json.dumps(cases_data, indent=2), encoding="utf-8")

    # Write top-level index.html (redirect to runs)
    index_html = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">'
    index_html += '\n<meta http-equiv="refresh" content="0;url=runs/">'
    index_html += (
        '\n</head>\n<body><p><a href="runs/">CFD-Benchmark Dashboard</a></p></body>\n</html>'
    )
    (export_dir / "index.html").write_text(index_html, encoding="utf-8")

    typer.echo(f"[OK] Static site exported to {export_dir.absolute()}")


def _copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy a directory tree."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            target.write_bytes(item.read_bytes())


# ============================================================================
# P4 (Architecture v4.0): trust-platform commands
# ============================================================================

failures_app = typer.Typer(
    name="failures",
    help="Failure mode library: ingest, list, annotate (P4-C).",
    no_args_is_help=True,
)
app.add_typer(failures_app, name="failures")

baseline_app = typer.Typer(
    name="baseline",
    help="Baseline governance: list, human-signed promote (P4-D).",
    no_args_is_help=True,
)
app.add_typer(baseline_app, name="baseline")

agent_eval_app = typer.Typer(
    name="agent-eval",
    help="Frozen-ruler agent submission scoring (P4-E).",
    no_args_is_help=True,
)
app.add_typer(agent_eval_app, name="agent-eval")

_CasesDirOption = Annotated[
    Path,
    typer.Option("--cases-dir", help="Directory containing case categories."),
]
_RunsDirOption = Annotated[
    Path,
    typer.Option("--runs-dir", help="Directory containing run outputs."),
]
_BaselinesOption = Annotated[
    Path,
    typer.Option("--baselines", help="Path to baselines.json."),
]
_FailureLibraryOption = Annotated[
    Path,
    typer.Option("--library", help="Path to the failure library JSON file."),
]
_AgentbenchDirOption = Annotated[
    Path,
    typer.Option("--agentbench-dir", help="Directory holding contracts and ledgers."),
]

_GATE_EXIT_CODES: dict[str, int] = {
    "PASS": 0,
    "REGRESSION": 1,
    "INVALID_RUN": 1,
    "NO_BASELINE": 2,
    "TAMPERED": 3,
}


@app.command("provenance")
def provenance_cmd(
    cases_dir: _CasesDirOption = Path("cases"),
) -> None:
    """Audit reference-data provenance for all cases (P4-A)."""
    from cfdb.provenance import ProvenanceRecord, audit_all

    records = audit_all(cases_dir)
    if not records:
        typer.echo(f"No cases found under {cases_dir}.")
        return

    def hash_state(record: ProvenanceRecord) -> str:
        """Summarize per-file hash verification into one table cell."""
        if not record.file_status:
            return "no-files"
        bad = sorted(s for s in record.file_status.values() if s != "ok")
        if not bad:
            return "ok"
        counts: dict[str, int] = {}
        for status in bad:
            counts[status] = counts.get(status, 0) + 1
        return ",".join(f"{k}:{v}" for k, v in sorted(counts.items()))

    typer.echo(f"{'ID':<25} {'Type':<13} {'Honesty':<23} {'Hashes':<14} Citation")
    typer.echo("-" * 100)
    for record in records:
        citation = record.citation or "-"
        typer.echo(
            f"{record.case_id:<25} {record.reference_type:<13} "
            f"{record.honesty:<23} {hash_state(record):<14} {citation}"
        )
    typer.echo(f"\nTotal: {len(records)} case(s)")


@app.command("trust")
def trust_cmd(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID to profile.")],
    solver: Annotated[str, typer.Option("--solver", "-s", help="Solver name to profile.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the profile as JSON instead of a table."),
    ] = False,
    svg: Annotated[
        Path | None,
        typer.Option("--svg", help="Also write the five-axis radar SVG to this path."),
    ] = None,
    cases_dir: _CasesDirOption = Path("cases"),
    runs_dir: _RunsDirOption = Path("runs"),
) -> None:
    """Build the TrustProfile for one (case, solver) pair (P4-B)."""
    from cfdb.provenance import audit_case
    from cfdb.trust import DIMENSION_NAMES, build_profile
    from cfdb.trust.radar_svg import render as render_radar

    registry = CaseRegistry(cases_dir)
    try:
        spec = registry.load(case)
        case_dir = registry.get_case_dir(case)
    except KeyError as e:
        typer.echo(f"[FAIL] Unknown case '{case}': {e}", err=True)
        raise typer.Exit(code=1) from e

    # Honesty banner is composed here from the provenance audit (the trust
    # module deliberately does not import provenance).
    honesty = audit_case(case_dir).honesty
    repo = JsonManifestRepository(runs_dir)
    profile = build_profile(spec, solver, repo, honesty=honesty)

    if svg is not None:
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text(render_radar(profile), encoding="utf-8")

    if json_output:
        typer.echo(profile.model_dump_json(indent=2))
        return

    typer.echo(f"Trust Profile — {profile.case_id} / {profile.solver}")
    typer.echo(f"Honesty: {profile.honesty}")
    typer.echo(f"Runs:    {profile.n_runs}")
    typer.echo("-" * 72)
    for name in DIMENSION_NAMES:
        dim = profile.dimension(name)
        shown = f"{dim.score:.3f}" if dim.score is not None else "n/a (insufficient data)"
        typer.echo(f"{name:<17} {shown}")
        for line in dim.evidence:
            typer.echo(f"                  - {line}")
    for note in profile.notes:
        typer.echo(f"note: {note}")
    if svg is not None:
        typer.echo(f"[OK] Radar SVG: {svg}")


@failures_app.command("ingest")
def failures_ingest_cmd(
    runs_dir: _RunsDirOption = Path("runs"),
    library: _FailureLibraryOption = Path("failures/library.json"),
) -> None:
    """Scan runs and ingest failures into the append-only library.

    Exit codes: 0 = clean scan; 1 = one or more runs could not be ingested
    (missing runs directory, missing/corrupt manifest.json). Partial results
    are still persisted and summarized before the non-zero exit.
    """
    from cfdb.failures import FailureLibrary

    lib = FailureLibrary(library)
    summary = lib.ingest(runs_dir)
    typer.echo(
        f"Scanned {summary.scanned} run(s): {summary.new_records} new failure(s), "
        f"{summary.updated_records} recurrence(s), {summary.passed} verified pass, "
        f"{summary.dry_run_skipped} dry-run skipped (executed nothing, verified nothing), "
        f"{summary.already_ingested} already ingested."
    )
    for error in summary.errors:
        typer.echo(f"[FAIL] {error}", err=True)
    typer.echo(f"Library: {library}")
    if len(summary.errors) > 0:
        typer.echo(
            f"[FAIL] {len(summary.errors)} error(s) during ingest — "
            "partial results above were persisted (exit 1).",
            err=True,
        )
        raise typer.Exit(code=1)


@failures_app.command("list")
def failures_list_cmd(
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Filter by failure mode (e.g. MESH_FAILURE)."),
    ] = None,
    library: _FailureLibraryOption = Path("failures/library.json"),
) -> None:
    """List failure records, optionally filtered by mode."""
    from typing import cast

    from cfdb.failures import FAILURE_MODES, FailureLibrary, FailureMode

    if mode is not None and mode not in FAILURE_MODES:
        typer.echo(
            f"[FAIL] Unknown mode '{mode}'. Valid modes: {', '.join(FAILURE_MODES)}",
            err=True,
        )
        raise typer.Exit(code=1)

    lib = FailureLibrary(library)
    records = lib.records(mode=cast("FailureMode | None", mode))
    if not records:
        typer.echo("Failure library is empty (no matching records).")
        return

    typer.echo(f"{'Fingerprint':<18} {'Case':<20} {'Solver':<10} {'Mode':<19} {'Count':<6} Guard")
    typer.echo("-" * 100)
    for record in records:
        guard = record.guard or "-"
        typer.echo(
            f"{record.fingerprint:<18} {record.case_id:<20} {record.solver:<10} "
            f"{record.mode:<19} {record.count:<6} {guard}"
        )
        typer.echo(f"{'':<18} signature: {record.signature}  last_seen: {record.last_seen}")
    typer.echo(f"\nTotal: {len(records)} record(s)")


@failures_app.command("annotate")
def failures_annotate_cmd(
    fingerprint: Annotated[str, typer.Argument(help="Fingerprint of the record to annotate.")],
    guard: Annotated[
        str,
        typer.Option("--guard", help="Human-written guard note (how to prevent this failure)."),
    ],
    library: _FailureLibraryOption = Path("failures/library.json"),
) -> None:
    """Attach a human-written guard note to a failure record."""
    from cfdb.failures import FailureLibrary

    lib = FailureLibrary(library)
    try:
        record = lib.annotate(fingerprint, guard)
    except (KeyError, ValueError) as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"[OK] Guard noted for {record.fingerprint} ({record.case_id}/{record.solver}):")
    typer.echo(f"  {record.guard}")


@baseline_app.command("list")
def baseline_list_cmd(
    baselines: _BaselinesOption = Path("baselines/baselines.json"),
    runs_dir: _RunsDirOption = Path("runs"),
) -> None:
    """List promoted baselines and the public regression margin."""
    from cfdb.regression import BaselineStore
    from cfdb.regression.baseline import BaselineFileError

    store = BaselineStore(baselines, runs_dir)
    try:
        data = store.load()
    except BaselineFileError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=3) from e
    margin = data.regression_margin
    typer.echo(f"Regression margin: absolute={margin.absolute}, relative={margin.relative}")
    if not data.baselines:
        typer.echo("No baselines promoted yet.")
        return

    typer.echo(f"{'Case':<22} {'Solver':<10} {'Run ID':<38} {'Promoted by':<14} Promoted at")
    typer.echo("-" * 110)
    for key in sorted(data.baselines):
        entry = data.baselines[key]
        typer.echo(
            f"{entry.case_id:<22} {entry.solver:<10} {entry.run_id:<38} "
            f"{entry.promoted_by:<14} {entry.promoted_at}"
        )
    typer.echo(f"\nTotal: {len(data.baselines)} baseline(s)")


@baseline_app.command("promote")
def baseline_promote_cmd(
    run_id: Annotated[str, typer.Argument(help="Run ID to promote as baseline.")],
    engineer: Annotated[
        str,
        typer.Option(
            "--engineer",
            help="Name of the engineer signing the promotion (required, no default).",
        ),
    ],
    baselines: _BaselinesOption = Path("baselines/baselines.json"),
    runs_dir: _RunsDirOption = Path("runs"),
) -> None:
    """Promote a passing run to baseline (human-signed, fail-closed)."""
    from cfdb.regression import BaselineStore
    from cfdb.regression.baseline import BaselineFileError

    store = BaselineStore(baselines, runs_dir)
    try:
        entry = store.promote(run_id, engineer)
    except BaselineFileError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=3) from e
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(
        f"[OK] Promoted run '{entry.run_id}' as baseline for "
        f"{entry.case_id}/{entry.solver} (by {entry.promoted_by})."
    )
    typer.echo(f"  metrics_sha256: {entry.metrics_sha256}")


@app.command("gate")
def gate_cmd(
    run_id: Annotated[str, typer.Argument(help="Candidate run ID to gate.")],
    baselines: _BaselinesOption = Path("baselines/baselines.json"),
    runs_dir: _RunsDirOption = Path("runs"),
) -> None:
    """Evaluate a run against its promoted baseline (P4-D regression gate).

    Exit codes: 0=PASS, 1=REGRESSION/INVALID_RUN, 2=NO_BASELINE, 3=TAMPERED.
    A corrupt/unreadable baselines.json also exits 3 (fail-closed: a broken
    anchor store must never degrade into NO_BASELINE or a crash).
    """
    from cfdb.regression import BaselineStore, evaluate
    from cfdb.regression.baseline import BaselineFileError

    store = BaselineStore(baselines, runs_dir)
    try:
        verdict = evaluate(run_id, store)
    except BaselineFileError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=_GATE_EXIT_CODES["TAMPERED"]) from e

    typer.echo(f"Gate verdict for run '{run_id}': {verdict.verdict}")
    for qoi, delta in sorted(verdict.deltas.items()):
        typer.echo(f"  delta[{qoi}] = {delta:+.6g}")
    for reason in verdict.reasons:
        typer.echo(f"  {reason}")
    raise typer.Exit(code=_GATE_EXIT_CODES[verdict.verdict])


@agent_eval_app.command("init")
def agent_eval_init_cmd(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID to freeze.")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-anchor an existing contract (changes the ruler; prints a "
            "loud warning with the old and new ruler ids).",
        ),
    ] = False,
    cases_dir: _CasesDirOption = Path("cases"),
    agentbench_dir: _AgentbenchDirOption = Path("agentbench"),
) -> None:
    """Create the frozen scoring contract for a case (hashes the ruler now).

    An existing contract is never silently overwritten: re-anchoring the
    ruler requires --force and is announced loudly (old -> new ruler id),
    because scores taken with different rulers are not comparable.
    """
    import hashlib

    from cfdb.agentbench import init_contract, save_contract

    contract_path = agentbench_dir / case / "contract.json"
    old_ruler_id: str | None = None
    if contract_path.exists():
        if force is False:
            typer.echo(
                f"[FAIL] Scoring contract already exists at {contract_path}: "
                "refusing to overwrite the frozen ruler. "
                "Pass --force to re-anchor deliberately.",
                err=True,
            )
            raise typer.Exit(code=1)
        old_ruler_id = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]

    registry = CaseRegistry(cases_dir)
    try:
        contract = init_contract(case, registry)
    except (KeyError, FileNotFoundError, ValueError) as e:
        # ValueError covers checker-admission refusals and contract
        # validation failures (Codex R1 P2): the normal init path must
        # exit with a structured [FAIL], never an uncaught traceback.
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    save_contract(contract, contract_path, force=force)
    new_ruler_id = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]
    if old_ruler_id is not None:
        typer.echo("!" * 64, err=True)
        typer.echo(
            f"[WARNING] RULER RE-ANCHORED for '{case}': #{old_ruler_id} -> #{new_ruler_id}",
            err=True,
        )
        typer.echo(
            "[WARNING] Scores in the existing ledger were measured with the "
            "old ruler and are NOT comparable to new scores.",
            err=True,
        )
        typer.echo("!" * 64, err=True)
    typer.echo(f"[OK] Scoring contract for '{case}': {contract_path}")
    typer.echo(f"  Ruler id: #{new_ruler_id}")
    typer.echo(f"  Frozen items: {len(contract.frozen)}")
    typer.echo(f"  Weights: {contract.weights}")
    typer.echo(f"  Validity gates: {', '.join(contract.validity_gates)}")


@agent_eval_app.command("score")
def agent_eval_score_cmd(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID to score against.")],
    submission: Annotated[
        Path,
        typer.Option("--submission", help="Submission directory holding qoi.json."),
    ],
    cases_dir: _CasesDirOption = Path("cases"),
    agentbench_dir: _AgentbenchDirOption = Path("agentbench"),
) -> None:
    """Score an agent submission against the frozen contract.

    Exit code 3 means the frozen ruler drifted; scoring was refused.
    A missing submission directory or an unparsable qoi.json exits 1
    WITHOUT writing to the ledger: the ledger records scoring events on
    real submissions (including valid submissions that fail gates), never
    void inputs that could not be read at all.
    """
    import json

    from cfdb.agentbench import (
        EXIT_FROZEN_DRIFT,
        FrozenDriftError,
        load_contract,
        score_submission,
    )

    # Void-input pre-gate: nothing below this block may touch the ledger
    # unless the submission is at least structurally readable. What counts
    # as "readable" is domain-aware (v5.0): a cfd submission must carry a
    # parsable qoi.json; a coding/agentic submission is a code/artifact
    # directory and merely must not be empty.
    if not submission.is_dir():
        typer.echo(
            f"[FAIL] Submission directory not found: {submission} "
            "(void input — nothing written to the ledger).",
            err=True,
        )
        raise typer.Exit(code=1)

    pre_registry = CaseRegistry(cases_dir)
    try:
        pre_spec = pre_registry.load(case)
    except (KeyError, ValueError, FileNotFoundError) as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    if pre_spec.domain == "cfd":
        qoi_path = submission / "qoi.json"
        try:
            parsed_qoi = json.loads(qoi_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            typer.echo(
                f"[FAIL] Cannot parse {qoi_path}: {e} "
                "(void input — nothing written to the ledger).",
                err=True,
            )
            raise typer.Exit(code=1) from e
        if not isinstance(parsed_qoi, dict):
            typer.echo(
                f"[FAIL] {qoi_path} must contain a JSON object "
                "(void input — nothing written to the ledger).",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if next(submission.iterdir(), None) is None:
            typer.echo(
                f"[FAIL] Submission directory is empty: {submission} "
                "(void input — nothing written to the ledger).",
                err=True,
            )
            raise typer.Exit(code=1)

    contract_path = agentbench_dir / case / "contract.json"
    if not contract_path.exists():
        typer.echo(
            f"[FAIL] No contract at {contract_path} — run 'cfdb agent-eval init -c {case}' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    registry = CaseRegistry(cases_dir)
    try:
        contract = load_contract(contract_path)
        spec = registry.load(case)
        case_dir = registry.get_case_dir(case)
    except (KeyError, ValueError, FileNotFoundError) as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    import hashlib

    ledger_path = agentbench_dir / case / "ledger.jsonl"
    current_ruler = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]
    try:
        result = score_submission(
            contract,
            spec,
            case_dir,
            submission,
            ledger_path=ledger_path,
            ruler_id=current_ruler,
        )
    except FrozenDriftError as e:
        typer.echo("[FAIL] Frozen ruler drifted — scoring refused (exit 3):", err=True)
        for key in e.drifted:
            typer.echo(f"  drifted: {key}", err=True)
        raise typer.Exit(code=EXIT_FROZEN_DRIFT) from e
    except ValueError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    shown_score = f"{result.score:.6g}" if result.score is not None else "None (not rankable)"
    typer.echo(f"Submission: {result.submission_id}")
    typer.echo(f"Valid:      {result.valid}")
    typer.echo(f"Score:      {shown_score}")
    for gate, ok in result.gates.items():
        typer.echo(f"  gate {gate}: {'pass' if ok is True else 'FAIL'}")
    for metric, contribution in result.breakdown.items():
        typer.echo(f"  breakdown {metric}: {contribution:.6g}")
    for note in result.notes:
        typer.echo(f"  note: {note}")
    typer.echo(f"  ruler: #{current_ruler}")
    typer.echo(f"[OK] Appended to ledger: {ledger_path}")


@agent_eval_app.command("ledger")
def agent_eval_ledger_cmd(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID whose ledger to show.")],
    agentbench_dir: _AgentbenchDirOption = Path("agentbench"),
) -> None:
    """Print the append-only scoring ledger for a case."""
    from cfdb.agentbench import read_ledger

    ledger_path = agentbench_dir / case / "ledger.jsonl"
    try:
        entries = read_ledger(ledger_path)
    except ValueError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    if not entries:
        typer.echo(f"Ledger is empty (no submissions scored for '{case}').")
        return

    import hashlib

    contract_path = agentbench_dir / case / "contract.json"
    current_ruler: str | None = None
    if contract_path.exists():
        current_ruler = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]

    typer.echo(f"{'Submission':<28} {'Valid':<7} {'Score':<14} {'Ruler':<10} Scored at")
    typer.echo("-" * 96)
    stale = 0
    for entry in entries:
        shown = f"{entry.score:.6g}" if entry.score is not None else "-"
        lineage = f"#{entry.ruler_id}" if entry.ruler_id else "unknown"
        if current_ruler is not None and entry.ruler_id != current_ruler:
            lineage += "*"
            stale += 1
        typer.echo(
            f"{entry.submission_id:<28} {str(entry.valid):<7} {shown:<14} "
            f"{lineage:<10} {entry.scored_at}"
        )
    typer.echo(f"\nTotal: {len(entries)} scoring event(s)")
    if stale > 0:
        typer.echo(
            f"  * {stale} row(s) were scored under a different/unknown ruler "
            "and are excluded from ranking (like-with-like only)."
        )


@agent_eval_app.command("passk")
def agent_eval_passk_cmd(
    case: Annotated[str, typer.Option("--case", "-c", help="Case ID whose ledger to analyze.")],
    k: Annotated[int, typer.Option("--k", "-k", help="Number of draws for pass@k.")] = 1,
    cases_dir: _CasesDirOption = Path("cases"),
    agentbench_dir: _AgentbenchDirOption = Path("agentbench"),
) -> None:
    """Compute unbiased pass@k over the case ledger (current ruler only).

    Samples are unique attempts identified by submission CONTENT (never by
    directory basename); rescoring identical content collapses into one
    attempt, and an attempt passes only if every row is rankable AND
    carries the domain's binary success gate True. The frozen ruler is
    verified against the live case tree BEFORE its domain is trusted —
    a drifted case.yaml (e.g. relabeled to a pass@k-capable domain) is
    refused with exit 3, never used to legitimize the metric. Exits 1 when
    not honestly computable (non-binary ruler, or fewer than k attempts —
    never extrapolated).
    """
    import hashlib

    from cfdb.agentbench import (
        EXIT_FROZEN_DRIFT,
        load_contract,
        read_ledger,
        verify_frozen,
    )
    from cfdb.agentbench.contract import missing_required_anchors
    from cfdb.agentbench.scorer import pass_at_k

    registry = CaseRegistry(cases_dir)
    contract_path = agentbench_dir / case / "contract.json"
    if not contract_path.exists():
        typer.echo(
            f"[FAIL] No contract at {contract_path} — pass@k is only meaningful "
            "against a frozen ruler.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        contract = load_contract(contract_path)
        spec = registry.load(case)
        case_dir = registry.get_case_dir(case)
    except (KeyError, ValueError, FileNotFoundError) as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    # Bind the domain decision to the FROZEN ruler (Codex R6-R1 P1): only a
    # case tree that still matches the anchored case.yaml (and carries every
    # mandatory anchor) may tell us its domain — otherwise a drifted
    # `domain:` line could turn continuous scores into fabricated "passes".
    drifted = verify_frozen(contract, case_dir)
    if len(drifted) > 0:
        typer.echo("[FAIL] Frozen ruler drifted — pass@k refused (exit 3):", err=True)
        for key in drifted:
            typer.echo(f"  drifted: {key}", err=True)
        raise typer.Exit(code=EXIT_FROZEN_DRIFT)
    missing = missing_required_anchors(contract, spec, case_dir)
    if len(missing) > 0:
        typer.echo("[FAIL] Ruler incomplete — pass@k refused (exit 3):", err=True)
        for key in missing:
            typer.echo(f"  missing anchor: {key}", err=True)
        raise typer.Exit(code=EXIT_FROZEN_DRIFT)

    current_ruler = hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8]

    ledger_path = agentbench_dir / case / "ledger.jsonl"
    try:
        entries = read_ledger(ledger_path)
    except ValueError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    try:
        result = pass_at_k(
            entries, k, ruler_id=current_ruler, contract=contract, domain=spec.domain
        )
    except ValueError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e

    in_lineage = [e for e in entries if e.ruler_id == current_ruler]
    legacy = sum(1 for e in in_lineage if e.attempt_id is None)
    if result is None:
        unique = len({e.attempt_id for e in in_lineage if e.attempt_id is not None})
        typer.echo(
            f"[FAIL] pass@{k} not computable for '{case}': {unique} unique "
            f"attempt(s) under current ruler #{current_ruler}, need at least {k} "
            "(never extrapolated)."
            + (
                f" {legacy} row(s) lack a content identity (legacy) and cannot be samples."
                if legacy > 0
                else ""
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    value, n, c = result
    typer.echo(f"pass@{k} for '{case}' under ruler #{current_ruler}: {value:.6g}")
    typer.echo(f"  samples: {n} unique attempt(s) in current lineage, {c} pass(es)")
    collapsed = len(in_lineage) - legacy - n
    if collapsed > 0:
        typer.echo(
            f"  collapsed: {collapsed} rescoring event(s) of identical content folded "
            "into their attempt (samples must be independent)"
        )
    if legacy > 0:
        typer.echo(f"  excluded: {legacy} legacy row(s) without content identity (never samples)")
    excluded = len(entries) - len(in_lineage)
    if excluded > 0:
        typer.echo(f"  excluded: {excluded} row(s) from older/unknown rulers (like-with-like only)")


@app.command("showcase")
def showcase_cmd(
    out: Annotated[
        Path,
        typer.Option("--out", help="Output path for the self-contained showcase HTML."),
    ] = Path("showcase.html"),
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root",
            help="Repository root containing cases/, runs/, failures/, baselines/ and agentbench/.",
        ),
    ] = Path("."),
) -> None:
    """Render the single-file trust-platform showcase HTML (P4-F)."""
    from cfdb.reporting.showcase import render_showcase

    try:
        written = render_showcase(repo_root, out)
    except ValueError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"[OK] Showcase: {written}")


if __name__ == "__main__":
    app()
