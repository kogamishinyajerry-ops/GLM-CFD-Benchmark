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
    from cfdb.data import dvc_available, dvc_status, DVCError

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
    from cfdb.data import dvc_available, dvc_pull, DVCError

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


if __name__ == "__main__":
    app()
