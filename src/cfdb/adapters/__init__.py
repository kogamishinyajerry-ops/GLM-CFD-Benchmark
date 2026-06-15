"""Adapters subpackage: solver adapter registry."""

from __future__ import annotations

from cfdb.adapters.base import SolverAdapter
from cfdb.adapters.generic_command import GenericCommandAdapter

_ADAPTERS: dict[str, type[SolverAdapter]] = {
    "generic": GenericCommandAdapter,
}


def get_adapter(name: str) -> SolverAdapter:
    """Get an adapter instance by name.

    Args:
        name: Adapter name (e.g. 'generic').

    Returns:
        SolverAdapter instance.

    Raises:
        KeyError: If adapter name is not registered.
    """
    if name not in _ADAPTERS:
        raise KeyError(f"Unknown adapter: '{name}'. Available: {list(_ADAPTERS)}")
    return _ADAPTERS[name]()


def register_adapter(name: str, adapter_cls: type[SolverAdapter]) -> None:
    """Register a new adapter (for P1/P2 extension).

    Args:
        name: Adapter name.
        adapter_cls: Adapter class implementing SolverAdapter Protocol.
    """
    _ADAPTERS[name] = adapter_cls


__all__ = ["SolverAdapter", "GenericCommandAdapter", "get_adapter", "register_adapter"]
