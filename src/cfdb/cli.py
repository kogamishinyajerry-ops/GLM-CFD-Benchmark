"""CLI entry point: 4 commands via Typer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

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
        typer.Option("--backend", "-b", help="Execution backend name."),
    ] = "local",
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
) -> None:
    """Run a specified case with a given solver and backend."""
    from cfdb.core.runner import Runner

    registry = CaseRegistry(cases_dir)
    repo = JsonManifestRepository(runs_dir)
    runner = Runner(registry, repo, runs_dir)

    cli_args: dict[str, str] = {
        "case": case,
        "solver": solver,
        "backend": backend,
    }
    if dry_run:
        cli_args["dry_run"] = "true"

    manifest = runner.execute(
        case_id=case,
        solver=solver,
        backend=backend,
        generate_report=report,
        cli_args=cli_args,
        dry_run=dry_run,
    )

    typer.echo("=" * 60)
    typer.echo(f"Run ID:    {manifest.run_id}")
    typer.echo(f"Case:      {manifest.case_id}")
    typer.echo(f"Solver:    {manifest.solver}")
    typer.echo(f"Backend:   {manifest.backend}")
    typer.echo(f"Status:    {manifest.status}")
    typer.echo(f"Wall Time: {manifest.timing.wall_time_sec:.3f}s")
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

    html_path = generate_html_report(manifest, metrics, run_dir)
    typer.echo(f"[OK] Report generated: {html_path}")


if __name__ == "__main__":
    app()
