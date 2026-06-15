"""ExecutionBackend Protocol."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cfdb.adapters.base import RunResult


@runtime_checkable
class ExecutionBackend(Protocol):
    """Execution backend interface.

    Shields different execution environments (local / Docker / Slurm).
    Adapters execute commands through this interface.
    """

    name: str
    """Backend unique identifier."""

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute a command and return the result.

        Args:
            command: Command and arguments list, e.g. ['bash', 'run.sh'].
            cwd: Working directory.
            timeout: Timeout in seconds (None = unlimited).
            env: Environment variable overrides.

        Returns:
            RunResult: contains exit_code / stdout / stderr / wall_time_sec / timed_out.
        """
        ...
