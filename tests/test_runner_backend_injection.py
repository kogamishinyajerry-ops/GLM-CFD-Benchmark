"""Tests for backend injection into adapters (P2-b).

Verifies that OpenFOAMAdapter / SU2Adapter accept an optional `backend` arg
and use the injected backend instead of hard-coding LocalExecutionBackend.
"""

from __future__ import annotations

from pathlib import Path

from cfdb.adapters import get_adapter
from cfdb.adapters.base import RunResult
from cfdb.adapters.openfoam import OpenFOAMAdapter
from cfdb.adapters.su2 import SU2Adapter
from cfdb.execution.base import ExecutionBackend
from cfdb.execution.local import LocalExecutionBackend


class _FakeBackend:
    """Minimal fake ExecutionBackend for testing injection."""

    name: str = "fake"

    def __init__(self, *, return_exit: int = 0) -> None:
        self.return_exit = return_exit
        self.calls: list[tuple[list[str], Path]] = []

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        self.calls.append((command, cwd))
        return RunResult(
            exit_code=self.return_exit,
            stdout="[fake] executed " + " ".join(command),
            stderr="",
            wall_time_sec=0.001,
            timed_out=False,
        )


class TestBackendInjectionDefault:
    """When backend=None, adapter defaults to LocalExecutionBackend."""

    def test_openfoam_default_local(self) -> None:
        adapter = OpenFOAMAdapter(dry_run=True)
        assert isinstance(adapter._backend, LocalExecutionBackend)

    def test_su2_default_local(self) -> None:
        adapter = SU2Adapter(dry_run=True)
        assert isinstance(adapter._backend, LocalExecutionBackend)


class TestBackendInjectionCustom:
    """When backend provided, adapter stores it."""

    def test_openfoam_accepts_custom_backend(self) -> None:
        fake = _FakeBackend()
        adapter = OpenFOAMAdapter(dry_run=True, backend=fake)
        assert adapter._backend is fake

    def test_su2_accepts_custom_backend(self) -> None:
        fake = _FakeBackend()
        adapter = SU2Adapter(dry_run=True, backend=fake)
        assert adapter._backend is fake

    def test_openfoam_uses_injected_backend_in_dry_run(self) -> None:
        """Even in dry_run mode, the backend attribute is set (but not called)."""
        fake = _FakeBackend()
        adapter = OpenFOAMAdapter(dry_run=True, backend=fake)
        # In dry_run, no execution — backend attribute just sits there
        assert len(fake.calls) == 0


class TestGetAdapterFactory:
    """get_adapter() must pass backend through for openfoam/su2."""

    def test_get_adapter_openfoam_with_backend(self) -> None:
        fake = _FakeBackend()
        adapter = get_adapter("openfoam", dry_run=True, backend=fake)
        # Adapter must be an OpenFOAMAdapter with our fake backend
        assert isinstance(adapter, OpenFOAMAdapter)
        assert adapter._backend is fake

    def test_get_adapter_su2_with_backend(self) -> None:
        fake = _FakeBackend()
        adapter = get_adapter("su2", dry_run=True, backend=fake)
        assert isinstance(adapter, SU2Adapter)
        assert adapter._backend is fake

    def test_get_adapter_generic_ignores_backend(self) -> None:
        """generic adapter doesn't take backend (P0 design — Runner uses it directly)."""
        fake = _FakeBackend()
        # Should not raise even though backend is provided
        adapter = get_adapter("generic", dry_run=True, backend=fake)
        assert adapter is not None

    def test_get_adapter_openfoam_default_no_backend(self) -> None:
        """When backend=None, OpenFOAMAdapter defaults to LocalExecutionBackend."""
        adapter = get_adapter("openfoam", dry_run=True)
        assert isinstance(adapter, OpenFOAMAdapter)
        assert isinstance(adapter._backend, LocalExecutionBackend)


class TestDockerBackendProtocol:
    """DockerBackend must satisfy ExecutionBackend Protocol."""

    def test_docker_backend_isinstance(self) -> None:
        from cfdb.execution.docker import DockerBackend
        b: ExecutionBackend = DockerBackend(image="test/img")  # type: ignore[assignment]
        # runtime_checkable Protocol: checks presence of name + execute attrs
        assert isinstance(b, ExecutionBackend)

    def test_local_backend_isinstance(self) -> None:
        b: ExecutionBackend = LocalExecutionBackend()  # type: ignore[assignment]
        assert isinstance(b, ExecutionBackend)

    def test_fake_backend_isinstance(self) -> None:
        """Our _FakeBackend test helper also satisfies the Protocol."""
        b: ExecutionBackend = _FakeBackend()  # type: ignore[assignment]
        assert isinstance(b, ExecutionBackend)
