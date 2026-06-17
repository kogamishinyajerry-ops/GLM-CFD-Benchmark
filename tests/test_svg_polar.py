"""Tests for cfdb.reporting.svg_polar (P2-c)."""

from __future__ import annotations

from cfdb.reporting.svg_polar import (
    PolarCurve,
    PolarPoint,
    render_polar_svg,
)


class TestRenderPolarSvg:
    """Tests for render_polar_svg."""

    def test_empty_curves_and_no_reference_returns_placeholder(self) -> None:
        svg = render_polar_svg(curves=[], reference=None)
        assert "<svg" in svg
        assert "No polar data to display" in svg

    def test_single_curve_renders_subplots(self) -> None:
        curve = PolarCurve(
            solver="OpenFOAM",
            points=[
                PolarPoint(0.0, 0.0, 0.0086),
                PolarPoint(5.0, 0.456, 0.0095),
                PolarPoint(10.0, 0.862, 0.0125),
            ],
        )
        svg = render_polar_svg(curves=[curve])
        assert "<svg" in svg
        assert "</svg>" in svg
        # Both subplot titles should be present
        assert "Lift Coefficient" in svg
        assert "Drag Coefficient" in svg
        # Solver name in legend
        assert "OpenFOAM" in svg
        # Curve color (Okabe-Ito blue = first color)
        assert "#0072B2" in svg

    def test_multiple_curves_get_different_colors(self) -> None:
        c1 = PolarCurve(solver="OF", points=[PolarPoint(0.0, 0.0, 0.01)])
        c2 = PolarCurve(solver="SU2", points=[PolarPoint(0.0, 0.0, 0.01)])
        svg = render_polar_svg(curves=[c1, c2])
        # Both Okabe-Ito blue + vermillion should appear
        assert "#0072B2" in svg
        assert "#D55E00" in svg

    def test_reference_curve_uses_dashed_black(self) -> None:
        curve = PolarCurve(solver="OF", points=[PolarPoint(0.0, 0.0, 0.01)])
        ref = PolarCurve(
            solver="Ladson 1988",
            points=[PolarPoint(0.0, 0.0, 0.0086)],
            is_reference=True,
        )
        svg = render_polar_svg(curves=[curve], reference=ref)
        # Dashed pattern + black stroke for reference
        assert 'stroke-dasharray="5 3"' in svg
        assert "Ladson 1988" in svg

    def test_viewbox_dimensions(self) -> None:
        """viewBox must be 680x800 for dual subplot layout."""
        curve = PolarCurve(solver="X", points=[PolarPoint(0.0, 0.0, 0.01)])
        svg = render_polar_svg(curves=[curve])
        assert 'viewBox="0 0 680 800"' in svg

    def test_xml_escaping_in_solver_name(self) -> None:
        """Solver names with < > & should be escaped."""
        curve = PolarCurve(solver="<weird&solver>", points=[PolarPoint(0.0, 0.0, 0.01)])
        svg = render_polar_svg(curves=[curve])
        assert "<weird" not in svg  # raw < would break XML
        assert "&lt;weird&amp;solver&gt;" in svg

    def test_empty_curve_points_skipped(self) -> None:
        """A curve with no points should not crash, just not render a line."""
        empty = PolarCurve(solver="Empty", points=[])
        full = PolarCurve(solver="OF", points=[PolarPoint(0.0, 0.0, 0.01)])
        svg = render_polar_svg(curves=[empty, full])
        assert "<svg" in svg
