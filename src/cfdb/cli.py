"""CLI entry point: 4 commands via Typer."""

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
        return

    typer.echo(f"{'ID':<25} {'Category':<15} {'Solvers':<20} {'Name'}")
    typer.echo("-" * 80)
    for case in cases:
        solvers = ", ".join(s.name for s in case.solvers)
        typer.echo(f"{case.id:<25} {case.category:<15} {solvers:<20} {case.name}")

    typer.echo(f"\nTotal: {len(cases)} case(s)")


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
        res_parts = ", ".join(
            f"{k}={v:.2e}" for k, v in manifest.final_residuals.items()
        )
        typer.echo(f"Residuals: {res_parts}")

    # P2-b: Print container_digest if available
    if manifest.container_digest:
        typer.echo(f"Digest:    {manifest.container_digest}")

    if manifest.dry_run_skipped_commands:
        typer.echo(
            f"[DRY-RUN] Skipped {len(manifest.dry_run_skipped_commands)} command(s):"
        )
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
        typer.Argument(
            help="Specific .dvc targets (relative paths). If empty, pulls all."
        ),
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

    html = render_compare_html(
        manifest1, manifest2, comparisons, residual_svg=residual_svg
    )

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
            cl = (
                met.qoi_computed_values.get("cl")
                if met.qoi_computed_values
                else None
            )
            cd = (
                met.qoi_computed_values.get("cd")
                if met.qoi_computed_values
                else None
            )
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
        '\n</head>\n<body><p><a href="runs/">'
        'CFD-Benchmark Dashboard</a></p></body>\n</html>'
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


if __name__ == "__main__":
    app()
