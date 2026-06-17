"""ML mesh surrogate package for CFD-Benchmark.

Provides machine learning models to predict mesh convergence corrections
for coarse-grid CFD runs. Includes feature extraction, model training
(LightGBM/XGBoost), prediction, and serialization.

Main exports:
    - MeshSurrogateModel: Main model class with fit/predict interface.
    - TrainingConfig, SurrogateMeta, SurrogatePrediction: Schema models.
    - build_feature_vector, build_target_vector: Feature engineering.
    - save_model, load_model: Model serialization.
"""

from cfdb.ml.features import build_feature_vector, build_target_vector
from cfdb.ml.io import load_model, save_model
from cfdb.ml.models import MeshSurrogateModel
from cfdb.ml.schema import SurrogateMeta, SurrogatePrediction, TrainingConfig

__all__ = [
    "MeshSurrogateModel",
    "TrainingConfig",
    "SurrogateMeta",
    "SurrogatePrediction",
    "build_feature_vector",
    "build_target_vector",
    "save_model",
    "load_model",
]
