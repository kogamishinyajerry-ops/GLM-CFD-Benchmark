"""Generate NACA 4-digit airfoil coordinates (cosine spacing).

References:
- NACA Report 460 (1933): fundamental equations
- Ladson, C. L., "Effects of Independent Variation of Mach and Reynolds
  Numbers on the Low-Speed Aerodynamic Characteristics of the NACA 0012
  Airfoil Section," NASA TM-4074, 1988.
- Eppler, R., "Airfoil Design and Data," Springer, 1990 (Selig format)

This module generates NACA0012 (and any NACA00xx symmetric) airfoil coordinates
using the standard thickness distribution with cosine spacing for x. The output
is suitable for:
- Selig .dat format (2D, for OpenFOAM / SU2 mesh generators)
- STL format (thin 3D slab, for snappyHexMesh)

P2-b feature.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def naca4_thickness(t: float, n: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Generate NACA 4-digit SYMMETRIC airfoil coordinates (zero camber).

    Only supports symmetric airfoils (NACA00xx where m=p=0). For cambered
    airfoils use a different generator.

    Args:
        t: Maximum thickness as fraction of chord (e.g. 0.12 for NACA0012).
            Must be in (0, 0.5].
        n: Number of points per surface (upper or lower). Default 200.
            Total points = 2*n (upper from LE to TE, then lower from TE to LE).

    Returns:
        Tuple (x, y) of np.ndarrays, each of length 2*n. Upper surface comes
        first (x from 0 → 1), then lower surface reversed (x from 1 → 0).

    Raises:
        ValueError: If t is out of range.

    Notes:
        Uses closed trailing edge coefficient (-0.1015 instead of -0.1036) to
        ensure the upper and lower surfaces meet at x=1.
    """
    if not (0 < t <= 0.5):
        raise ValueError(f"thickness t must be in (0, 0.5], got {t}")

    # Cosine spacing for x ∈ [0, 1]: clusters points near LE (x→0) and TE (x→1)
    beta = np.linspace(0.0, math.pi, n)
    x = 0.5 * (1.0 - np.cos(beta))

    # Standard NACA 4-digit thickness distribution yt(x)
    # Closed TE (coefficient -0.1015 instead of -0.1036)
    yt = 5.0 * t * (
        0.2969 * np.sqrt(x)
        - 0.1260 * x
        - 0.3516 * x**2
        + 0.2843 * x**3
        - 0.1015 * x**4
    )

    # Symmetric airfoil: camber line = 0, so upper = +yt, lower = -yt
    x_upper = x
    y_upper = yt
    x_lower = x
    y_lower = -yt

    # Concatenate: upper LE→TE, then lower TE→LE (closed loop)
    x_out = np.concatenate([x_upper, x_lower[::-1]])
    y_out = np.concatenate([y_upper, y_lower[::-1]])
    return x_out, y_out


def write_selig_format(x: np.ndarray, y: np.ndarray, path: Path, name: str = "NACA0012") -> None:
    """Write airfoil coordinates in Selig .dat format.

    Format:
        <NAME>              (header line, typically airfoil designation)
        <x_upper> <y_upper>
        ...
        <x_lower> <y_lower>
        ...

    Args:
        x, y: Coordinate arrays (upper surface first, then lower reversed).
        path: Output file path.
        name: Airfoil name for header line (default 'NACA0012').
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [name]
    for xi, yi in zip(x, y):
        lines.append(f"{xi:.6f} {yi:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_stl(
    x: np.ndarray,
    y: np.ndarray,
    path: Path,
    z_extent: float = 0.1,
    name: str = "naca0012",
    z_center: float = 0.0,
) -> None:
    """Write airfoil as a thin 3D STL (extruded in z), centered on z_center.

    snappyHexMesh requires a closed 3D surface. We extrude the 2D profile by
    z_extent along z, with the slab centered on ``z_center`` so the two end-
    cap planes sit at ``z_center ± z_extent/2``. This matches the default
    blockMeshDict layout (z from -span_half to +span_half with span_half =
    span_z/2) and avoids the previous z-offset bug where the slab sat at
    z∈[0, z_extent] while blockMesh used z∈[-z_extent/2, +z_extent/2], so
    the STL fell half outside the background mesh.

    Args:
        x, y: 2D profile coordinate arrays (upper surface + reversed lower).
        path: Output STL file path.
        z_extent: Slab thickness in z direction (default 0.1).
        name: Solid name in STL header.
        z_center: Mid-plane z coordinate of the slab (default 0.0, matching
            blockMeshDict's symmetric ±span_half vertex layout).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n_half = len(x) // 2  # points on upper (and lower) surface

    # Two end-cap planes, centered on z_center so the STL is symmetric in z
    # and aligns with the blockMesh slab (z ∈ [z_center-z_extent/2,
    # z_center+z_extent/2]).
    z_top = z_center + z_extent / 2.0
    z_bot = z_center - z_extent / 2.0

    lines = [f"solid {name}"]

    # Top cap (z = z_top): triangles fanning the upper-lower closed curve
    for i in range(2 * n_half - 1):
        v0 = (float(x[i]), float(y[i]), z_top)
        v1 = (float(x[i + 1]), float(y[i + 1]), z_top)
        # Connect to the symmetric point on the other side (closing the slab)
        # For simplicity, triangulate as a fan from the first vertex
        if i + 2 < 2 * n_half:
            v2 = (float(x[i + 2]), float(y[i + 2]), z_top)
            _write_triangle(lines, v0, v1, v2)

    # Bottom cap (z = z_bot): mirror of top cap (reverse winding for normal flip)
    for i in range(2 * n_half - 1):
        v0 = (float(x[i]), float(y[i]), z_bot)
        if i + 2 < 2 * n_half:
            v2 = (float(x[i + 2]), float(y[i + 2]), z_bot)
            v1 = (float(x[i + 1]), float(y[i + 1]), z_bot)
            _write_triangle(lines, v0, v1, v2)

    # Side walls connecting the two z planes (the actual airfoil outer surface
    # that snappyHexMesh refines and extrudes prism layers from).
    for i in range(2 * n_half - 1):
        v0 = (float(x[i]), float(y[i]), z_bot)
        v1 = (float(x[i + 1]), float(y[i + 1]), z_bot)
        v2 = (float(x[i + 1]), float(y[i + 1]), z_top)
        v3 = (float(x[i]), float(y[i]), z_top)
        _write_triangle(lines, v0, v1, v2)
        _write_triangle(lines, v0, v2, v3)

    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_triangle(lines: list[str], v0: tuple[float, float, float],
                    v1: tuple[float, float, float],
                    v2: tuple[float, float, float]) -> None:
    """Append a single STL facet with zero normal (snappyHexMesh recomputes normals)."""
    lines.append("  facet normal 0 0 0")
    lines.append("    outer loop")
    lines.append(f"      vertex {v0[0]:.6f} {v0[1]:.6f} {v0[2]:.6f}")
    lines.append(f"      vertex {v1[0]:.6f} {v1[1]:.6f} {v1[2]:.6f}")
    lines.append(f"      vertex {v2[0]:.6f} {v2[1]:.6f} {v2[2]:.6f}")
    lines.append("    endloop")
    lines.append("  endfacet")


def generate_naca0012(
    out_dir: Path,
    n_points: int = 200,
    thickness: float = 0.12,
    z_extent: float = 0.1,
    z_center: float = 0.0,
) -> tuple[Path, Path]:
    """Generate NACA0012 airfoil geometry files (.dat + .stl).

    Convenience wrapper around naca4_thickness + write_selig_format + write_stl.

    Args:
        out_dir: Output directory (e.g. cases/validation/naca0012/geometry/).
        n_points: Points per surface (default 200).
        thickness: Thickness fraction (default 0.12 for NACA0012).
        z_extent: STL slab thickness (default 0.1).
        z_center: Mid-plane z of the slab (default 0.0 to match blockMesh's
            symmetric ±span_half vertex layout).

    Returns:
        Tuple (dat_path, stl_path) of generated files.
    """
    x, y = naca4_thickness(t=thickness, n=n_points)
    dat_path = out_dir / "naca0012.dat"
    stl_path = out_dir / "naca0012.stl"
    write_selig_format(x, y, dat_path, name="NACA0012")
    write_stl(x, y, stl_path, z_extent=z_extent, z_center=z_center, name="naca0012")
    return dat_path, stl_path


if __name__ == "__main__":
    # Run as: python -m cfdb.cases.naca0012.gen_geometry  OR  python gen_geometry.py
    out = Path(__file__).parent / "geometry"
    dat, stl = generate_naca0012(out)
    print(f"Generated NACA0012 geometry:")
    print(f"  Selig .dat: {dat}")
    print(f"  STL       : {stl}")
