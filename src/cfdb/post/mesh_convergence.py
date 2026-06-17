r"""Mesh convergence analysis — GCI + Richardson extrapolation (Roache 1994).

References:
- Roache, P.J. (1994) "Perspective: A Method for Uniform Reporting of Grid Refinement Studies"
  ASME J. Fluids Engineering, 116(3), 405-413.
- Celik, I.B. et al. (2008) "Procedure for Estimation and Reporting of Uncertainty Due to
  Discretization in CFD Applications" ASME J. Fluids Engineering, 130(7).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MeshLevelResult:
    """Result from a single mesh refinement level."""

    level: int
    base_size: float
    cell_count: int
    cl: float | None = None
    cd: float | None = None
    elapsed_sec: float | None = None
    iters: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class GCICouple:
    """GCI computed between two consecutive mesh levels."""

    qoi: str  # "cl" or "cd"
    level_coarse: int
    level_fine: int
    h_coarse: float  # base size
    h_fine: float
    refinement_ratio: float  # r = h_coarse / h_fine
    f_coarse: float
    f_fine: float
    epsilon: float  # relative error
    order: float  # observed order of accuracy (p)
    gci_coarse: float  # GCI for coarse grid
    gci_fine: float  # GCI for fine grid
    asymptotic_range: float  # close to 1.0 -> in asymptotic range
    richardson: float  # Richardson extrapolated value (h -> 0)


@dataclass
class MeshConvergenceReport:
    """Full mesh convergence analysis report."""

    levels: list[MeshLevelResult]
    gci_pairs: list[GCICouple]
    is_converged: dict[str, bool]  # per-QoI convergence flag
    asymptotic_ratio: float | None  # GCI ratio check
    recommended_level: str | None  # "mesh_160" | "mesh_80" | "mesh_40" | "mesh_20"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_mesh_convergence_table(
    run_dir: Path, levels: list[str] | None = None
) -> list[MeshLevelResult]:
    """Extract mesh convergence data from multi-level run directory.

    Reads mesh_conv_summary.json from run_dir/level_NN/case/ for each level.

    Args:
        run_dir: Root directory containing level_00, level_01, ...
        levels: Level names (e.g. ["mesh_160", "mesh_80"]). If None, scans run_dir.

    Returns:
        List of MeshLevelResult ordered by level (coarse to fine).
    """
    results: list[MeshLevelResult] = []

    if levels is None:
        # Scan for level_XX directories
        level_dirs = sorted(
            [d for d in run_dir.iterdir() if d.is_dir() and d.name.startswith("level_")]
        )
    else:
        level_dirs = [
            run_dir / f"level_{i:02d}"
            for i in range(len(levels))
            if (run_dir / f"level_{i:02d}").is_dir()
        ]

    for ld in level_dirs:
        summary_path = ld / "case" / "mesh_conv_summary.json"
        if not summary_path.exists():
            logger.warning("no mesh_conv_summary.json in %s, skipping", ld)
            continue

        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("failed to read %s: %s", summary_path, e)
            continue

        level_idx = int(ld.name.split("_")[-1]) if ld.name.startswith("level_") else 0
        results.append(
            MeshLevelResult(
                level=level_idx,
                base_size=data.get("base_size_m", 0.0),
                cell_count=data.get("cell_count", 0),
                cl=data.get("cl"),
                cd=data.get("cd"),
                elapsed_sec=data.get("elapsed_sec"),
                iters=data.get("iters", 0),
                raw=data,
            )
        )

    # Sort by base_size descending (coarse first)
    results.sort(key=lambda r: r.base_size, reverse=True)
    return results


# ---------------------------------------------------------------------------
# GCI computation (Roache 1994)
# ---------------------------------------------------------------------------


def compute_gci(
    levels: list[MeshLevelResult],
    qoi: str = "cl",
    safety_factor: float = 1.25,
    order_of_accuracy: float = 2.0,
) -> list[GCICouple]:
    """Compute Grid Convergence Index for a sequence of mesh levels.

    Uses Roache's GCI formula with uniform refinement ratio r = h_coarse / h_fine.

    Args:
        levels: Mesh level results ordered from coarse to fine (base_size descending).
        qoi: Quantity of interest ("cl" or "cd").
        safety_factor: F_s factor (1.25 for 3+ grids, 3.0 for 2 grids).
        order_of_accuracy: Formal order p (2.0 for second-order methods; if < 3 levels,
            formal order is used; with >= 3 levels observed order is computed).

    Returns:
        List of GCICouple for each consecutive pair (coarse→fine).
    """
    gci_pairs: list[GCICouple] = []

    for i in range(len(levels) - 1):
        coarse = levels[i]
        fine = levels[i + 1]

        f_coarse = getattr(coarse, qoi, None)
        f_fine = getattr(fine, qoi, None)
        if f_coarse is None or f_fine is None or f_fine == 0:
            logger.warning(
                "skipping GCI for %s level %d->%d: value missing or zero",
                qoi,
                coarse.level,
                fine.level,
            )
            continue

        h_c = coarse.base_size
        h_f = fine.base_size
        if h_f <= 0:
            continue

        r = h_c / h_f
        epsilon = (f_fine - f_coarse) / abs(f_fine) if f_fine != 0 else 0.0

        # With >=3 levels total, compute observed order of accuracy
        p = order_of_accuracy
        if len(levels) >= 3 and i + 2 < len(levels):
            extra = levels[i + 2]
            f_extra = getattr(extra, qoi, None)
            if f_extra is not None and f_fine is not None and f_coarse is not None:
                eps21 = abs(f_fine - f_coarse)  # change fine→coarse
                eps32 = abs(f_extra - f_fine)  # change extra→fine
                if eps21 > 1e-15 and eps32 > 1e-15:
                    ratio = eps32 / eps21
                    if ratio > 0:
                        p = max(
                            0.5,
                            min(10.0, abs(math.log(ratio)) / math.log(r)),
                        )

        # GCI
        rp = r ** p
        if rp == 1.0:
            rp = 1.0001  # avoid division by zero
        gci_coarse = safety_factor * abs(epsilon) / (rp - 1.0)
        gci_fine = gci_coarse / rp

        # Asymptotic range check: GCI_{fine} ≈ r^p * GCI_{coarse}?
        gci_ratio = gci_coarse / max(gci_fine, 1e-15) if gci_fine > 0 else 0.0
        asymptotic_range = gci_ratio / rp

        # Richardson extrapolation to h -> 0
        richardson = f_fine + (f_fine - f_coarse) / (rp - 1.0)

        gci_pairs.append(
            GCICouple(
                qoi=qoi,
                level_coarse=coarse.level,
                level_fine=fine.level,
                h_coarse=h_c,
                h_fine=h_f,
                refinement_ratio=r,
                f_coarse=f_coarse,
                f_fine=f_fine,
                epsilon=epsilon,
                order=p,
                gci_coarse=gci_coarse,
                gci_fine=gci_fine,
                asymptotic_range=asymptotic_range,
                richardson=richardson,
            )
        )

    return gci_pairs


def richardson_extrapolate(
    levels: list[MeshLevelResult],
    qoi: str = "cl",
    order_of_accuracy: float = 2.0,
) -> float | None:
    """Richardson extrapolate to h→0 using the finest two meshes.

    Args:
        levels: Mesh level results (at least 2).
        qoi: Quantity of interest ("cl" or "cd").
        order_of_accuracy: Formal order p.

    Returns:
        Extrapolated value or None if insufficient data.
    """
    if len(levels) < 2:
        return None

    fine = levels[-1]
    coarse = levels[-2]

    f_fine = getattr(fine, qoi, None)
    f_coarse = getattr(coarse, qoi, None)
    if f_fine is None or f_coarse is None:
        return None

    r = coarse.base_size / fine.base_size if fine.base_size > 0 else 2.0
    rp = r ** order_of_accuracy
    denom = rp - 1.0
    if abs(denom) < 1e-15:
        return f_fine

    return f_fine + (f_fine - f_coarse) / denom


# ---------------------------------------------------------------------------
# Convergence assessment
# ---------------------------------------------------------------------------


def assess_convergence(
    gci_pairs: list[GCICouple],
    gci_threshold: float = 0.05,
) -> MeshConvergenceReport:
    """Assess whether mesh convergence is achieved.

    Args:
        gci_pairs: List of GCICouple from compute_gci().
        gci_threshold: GCI below which mesh is considered converged (default 5%).

    Returns:
        MeshConvergenceReport with is_converged flags and recommendations.
    """
    is_converged: dict[str, bool] = {}
    if not gci_pairs:
        return MeshConvergenceReport(
            levels=[],
            gci_pairs=[],
            is_converged={"cl": False, "cd": False},
            asymptotic_ratio=None,
            recommended_level=None,
        )

    # Group by QoI
    qois = set(g.qoi for g in gci_pairs)
    for qoi in qois:
        qoi_gcis = [g for g in gci_pairs if g.qoi == qoi]
        # Converged if the finest GCI < threshold
        is_converged[qoi] = qoi_gcis[-1].gci_fine < gci_threshold

    # Asymptotic ratio check: successive GCI ratios ≈ r^p
    if len(gci_pairs) >= 2:
        ratios = []
        for i in range(len(gci_pairs) - 1):
            r = gci_pairs[i].gci_fine / max(gci_pairs[i + 1].gci_fine, 1e-15)
            ratios.append(r)
        asymptotic_ratio = sum(ratios) / len(ratios) if ratios else None
    else:
        asymptotic_ratio = None

    # Recommend finest level if converged, otherwise next finer
    recommended = None
    if gci_pairs and gci_pairs[-1].gci_fine < gci_threshold:
        recommended = f"mesh_{int(gci_pairs[-1].h_fine * 1000)}"
    elif gci_pairs:
        recommended = "mesh_20"  # need finer

    return MeshConvergenceReport(
        levels=[],
        gci_pairs=gci_pairs,
        is_converged=is_converged,
        asymptotic_ratio=asymptotic_ratio,
        recommended_level=recommended,
    )
