"""ML surrogate model wrappers: training, prediction, and cross-validation.

Supports LightGBM (default) and XGBoost with a unified fit/predict interface.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np

from cfdb.ml.schema import SurrogatePrediction, TrainingConfig

logger = logging.getLogger(__name__)


class MeshSurrogateModel:
    """Multi-output surrogate model for mesh convergence correction.

    Wraps LightGBM or XGBoost with a simple fit/predict API. Supports
    training on delta targets and predicting corrections for new coarse runs.

    Example usage::

        model = MeshSurrogateModel(config=TrainingConfig(target_qoi=["cl", "cd"]))
        model.fit(X_train, Y_train, feature_names, target_names)
        pred = model.predict(X_new, coarse_qoi={"cl": 0.5, "cd": 0.02}, run_id="r1")
    """

    def __init__(
        self,
        config: TrainingConfig | None = None,
        model_version: str = "v0",
    ):
        """Initialize the surrogate model.

        Args:
            config: TrainingConfig specifying model class, targets, hyperparams.
            model_version: Version string for tracking.
        """
        self.config = config or TrainingConfig()
        self.model_version = model_version
        self._models: dict[str, Any] = {}  # target_name -> trained model
        self._feature_names: list[str] = []
        self._target_names: list[str] = []
        self.cv_rmse: dict[str, float] = {}
        self.cv_r2: dict[str, float] = {}

    def fit(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        feature_names: list[str] | None = None,
        target_names: list[str] | None = None,
    ) -> MeshSurrogateModel:
        """Train one regressor per target QoI.

        Args:
            X: Feature matrix of shape (n_samples, n_features).
            Y: Target matrix of shape (n_samples, n_targets).
            feature_names: Names of input features in column order.
            target_names: Names of target outputs in column order.

        Returns:
            Self, for chaining.
        """
        if target_names is None:
            target_names = self.config.target_qoi
        if feature_names is None:
            feature_names = [f"f{i}" for i in range(X.shape[1])]

        self._feature_names = list(feature_names)
        self._target_names = list(target_names)

        n_targets = Y.shape[1] if Y.ndim > 1 else 1
        Y_2d = Y.reshape(-1, n_targets) if Y.ndim == 1 else Y

        base_params = dict(self.config.hyperparams)
        if "random_state" not in base_params:
            base_params["random_state"] = self.config.random_seed

        for i, target_name in enumerate(target_names):
            y_col = Y_2d[:, i]
            # Drop NaN rows for this target
            mask = ~np.isnan(y_col)
            if not mask.any():
                logger.warning("Target '%s' has all-NaN values, skipping", target_name)
                continue
            X_valid = X[mask]
            y_valid = y_col[mask]

            model = self._create_model(base_params)
            try:
                model.fit(X_valid, y_valid)
            except Exception:
                logger.exception("Failed to train model for target '%s'", target_name)
                raise
            self._models[target_name] = model

        return self

    def predict(
        self,
        X: np.ndarray,
        coarse_qoi: dict[str, float] | None = None,
        run_id: str = "",
    ) -> SurrogatePrediction:
        """Predict correction deltas and compute corrected QoI.

        Args:
            X: Feature matrix (1 sample or n_samples).
            coarse_qoi: Dict of coarse-grid QoI values indexed by name.
            run_id: Run identifier for the prediction record.

        Returns:
            SurrogatePrediction with deltas and corrected values.
        """
        X_2d = X.reshape(1, -1) if X.ndim == 1 else X
        coarse = coarse_qoi or {}

        predicted_delta: dict[str, float] = {}
        for target_name, model in self._models.items():
            try:
                pred = float(model.predict(X_2d)[0])
            except Exception:
                logger.exception("Prediction failed for target '%s'", target_name)
                pred = 0.0
            predicted_delta[target_name] = pred

        corrected_qoi: dict[str, float] = {}
        for target_name, delta in predicted_delta.items():
            base_name = target_name.replace("delta_", "")
            coarse_val = coarse.get(base_name, 0.0)
            corrected_qoi[base_name] = coarse_val + delta

        return SurrogatePrediction(
            run_id=run_id,
            coarse_qoi=coarse,
            predicted_delta=predicted_delta,
            corrected_qoi=corrected_qoi,
            model_version=self.model_version,
        )

    def cross_validate(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        target_names: list[str] | None = None,
    ) -> dict[str, dict[str, float]]:
        """Run k-fold cross-validation and compute per-target RMSE/R2.

        Args:
            X: Feature matrix (n_samples, n_features).
            Y: Target matrix (n_samples, n_targets).
            target_names: Names of targets. Uses self._target_names if None.

        Returns:
            Dict mapping target_name -> {"rmse": float, "r2": float}.
        """
        try:
            from sklearn.metrics import mean_squared_error, r2_score
            from sklearn.model_selection import KFold
        except ImportError:
            logger.warning("scikit-learn not available, skipping cross-validation")
            return {}

        names = target_names or self._target_names
        n_targets = Y.shape[1] if Y.ndim > 1 else 1
        Y_2d = Y.reshape(-1, n_targets) if Y.ndim == 1 else Y
        n_folds = min(self.config.cv_folds, len(X))

        kf = KFold(n_splits=n_folds, shuffle=True, random_state=self.config.random_seed)
        results: dict[str, dict[str, list[float]]] = {
            name: {"rmse": [], "r2": []} for name in names
        }

        base_params = dict(self.config.hyperparams)
        if "random_state" not in base_params:
            base_params["random_state"] = self.config.random_seed

        for train_idx, val_idx in kf.split(X):
            X_train, X_val = X[train_idx], X[val_idx]
            for i, name in enumerate(names):
                y_col = Y_2d[:, i]
                y_train = y_col[train_idx]
                y_val = y_col[val_idx]

                mask_train = ~np.isnan(y_train)
                mask_val = ~np.isnan(y_val)
                if not mask_train.any() or not mask_val.any():
                    continue

                model = self._create_model(base_params)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.fit(X_train[mask_train], y_train[mask_train])
                y_pred = model.predict(X_val[mask_val])

                rmse = float(np.sqrt(mean_squared_error(y_val[mask_val], y_pred)))
                try:
                    r2 = float(r2_score(y_val[mask_val], y_pred))
                except Exception:
                    r2 = float("nan")

                results[name]["rmse"].append(rmse)
                results[name]["r2"].append(r2)

        self.cv_rmse = {k: float(np.mean(v["rmse"])) for k, v in results.items() if v["rmse"]}
        self.cv_r2 = {k: float(np.mean(v["r2"])) for k, v in results.items() if v["r2"]}
        return {k: {"rmse": self.cv_rmse.get(k, float("nan")),
                     "r2": self.cv_r2.get(k, float("nan"))} for k in names}

    def _create_model(self, base_params: dict[str, Any]) -> Any:
        """Create a model instance based on model_class config."""
        if self.config.model_class == "xgboost":
            try:
                import xgboost as xgb

                params = {
                    "n_estimators": 100,
                    "max_depth": 4,
                    "learning_rate": 0.1,
                    "reg_alpha": 0.1,
                    "reg_lambda": 1.0,
                    "subsample": 0.8,
                    "colsample_bytree": 0.8,
                    "verbosity": 0,
                    **base_params,
                }
                return xgb.XGBRegressor(**params)
            except ImportError:
                logger.warning("xgboost not installed, falling back to lightgbm")
                return self._create_lightgbm(base_params)
        return self._create_lightgbm(base_params)

    def _create_lightgbm(self, base_params: dict[str, Any]) -> Any:
        """Create a LightGBM model instance."""
        import lightgbm as lgb

        params = {
            "n_estimators": 100,
            "max_depth": 5,
            "learning_rate": 0.1,
            "reg_alpha": 0.0,
            "reg_lambda": 0.0,
            "num_leaves": 31,
            "verbosity": -1,
            **base_params,
        }
        return lgb.LGBMRegressor(**params)
