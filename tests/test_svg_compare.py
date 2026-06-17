"""Tests for cfdb.reporting.svg_compare (P2-c)."""

from __future__ import annotations

from cfdb.reporting.svg_compare import (
    render_cp_comparison_svg,
    render_residual_comparison_svg,
)


class TestRenderCpComparison:
    """Tests for render_cp_comparison_svg."""

    def test_empty_input_returns_placeholder(self) -> None:
        svg = render_cp_comparison_svg(solver_data={}, reference_data=None)
        assert "<svg" in svg
        assert "No Cp data to compare" in svg

    def test_single_solver(self) -> None:
        svg = render_cp_comparison_svg(
            solver_data={"OpenFOAM": ([0.0, 0.5, 1.0], [1.0, -0.5, 0.05])}
        )
        assert "<svg" in svg
        assert "OpenFOAM" in svg
        assert "#0072B2" in svg  # Okabe-Ito blue for first solver

    def test_multiple_solvers_different_colors(self) -> None:
        svg = render_cp_comparison_svg(
            solver_data={
                "OpenFOAM": ([0.0, 1.0], [1.0, 0.0]),
                "SU2": ([0.0, 1.0], [0.9, 0.1]),
            }
        )
        assert "#0072B2" in svg
        assert "#D55E00" in svg
        assert "OpenFOAM" in svg
        assert "SU2" in svg

    def test_with_reference(self) -> None:
        svg = render_cp_comparison_svg(
            solver_data={"OF": ([0.0, 1.0], [1.0, 0.0])},
            reference_data=([0.0, 0.5, 1.0], [1.0, -0.4, 0.05]),
        )
        assert 'stroke-dasharray="5 3"' in svg  # reference dashed
        assert "Reference" in svg

    def test_empty_solver_data_lists_return_placeholder(self) -> None:
        svg = render_cp_comparison_svg(
            solver_data={"OF": ([], [])}
        )
        assert "No Cp data to compare" in svg

    def test_cp_axis_label_present(self) -> None:
        svg = render_cp_comparison_svg(
            solver_data={"OF": ([0.0, 1.0], [1.0, 0.0])}
        )
        assert ">Cp<" in svg
        assert ">x/c<" in svg


class TestRenderResidualComparison:
    """Tests for render_residual_comparison_svg."""

    def test_empty_input_returns_placeholder(self) -> None:
        svg = render_residual_comparison_svg(solver_data={})
        assert "No residual data to compare" in svg

    def test_single_solver_single_field(self) -> None:
        svg = render_residual_comparison_svg(
            solver_data={"OF": {"Ux": [1e-2, 1e-3, 1e-4]}}
        )
        assert "<svg" in svg
        assert "OF:Ux" in svg  # legend label

    def test_multiple_solvers(self) -> None:
        svg = render_residual_comparison_svg(
            solver_data={
                "OF": {"Ux": [1e-2, 1e-3]},
                "SU2": {"RMS_DENSITY": [1e-1, 1e-2]},
            }
        )
        assert "OF:Ux" in svg
        assert "SU2:RMS_DENSITY" in svg

    def test_all_non_positive_log_scale_returns_placeholder(self) -> None:
        """log_scale=True with all non-positive values cannot render."""
        svg = render_residual_comparison_svg(
            solver_data={"OF": {"Ux": [-1.0, -2.0]}},
            log_scale=True,
        )
        assert "non-positive" in svg

    def test_linear_scale_renders_with_negatives(self) -> None:
        svg = render_residual_comparison_svg(
            solver_data={"OF": {"Ux": [-1.0, 1.0]}},
            log_scale=False,
        )
        assert "<svg" in svg
        assert "residual" in svg  # axis label
