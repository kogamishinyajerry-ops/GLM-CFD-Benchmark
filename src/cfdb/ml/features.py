"""Feature engineering for ML mesh surrogate.

Extracts structured feature vectors from coarse-grid CFD run artifacts
for use as input to the surrogate model.
"""

from __future__ import annotations

import numpy as np

# Default feature names in canonical order
DEFAULT_FEATURE_NAMES: list[str] = [
    "cell_count",
    "target_y_plus",
    "residual_Ux",
    "residual_Uy",
    "residual_p",
    "cl_coarse",
    "cd_coarse",
    "umax_coarse",
    "cp_xc_0.00",
    "cp_xc_0.25",
    "cp_xc_0.50",
    "cp_xc_0.75",
    "cp_xc_1.00",
]


def build_feature_vector(
    cell_count: int | None = None,
    target_y_plus: float | None = None,
    final_residuals: dict[str, float] | None = None,
    cl_coarse: float | None = None,
    cd_coarse: float | None = None,
    umax_coarse: float | None = None,
    cp_select_xc: dict[float, float] | None = None,
    feature_names: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Build a feature vector from coarse-grid run data.

    Args:
        cell_count: Mesh cell count from RunManifest.
        target_y_plus: Target y+ from MeshSpec.
        final_residuals: Final residuals dict with keys 'Ux', 'Uy', 'p'.
        cl_coarse: Coarse-grid lift coefficient.
        cd_coarse: Coarse-grid drag coefficient.
        umax_coarse: Coarse-grid centerline max velocity.
        cp_select_xc: Dict mapping x/c position to Cp value at that location.
        feature_names: Custom feature name list. Uses DEFAULT_FEATURE_NAMES if None.

    Returns:
        Tuple of (feature_array, feature_names) where feature_array is a 1D
        NumPy array and feature_names is the ordered list of feature names.
    """
    names = feature_names or DEFAULT_FEATURE_NAMES
    res = final_residuals or {}

    # Build a lookup for feature values
    values: dict[str, float | None] = {
        "cell_count": float(cell_count) if cell_count is not None else None,
        "target_y_plus": target_y_plus,
        "residual_Ux": res.get("Ux"),
        "residual_Uy": res.get("Uy"),
        "residual_p": res.get("p"),
        "cl_coarse": cl_coarse,
        "cd_coarse": cd_coarse,
        "umax_coarse": umax_coarse,
    }

    # Add Cp at select x/c positions
    cp = cp_select_xc or {}
    for xc in [0.0, 0.25, 0.5, 0.75, 1.0]:
        key = f"cp_xc_{xc:.2f}"
        values[key] = cp.get(xc)

    # Assemble array, using NaN for missing values
    arr = np.array([values.get(name, np.nan) for name in names], dtype=np.float64)
    return arr, names


def build_target_vector(
    delta_cl: float | None = None,
    delta_cd: float | None = None,
    target_names: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Build a target vector from known fine-coarse deltas.

    Args:
        delta_cl: Cl_fine - Cl_coarse.
        delta_cd: Cd_fine - Cd_coarse.
        target_names: Custom target name list.

    Returns:
        Tuple of (target_array, target_names).
    """
    names = target_names or ["delta_cl", "delta_cd"]

    values: dict[str, float | None] = {
        "delta_cl": delta_cl,
        "delta_cd": delta_cd,
    }

    arr = np.array([values.get(name, np.nan) for name in names], dtype=np.float64)
    return arr, names


def extract_features_from_manifest_and_qoi(
    manifest: dict,
    coarse_qoi: dict[str, float],
    cp_select_dict: dict[float, float] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Convenience: extract features from a RunManifest dict and QoI dict.

    Args:
        manifest: Dict with keys 'cell_count', 'final_residuals', optionally
            'target_y_plus'.
        coarse_qoi: Dict with keys 'cl', 'cd', 'umax'.
        cp_select_dict: Dict mapping x/c positions to Cp values.

    Returns:
        Tuple of (feature_array, feature_names).
    """
    residuals = manifest.get("final_residuals", {}) or {}
    return build_feature_vector(
        cell_count=manifest.get("cell_count"),
        target_y_plus=manifest.get("target_y_plus"),
        final_residuals={
            "Ux": residuals.get("Ux"),
            "Uy": residuals.get("Uy"),
            "p": residuals.get("p"),
        },
        cl_coarse=coarse_qoi.get("cl"),
        cd_coarse=coarse_qoi.get("cd"),
        umax_coarse=coarse_qoi.get("umax"),
        cp_select_xc=cp_select_dict,
    )
