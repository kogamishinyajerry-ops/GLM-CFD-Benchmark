"""Cp distribution extraction from OpenFOAM surface samples (R8 backlog).

Turns a ``surfaces`` function-object raw file (``x y z p`` rows sampled on
the airfoil patch) into a pressure-coefficient curve on the case's
reference x/c grid, ready for the strict ``compute_curve_l2`` gate (which
requires exactly matching abscissas).

Conventions (documented, not silently assumed):

- ``p`` is simpleFoam kinematic pressure (p/rho, m^2/s^2) with freestream
  reference 0, so ``Cp = p / (0.5 * u_inf^2)``.
- The UPPER surface (y > 0) is extracted — the shipped Ladson 1988
  references are single-valued in x/c (one surface). The surface
  convention is recorded in the scoring notes by the adapter.
- Resampling interpolates simulation Cp onto the reference x/c grid
  (y-values remain purely simulated; borrowing the public abscissa is
  standard V&V practice). NO extrapolation: a reference point outside the
  sampled x/c range rejects the whole curve (fail-closed None).

Every parse step is strict: one malformed data row rejects the whole raw
file — a curve is judging input, and lenient loading is how fake greens
are born.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MIN_SURFACE_POINTS = 10
"""Minimum upper-surface sample points for a usable Cp curve — below this
the surface resolution cannot support interpolation onto the reference
grid (fail-closed None, never a sparse fake curve)."""

ENDPOINT_CLAMP_MAX_FRACTION = 0.01
"""Maximum reference-endpoint overhang (as a fraction of the reference x
span) absorbed by edge clamping instead of rejection. Face-centre samples
never reach the nominal leading/trailing edge stations (0.0 / 1.0) that
experimental tables use — the real NACA surface's edge gaps are ~7e-4 and
~4e-3 chord, well under this 1% cap, and of the same order as the
experimental stations' own nominal precision. Anything larger is a real
data hole: rejected, never extrapolated."""


def parse_surface_raw(path: Path) -> list[tuple[float, float, float, float]] | None:
    """Parse an OpenFOAM ``surfaceFormat raw`` sample file strictly.

    Args:
        path: Raw file (``#`` comment lines, then ``x y z value`` rows).

    Returns:
        List of (x, y, z, value) tuples, or None when the file is missing,
        unreadable, or any data row is not exactly four finite floats.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("cannot read surface raw file %s: %s", path, e)
        return None
    rows: list[tuple[float, float, float, float]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 4:
            logger.warning(
                "surface raw %s line %d has %d columns, expected 4 (whole file rejected)",
                path,
                lineno,
                len(parts),
            )
            return None
        try:
            values = tuple(float(part) for part in parts)
        except ValueError:
            logger.warning(
                "surface raw %s line %d is not numeric (whole file rejected)", path, lineno
            )
            return None
        if not all(math.isfinite(v) for v in values):
            logger.warning(
                "surface raw %s line %d has non-finite values (whole file rejected)",
                path,
                lineno,
            )
            return None
        rows.append(values)  # type: ignore[arg-type]
    if len(rows) == 0:
        logger.warning("surface raw %s has no data rows", path)
        return None
    return rows


def extract_cp_distribution(
    raw_path: Path,
    u_inf: float,
    l_ref: float,
    reference_x: list[float],
) -> list[tuple[float, float]] | None:
    """Extract the upper-surface Cp curve resampled onto ``reference_x``.

    Args:
        raw_path: ``surfaces`` function-object raw file (kinematic p).
        u_inf: Freestream speed (m/s); must be positive and finite.
        l_ref: Chord length (m); must be positive and finite.
        reference_x: Target x/c grid (the case reference's abscissa).

    Returns:
        ``[(x_ref_i, cp_i), ...]`` on exactly the reference grid, or None
        when anything is unusable: bad raw file, too few upper-surface
        points, non-monotonic collapse failure, or a reference point
        outside the sampled x/c range (extrapolation is never performed).
    """
    if not (math.isfinite(u_inf) and u_inf > 0 and math.isfinite(l_ref) and l_ref > 0):
        logger.warning("cp extraction refused: u_inf=%r l_ref=%r invalid", u_inf, l_ref)
        return None
    if len(reference_x) == 0 or not all(math.isfinite(x) for x in reference_x):
        logger.warning("cp extraction refused: reference x grid empty or non-finite")
        return None
    rows = parse_surface_raw(raw_path)
    if rows is None:
        return None

    q = 0.5 * u_inf * u_inf
    upper = [(x / l_ref, p / q) for x, y, _z, p in rows if y > 0.0]
    if len(upper) < MIN_SURFACE_POINTS:
        logger.warning(
            "cp extraction refused: %d upper-surface points < minimum %d",
            len(upper),
            MIN_SURFACE_POINTS,
        )
        return None

    # Collapse duplicate x/c stations (e.g. multiple faces at one station)
    # by averaging, then sort ascending for interpolation.
    xs = np.array([x for x, _ in upper])
    cps = np.array([cp for _, cp in upper])
    unique_x, inverse = np.unique(xs, return_inverse=True)
    mean_cp = np.zeros_like(unique_x)
    counts = np.zeros_like(unique_x)
    np.add.at(mean_cp, inverse, cps)
    np.add.at(counts, inverse, 1.0)
    mean_cp = mean_cp / counts

    ref = np.array(reference_x, dtype=float)
    # No extrapolation beyond the sampled surface — with ONE physically
    # honest exception (real-run evidence, R8): cell-centre sampling can
    # never place a point exactly at the leading/trailing edge (the real
    # 4334-face NACA surface spans x/c [7e-4, 0.996]), while experimental
    # references tabulate NOMINAL stations 0.0 and 1.0. An endpoint
    # overhang up to ENDPOINT_CLAMP_MAX_FRACTION of the reference span is
    # clamped to the nearest sampled value (np.interp's edge behavior) and
    # logged. A per-sample-spacing criterion was tried first and REFUSED
    # real data: refined trailing-edge meshes make the local spacing
    # (~1e-5) far smaller than the geometric edge gap, so better meshes
    # rejected more — direction inverted. A larger gap is a genuine data
    # hole and still rejects the whole curve.
    span = float(ref.max() - ref.min())
    max_gap = ENDPOINT_CLAMP_MAX_FRACTION * span
    gap_lo = float(unique_x[0] - ref.min())
    gap_hi = float(ref.max() - unique_x[-1])
    if gap_lo > max_gap or gap_hi > max_gap:
        logger.warning(
            "cp extraction refused: reference x/c range [%g, %g] exceeds sampled "
            "range [%g, %g] by more than %g of the reference span "
            "(no extrapolation)",
            ref.min(),
            ref.max(),
            unique_x[0],
            unique_x[-1],
            ENDPOINT_CLAMP_MAX_FRACTION,
        )
        return None
    if gap_lo > 0 or gap_hi > 0:
        logger.info(
            "cp extraction: reference endpoint(s) clamped within sub-chord gap(s) "
            "(lo %g, hi %g, cap %g)",
            max(gap_lo, 0.0),
            max(gap_hi, 0.0),
            max_gap,
        )
    interpolated = np.interp(ref, unique_x, mean_cp)
    if not np.all(np.isfinite(interpolated)):
        logger.warning("cp extraction refused: non-finite interpolated values")
        return None
    return [(float(x), float(cp)) for x, cp in zip(ref, interpolated, strict=True)]
