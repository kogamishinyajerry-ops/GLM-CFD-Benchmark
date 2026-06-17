"""FastAPI app factory with dependency injection for repository + registry."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from cfdb.registry import CaseRegistry

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    repo: object,
    registry: CaseRegistry,
    runs_dir: Path,
    cases_dir: Path,
) -> FastAPI:
    """Create a FastAPI app with injected repository and registry.

    Args:
        repo: Repository instance (JsonManifestRepository or SqliteRepository).
        registry: CaseRegistry instance.
        runs_dir: Path to the runs directory.
        cases_dir: Path to the cases directory.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(title="CFD-Benchmark Dashboard", version="0.1.0")

    # Mount static files
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Jinja2 environment for web templates
    web_templates = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    # Jinja2 environment for reporting templates (reused from reporting/)
    reporting_loader = FileSystemLoader(
        str(Path(__file__).parent.parent / "reporting" / "templates")
    )
    reporting_env = Environment(
        loader=reporting_loader,
        autoescape=select_autoescape(["html"]),
    )

    # Dependency storage
    app.state.repo = repo
    app.state.registry = registry
    app.state.runs_dir = runs_dir
    app.state.cases_dir = cases_dir
    app.state.web_templates = web_templates
    app.state.reporting_env = reporting_env

    # Register routes
    from cfdb.web.routes import router

    app.include_router(router)

    return app
