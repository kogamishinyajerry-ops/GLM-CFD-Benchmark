"""Tests for v5.0 Wave A Lane A1+A5: adapter sandbox fail-closed + recursive
artifact collection.

Tamper-witness paradigm (Architecture v5.0 §6): prove the untampered
baseline PASSes, then flip a single fact and assert the judgment flips to
the prescribed fail-closed state. Sandbox gate decisions use ``is True`` /
``is not True`` — never truthiness (v5.0 §0 constitution) — so a stub
backend with a truthy-but-not-``True`` ``is_sandbox`` is included as a
witness that the implementation does not regress to truthiness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from cfdb.adapters import get_adapter
from cfdb.adapters.base import RunResult
from cfdb.adapters.generic_command import GenericCommandAdapter
from cfdb.core.runner import Runner
from cfdb.registry import CaseRegistry
from cfdb.schema import CaseSpec
from cfdb.storage.json_repo import JsonManifestRepository

# ============================================================================
# Stub backends — is_sandbox absent / True / False / truthy-non-bool
# ============================================================================


class _StubLocalBackend:
    """Mimics LocalExecutionBackend's shape: no ``is_sandbox`` attribute at all."""

    name: str = "stub-local"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        self.calls.append(command)
        return RunResult(
            exit_code=0, stdout="ok", stderr="", wall_time_sec=0.001, timed_out=False
        )


class _StubSandboxBackend(_StubLocalBackend):
    """Mimics DockerBackend(sandbox=True): declares is_sandbox=True."""

    name: str = "stub-sandbox"
    is_sandbox: bool = True


class _StubNonSandboxBackend(_StubLocalBackend):
    """Mimics DockerBackend(sandbox=False): declares is_sandbox=False explicitly."""

    name: str = "stub-nonsandbox"
    is_sandbox: bool = False


class _StubTruthyNonBooleanBackend(_StubLocalBackend):
    """is_sandbox is truthy (1) but not the literal ``True`` singleton.

    The gate must use ``is not True``, never truthiness — this backend must
    still be refused.
    """

    name: str = "stub-truthy"
    is_sandbox: int = 1


# ============================================================================
# Case fixtures
# ============================================================================


def _case_dict(case_id: str, requires_sandbox: bool) -> dict[str, Any]:
    """Minimal valid CaseSpec dict; only the sandbox flag varies (single-point tamper)."""
    return {
        "id": case_id,
        "name": "Sandbox Fail-Closed Test Case",
        "category": "smoke",
        "solvers": [{"name": "generic", "command": "bash {{ case_dir }}/run.sh"}],
        "outputs": {"fields": [], "curves": [], "qoi": []},
        "metrics": {},
        "execution": {"requires_sandbox": requires_sandbox},
    }


@pytest.fixture
def cases_root(tmp_path: Path) -> Path:
    """cases/smoke/{sandboxed_case,unsandboxed_case}/ — identical except the flag."""
    root = tmp_path / "cases"
    for case_id, requires_sandbox in [
        ("sandboxed_case", True),
        ("unsandboxed_case", False),
    ]:
        case_dir = root / "smoke" / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "case.yaml").write_text(
            yaml.dump(_case_dict(case_id, requires_sandbox)), encoding="utf-8"
        )
        (case_dir / "run.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\necho done\nexit 0\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def runner(cases_root: Path, runs_root: Path) -> Runner:
    registry = CaseRegistry(cases_root)
    repo = JsonManifestRepository(runs_root)
    return Runner(registry, repo, runs_root)


# ============================================================================
# A1a: adapter-level backend injection (fixes generic adapter fail-open)
# ============================================================================


class TestGenericAdapterAcceptsBackend:
    def test_default_backend_is_local(self) -> None:
        from cfdb.execution.local import LocalExecutionBackend

        adapter = GenericCommandAdapter()
        assert isinstance(adapter._backend, LocalExecutionBackend)

    def test_accepts_injected_backend(self) -> None:
        stub = _StubSandboxBackend()
        adapter = GenericCommandAdapter(backend=stub)
        assert adapter._backend is stub

    def test_dry_run_flag_still_respected_with_injected_backend(self) -> None:
        stub = _StubSandboxBackend()
        adapter = GenericCommandAdapter(dry_run=True, backend=stub)
        assert adapter._dry_run is True
        assert adapter._backend is stub

    def test_get_adapter_generic_injects_backend(self) -> None:
        """Regression witness for the A1 fail-open bug: get_adapter() used to
        silently drop the backend for 'generic' (P0 special-case), so
        `--backend docker` appeared to take effect on the CLI while the
        adapter still ran unsandboxed via LocalExecutionBackend. Post-fix the
        backend must actually reach the adapter.
        """
        stub = _StubSandboxBackend()
        adapter = get_adapter("generic", dry_run=True, backend=stub)
        assert adapter._backend is stub

    def test_get_adapter_generic_default_none_still_local(self) -> None:
        from cfdb.execution.local import LocalExecutionBackend

        adapter = get_adapter("generic", dry_run=True)
        assert isinstance(adapter._backend, LocalExecutionBackend)


# ============================================================================
# A1b: Runner-level fail-closed sandbox enforcement (tamper witness)
# ============================================================================


class TestRunnerSandboxFailClosed:
    """Baseline PASS (requires_sandbox=False) -> single-point tamper
    (requires_sandbox=True) -> refuse, using the real production
    LocalExecutionBackend (no stub) for the strongest possible witness.
    """

    def test_baseline_unsandboxed_case_runs_on_local_backend(
        self, runner: Runner
    ) -> None:
        manifest = runner.execute(
            case_id="unsandboxed_case", solver="generic", backend="local"
        )
        assert manifest.status == "success"

    def test_sandboxed_case_on_local_backend_refuses(self, runner: Runner) -> None:
        with pytest.raises(RuntimeError, match="requires_sandbox"):
            runner.execute(
                case_id="sandboxed_case", solver="generic", backend="local"
            )

    def test_sandboxed_case_on_stub_sandbox_backend_runs(
        self, runner: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _StubSandboxBackend()
        monkeypatch.setattr(Runner, "_build_backend", lambda self, name, opts: stub)
        manifest = runner.execute(
            case_id="sandboxed_case", solver="generic", backend="local"
        )
        assert manifest.status == "success"
        assert len(stub.calls) == 1

    def test_sandboxed_case_on_stub_explicit_false_refuses(
        self, runner: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _StubNonSandboxBackend()
        monkeypatch.setattr(Runner, "_build_backend", lambda self, name, opts: stub)
        with pytest.raises(RuntimeError, match="requires_sandbox"):
            runner.execute(
                case_id="sandboxed_case", solver="generic", backend="local"
            )

    def test_sandboxed_case_on_truthy_non_bool_backend_refuses(
        self, runner: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constitutional guard: gate must use `is not True`, never
        truthiness. A backend whose is_sandbox is truthy (1) but not the
        literal True singleton must still be refused."""
        stub = _StubTruthyNonBooleanBackend()
        monkeypatch.setattr(Runner, "_build_backend", lambda self, name, opts: stub)
        with pytest.raises(RuntimeError, match="requires_sandbox"):
            runner.execute(
                case_id="sandboxed_case", solver="generic", backend="local"
            )

    def test_unsandboxed_case_on_sandbox_capable_backend_still_runs(
        self, runner: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """requires_sandbox=False never blocks, regardless of backend capability."""
        stub = _StubSandboxBackend()
        monkeypatch.setattr(Runner, "_build_backend", lambda self, name, opts: stub)
        manifest = runner.execute(
            case_id="unsandboxed_case", solver="generic", backend="local"
        )
        assert manifest.status == "success"


# ============================================================================
# A5: recursive collect_outputs
# ============================================================================


class TestCollectOutputsRecursive:
    def test_recurses_into_nested_dirs(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        nested = run_dir / "reports" / "junit"
        nested.mkdir(parents=True)
        (nested / "results.xml").write_text("<xml/>", encoding="utf-8")
        (run_dir / "top.log").write_text("top", encoding="utf-8")

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)

        assert "top.log" in artifacts.files
        nested_key = str(Path("reports") / "junit" / "results.xml")
        assert nested_key in artifacts.files
        assert artifacts.files[nested_key] == Path("reports") / "junit" / "results.xml"

    def test_directories_themselves_not_listed_as_files(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        (run_dir / "empty_subdir").mkdir(parents=True)

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)

        assert "empty_subdir" not in artifacts.files

    def test_top_level_qoi_json_still_collected(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        """Regression guard: switching iterdir() -> rglob() must not break
        the existing top-level qoi.json parsing path."""
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "qoi.json").write_text(json.dumps({"x": 1.0}), encoding="utf-8")

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)

        assert artifacts.qoi_values == {"x": 1.0}
        assert "qoi.json" in artifacts.files
