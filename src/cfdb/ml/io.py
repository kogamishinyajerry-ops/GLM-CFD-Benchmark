"""Model serialization and dataset I/O for the ML surrogate system.

Handles saving/loading trained models (pickle) and metadata (JSON),
plus run directory scanning for dataset assembly.
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Any

from cfdb.ml.schema import SurrogateMeta

logger = logging.getLogger(__name__)


def save_model(
    model: Any,
    meta: SurrogateMeta,
    output_dir: Path,
) -> Path:
    """Save a trained model and its metadata to disk.

    Creates two files under output_dir:
      - model.pkl: Pickled model object.
      - meta.json: JSON serialization of SurrogateMeta.

    Args:
        model: The trained model object (MeshSurrogateModel or sklearn-style).
        meta: SurrogateMeta describing the model.
        output_dir: Directory to write files into.

    Returns:
        Path to the written model file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    model_path = output_dir / "model.pkl"
    meta_path = output_dir / "meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    logger.info("Saved model to %s", model_path)

    meta_data = meta.model_dump(mode="json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, indent=2, default=str)
    logger.info("Saved metadata to %s", meta_path)

    return model_path


def load_model(
    model_dir: Path,
) -> tuple[Any, SurrogateMeta]:
    """Load a trained model and its metadata from disk.

    Args:
        model_dir: Directory containing model.pkl and meta.json.

    Returns:
        Tuple of (model_object, SurrogateMeta).

    Raises:
        FileNotFoundError: If either model.pkl or meta.json is missing.
    """
    model_path = model_dir / "model.pkl"
    meta_path = model_dir / "meta.json"

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {meta_path}")

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    with open(meta_path, encoding="utf-8") as f:
        meta_raw = json.load(f)
    meta = SurrogateMeta(**meta_raw)

    logger.info("Loaded model from %s (version: %s)", model_dir, meta.model_version)
    return model, meta


def save_dataset_csv(
    X: list[list[float]] | Any,
    Y: list[list[float]] | Any,
    feature_names: list[str],
    target_names: list[str],
    output_path: Path,
    run_ids: list[str] | None = None,
) -> Path:
    """Save a feature/target dataset as CSV.

    Args:
        X: Feature matrix (n_samples, n_features) as list of lists or ndarray.
        Y: Target matrix (n_samples, n_targets) as list of lists or ndarray.
        feature_names: Column names for features.
        target_names: Column names for targets.
        output_path: Path to write CSV file.
        run_ids: Optional run identifiers per row.

    Returns:
        Path to the written CSV file.
    """
    import csv

    import numpy as np

    X_arr = np.asarray(X)
    Y_arr = np.asarray(Y)

    headers = list(feature_names) + list(target_names)
    if run_ids:
        headers = ["run_id"] + headers

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for i in range(X_arr.shape[0]):
            row = []
            if run_ids and i < len(run_ids):
                row.append(run_ids[i])
            row.extend(X_arr[i].tolist())
            row.extend(Y_arr[i].tolist())
            writer.writerow(row)

    logger.info("Saved dataset CSV to %s (%d rows)", output_path, X_arr.shape[0])
    return output_path


def discover_runs_with_levels(
    runs_root: Path,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    """Scan run directories and find runs with mesh level information.

    Looks for run_manifest.json files and extracts mesh level info from
    context or directory structure.

    Args:
        runs_root: Root directory containing run subdirectories.
        case_id: Optional filter by case ID.

    Returns:
        List of dicts with keys: run_id, run_dir, cell_count, final_residuals,
        case_id, solver.
    """
    runs: list[dict[str, Any]] = []
    if not runs_root.exists():
        logger.warning("Runs root does not exist: %s", runs_root)
        return runs

    for manifest_path in sorted(runs_root.rglob("run_manifest.json")):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read manifest %s: %s", manifest_path, e)
            continue

        found_case_id = manifest.get("case_id", "")
        if case_id and found_case_id != case_id:
            continue

        runs.append({
            "run_id": manifest.get("run_id", ""),
            "run_dir": str(manifest_path.parent),
            "cell_count": manifest.get("cell_count"),
            "final_residuals": manifest.get("final_residuals"),
            "case_id": found_case_id,
            "solver": manifest.get("solver", ""),
        })

    logger.info("Discovered %d runs with manifests", len(runs))
    return runs
