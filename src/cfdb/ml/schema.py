"""Pydantic models for ML mesh surrogate system.

Defines the data structures used throughout training, prediction, and
model metadata tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TrainingConfig(BaseModel):
    """Configuration for surrogate model training."""

    model_config = ConfigDict(extra="forbid")

    model_class: Literal["xgboost", "lightgbm"] = "lightgbm"
    """Model class to use for training."""

    target_qoi: list[str] = Field(default_factory=lambda: ["cl", "cd"])
    """List of QoI names to predict deltas for."""

    hyperparams: dict[str, Any] = Field(default_factory=dict)
    """Hyperparameters passed directly to the model constructor."""

    cv_folds: int = Field(default=5, ge=2)
    """Number of cross-validation folds."""

    random_seed: int = 42
    """Random seed for reproducibility."""


class SurrogateMeta(BaseModel):
    """Metadata describing a trained surrogate model."""

    model_config = ConfigDict(extra="forbid")

    model_version: str
    """Unique version identifier for this trained model."""

    model_class: str
    """Model class used (e.g. 'lightgbm', 'xgboost')."""

    trained_on_run_ids: list[str] = Field(default_factory=list)
    """Run IDs used for training."""

    feature_names: list[str] = Field(default_factory=list)
    """Names of input features in order."""

    target_names: list[str] = Field(default_factory=list)
    """Names of target outputs in order."""

    cv_rmse: dict[str, float] = Field(default_factory=dict)
    """Cross-validation RMSE per target QoI."""

    cv_r2: dict[str, float] = Field(default_factory=dict)
    """Cross-validation R2 per target QoI."""

    training_config: dict[str, Any] = Field(default_factory=dict)
    """Snapshot of TrainingConfig used."""

    num_samples: int = 0
    """Number of training samples."""

    git_commit: str | None = None
    """Git commit hash at training time."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """Timestamp when model was trained."""


class SurrogatePrediction(BaseModel):
    """Result of a single surrogate model prediction."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    """Run identifier for which prediction was made."""

    coarse_qoi: dict[str, float] = Field(default_factory=dict)
    """Coarse-grid QoI values before correction."""

    predicted_delta: dict[str, float] = Field(default_factory=dict)
    """Predicted correction deltas (coarse -> fine)."""

    corrected_qoi: dict[str, float] = Field(default_factory=dict)
    """Corrected QoI = coarse_qoi + predicted_delta."""

    model_version: str
    """Version of the model used for prediction."""
