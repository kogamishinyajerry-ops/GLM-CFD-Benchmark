"""Multi-solver comparison SVG generators — pure Python, zero deps.

P2-c feature. Provides:

- render_cp_comparison_svg: Cp vs x/c with multiple solvers + reference overlay
- render_residual_comparison_svg: residual history curves from multiple runs

Reuses P2-a svg_residuals Okabe-Ito palette + escape conventions.
"""

from __future__ import annotations

import math

_OKABE_ITO = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black (reference)
]

_VIEW_W = 680
_VIEW_H = 400
_MARGIN_LEFT = 70
_MARGIN_RIGHT = 30
_MARGIN_TOP = 50
_MARGIN_BOTTOM = 60
_PLOT_W = _VIEW_W - _MARGIN_LEFT - _MARGIN_RIGHT
_PLOT_H = _VIEW_H - _MARGIN_TOP - _MARGIN_BOTTOM


def _escape_xml(text: str) -> str:
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
    if val_max == val_min:
        return (plot_min + plot_max) / 2
    return plot_min + (val - val_min) * (plot_max - plot_min) / (val_max - val_min)


def render_cp_comparison_svg(
    solver_data: dict[str, tuple[list[float], list[float]]],
    reference_data: tuple[list[float], list[float]] | None = None,
    title: str = "Cp Distribution Comparison",
) -> str:
    """Render Cp vs x/c with multiple solver curves + optional reference overlay.

    Cp convention: negative Cp (suction) is plotted upward (aerodynamics convention).

    Args:
        solver_data: Dict of solver_name -> (x_list, cp_list). Each solver gets
            one Okabe-Ito color, solid line.
        reference_data: Optional (x_list, cp_list) of experimental reference,
            rendered as dashed black open circles.

    Returns:
        SVG string. Empty input returns placeholder.
    """
    if not solver_data and reference_data is None:
        return (
            f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
            f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
            f'fill="#999" font-size="14">No Cp data to compare</text>'
            f"</svg>"
        )

    # Gather all x and cp values
    all_x: list[float] = []
    all_cp: list[float] = []
    for x_list, cp_list in solver_data.values():
        all_x.extend(x_list)
        all_cp.extend(cp_list)
    if reference_data is not None:
        all_x.extend(reference_data[0])
        all_cp.extend(reference_data[1])

    if not all_x or not all_cp:
        return (
            f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
            f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
            f'fill="#999" font-size="14">No Cp data to compare</text>'
            f"</svg>"
        )

    x_min = 0.0
    x_max = max(1.0, max(all_x))
    # Cp convention: invert Y (negative Cp up)
    cp_min = min(all_cp) - 0.2
    cp_max = max(all_cp) + 0.2

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
    )
    parts.append(f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>')
    parts.append(
        f'<text x="{_VIEW_W / 2}" y="25" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="#222">{_escape_xml(title)}</text>'
    )

    plot_left = _MARGIN_LEFT
    plot_right = _VIEW_W - _MARGIN_RIGHT
    plot_top = _MARGIN_TOP
    plot_bottom = _VIEW_H - _MARGIN_BOTTOM

    # Plot border
    parts.append(
        f'<rect x="{plot_left}" y="{plot_top}" width="{_PLOT_W}" height="{_PLOT_H}" '
        f'fill="none" stroke="#666" stroke-width="1"/>'
    )

    # Y-axis grid + labels (Cp, inverted)
    cp_step = 0.5
    cp_lo = math.floor(cp_min / cp_step) * cp_step
    cp_hi = math.ceil(cp_max / cp_step) * cp_step
    cp_val = cp_lo
    while cp_val <= cp_hi + 1e-9:
        y_px = _linear_map(cp_val, cp_min, cp_max, plot_bottom, plot_top)
        parts.append(
            f'<line x1="{plot_left}" y1="{y_px:.1f}" x2="{plot_right}" y2="{y_px:.1f}" '
            f'stroke="#e0e0e0" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{plot_left - 8}" y="{y_px + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#666">{cp_val:+.1f}</text>'
        )
        cp_val += cp_step

    # X-axis grid + labels (x/c)
    for x_tick in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        x_px = _linear_map(x_tick, x_min, x_max, plot_left, plot_right)
        parts.append(
            f'<line x1="{x_px:.1f}" y1="{plot_top}" x2="{x_px:.1f}" y2="{plot_bottom}" '
            f'stroke="#e0e0e0" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{x_px:.1f}" y="{plot_bottom + 18}" text-anchor="middle" '
            f'font-size="11" fill="#666">{x_tick:.1f}</text>'
        )

    # Axis labels
    parts.append(
        f'<text x="{plot_left - 50}" y="{(plot_top + plot_bottom) / 2:.0f}" '
        f'text-anchor="middle" font-size="13" fill="#333" '
        f'transform="rotate(-90 {plot_left - 50} {(plot_top + plot_bottom) / 2:.0f})">'
        f"Cp</text>"
    )
    parts.append(
        f'<text x="{(plot_left + plot_right) / 2:.0f}" y="{plot_bottom + 42}" '
        f'text-anchor="middle" font-size="13" fill="#333">x/c</text>'
    )

    # Draw reference first (behind solver curves)
    if reference_data is not None:
        ref_x, ref_cp = reference_data
        if ref_x:
            pairs = sorted(zip(ref_x, ref_cp, strict=False))
            path_parts = []
            for i, (xi, cpi) in enumerate(pairs):
                x_px = _linear_map(xi, x_min, x_max, plot_left, plot_right)
                y_px = _linear_map(cpi, cp_min, cp_max, plot_bottom, plot_top)
                cmd = "M" if i == 0 else "L"
                path_parts.append(f"{cmd} {x_px:.1f} {y_px:.1f}")
            parts.append(
                f'<path d="{" ".join(path_parts)}" fill="none" stroke="#000" '
                f'stroke-width="1.5" stroke-dasharray="5 3"/>'
            )
            for xi, cpi in pairs:
                x_px = _linear_map(xi, x_min, x_max, plot_left, plot_right)
                y_px = _linear_map(cpi, cp_min, cp_max, plot_bottom, plot_top)
                parts.append(
                    f'<circle cx="{x_px:.1f}" cy="{y_px:.1f}" r="4" '
                    f'fill="white" stroke="#000" stroke-width="1.5"/>'
                )

    # Draw solver curves
    for i, (_, (x_list, cp_list)) in enumerate(solver_data.items()):
        if not x_list:
            continue
        color = _OKABE_ITO[i % len(_OKABE_ITO)]
        pairs = sorted(zip(x_list, cp_list, strict=False))
        path_parts = []
        for j, (xi, cpi) in enumerate(pairs):
            x_px = _linear_map(xi, x_min, x_max, plot_left, plot_right)
            y_px = _linear_map(cpi, cp_min, cp_max, plot_bottom, plot_top)
            cmd = "M" if j == 0 else "L"
            path_parts.append(f"{cmd} {x_px:.1f} {y_px:.1f}")
        parts.append(
            f'<path d="{" ".join(path_parts)}" fill="none" stroke="{color}" '
            f'stroke-width="2"/>'
        )

    # Legend
    legend_y = _VIEW_H - 15
    legend_x = plot_left
    parts.append(
        f'<text x="{legend_x}" y="{legend_y}" font-size="12" fill="#333" '
        f'font-weight="bold">Legend:</text>'
    )
    x_cursor = legend_x + 60
    for i, solver_name in enumerate(solver_data.keys()):
        color = _OKABE_ITO[i % len(_OKABE_ITO)]
        parts.append(
            f'<line x1="{x_cursor}" y1="{legend_y - 4}" x2="{x_cursor + 18}" '
            f'y2="{legend_y - 4}" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x_cursor + 24}" y="{legend_y}" font-size="12" fill="#333">'
            f"{_escape_xml(solver_name)}</text>"
        )
        x_cursor += 24 + len(solver_name) * 7 + 20
    if reference_data is not None:
        parts.append(
            f'<line x1="{x_cursor}" y1="{legend_y - 4}" x2="{x_cursor + 18}" '
            f'y2="{legend_y - 4}" stroke="#000" stroke-width="1.5" stroke-dasharray="5 3"/>'
        )
        parts.append(
            f'<circle cx="{x_cursor + 9}" cy="{legend_y - 4}" r="3.5" fill="white" '
            f'stroke="#000" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{x_cursor + 24}" y="{legend_y}" font-size="12" fill="#333">'
            f"Reference</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def render_residual_comparison_svg(
    solver_data: dict[str, dict[str, list[float]]],
    title: str = "Residual Convergence Comparison",
    log_scale: bool = True,
) -> str:
    """Render residual history from multiple runs on shared axes (log Y).

    Args:
        solver_data: Dict of solver_name -> {field_name -> [residual values over iterations]}.
            Example: {"OpenFOAM": {"Ux": [1e-2, 1e-3, ...], "p": [...]},
                      "SU2": {"RMS_DENSITY": [...]}}.
        title: Plot title.
        log_scale: If True, log10 scale on Y axis (default).

    Returns:
        SVG string. Empty input returns placeholder.
    """
    if not solver_data:
        return (
            f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
            f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
            f'fill="#999" font-size="14">No residual data to compare</text>'
            f"</svg>"
        )

    # Flatten: each (solver, field) becomes one curve
    flat: list[tuple[str, str, list[float]]] = []  # (solver, field, values)
    for solver, fields in solver_data.items():
        for field, values in fields.items():
            if values:
                flat.append((solver, field, values))

    if not flat:
        return (
            f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
            f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
            f'fill="#999" font-size="14">No residual data to compare</text>'
            f"</svg>"
        )

    max_iters = max(len(v) for _, _, v in flat)
    x_min = 0
    x_max = max(1, max_iters - 1)

    if log_scale:
        positive_values = [v for _, _, values in flat for v in values if v > 0]
        if not positive_values:
            return (
                f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
                f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>'
                f'<text x="{_VIEW_W / 2}" y="{_VIEW_H / 2}" text-anchor="middle" '
                f'fill="#999" font-size="14">All residual values non-positive; cannot log-scale</text>'
                f"</svg>"
            )
        y_min = math.log10(min(positive_values))
        y_max = math.log10(max(positive_values))
        if y_max - y_min < 1:
            y_max = y_min + 1

        def _y_to_px(val: float) -> float:
            if val <= 0:
                return plot_bottom
            lv = math.log10(val)
            return _linear_map(lv, y_min, y_max, plot_bottom, plot_top)
    else:
        all_v = [v for _, _, values in flat for v in values]
        y_min = min(all_v) if all_v else 0
        y_max = max(all_v) if all_v else 1
        if y_max - y_min < 1e-9:
            y_max = y_min + 1

        def _y_to_px(val: float) -> float:
            return _linear_map(val, y_min, y_max, plot_bottom, plot_top)

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {_VIEW_W} {_VIEW_H}" xmlns="http://www.w3.org/2000/svg">'
    )
    parts.append(f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>')
    parts.append(
        f'<text x="{_VIEW_W / 2}" y="25" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="#222">{_escape_xml(title)}</text>'
    )

    plot_left = _MARGIN_LEFT
    plot_right = _VIEW_W - _MARGIN_RIGHT
    plot_top = _MARGIN_TOP
    plot_bottom = _VIEW_H - _MARGIN_BOTTOM

    parts.append(
        f'<rect x="{plot_left}" y="{plot_top}" width="{_PLOT_W}" height="{_PLOT_H}" '
        f'fill="none" stroke="#666" stroke-width="1"/>'
    )

    # Y-axis ticks
    if log_scale:
        for decade in range(int(math.floor(y_min)), int(math.ceil(y_max)) + 1):
            y_px = _linear_map(float(decade), y_min, y_max, plot_bottom, plot_top)
            parts.append(
                f'<line x1="{plot_left}" y1="{y_px:.1f}" x2="{plot_right}" y2="{y_px:.1f}" '
                f'stroke="#e0e0e0" stroke-width="0.5"/>'
            )
            parts.append(
                f'<text x="{plot_left - 8}" y="{y_px + 4:.1f}" text-anchor="end" '
                f'font-size="11" fill="#666">1e{decade}</text>'
            )
    else:
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
                f'font-size="11" fill="#666">{y_val:.2e}</text>'
            )

    # X-axis ticks
    x_tick_step = max(1, (x_max - x_min) // 5)
    for x_tick in range(x_min, x_max + 1, x_tick_step):
        x_px = _linear_map(float(x_tick), float(x_min), float(x_max), plot_left, plot_right)
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
        f"{'log10(residual)' if log_scale else 'residual'}</text>"
    )
    parts.append(
        f'<text x="{(plot_left + plot_right) / 2:.0f}" y="{plot_bottom + 42}" '
        f'text-anchor="middle" font-size="13" fill="#333">iteration</text>'
    )

    # Draw curves
    for i, (_, _, values) in enumerate(flat):
        color = _OKABE_ITO[i % len(_OKABE_ITO)]
        path_parts = []
        for j, v in enumerate(values):
            x_px = _linear_map(float(j), 0.0, float(max(1, len(values) - 1)),
                               plot_left, plot_right)
            y_px = _y_to_px(v)
            cmd = "M" if j == 0 else "L"
            path_parts.append(f"{cmd} {x_px:.1f} {y_px:.1f}")
        parts.append(
            f'<path d="{" ".join(path_parts)}" fill="none" stroke="{color}" '
            f'stroke-width="1.5"/>'
        )

    # Legend
    legend_y = _VIEW_H - 15
    legend_x = plot_left
    parts.append(
        f'<text x="{legend_x}" y="{legend_y}" font-size="12" fill="#333" '
        f'font-weight="bold">Legend:</text>'
    )
    x_cursor = legend_x + 60
    for i, (solver, field, _) in enumerate(flat):
        color = _OKABE_ITO[i % len(_OKABE_ITO)]
        label = f"{solver}:{field}"
        parts.append(
            f'<line x1="{x_cursor}" y1="{legend_y - 4}" x2="{x_cursor + 18}" '
            f'y2="{legend_y - 4}" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x_cursor + 24}" y="{legend_y}" font-size="11" fill="#333">'
            f"{_escape_xml(label)}</text>"
        )
        x_cursor += 24 + len(label) * 6 + 20

    parts.append("</svg>")
    return "\n".join(parts)
