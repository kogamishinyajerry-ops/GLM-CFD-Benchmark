"""Five-axis trust radar SVG renderer — pure Python, zero dependencies.

Renders a TrustProfile as a standalone SVG string, following the same
conventions as the reporting/ SVG generators (Okabe-Ito colorblind-safe
palette, fixed viewBox, escaped text).

Honest-floor rendering rules (Architecture v4.0 §3):

- A dimension with ``score = None`` is drawn as a dashed axis gap with an
  explicit "insufficient data" annotation — it is **never** drawn as 0.
- No aggregate score appears anywhere in the figure (by design).
"""

from __future__ import annotations

import math

from cfdb.trust.profile import DIMENSION_NAMES, TrustProfile

# Okabe-Ito palette subset (matches reporting/svg_residuals.py)
_DATA_COLOR = "#0072B2"  # blue — scored polygon / markers
_MISSING_COLOR = "#D55E00"  # vermillion — insufficient-data annotations
_GRID_COLOR = "#dddddd"
_AXIS_COLOR = "#bbbbbb"
_TEXT_COLOR = "#333333"

_HONESTY_COLORS = {
    "REAL": "#009E73",
    "ANALYTIC": "#0072B2",
    "MANUFACTURED": "#56B4E9",
    "PREVIOUS_RUN": "#E69F00",
    "SURROGATE": "#CC79A7",
    "DECLARED-NOT-VERIFIED": "#D55E00",
}

# SVG layout constants
_VIEW_W = 680
_VIEW_H = 620
_CX = 340.0
_CY = 340.0
_RADIUS = 180.0
_LABEL_R = 1.22  # label distance as a fraction of _RADIUS
_GRID_LEVELS = (0.25, 0.5, 0.75, 1.0)


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


def _axis_point(index: int, fraction: float) -> tuple[float, float]:
    """Return the (x, y) point on axis `index` at radial `fraction`.

    Axis 0 points straight up; axes proceed clockwise every 72 degrees.

    Args:
        index: Axis index in [0, 5).
        fraction: Radial fraction (0 = center, 1 = rim).

    Returns:
        (x, y) coordinates in viewBox space.
    """
    angle = math.radians(-90.0 + index * 72.0)
    return (
        _CX + _RADIUS * fraction * math.cos(angle),
        _CY + _RADIUS * fraction * math.sin(angle),
    )


def _text_anchor(x: float) -> str:
    """Pick a text-anchor keeping labels outside the pentagon readable."""
    if abs(x - _CX) < 10.0:
        return "middle"
    return "start" if x > _CX else "end"


def render(profile: TrustProfile) -> str:
    """Render a TrustProfile as a five-axis radar SVG string.

    Args:
        profile: The trust profile to render.

    Returns:
        Standalone SVG document as a string.
    """
    scores: list[float | None] = [profile.dimension(name).score for name in DIMENSION_NAMES]

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_VIEW_W} {_VIEW_H}" '
        f'font-family="sans-serif">',
        f'<rect width="{_VIEW_W}" height="{_VIEW_H}" fill="white"/>',
    ]

    # --- header: title + honesty banner ---
    title = f"Trust Profile — {profile.case_id} / {profile.solver}"
    parts.append(
        f'<text x="{_CX:.0f}" y="32" text-anchor="middle" font-size="20" '
        f'fill="{_TEXT_COLOR}">{_escape_xml(title)}</text>'
    )
    honesty_color = _HONESTY_COLORS.get(profile.honesty, "#666666")
    parts.append(
        f'<rect x="{_CX - 130:.0f}" y="44" width="260" height="26" rx="4" '
        f'fill="{honesty_color}" fill-opacity="0.15" stroke="{honesty_color}"/>'
    )
    parts.append(
        f'<text x="{_CX:.0f}" y="62" text-anchor="middle" font-size="14" '
        f'fill="{honesty_color}">honesty: {_escape_xml(profile.honesty)}</text>'
    )

    # --- concentric grid pentagons ---
    for level in _GRID_LEVELS:
        pts = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (_axis_point(i, level) for i in range(5))
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="{_GRID_COLOR}"/>')
        gx, gy = _axis_point(0, level)
        parts.append(
            f'<text x="{gx + 6:.1f}" y="{gy - 3:.1f}" font-size="10" '
            f'fill="{_AXIS_COLOR}">{level:g}</text>'
        )

    # --- axes: solid when scored, dashed gap when insufficient data ---
    for i, score in enumerate(scores):
        rim_x, rim_y = _axis_point(i, 1.0)
        if score is None:
            style = f'stroke="{_MISSING_COLOR}" stroke-dasharray="6 4"'
        else:
            style = f'stroke="{_AXIS_COLOR}"'
        parts.append(
            f'<line x1="{_CX:.1f}" y1="{_CY:.1f}" x2="{rim_x:.1f}" y2="{rim_y:.1f}" {style}/>'
        )

    # --- data polygon: only segments whose both endpoints are scored ---
    if all(s is not None for s in scores):
        pts = " ".join(
            f"{x:.1f},{y:.1f}"
            for x, y in (_axis_point(i, s) for i, s in enumerate(scores))  # type: ignore[arg-type]
        )
        parts.append(
            f'<polygon points="{pts}" fill="{_DATA_COLOR}" fill-opacity="0.15" '
            f'stroke="{_DATA_COLOR}" stroke-width="2"/>'
        )
    else:
        for i in range(5):
            j = (i + 1) % 5
            si, sj = scores[i], scores[j]
            if si is None or sj is None:
                continue  # honest gap — never bridge through a missing dimension
            x1, y1 = _axis_point(i, si)
            x2, y2 = _axis_point(j, sj)
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{_DATA_COLOR}" stroke-width="2"/>'
            )

    # --- vertex markers + labels ---
    for i, (name, score) in enumerate(zip(DIMENSION_NAMES, scores, strict=True)):
        lx, ly = _axis_point(i, _LABEL_R)
        anchor = _text_anchor(lx)
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="14" '
            f'fill="{_TEXT_COLOR}">{_escape_xml(name)}</text>'
        )
        if score is None:
            parts.append(
                f'<text x="{lx:.1f}" y="{ly + 16:.1f}" text-anchor="{anchor}" '
                f'font-size="12" font-style="italic" fill="{_MISSING_COLOR}">'
                "insufficient data</text>"
            )
        else:
            vx, vy = _axis_point(i, score)
            parts.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="4" fill="{_DATA_COLOR}"/>')
            parts.append(
                f'<text x="{lx:.1f}" y="{ly + 16:.1f}" text-anchor="{anchor}" '
                f'font-size="12" fill="{_DATA_COLOR}">{score:.2f}</text>'
            )

    # --- footer: run count + explicit no-aggregate statement ---
    parts.append(
        f'<text x="{_CX:.0f}" y="{_VIEW_H - 28}" text-anchor="middle" font-size="12" '
        f'fill="{_TEXT_COLOR}">n_runs = {profile.n_runs}</text>'
    )
    parts.append(
        f'<text x="{_CX:.0f}" y="{_VIEW_H - 10}" text-anchor="middle" font-size="11" '
        f'fill="{_AXIS_COLOR}">five dimensions, no aggregate score (by design)</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)
