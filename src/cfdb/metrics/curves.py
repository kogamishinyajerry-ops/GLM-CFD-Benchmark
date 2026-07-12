"""Curve L2 norm computation and strict reference-curve loading."""

from __future__ import annotations

import csv
import json
import logging
import math
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def load_reference_curve(path: Path) -> list[tuple[float, float]] | None:
    """Load reference curve data from a file (single strict spec, R8).

    Shared by the metrics engine (curve gate) and adapters (resampling
    onto the reference abscissa) so the loading rules cannot drift apart.
    Two formats: a ``.csv`` file (two numeric columns, an optional single
    header row that is POSITIVELY validated — exactly two columns, neither
    parseable as a float) or, for any other extension, a JSON list of
    [x, y] pairs. One malformed row rejects the WHOLE file (None).

    Args:
        path: Path to the reference file.

    Returns:
        List of (x, y) points, or None if the file is missing, unreadable,
        or malformed (never raises — callers treat None as 'not available').
    """
    if path.suffix.lower() == ".csv":
        return _load_csv_curve(path)
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [(float(pt[0]), float(pt[1])) for pt in parsed]
    except (
        json.JSONDecodeError,
        ValueError,
        TypeError,
        OSError,
        IndexError,
    ) as e:
        logger.warning("failed to load reference curve from %s: %s", path, e)
    return None


def _load_csv_curve(path: Path) -> list[tuple[float, float]] | None:
    """Load a two-column CSV reference curve (strict; see loader above).

    Args:
        path: Path to the CSV file.

    Returns:
        List of (x, y) points, or None when the file is unusable.
    """
    try:
        with path.open(newline="", encoding="utf-8") as f:
            rows = [row for row in csv.reader(f) if len(row) > 0]
    # csv.Error (e.g. a field beyond field_size_limit) and decoding
    # failures surface during iteration, not open() — all three are
    # 'unusable reference', never a crash (Codex R7 P2).
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        logger.warning("failed to read reference curve CSV %s: %s", path, e)
        return None
    if len(rows) == 0:
        logger.warning("reference curve CSV %s is empty", path)
        return None

    def _parse(row: list[str]) -> tuple[float, float] | None:
        if len(row) != 2:
            return None
        try:
            x, y = float(row[0]), float(row[1])
        except ValueError:
            return None
        if math.isfinite(x) is False or math.isfinite(y) is False:
            return None
        return (x, y)

    def _is_header(row: list[str]) -> bool:
        if len(row) != 2:
            return False
        for cell in row:
            try:
                float(cell)
            except ValueError:
                continue
            return False  # a numeric cell means data, not a header
        return True

    has_header = _is_header(rows[0])
    data_rows = rows[1:] if has_header else rows
    if len(data_rows) == 0:
        logger.warning("reference curve CSV %s has a header but no data rows", path)
        return None
    points: list[tuple[float, float]] = []
    for offset, row in enumerate(data_rows):
        lineno = offset + (2 if has_header else 1)
        point = _parse(row)
        if point is None:
            logger.warning(
                "reference curve CSV %s line %d is not two finite floats: %r (whole file rejected)",
                path,
                lineno,
                row,
            )
            return None
        points.append(point)
    return points


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
