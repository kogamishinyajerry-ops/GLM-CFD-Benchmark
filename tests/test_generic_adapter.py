"""Tests for cfdb.adapters.generic_command.GenericCommandAdapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfdb.adapters.base import ResourceSpec
from cfdb.adapters.generic_command import GenericCommandAdapter
from cfdb.schema import CaseSpec


class TestGenericCommandAdapter:
    def test_name(self) -> None:
        adapter = GenericCommandAdapter()
        assert adapter.name == "generic"

    def test_prepare_creates_run_sh(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(sample_case_spec, case_dir, run_dir)

        assert run_dir.exists()
        assert (run_dir / "run.sh").exists()
        content = (run_dir / "run.sh").read_text()
        assert "bash" in content
        assert case_dir.resolve().as_posix() in content

    def test_prepare_renders_case_dir(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        case_dir = tmp_path / "mycase"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(sample_case_spec, case_dir, run_dir)

        script = (run_dir / "run.sh").read_text()
        assert case_dir.resolve().as_posix() in script

    def test_run_success(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        (case_dir / "run.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'hello'\nexit 0\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "run"
        adapter.prepare(sample_case_spec, case_dir, run_dir)

        # Overwrite with a real success script
        (run_dir / "run.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'hello'\nexit 0\n",
            encoding="utf-8",
        )
        result = adapter.run(sample_case_spec, case_dir, run_dir, resources=None)
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.timed_out is False

    def test_run_failure_exit_code(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        adapter.prepare(sample_case_spec, case_dir, run_dir)

        (run_dir / "run.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'error' >&2\nexit 1\n",
            encoding="utf-8",
        )
        result = adapter.run(sample_case_spec, case_dir, run_dir, resources=None)
        assert result.exit_code == 1
        assert "error" in result.stderr

    def test_collect_outputs_with_qoi(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "qoi.json").write_text(
            json.dumps({"centerline_umax": 0.373}), encoding="utf-8"
        )
        (run_dir / "stdout.log").write_text("output", encoding="utf-8")

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)
        assert artifacts.qoi_values is not None
        assert artifacts.qoi_values["centerline_umax"] == pytest.approx(0.373)
        assert "qoi.json" in artifacts.files
        assert "stdout.log" in artifacts.files

    def test_collect_outputs_no_qoi(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)
        assert artifacts.qoi_values is None

    def test_collect_outputs_invalid_qoi_json(
        self, sample_case_spec: CaseSpec, tmp_path: Path
    ) -> None:
        adapter = GenericCommandAdapter()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "qoi.json").write_text("{invalid json", encoding="utf-8")

        artifacts = adapter.collect_outputs(sample_case_spec, run_dir)
        assert artifacts.qoi_values is None

    def test_get_timeout_from_case(self, sample_case_spec: CaseSpec) -> None:
        adapter = GenericCommandAdapter()
        assert adapter._get_timeout(sample_case_spec, None) is None

    def test_get_timeout_from_resources(self, sample_case_spec: CaseSpec) -> None:
        adapter = GenericCommandAdapter()
        resources = ResourceSpec(wall_time_sec=60)
        assert adapter._get_timeout(sample_case_spec, resources) == 60

    def test_find_solver_config_not_found(self, tmp_path: Path) -> None:
        from cfdb.schema import (
            ConditionsSpec,
            MetricSpec,
            OutputSpec,
            PhysicsSpec,
            SolverConfig,
        )

        case = CaseSpec(
            id="test",
            name="Test",
            category="smoke",
            physics=PhysicsSpec(flow="incompressible"),
            conditions=ConditionsSpec(),
            solvers=[SolverConfig(name="other", command="echo hi")],
            outputs=OutputSpec(),
            metrics=MetricSpec(),
        )
        adapter = GenericCommandAdapter()
        with pytest.raises(ValueError, match="no 'generic'"):
            adapter._find_solver_config(case)
