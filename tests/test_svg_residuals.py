"""Tests for cfdb.reporting.svg_residuals — pure Python SVG renderer."""

from __future__ import annotations

from cfdb.reporting.svg_residuals import render_residual_svg

_OKABE_ITO = [
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#F0E442",
    "#000000",
]


class TestRenderResidualSvg:
    def test_basic_render(self) -> None:
        """Basic SVG generation with 2 fields."""
        residuals = {
            "Ux": [1e-1, 1e-2, 1e-3, 1e-4, 1e-5],
            "p": [1e-2, 1e-3, 1e-4, 1e-5, 1e-6],
        }
        svg = render_residual_svg(residuals, title="Test Convergence", log_scale=True)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "0 0 680 400" in svg

    def test_viewbox(self) -> None:
        """SVG has correct viewBox attribute."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals)
        assert 'viewBox="0 0 680 400"' in svg

    def test_title_in_svg(self) -> None:
        """Title appears in SVG."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals, title="My Custom Title")
        assert "My Custom Title" in svg

    def test_field_names_in_legend(self) -> None:
        """Field names appear in SVG legend."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3], "Uy": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals)
        assert "Ux" in svg
        assert "Uy" in svg

    def test_okabe_ito_colors(self) -> None:
        """At least one Okabe-Ito color appears in the SVG."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3], "p": [1e-2, 1e-3, 1e-4]}
        svg = render_residual_svg(residuals)
        found = any(color in svg for color in _OKABE_ITO)
        assert found, "No Okabe-Ito color found in SVG"

    def test_polyline_elements(self) -> None:
        """Polyline elements are generated for data curves."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3, 1e-4, 1e-5]}
        svg = render_residual_svg(residuals)
        assert "<polyline" in svg

    def test_log_scale_labels(self) -> None:
        """Log scale Y-axis labels contain '1e' notation."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3, 1e-4, 1e-5]}
        svg = render_residual_svg(residuals, log_scale=True)
        assert "1e-" in svg

    def test_linear_scale(self) -> None:
        """Linear scale mode renders without error."""
        residuals = {"Ux": [0.5, 0.3, 0.1, 0.05]}
        svg = render_residual_svg(residuals, log_scale=False)
        assert "<svg" in svg
        assert "<polyline" in svg

    def test_empty_data_returns_placeholder(self) -> None:
        """Empty dict returns placeholder SVG."""
        svg = render_residual_svg({})
        assert "<svg" in svg
        assert "No residual data" in svg

    def test_single_point_returns_placeholder(self) -> None:
        """Single data point (max_iters < 2) returns placeholder."""
        svg = render_residual_svg({"Ux": [1e-3]})
        assert "No residual data" in svg

    def test_xml_escaping_in_title(self) -> None:
        """Title with XML special chars is escaped."""
        residuals = {"Ux": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals, title="Test <script>")
        assert "&lt;script&gt;" in svg
        assert "<script>" not in svg.replace("<svg", "").replace("</svg", "")

    def test_xml_escaping_in_field_name(self) -> None:
        """Field name with special chars is escaped."""
        residuals = {"U<x": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals)
        assert "&lt;" in svg

    def test_many_fields(self) -> None:
        """More than 8 fields cycle through palette."""
        residuals = {f"f{i}": [1e-1, 1e-2, 1e-3] for i in range(10)}
        svg = render_residual_svg(residuals)
        assert "<svg" in svg
        for i in range(10):
            assert f"f{i}" in svg

    def test_html_embeddable(self) -> None:
        """SVG is valid for HTML embedding (no DOCTYPE, no external CSS refs).

        Note: xmlns="http://www.w3.org/2000/svg" is a required XML namespace
        attribute, not an external resource reference.
        """
        residuals = {"Ux": [1e-1, 1e-2, 1e-3]}
        svg = render_residual_svg(residuals)
        assert not svg.strip().startswith("<!DOCTYPE")
        # No external stylesheet/script references (only xmlns is allowed)
        assert "src=" not in svg
        assert "href=" not in svg
        assert "<link" not in svg
        assert "<script" not in svg

    def test_skip_nonpositive_log(self) -> None:
        """Non-positive values are skipped on log scale (no crash)."""
        residuals = {"Ux": [1e-1, 0.0, 1e-3, -1.0, 1e-5]}
        svg = render_residual_svg(residuals, log_scale=True)
        assert "<polyline" in svg
