"""Execution subpackage: backend registry."""

from __future__ import annotations

from cfdb.execution.base import ExecutionBackend
from cfdb.execution.local import LocalExecutionBackend

_BACKENDS: dict[str, type[ExecutionBackend]] = {
    "local": LocalExecutionBackend,
}


def get_backend(name: str) -> ExecutionBackend:
    """Get a backend instance by name.

    Args:
        name: Backend name (e.g. 'local').

    Returns:
        ExecutionBackend instance.

    Raises:
        KeyError: If backend name is not registered.
    """
    if name not in _BACKENDS:
        raise KeyError(f"Unknown backend: '{name}'. Available: {list(_BACKENDS)}")
    return _BACKENDS[name]()


__all__ = ["ExecutionBackend", "LocalExecutionBackend", "get_backend"]
