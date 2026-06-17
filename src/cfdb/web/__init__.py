"""Web dashboard package — read-only FastAPI + Jinja2 web interface."""

from __future__ import annotations

from cfdb.web.app import create_app

__all__ = ["create_app"]
