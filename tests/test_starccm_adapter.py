"""Tests for cfdb.adapters.starccm.StarCCMAdapter.

All Star-CCM+ fixtures live in tests/conftest.py (P4.5b refactor).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.base import RunResult
from cfdb.adapters.starccm import StarCCMAdapter
from cfdb.schema import CaseSpec, SolverConfig

# ============================================================================
# Init
# ============================================================================


class TestStarCCMAdapterInit:
    def test_name(self) -> None:
        adapter = StarCCMAdapter()
        assert adapter.name == "starccm"

    def test_default_dry_run_false(self) -> None:
        adapter = StarCCMAdapter()
        assert adapter._dry_run is False

    def test_dry_run_true(self) -> None:
        adapter = StarCCMAdapter(dry_run=True)
        assert adapter._dry_run is True


# ============================================================================
# Prepare
# ============================================================================


class TestStarCCMPrepare:
    def test_prepare_creates_macro(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_dry: StarCCMAdapter,
        starccm_prepared: tuple,
    ) -> None:
        _, run_dir, _ = starccm_prepared
        case_out = run_dir / "case"
        assert case_out.is_dir()
        assert (case_out / "run.java").exists()

    def test_macro_contains_case_id(
        self, starccm_prepared: tuple
    ) -> None:
        _, _, macro = starccm_prepared
        assert "flat_plate_starccm" in macro
        assert "StarMacro" in macro

    def test_macro_contains_parameters(
        self, starccm_prepared: tuple
    ) -> None:
        _, _, macro = starccm_prepared
        assert "100.0" in macro  # u_inf default

    def test_naca_routing_true(
        self, starccm_naca_prepared: tuple
    ) -> None:
        _, _, macro = starccm_naca_prepared
        assert "NACA0012" in macro
        assert "Force Coefficient NACA0012" in macro
        assert "forces.csv" in macro

    def test_naca_routing_false(
        self, starccm_prepared: tuple
    ) -> None:
        _, _, macro = starccm_prepared
        # Non-NACA case should NOT have NACA-specific content
        assert "NACA0012" not in macro

    def test_prepare_with_parameters_override(
        self,
        starccm_adapter_dry: StarCCMAdapter,
        tmp_path: Path,
    ) -> None:
        from cfdb.schema import CommandStep

        case = _make_starccm_case(
            steps=[
                CommandStep(
                    name="solve",
                    command="starccm+ -batch {{ case_dir }}/run.java",
                ),
            ],
            parameters={"mach": 0.5, "n_iter": 1000},
        )
        case_dir = tmp_path / "custom_case"
        case_dir.mkdir()
        run_dir = tmp_path / "custom_run"

        starccm_adapter_dry.prepare(case, case_dir, run_dir)

        macro = (run_dir / "case" / "run.java").read_text(encoding="utf-8")
        assert "1000" in macro  # n_iter override


# ============================================================================
# Run
# ============================================================================


class TestStarCCMRun:
    def test_run_dry_run_with_steps(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_dry: StarCCMAdapter,
        starccm_prepared: tuple,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared
        result = starccm_adapter_dry.run(
            starccm_case, case_dir, run_dir, resources=None,
        )
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1
        assert "starccm+" in result.skipped_commands[0]

    def test_run_dry_run_without_steps(
        self,
        starccm_case_no_steps: CaseSpec,
        starccm_adapter_dry: StarCCMAdapter,
        tmp_path: Path,
    ) -> None:
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"
        starccm_adapter_dry.prepare(starccm_case_no_steps, case_dir, run_dir)

        result = starccm_adapter_dry.run(
            starccm_case_no_steps, case_dir, run_dir, resources=None,
        )
        assert result.exit_code == 0
        assert result.skipped_commands is not None
        assert len(result.skipped_commands) == 1

    def test_run_real_execution_all_steps_success(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_prepared: tuple,
        starccm_stdout_version: str,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared

        mock_result = RunResult(
            exit_code=0,
            stdout=starccm_stdout_version +
                "Iteration: 1  Continuity: 1.0e-3  X-Momentum: 1.0e-2\n"
                "Iteration: 2  Continuity: 5.0e-4  X-Momentum: 5.0e-3\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = starccm_adapter_real.run(
                starccm_case, case_dir, run_dir, resources=None,
            )

        assert result.exit_code == 0
        assert result.skipped_commands is None
        assert result.solver_version is not None
        assert "19.02.009" in (result.solver_version or "")

    def test_critical_step_failure_aborts(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_prepared: tuple,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared

        mock_fail = RunResult(
            exit_code=1,
            stdout="",
            stderr="STARCCM FATAL ERROR",
            wall_time_sec=0.1,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_fail,
        ):
            result = starccm_adapter_real.run(
                starccm_case, case_dir, run_dir, resources=None,
            )

        assert result.exit_code != 0


# ============================================================================
# Collect outputs
# ============================================================================


class TestStarCCMCollectOutputs:
    def test_collect_outputs_lists_files(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_dry: StarCCMAdapter,
        starccm_prepared: tuple,
    ) -> None:
        _, run_dir, _ = starccm_prepared
        artifacts = starccm_adapter_dry.collect_outputs(starccm_case, run_dir)
        assert "case/run.java" in artifacts.files
        assert artifacts.qoi_values is None

    def test_collect_outputs_extracts_cl_cd_from_csv(
        self,
        starccm_naca_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_naca_prepared: tuple,
    ) -> None:
        _, run_dir, _ = starccm_naca_prepared
        case_out = run_dir / "case"

        # Write a mock forces.csv
        (case_out / "forces.csv").write_text(
            "Iteration, Cd, Cl, Cm\n"
            "0, 0.0, 0.0, 0.0\n"
            "100, 0.005, 0.200, -0.01\n"
            "500, 0.0086, 0.3240, -0.015\n",
            encoding="utf-8",
        )

        artifacts = starccm_adapter_real.collect_outputs(
            starccm_naca_case, run_dir,
        )
        assert artifacts.qoi_values is not None
        assert artifacts.qoi_values["cl"] == 0.3240
        assert artifacts.qoi_values["cd"] == 0.0086


# ============================================================================
# Find solver config
# ============================================================================


class TestStarCCMFindSolverConfig:
    _BAD_CASE = CaseSpec(
        id="bad",
        name="Bad",
        category="smoke",
        physics=__import__("cfdb.schema", fromlist=["PhysicsSpec"]).PhysicsSpec(
            flow="incompressible",
        ),
        conditions=__import__("cfdb.schema", fromlist=["ConditionsSpec"]).ConditionsSpec(),
        solvers=[SolverConfig(name="other", command="echo")],
        outputs=__import__("cfdb.schema", fromlist=["OutputSpec"]).OutputSpec(),
        metrics=__import__("cfdb.schema", fromlist=["MetricSpec"]).MetricSpec(),
    )

    def test_not_found_raises_value_error(self) -> None:
        adapter = StarCCMAdapter()
        with pytest.raises(ValueError, match="no 'starccm'"):
            adapter._find_solver_config(self._BAD_CASE)


# ============================================================================
# P2-a fields
# ============================================================================


class TestStarCCMP2aFields:
    def test_merge_step_details(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_prepared: tuple,
        starccm_stdout_version: str,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared

        mock_result = RunResult(
            exit_code=0,
            stdout=starccm_stdout_version
                + "Iteration: 1  Continuity: 1.0e-3\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = starccm_adapter_real.run(
                starccm_case, case_dir, run_dir, resources=None,
            )

        assert result.step_details is not None
        assert len(result.step_details) == 1
        assert result.step_details[0]["name"] == "solve"

    def test_merge_cell_count(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_prepared: tuple,
        starccm_stdout_version: str,
        starccm_stdout_cell_count: str,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared

        mock_result = RunResult(
            exit_code=0,
            stdout=starccm_stdout_version
                + starccm_stdout_cell_count
                + "\nIteration: 1  Continuity: 1.0e-3\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = starccm_adapter_real.run(
                starccm_case, case_dir, run_dir, resources=None,
            )

        assert result.cell_count == 400

    def test_merge_residuals_history(
        self,
        starccm_case: CaseSpec,
        starccm_adapter_real: StarCCMAdapter,
        starccm_prepared: tuple,
        starccm_stdout_version: str,
    ) -> None:
        case_dir, run_dir, _ = starccm_prepared

        mock_result = RunResult(
            exit_code=0,
            stdout=starccm_stdout_version +
                "Iteration: 1  Continuity: 1.0e-1  X-Momentum: 2.0e-1\n"
                "Iteration: 2  Continuity: 1.0e-2  X-Momentum: 2.0e-2\n"
                "Iteration: 3  Continuity: 1.0e-3  X-Momentum: 2.0e-3\n",
            stderr="",
            wall_time_sec=1.0,
        )

        with patch(
            "cfdb.execution.local.LocalExecutionBackend.execute",
            return_value=mock_result,
        ):
            result = starccm_adapter_real.run(
                starccm_case, case_dir, run_dir, resources=None,
            )

        assert result.residuals_history is not None
        assert "Continuity" in result.residuals_history
        assert len(result.residuals_history["Continuity"]) == 3
        assert "X-Momentum" in result.residuals_history
        assert len(result.residuals_history["X-Momentum"]) == 3


# ============================================================================
# NACA template content
# ============================================================================


class TestStarCCMNacaTemplate:
    def test_naca_macro_has_force_report(
        self, starccm_naca_prepared: tuple
    ) -> None:
        _, _, macro = starccm_naca_prepared
        assert "ForceCoefficientReport" in macro
        assert "airfoil" in macro

    def test_naca_macro_has_cl_cd_export(
        self, starccm_naca_prepared: tuple
    ) -> None:
        _, _, macro = starccm_naca_prepared
        assert "forces.csv" in macro
        assert "result.sim" in macro


# ============================================================================
# Post functions (stateless — no adapter needed)
# ============================================================================


class TestPostFunctions:
    def test_extract_starccm_version(
        self, starccm_stdout_version: str,
    ) -> None:
        from cfdb.post.residuals import extract_starccm_version

        version = extract_starccm_version(starccm_stdout_version)
        assert version == "StarCCM+ 19.02.009"

    def test_extract_starccm_version_none(self) -> None:
        from cfdb.post.residuals import extract_starccm_version

        version = extract_starccm_version(
            "some random output\nwithout starccm banner\n",
        )
        assert version is None

    def test_parse_starccm_residuals_single_line(
        self, starccm_stdout_residuals_singleline: str,
    ) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        residuals = parse_starccm_residuals(
            starccm_stdout_residuals_singleline,
        )
        assert "Continuity" in residuals
        assert len(residuals["Continuity"]) == 2
        assert residuals["Continuity"] == [1.0, 0.5]
        assert residuals["X-Momentum"] == [1.0, 0.5]

    def test_parse_starccm_residuals_csv(
        self, starccm_stdout_residuals_csv: str,
    ) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        residuals = parse_starccm_residuals(starccm_stdout_residuals_csv)
        assert "Continuity" in residuals
        assert len(residuals["Continuity"]) == 3
        assert residuals["Continuity"] == [1.0, 0.5, 0.01]

    def test_parse_starccm_residuals_empty(self) -> None:
        from cfdb.post.residuals import parse_starccm_residuals

        residuals = parse_starccm_residuals(
            "No residual data here.\nJust some text.\n",
        )
        assert residuals == {}

    def test_extract_starccm_cell_count(
        self, starccm_stdout_cell_count: str,
    ) -> None:
        from cfdb.post.mesh_stats import extract_starccm_cell_count

        count = extract_starccm_cell_count(starccm_stdout_cell_count)
        assert count == 400

    def test_extract_starccm_cell_count_none(self) -> None:
        from cfdb.post.mesh_stats import extract_starccm_cell_count

        count = extract_starccm_cell_count("No cell info here.")
        assert count is None

    def test_extract_cl_cd_starccm(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text(
            "Iteration, Cd, Cl, Cm\n"
            "0, 0.0, 0.0, 0.0\n"
            "500, 0.0086, 0.3240, -0.015\n",
            encoding="utf-8",
        )

        cl, cd = extract_cl_cd_starccm(forces_csv)
        assert cl == 0.3240
        assert cd == 0.0086

    def test_extract_cl_cd_starccm_alt_headers(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text(
            "Iter, Drag_Coefficient, Lift_Coefficient, Cm\n"
            "1, 0.001, 0.05, -0.01\n",
            encoding="utf-8",
        )

        cl, cd = extract_cl_cd_starccm(forces_csv)
        assert cl == 0.05
        assert cd == 0.001

    def test_extract_cl_cd_starccm_none(self, tmp_path: Path) -> None:
        from cfdb.post.qoi_extractor import extract_cl_cd_starccm

        forces_csv = tmp_path / "forces.csv"
        forces_csv.write_text("X, Y, Z\n1, 2, 3\n", encoding="utf-8")

        result = extract_cl_cd_starccm(forces_csv)
        assert result is None


# ============================================================================
# Registry
# ============================================================================


class TestStarCCMRegistry:
    def test_adapter_registered(self) -> None:
        from cfdb.adapters import get_adapter

        adapter = get_adapter("starccm", dry_run=True)
        assert adapter.name == "starccm"
        assert adapter._dry_run is True


# ============================================================================
# Local helper (only used in this file for custom-parameter tests)
# ============================================================================


def _make_starccm_case(
    steps=None, parameters=None, category: str = "smoke"
) -> CaseSpec:
    """Create a minimal CaseSpec with a 'starccm' solver config.

    Used only by test_prepare_with_parameters_override which needs
    a custom parameter dict — all other cases come from conftest fixtures.
    """
    from cfdb.schema import (
        ConditionsSpec,
        MetricSpec,
        OutputSpec,
        PhysicsSpec,
    )

    solver = SolverConfig(
        name="starccm",
        command="starccm+ -batch {{ case_dir }}/run.java",
        steps=steps,
        parameters=parameters,
    )
    return CaseSpec(
        id="flat_plate_starccm",
        name="Flat Plate StarCCM",
        category=category,
        physics=PhysicsSpec(
            flow="compressible", dimensionality="2d", steady=True,
        ),
        conditions=ConditionsSpec(
            reynolds=1_000_000, mach=0.3, alpha_deg=0.0,
        ),
        solvers=[solver],
        outputs=OutputSpec(fields=["velocity", "pressure"], qoi=["cl", "cd"]),
        metrics=MetricSpec(qoi_relative_tolerance={"cl": 0.1, "cd": 0.1}),
    )
