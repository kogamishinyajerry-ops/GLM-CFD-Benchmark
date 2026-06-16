"""Polar curve SVG renderer (Cl-α and Cd-α dual subplot) — pure Python, zero deps.

P2-c feature. Reuses P2-a svg_residuals aesthetic conventions (Okabe-Ito
colorblind-safe palette, viewBox, axis labels, escaped text).

Layout: viewBox 680x800. Upper subplot = Cl-α (height 360), lower subplot = Cd-α
(height 360), separated by 40px gap. Title row on top (40px), axis labels at
bottom of each subplot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Reuse the same Okabe-Ito palette as svg_residuals for consistency
_OKABE_ITO = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black (reserved for reference)
]

_VIEW_W = 680
_VIEW_H = 800
_MARGIN_LEFT = 70
_MARGIN_RIGHT = 30
_MARGIN_TOP = 50
_SUBPLOT_H = 360
_SUBPLOT_GAP = 30
_BOTTOM_MARGIN = 40


@dataclass
class PolarPoint:
    """Single point on a polar curve."""

    alpha_deg: float
    cl: float
    cd: float


@dataclass
class PolarCurve:
    """Polar curve for one solver (or reference): list of PolarPoint."""

    solver: str
    points: list[PolarPoint] = field(default_factory=list)
    color: str | None = None  # auto-assigned if None
    is_reference: bool = False  # reference curve rendered as dashed


def _escape_xml(text: str) -> str:
    """Escape XML special characters in text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _linear_map(
    val: float, val_min: float, val_max: float, plot_min: float, plot_max: float
) -> float:
    """Linear interpolation from val range to plot pixel range."""
    if val_max == val_min:
        return (plot_min + plot_max) / 2
    return plot_min + (val - val_min) * (plot_max - plot_min) / (val_max - val_min)


def _nice_range(values: list[float], padding: float = 0.1) -> tuple[float, float]:
    """Compute a nice [min, max] range from a list of values with padding."""
    if not values:
        return 0.0, 1.0
    vmin = min(values)
    vmax = max(values)
    span = vmax - vmin
    if span < 1e-9:
        span = max(abs(vmin), 1.0)
    pad = span * padding
    return vmin - pad, vmax + pad


def _build_subplot(
    curves: list[PolarCurve],
    reference: PolarCurve | None,
    y_values_key: str,  # "cl" or "cd"
    y_label: str,
    subplot_origin_y: float,
    title: str,
    x_min: float = 0.0,
    x_max: float = 15.0,
) -> str:
    """Build SVG for one subplot (Cl-α or Cd-α).

    Args:
        curves: Solver curves (solid lines + circles).
        reference: Optional reference curve (dashed).
        y_values_key: "cl" or "cd" — which PolarPoint attribute to plot on Y.
        y_label: Y-axis label text.
        subplot_origin_y: Top-Y pixel of this subplot.
        title: Subplot title (drawn at top).
        x_min, x_max: X-axis (alpha_deg) range.

    Returns:
        SVG fragment string for this subplot.
    """
    plot_left = _MARGIN_LEFT
    plot_right = _VIEW_W - _MARGIN_RIGHT
    plot_top = subplot_origin_y + 50  # leave room for subplot title
    plot_bottom = subplot_origin_y + _SUBPLOT_H - 40
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top

    # Gather all Y values to compute range
    all_y: list[float] = []
    for c in curves:
        all_y.extend(getattr(p, y_values_key) for p in c.points)
    if reference is not None:
        all_y.extend(getattr(p, y_values_key) for p in reference.points)
    if not all_y:
        # Placeholder subplot
        return (
            f'<text x="{_VIEW_W / 2:.0f}" y="{subplot_origin_y + _SUBPLOT_H / 2:.0f}" '
            f'text-anchor="middle" fill="#999" font-size="14">No data for {title}</text>'
        )

    y_min, y_max = _nice_range(all_y, padding=0.15)

    parts: list[str] = []
    parts.append(
        f'<text x="{plot_left + plot_w / 2:.0f}" y="{subplot_origin_y + 25:.0f}" '
        f'text-anchor="middle" font-size="16" font-weight="bold" fill="#333">'
        f"{_escape_xml(title)}</text>"
    )

    # Plot border
    parts.append(
        f'<rect x="{plot_left}" y="{plot_top}" width="{plot_w}" height="{plot_h}" '
        f'fill="none" stroke="#666" stroke-width="1"/>'
    )

    # Y-axis ticks (5 divisions)
    for i in range(6):
        frac = i / 5
        y_val = y_min + frac * (y_max - y_min)
        y_px = _linear_map(y_val, y_min, y_max, plot_bottom, plot_top)
        parts.append(
            f'<line x1="{plot_left}" y1="{y_px:.1f}" x2="{plot_right}" y2="{y_px:.1f}" '
            f'stroke="#e0e0e0" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{plot_left - 8}" y="{y_px + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#666">{y_val:.3f}</text>'
        )

    # X-axis ticks (0, 3, 6, 9, 12, 15)
    for x_tick in [0, 3, 6, 9, 12, 15]:
        if x_tick < x_min or x_tick > x_max:
            continue
        x_px = _linear_map(x_tick, x_min, x_max, plot_left, plot_right)
        parts.append(
            f'<line x1="{x_px:.1f}" y1="{plot_top}" x2="{x_px:.1f}" y2="{plot_bottom}" '
            f'stroke="#e0e0e0" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{x_px:.1f}" y="{plot_bottom + 18}" text-anchor="middle" '
            f'font-size="11" fill="#666">{x_tick}</text>'
        )

    # Axis labels
    parts.append(
        f'<text x="{plot_left - 50}" y="{(plot_top + plot_bottom) / 2:.0f}" '
        f'text-anchor="middle" font-size="13" fill="#333" '
        f'transform="rotate(-90 {plot_left - 50} {(plot_top + plot_bottom) / 2:.0f})">'
        f"{_escape_xml(y_label)}</text>"
    )
    parts.append(
        f'<text x="{(plot_left + plot_right) / 2:.0f}" y="{plot_bottom + 35}" '
        f'text-anchor="middle" font-size="13" fill="#333">α (degrees)</text>'
    )

    # Draw reference (dashed black) first, behind solver curves
    if reference is not None and reference.points:
        pts = sorted(reference.points, key=lambda p: p.alpha_deg)
        path_parts: list[str] = []
        for i, p in enumerate(pts):
            x_px = _linear_map(p.alpha_deg, x_min, x_max, plot_left, plot_right)
            y_val = getattr(p, y_values_key)
            y_px = _linear_map(y_val, y_min, y_max, plot_bottom, plot_top)
            cmd = "M" if i == 0 else "L"
            path_parts.append(f"{cmd} {x_px:.1f} {y_px:.1f}")
        if path_parts:
            parts.append(
                f'<path d="{" ".join(path_parts)}" fill="none" stroke="#000" '
                f'stroke-width="1.5" stroke-dasharray="5 3"/>'
            )
        # Reference points as open circles
        for p in pts:
            x_px = _linear_map(p.alpha_deg, x_min, x_max, plot_left, plot_right)
            y_val = getattr(p, y_values_key)
            y_px = _linear_map(y_val, y_min, y_max, plot_bottom, plot_top)
            parts.append(
                f'<circle cx="{x_px:.1f}" cy="{y_px:.1f}" r="4" '
                f'fill="white" stroke="#000" stroke-width="1.5"/>'
            )

    # Draw solver curves
    for i, curve in enumerate(curves):
        if not curve.points:
            continue
        color = curve.color or _OKABE_ITO[i % len(_OKABE_ITO)]
        pts = sorted(curve.points, key=lambda p: p.alpha_deg)
        # Line
        path_parts = []
        for j, p in enumerate(pts):
            x_px = _linear_map(p.alpha_deg, x_min, x_max, plot_left, plot_right)
            y_val = getattr(p, y_values_key)
            y_px = _linear_map(y_val, y_min, y_max, plot_bottom, plot_top)
            cmd = "M" if j == 0 else "L"
            path_parts.append(f"{cmd} {x_px:.1f} {y_px:.1f}")
        if path_parts:
            parts.append(
                f'<path d="{" ".join(path_parts)}" fill="none" stroke="{color}" '
                f'stroke-width="2"/>'
            )
        # Filled circles for data points
        for p in pts:
            x_px = _linear_map(p.alpha_deg, x_min, x_max, plot_left, plot_right)
            y_val = getattr(p, y_values_key)
            y_px = _linear_map(y_val, y_min, y_max, plot_bottom, plot_top)
            parts.append(
                f'<circle cx="{x_px:.1f}" cy="{y_px:.1f}" r="4" fill="{color}"/>'
            )

    return "\n".join(parts)


def render_polar_svg(
    curves: list[PolarCurve],
    reference: PolarCurve | None = None,
    title: str = "Lift/Drag Polar — NACA0012",
) -> str:
    """Render Cl-α + Cd-α dual subplot as a single SVG string.

    Layout: viewBox 680x800.
    - Top: title (40px)
    - Subplot 1: Cl-α (height ~360)
    - Gap (30px)
    - Subplot 2: Cd-α (height ~360)
    - Bottom margin (40px)
    - Legend at bottom

    Args:
        curves: One PolarCurve per solver (OpenFOAM / SU2 / etc.).
        reference: Optional Ladson 1988 reference (dashed black with open circles).
        title: Top-of-SVG title.

    Returns:
        SVG string. Empty curves + None reference returns placeholder SVG.
    """
    if not curves and reference is None:
        return (
            f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
            f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
            f'fill="#999" font-size="14">No polar data to display</text>'
            f"</svg>"
        )

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
    )
    parts.append(f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>')

    # Main title
    parts.append(
        f'<text x="{_VIEW_W / 2}" y="25" text-anchor="middle" font-size="18" '
        f'font-weight="bold" fill="#222">{_escape_xml(title)}</text>'
    )

    # Subplot 1: Cl-α
    subplot1_origin_y = _MARGIN_TOP
    parts.append(
        _build_subplot(
            curves=curves,
            reference=reference,
            y_values_key="cl",
            y_label="Cl (lift coefficient)",
            subplot_origin_y=subplot1_origin_y,
            title="Lift Coefficient vs Angle of Attack",
        )
    )

    # Subplot 2: Cd-α
    subplot2_origin_y = subplot1_origin_y + _SUBPLOT_H + _SUBPLOT_GAP
    parts.append(
        _build_subplot(
            curves=curves,
            reference=reference,
            y_values_key="cd",
            y_label="Cd (drag coefficient)",
            subplot_origin_y=subplot2_origin_y,
            title="Drag Coefficient vs Angle of Attack",
        )
    )

    # Legend at very bottom
    legend_y = _VIEW_H - 15
    legend_x = _MARGIN_LEFT
    legend_parts: list[str] = []
    legend_parts.append(
        f'<text x="{legend_x}" y="{legend_y}" font-size="12" fill="#333" font-weight="bold">'
        f"Legend:</text>"
    )
    x_cursor = legend_x + 60
    for i, c in enumerate(curves):
        color = c.color or _OKABE_ITO[i % len(_OKABE_ITO)]
        legend_parts.append(
            f'<line x1="{x_cursor}" y1="{legend_y - 4}" x2="{x_cursor + 18}" y2="{legend_y - 4}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        legend_parts.append(
            f'<circle cx="{x_cursor + 9}" cy="{legend_y - 4}" r="3" fill="{color}"/>'
        )
        legend_parts.append(
            f'<text x="{x_cursor + 24}" y="{legend_y}" font-size="12" fill="#333">'
            f"{_escape_xml(c.solver)}</text>"
        )
        x_cursor += 24 + len(c.solver) * 7 + 20
    if reference is not None:
        legend_parts.append(
            f'<line x1="{x_cursor}" y1="{legend_y - 4}" x2="{x_cursor + 18}" y2="{legend_y - 4}" '
            f'stroke="#000" stroke-width="1.5" stroke-dasharray="5 3"/>'
        )
        legend_parts.append(
            f'<circle cx="{x_cursor + 9}" cy="{legend_y - 4}" r="3.5" fill="white" '
            f'stroke="#000" stroke-width="1.5"/>'
        )
        legend_parts.append(
            f'<text x="{x_cursor + 24}" y="{legend_y}" font-size="12" fill="#333">'
            f"{_escape_xml(reference.solver)}</text>"
        )

    parts.extend(legend_parts)

    parts.append("</svg>")
    return "\n".join(parts)
