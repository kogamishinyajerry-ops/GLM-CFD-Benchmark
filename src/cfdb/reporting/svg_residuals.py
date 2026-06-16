"""Residual convergence SVG renderer — pure Python, zero dependencies.

Generates a standalone SVG string showing residual convergence curves.
Designed for embedding into HTML reports.

Color palette: Okabe-Ito colorblind-safe (8 colors).
"""

from __future__ import annotations

import math

# Okabe-Ito colorblind-safe palette
_OKABE_ITO = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black
]

# SVG layout constants
_VIEW_W = 680
_VIEW_H = 400
_MARGIN_LEFT = 70
_MARGIN_RIGHT = 30
_MARGIN_TOP = 50
_MARGIN_BOTTOM = 60
_PLOT_W = _VIEW_W - _MARGIN_LEFT - _MARGIN_RIGHT
_PLOT_H = _VIEW_H - _MARGIN_TOP - _MARGIN_BOTTOM


def _escape_xml(text: str) -> str:
    """Escape XML special characters in text.

    Args:
        text: Raw text that may contain XML special chars.

    Returns:
        Escaped text safe for XML/SVG.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def render_residual_svg(
    residuals: dict[str, list[float]],
    title: str = "Residual Convergence",
    log_scale: bool = True,
) -> str:
    """Render residual convergence curves as an SVG string.

    Args:
        residuals: Dict mapping field name to list of residual values over iterations.
                   Example: ``{'Ux': [0.1, 0.05, ..., 1.2e-6], 'p': [...]}``
        title: Chart title.
        log_scale: If True, Y-axis uses logarithmic scale (recommended for residuals).

    Returns:
        SVG string (viewBox="0 0 680 400"), suitable for direct HTML embedding.
        Returns placeholder SVG with "No residual data" message if residuals is empty.
    """
    if not residuals:
        return _render_empty_svg(title)

    # Determine max iteration count across all fields
    max_iters = max(len(v) for v in residuals.values())
    if max_iters < 2:
        return _render_empty_svg(title)

    # Compute Y-axis range (log scale)
    all_values = [v for values in residuals.values() for v in values if v > 0]
    if not all_values:
        return _render_empty_svg(title)

    if log_scale:
        y_min = math.log10(min(all_values))
        y_max = math.log10(max(all_values))
        if y_max - y_min < 1:
            y_max = y_min + 1  # Ensure at least 1 decade range
    else:
        y_min = 0.0
        y_max = max(all_values) * 1.1

    def x_map(iter_idx: int) -> float:
        """Map iteration index to SVG X coordinate."""
        if max_iters <= 1:
            return _MARGIN_LEFT
        return _MARGIN_LEFT + (iter_idx / (max_iters - 1)) * _PLOT_W

    def y_map(value: float) -> float:
        """Map residual value to SVG Y coordinate."""
        if log_scale:
            if value <= 0:
                return _MARGIN_TOP + _PLOT_H  # Clamp to bottom
            log_val = math.log10(value)
        else:
            log_val = value
        frac = (log_val - y_min) / (y_max - y_min) if y_max != y_min else 0.5
        return _MARGIN_TOP + _PLOT_H * (1.0 - frac)

    # Build SVG parts
    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;">'
    )

    # --- Title ---
    parts.append(
        f'<text x="{_VIEW_W / 2}" y="25" text-anchor="middle" '
        f'font-size="16" font-weight="bold" fill="#1a1a2e" '
        f'font-family="sans-serif">{_escape_xml(title)}</text>'
    )

    # --- Plot area background ---
    parts.append(
        f'<rect x="{_MARGIN_LEFT}" y="{_MARGIN_TOP}" '
        f'width="{_PLOT_W}" height="{_PLOT_H}" '
        f'fill="#fafafa" stroke="#ccc" stroke-width="1"/>'
    )

    # --- Grid lines + Y-axis labels ---
    if log_scale:
        decade_start = math.floor(y_min)
        decade_end = math.ceil(y_max)
        for decade in range(decade_start, decade_end + 1):
            y = _MARGIN_TOP + _PLOT_H * (1.0 - (decade - y_min) / (y_max - y_min))
            if _MARGIN_TOP <= y <= _MARGIN_TOP + _PLOT_H:
                parts.append(
                    f'<line x1="{_MARGIN_LEFT}" y1="{y:.1f}" '
                    f'x2="{_MARGIN_LEFT + _PLOT_W}" y2="{y:.1f}" '
                    f'stroke="#e0e0e0" stroke-width="1" stroke-dasharray="3,3"/>'
                )
                label = f"1e{decade}"
                parts.append(
                    f'<text x="{_MARGIN_LEFT - 8}" y="{y + 4:.1f}" '
                    f'text-anchor="end" font-size="10" fill="#666" '
                    f'font-family="monospace">{label}</text>'
                )
    else:
        for i in range(6):
            y = _MARGIN_TOP + (_PLOT_H / 5) * i
            val = y_max * (1 - i / 5)
            parts.append(
                f'<line x1="{_MARGIN_LEFT}" y1="{y:.1f}" '
                f'x2="{_MARGIN_LEFT + _PLOT_W}" y2="{y:.1f}" '
                f'stroke="#e0e0e0" stroke-width="1" stroke-dasharray="3,3"/>'
            )
            parts.append(
                f'<text x="{_MARGIN_LEFT - 8}" y="{y + 4:.1f}" '
                f'text-anchor="end" font-size="10" fill="#666" '
                f'font-family="monospace">{val:.2e}</text>'
            )

    # --- X-axis labels ---
    x_tick_count = min(5, max_iters)
    for i in range(x_tick_count + 1):
        iter_idx = (
            int((max_iters - 1) * i / x_tick_count) if max_iters > 1 else 0
        )
        x = x_map(iter_idx)
        parts.append(
            f'<line x1="{x:.1f}" y1="{_MARGIN_TOP + _PLOT_H}" '
            f'x2="{x:.1f}" y2="{_MARGIN_TOP + _PLOT_H + 5}" '
            f'stroke="#999" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{_MARGIN_TOP + _PLOT_H + 18}" '
            f'text-anchor="middle" font-size="10" fill="#666" '
            f'font-family="monospace">{iter_idx}</text>'
        )

    # --- Axis labels ---
    parts.append(
        f'<text x="{_MARGIN_LEFT + _PLOT_W / 2}" y="{_VIEW_H - 10}" '
        f'text-anchor="middle" font-size="12" fill="#333" '
        f'font-family="sans-serif">Iteration</text>'
    )
    y_label = "Residual (log\u2081\u2080)" if log_scale else "Residual"
    parts.append(
        f'<text x="20" y="{_MARGIN_TOP + _PLOT_H / 2}" '
        f'text-anchor="middle" font-size="12" fill="#333" '
        f'font-family="sans-serif" transform="rotate(-90, 20, '
        f'{_MARGIN_TOP + _PLOT_H / 2})">{y_label}</text>'
    )

    # --- Data curves ---
    for idx, (field_name, values) in enumerate(residuals.items()):
        color = _OKABE_ITO[idx % len(_OKABE_ITO)]
        points: list[str] = []
        for i, val in enumerate(values):
            if val <= 0 and log_scale:
                continue  # Skip non-positive values on log scale
            x = x_map(i)
            y = y_map(val)
            points.append(f"{x:.1f},{y:.1f}")

        if len(points) >= 2:
            polyline_points = " ".join(points)
            parts.append(
                f'<polyline points="{polyline_points}" '
                f'fill="none" stroke="{color}" stroke-width="1.8" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )

    # --- Legend ---
    legend_x = _MARGIN_LEFT + _PLOT_W - 120
    legend_y = _MARGIN_TOP + 15
    legend_height = len(residuals) * 18 + 8
    parts.append(
        f'<rect x="{legend_x - 8}" y="{legend_y - 12}" '
        f'width="125" height="{legend_height}" '
        f'fill="rgba(255,255,255,0.9)" stroke="#ccc" rx="4"/>'
    )
    for idx, field_name in enumerate(residuals):
        color = _OKABE_ITO[idx % len(_OKABE_ITO)]
        ly = legend_y + idx * 18
        parts.append(
            f'<line x1="{legend_x}" y1="{ly}" x2="{legend_x + 20}" y2="{ly}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{legend_x + 26}" y="{ly + 4}" font-size="11" '
            f'fill="#333" font-family="sans-serif">{_escape_xml(field_name)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _render_empty_svg(title: str) -> str:
    """Render an SVG with a 'No residual data' message.

    Args:
        title: Chart title to display.

    Returns:
        SVG string with placeholder message.
    """
    return (
        f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;">'
        f'<text x="{_VIEW_W / 2}" y="25" text-anchor="middle" '
        f'font-size="16" font-weight="bold" fill="#1a1a2e" '
        f'font-family="sans-serif">{_escape_xml(title)}</text>'
        f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
        f'font-size="14" fill="#999" font-family="sans-serif">'
        f"No residual data available</text>"
        f"</svg>"
    )
