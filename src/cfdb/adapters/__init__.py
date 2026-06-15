"""Adapters subpackage: solver adapter registry."""

from __future__ import annotations

from cfdb.adapters.base import SolverAdapter
from cfdb.adapters.generic_command import GenericCommandAdapter
from cfdb.adapters.openfoam import OpenFOAMAdapter
from cfdb.adapters.su2 import SU2Adapter

_ADAPTERS: dict[str, type[SolverAdapter]] = {
    "generic": GenericCommandAdapter,
    "openfoam": OpenFOAMAdapter,
    "su2": SU2Adapter,
}


def get_adapter(name: str, dry_run: bool = False) -> SolverAdapter:
    """Get an adapter instance by name.

    Args:
        name: Adapter name (e.g. 'generic', 'openfoam', 'su2').
        dry_run: If True, adapter is constructed in dry_run mode.

    Returns:
        SolverAdapter instance.

    Raises:
        KeyError: If adapter name is not registered.
    """
    if name not in _ADAPTERS:
        raise KeyError(f"Unknown adapter: '{name}'. Available: {list(_ADAPTERS)}")
    cls = _ADAPTERS[name]
    return cls(dry_run=dry_run)  # type: ignore[call-arg]


def register_adapter(name: str, adapter_cls: type[SolverAdapter]) -> None:
    """Register a new adapter (for P1/P2 extension).

    Args:
        name: Adapter name.
        adapter_cls: Adapter class implementing SolverAdapter Protocol.
    """
    _ADAPTERS[name] = adapter_cls


__all__ = [
    "SolverAdapter",
    "GenericCommandAdapter",
    "OpenFOAMAdapter",
    "SU2Adapter",
    "get_adapter",
    "register_adapter",
]
