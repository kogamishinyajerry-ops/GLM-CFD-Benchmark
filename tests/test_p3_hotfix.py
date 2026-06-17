"""Tests for P3-hotfix: OpenFOAM adapter NACA routing + qoi_computed_values.

Covers:
  1. U.naca.j2 template rendering (α=0° and α=5°)
  2. _is_naca_case() routing logic
  3. _prepare_naca() creates triSurface with STL
  4. MetricsEngine fills qoi_computed_values
  5. collect_outputs() calls extract_cl_cd_openfoam for NACA
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cfdb.adapters.base import ArtifactManifest, RunResult
from cfdb.adapters.openfoam import OpenFOAMAdapter
from cfdb.metrics.engine import MetricsEngine
from cfdb.schema import (
    CaseSpec,
    CommandStep,
    ConditionsSpec,
    GeometrySpec,
    MeshSpec,
    MetricSpec,
    OutputSpec,
    PhysicsSpec,
    ReferenceSpec,
    SolverConfig,
)


def _make_naca_case(alpha_deg: float = 5.0, case_id: str = "naca0012_a5") -> CaseSpec:
    """Create a NACA0012 CaseSpec for testing.

    Args:
        alpha_deg: Angle of attack in degrees.
        case_id: Case identifier string.

    Returns:
        A CaseSpec configured for NACA0012 RANS-SA.
    """
    solver = SolverConfig(
        name="openfoam",
        command="",
        timeout_sec=600,
        steps=[
            CommandStep(name="block_mesh", command="blockMesh -case {{ case_dir }}"),
            CommandStep(
                name="snappy_mesh",
                command="snappyHexMesh -overwrite -case {{ case_dir }}",
            ),
            CommandStep(name="solve", command="simpleFoam -case {{ case_dir }}"),
        ],
        parameters={
            "nu": 1.6667e-7,
            "u_inf": 100.0,
            "l_ref": 1.0,
            "alpha_deg": alpha_deg,
            "n_iter": 1000,
        },
    )
    return CaseSpec(
        id=case_id,
        name=f"NACA0012 alpha={alpha_deg}",
        category="validation",
        physics=PhysicsSpec(
            flow="rans",
            turbulence="rans_sa",
            dimensionality="2d",
            steady=True,
        ),
        conditions=ConditionsSpec(
            reynolds=6.0e6,
            mach=0.3,
            alpha_deg=alpha_deg,
        ),
        geometry=GeometrySpec(type="external"),
        mesh=MeshSpec(family="unstructured_hex", levels=["coarse"], target_y_plus=1.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["U", "p", "nuTilda"], qoi=["cl", "cd"]),
        reference=ReferenceSpec(type="experimental", qoi_values={"cl": 0.456, "cd": 0.0095}),
        metrics=MetricSpec(qoi_relative_tolerance={"cl": 0.10, "cd": 0.10}),
    )


def _make_ldc_case(case_id: str = "test_cavity") -> CaseSpec:
    """Create a minimal LDC (non-NACA) CaseSpec for routing tests.

    Args:
        case_id: Case identifier string.

    Returns:
        A CaseSpec that does NOT start with 'naca0012'.
    """
    solver = SolverConfig(name="openfoam", command="icoFoam")
    return CaseSpec(
        id=case_id,
        name="Test Cavity",
        category="validation",
        physics=PhysicsSpec(flow="incompressible", dimensionality="2d", steady=False),
        conditions=ConditionsSpec(reynolds=100.0),
        solvers=[solver],
        outputs=OutputSpec(fields=["U", "p"], qoi=["centerline_umax"]),
        metrics=MetricSpec(qoi_relative_tolerance={"centerline_umax": 0.05}),
    )


# === T01: U template rendering ===


class TestUTemplateRendering:
    """Tests for U.naca.j2 template rendering with different alpha values."""

    def test_u_template_alpha_zero(self, tmp_path: Path) -> None:
        """Render U.naca.j2 at α=0° → internalField should be uniform (100 0 0)."""
        case = _make_naca_case(alpha_deg=0.0, case_id="naca0012_a0")
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case_src"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        u_content = (run_dir / "case" / "0" / "U").read_text(encoding="utf-8")
        # At α=0°, u_cos=100*cos(0)=100, v_sin=100*sin(0)=0
        assert "uniform (100.0 0.0 0)" in u_content or "uniform (100 0 0)" in u_content

    def test_u_template_alpha_five(self, tmp_path: Path) -> None:
        """Render U.naca.j2 at α=5° → near (99.62 8.72 0)."""
        case = _make_naca_case(alpha_deg=5.0, case_id="naca0012_a5")
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case_src"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        u_content = (run_dir / "case" / "0" / "U").read_text(encoding="utf-8")
        # u_cos = 100*cos(5°) ≈ 99.619, v_sin = 100*sin(5°) ≈ 8.716
        # Check that the internal field contains values close to these
        assert "99.6" in u_content  # u_cos ≈ 99.62
        assert "8.7" in u_content  # v_sin ≈ 8.72

    def test_u_template_alpha_ten(self, tmp_path: Path) -> None:
        """Render U.naca.j2 at α=10° → u_cos ≈ 98.48, v_sin ≈ 17.36."""
        case = _make_naca_case(alpha_deg=10.0, case_id="naca0012_a10")
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case_src"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        u_content = (run_dir / "case" / "0" / "U").read_text(encoding="utf-8")
        assert "98.4" in u_content  # u_cos ≈ 98.48
        assert "17.3" in u_content  # v_sin ≈ 17.36


# === T02: Routing logic ===


class TestNacaRouting:
    """Tests for _is_naca_case() routing logic."""

    def test_is_naca_case_routing(self) -> None:
        """_is_naca_case returns True for naca0012* IDs, False otherwise."""
        adapter = OpenFOAMAdapter(dry_run=True)

        naca_a0 = _make_naca_case(alpha_deg=0.0, case_id="naca0012_a0")
        naca_a5 = _make_naca_case(alpha_deg=5.0, case_id="naca0012_a5")
        naca_a10 = _make_naca_case(alpha_deg=10.0, case_id="naca0012_a10")
        naca_a15 = _make_naca_case(alpha_deg=15.0, case_id="naca0012_a15")
        ldc = _make_ldc_case(case_id="test_cavity")

        assert adapter._is_naca_case(naca_a0) is True
        assert adapter._is_naca_case(naca_a5) is True
        assert adapter._is_naca_case(naca_a10) is True
        assert adapter._is_naca_case(naca_a15) is True
        assert adapter._is_naca_case(ldc) is False

    def test_prepare_naca_creates_trisurface(self, tmp_path: Path) -> None:
        """dry_run prepare creates constant/triSurface/naca0012.stl."""
        case = _make_naca_case(alpha_deg=5.0, case_id="naca0012_a5")
        adapter = OpenFOAMAdapter(dry_run=True)

        # Create a fake case dir with geometry/naca0012.stl
        case_dir = tmp_path / "case_src"
        geometry_dir = case_dir / "geometry"
        geometry_dir.mkdir(parents=True)
        stl_content = (
            "solid naca0012\n"
            "  facet normal 0 0 1\n"
            "    outer loop\n"
            "      vertex 0.0 0.0 0.0\n"
            "      vertex 1.0 0.0 0.0\n"
            "      vertex 0.5 0.05 0.0\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid naca0012\n"
        )
        (geometry_dir / "naca0012.stl").write_text(stl_content, encoding="utf-8")

        run_dir = tmp_path / "run"
        adapter.prepare(case, case_dir, run_dir)

        stl_dest = run_dir / "case" / "constant" / "triSurface" / "naca0012.stl"
        assert stl_dest.exists()
        assert "naca0012" in stl_dest.read_text(encoding="utf-8")

    def test_prepare_naca_creates_all_system_files(self, tmp_path: Path) -> None:
        """prepare renders all 5 NACA system/ files + blockMesh + snappy."""
        case = _make_naca_case(alpha_deg=5.0)
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case_src"
        case_dir.mkdir()
        geometry_dir = case_dir / "geometry"
        geometry_dir.mkdir()
        (geometry_dir / "naca0012.stl").write_text("dummy", encoding="utf-8")
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        system = run_dir / "case" / "system"
        assert (system / "controlDict").exists()
        assert (system / "fvSchemes").exists()
        assert (system / "fvSolution").exists()
        assert (system / "blockMeshDict").exists()
        assert (system / "snappyHexMeshDict").exists()

    def test_prepare_naca_creates_initial_fields(self, tmp_path: Path) -> None:
        """prepare renders 0/U, 0/p, 0/nuTilda for NACA."""
        case = _make_naca_case(alpha_deg=5.0)
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case_src"
        case_dir.mkdir()
        geometry_dir = case_dir / "geometry"
        geometry_dir.mkdir()
        (geometry_dir / "naca0012.stl").write_text("dummy", encoding="utf-8")
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        zero_dir = run_dir / "case" / "0"
        assert (zero_dir / "U").exists()
        assert (zero_dir / "p").exists()
        assert (zero_dir / "nuTilda").exists()

    def test_prepare_ldc_unchanged(self, tmp_path: Path) -> None:
        """LDC case still uses old template set (iron rule #1)."""
        case = _make_ldc_case()
        adapter = OpenFOAMAdapter(dry_run=True)
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        run_dir = tmp_path / "run"

        adapter.prepare(case, case_dir, run_dir)

        case_out = run_dir / "case"
        # LDC template files (not .naca.j2)
        assert (case_out / "system" / "controlDict").exists()
        assert (case_out / "system" / "fvSchemes").exists()
        # LDC should NOT have blockMeshDict or snappyHexMeshDict
        assert not (case_out / "system" / "blockMeshDict").exists()
        assert not (case_out / "system" / "snappyHexMeshDict").exists()
        # LDC 0/U should have uniform (0 0 0)
        u_content = (case_out / "0" / "U").read_text(encoding="utf-8")
        assert "uniform (0 0 0)" in u_content


# === T03: MetricsEngine qoi_computed_values ===


class TestMetricsQoiComputed:
    """Tests for MetricsEngine populating qoi_computed_values."""

    def test_metrics_qoi_computed_values(self) -> None:
        """MetricsEngine fills qoi_computed_values from artifacts."""
        case = _make_naca_case(alpha_deg=5.0)
        engine = MetricsEngine()

        artifacts = ArtifactManifest(
            files={},
            qoi_values={"cl": 0.42, "cd": 0.012},
            curves=None,
        )
        run_result = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=10.0,
        )

        result = engine.compute(case, artifacts, run_result)

        assert result.qoi_computed_values is not None
        assert result.qoi_computed_values["cl"] == pytest.approx(0.42)
        assert result.qoi_computed_values["cd"] == pytest.approx(0.012)

    def test_metrics_qoi_computed_none_when_no_qoi(self) -> None:
        """qoi_computed_values is None when artifacts have no qoi_values."""
        case = _make_naca_case(alpha_deg=5.0)
        engine = MetricsEngine()

        artifacts = ArtifactManifest(files={}, qoi_values=None, curves=None)
        run_result = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=10.0,
        )

        result = engine.compute(case, artifacts, run_result)

        # No computed QoI → None (or empty)
        assert result.qoi_computed_values is None

    def test_metrics_qoi_computed_dry_run(self) -> None:
        """qoi_computed_values is None in dry_run mode."""
        case = _make_naca_case(alpha_deg=5.0)
        engine = MetricsEngine()

        artifacts = ArtifactManifest(
            files={}, qoi_values={"cl": 0.42, "cd": 0.012}, curves=None
        )
        run_result = RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_time_sec=0.0,
            skipped_commands=["simpleFoam"],
        )

        result = engine.compute(case, artifacts, run_result)

        assert result.overall_status == "dry_run"
        assert result.qoi_computed_values is None


# === T04: collect_outputs NACA forces extraction ===


class TestCollectOutputsNaca:
    """Tests for collect_outputs() NACA forces.dat extraction routing."""

    def test_collect_outputs_naca_calls_extract(self, tmp_path: Path) -> None:
        """collect_outputs calls extract_cl_cd_openfoam for NACA cases."""
        case = _make_naca_case(alpha_deg=5.0)
        adapter = OpenFOAMAdapter(dry_run=False)

        run_dir = tmp_path / "run"
        case_dir_out = run_dir / "case"

        # Create a fake forces.dat
        forces_dir = case_dir_out / "postProcessing" / "forces" / "1000"
        forces_dir.mkdir(parents=True)
        forces_dat = forces_dir / "forces.dat"
        forces_dat.write_text(
            "# Forces\n"
            "# time forces (Fx Fy Fz) moments (Mx My Mz)\n"
            "0.000 (0.6125 -0.4560 0) (0 0 0.001)\n"
            "1.000 (0.6125 -0.4560 0) (0 0 0.001)\n",
            encoding="utf-8",
        )

        with patch(
            "cfdb.post.qoi_extractor.extract_cl_cd_openfoam",
            return_value=(0.42, 0.012),
        ) as mock_extract:
            artifacts = adapter.collect_outputs(case, run_dir)

        mock_extract.assert_called_once()
        args, kwargs = mock_extract.call_args
        # The first positional arg should be the forces.dat path
        assert "forces.dat" in str(args[0]) or "force.dat" in str(args[0])
        assert kwargs.get("rho") == 1.225
        assert kwargs.get("u_inf") == 100.0

        assert artifacts.qoi_values is not None
        assert artifacts.qoi_values["cl"] == pytest.approx(0.42)
        assert artifacts.qoi_values["cd"] == pytest.approx(0.012)

    def test_collect_outputs_naca_fallback_force_dat(self, tmp_path: Path) -> None:
        """collect_outputs falls back to force.dat (Foundation spelling)."""
        case = _make_naca_case(alpha_deg=5.0)
        adapter = OpenFOAMAdapter(dry_run=False)

        run_dir = tmp_path / "run"
        case_dir_out = run_dir / "case"

        # Create force.dat (Foundation spelling, no forces.dat)
        forces_dir = case_dir_out / "postProcessing" / "forces" / "500"
        forces_dir.mkdir(parents=True)
        force_dat = forces_dir / "force.dat"
        force_dat.write_text(
            "# time forces (Fx Fy Fz) moments (Mx My Mz)\n"
            "0.000 (0.6125 -0.4560 0) (0 0 0.001)\n",
            encoding="utf-8",
        )

        with patch(
            "cfdb.post.qoi_extractor.extract_cl_cd_openfoam",
            return_value=(0.42, 0.012),
        ):
            artifacts = adapter.collect_outputs(case, run_dir)

        assert artifacts.qoi_values is not None
        assert "cl" in artifacts.qoi_values

    def test_collect_outputs_naca_no_forces(self, tmp_path: Path) -> None:
        """collect_outputs returns empty qoi when no forces.dat found."""
        case = _make_naca_case(alpha_deg=5.0)
        adapter = OpenFOAMAdapter(dry_run=False)

        run_dir = tmp_path / "run"
        case_dir_out = run_dir / "case"
        case_dir_out.mkdir(parents=True)

        artifacts = adapter.collect_outputs(case, run_dir)

        assert artifacts.qoi_values is None or len(artifacts.qoi_values) == 0

    def test_collect_outputs_ldc_unchanged(self, tmp_path: Path) -> None:
        """collect_outputs still extracts centerline_umax for LDC cases."""
        case = _make_ldc_case()
        adapter = OpenFOAMAdapter(dry_run=False)

        run_dir = tmp_path / "run"
        case_dir_out = run_dir / "case"

        # Create fake probes output
        probes_dir = case_dir_out / "postProcessing" / "probes"
        probes_dir.mkdir(parents=True)
        (probes_dir / "U").write_text(
            "# Probe 0 (0.5 0.05 0)\n"
            "0.005  (0.0123 0.00456 0)\n"
            "0.010  (0.0234 0.00567 0)\n",
            encoding="utf-8",
        )

        artifacts = adapter.collect_outputs(case, run_dir)

        assert artifacts.qoi_values is not None
        assert "centerline_umax" in artifacts.qoi_values


# === T05: Schema backward compatibility ===


class TestSchemaBackwardCompat:
    """Tests for qoi_computed_values Optional field backward compatibility."""

    def test_metrics_result_defaults_none(self) -> None:
        """MetricsResult.qoi_computed_values defaults to None."""
        from cfdb.schema import MetricsResult

        result = MetricsResult()
        assert result.qoi_computed_values is None

    def test_metrics_result_accepts_values(self) -> None:
        """MetricsResult accepts qoi_computed_values dict."""
        from cfdb.schema import MetricsResult

        result = MetricsResult(qoi_computed_values={"cl": 0.5, "cd": 0.01})
        assert result.qoi_computed_values is not None
        assert result.qoi_computed_values["cl"] == 0.5

    def test_metrics_result_json_roundtrip(self) -> None:
        """MetricsResult with qoi_computed_values survives JSON roundtrip."""
        from cfdb.schema import MetricsResult

        result = MetricsResult(qoi_computed_values={"cl": 0.5, "cd": 0.01})
        json_str = result.model_dump_json()
        restored = MetricsResult.model_validate_json(json_str)
        assert restored.qoi_computed_values is not None
        assert restored.qoi_computed_values["cl"] == pytest.approx(0.5)
