"""Curve L2 norm computation."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def compute_curve_l2(
    reference: dict[str, list[tuple[float, float]]],
    computed: dict[str, list[tuple[float, float]]],
) -> dict[str, float]:
    """Compute L2 norm of differences for each curve.

    L2 norm = sqrt(sum((computed_y - reference_y)^2)).

    Curves must have matching x values. If lengths differ or x values don't
    match, the curve is skipped with a warning.

    Args:
        reference: Reference curves, key -> list of (x, y).
        computed: Computed curves, key -> list of (x, y).

    Returns:
        Dict mapping curve name to L2 norm.
    """
    results: dict[str, float] = {}
    for key in reference:
        if key not in computed:
            continue
        ref_curve = reference[key]
        comp_curve = computed[key]
        if len(ref_curve) != len(comp_curve):
            logger.warning(
                "curve '%s' length mismatch: reference=%d, computed=%d",
                key,
                len(ref_curve),
                len(comp_curve),
            )
            continue
        # x grids must actually match (Codex R0 P1: the docstring always
        # promised this but the code never checked — same-length curves
        # sampled at different abscissas could report L2=0 and pass).
        # Exact equality; NaN x never matches (fail-closed skip -> the
        # engine counts the curve as missing -> incomplete, never pass).
        ref_x = np.array([x for x, _ in ref_curve])
        comp_x = np.array([x for x, _ in comp_curve])
        if not np.array_equal(ref_x, comp_x):
            logger.warning(
                "curve '%s' x-grid mismatch: reference and computed abscissas differ",
                key,
            )
            continue
        ref_y = np.array([y for _, y in ref_curve])
        comp_y = np.array([y for _, y in comp_curve])
        diff = comp_y - ref_y
        l2 = float(np.sqrt(np.sum(diff**2)))
        results[key] = l2
    return results
