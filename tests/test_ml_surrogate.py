"""Tests for the ML mesh surrogate system (cfdb.ml).

Tests cover schema validation, feature engineering, model training/prediction,
and I/O (serialization and run discovery).
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from cfdb.ml.features import (
    DEFAULT_FEATURE_NAMES,
    build_feature_vector,
    build_target_vector,
    extract_features_from_manifest_and_qoi,
)
from cfdb.ml.io import (
    discover_runs_with_levels,
    load_model,
    save_dataset_csv,
    save_model,
)
from cfdb.ml.models import MeshSurrogateModel
from cfdb.ml.schema import SurrogateMeta, SurrogatePrediction, TrainingConfig

# ---- Schema Tests ----


class TestTrainingConfig:
    """Tests for TrainingConfig schema."""

    def test_defaults(self):
        cfg = TrainingConfig()
        assert cfg.model_class == "lightgbm"
        assert cfg.target_qoi == ["cl", "cd"]
        assert cfg.hyperparams == {}
        assert cfg.cv_folds == 5
        assert cfg.random_seed == 42

    def test_custom_config(self):
        cfg = TrainingConfig(
            model_class="xgboost",
            target_qoi=["cl", "cd", "cp"],
            hyperparams={"n_estimators": 200},
            cv_folds=10,
            random_seed=123,
        )
        assert cfg.model_class == "xgboost"
        assert cfg.target_qoi == ["cl", "cd", "cp"]
        assert cfg.hyperparams["n_estimators"] == 200
        assert cfg.cv_folds == 10
        assert cfg.random_seed == 123

    def test_cv_folds_minimum(self):
        cfg = TrainingConfig(cv_folds=2)
        assert cfg.cv_folds == 2

    def test_cv_folds_invalid(self):
        with pytest.raises(ValueError):
            TrainingConfig(cv_folds=1)


class TestSurrogateMeta:
    """Tests for SurrogateMeta schema."""

    def test_minimal(self):
        meta = SurrogateMeta(model_version="v1", model_class="lightgbm")
        assert meta.model_version == "v1"
        assert meta.model_class == "lightgbm"
        assert meta.feature_names == []
        assert meta.cv_rmse == {}

    def test_full(self):
        meta = SurrogateMeta(
            model_version="v2",
            model_class="xgboost",
            trained_on_run_ids=["r1", "r2"],
            feature_names=["cell_count", "cl_coarse"],
            target_names=["delta_cl", "delta_cd"],
            cv_rmse={"delta_cl": 0.01, "delta_cd": 0.0005},
            cv_r2={"delta_cl": 0.95, "delta_cd": 0.92},
            training_config={"model_class": "xgboost"},
            num_samples=50,
            git_commit="abc1234",
        )
        assert meta.num_samples == 50
        assert meta.cv_rmse["delta_cl"] == 0.01
        assert isinstance(meta.created_at, datetime)

    def test_json_roundtrip(self):
        meta = SurrogateMeta(
            model_version="v1",
            model_class="lightgbm",
            trained_on_run_ids=["r1"],
            cv_rmse={"delta_cl": 0.02},
        )
        data = meta.model_dump(mode="json")
        reloaded = SurrogateMeta(**json.loads(json.dumps(data)))
        assert reloaded.model_version == meta.model_version
        assert reloaded.cv_rmse == meta.cv_rmse


class TestSurrogatePrediction:
    """Tests for SurrogatePrediction schema."""

    def test_prediction(self):
        pred = SurrogatePrediction(
            run_id="run_001",
            coarse_qoi={"cl": 0.5, "cd": 0.02},
            predicted_delta={"delta_cl": 0.01, "delta_cd": 0.001},
            corrected_qoi={"cl": 0.51, "cd": 0.021},
            model_version="v1",
        )
        assert pred.corrected_qoi["cl"] == 0.51
        assert pred.model_version == "v1"


# ---- Feature Engineering Tests ----


class TestFeatureEngineering:
    """Tests for feature vector construction."""

    def test_build_full_feature_vector(self):
        arr, names = build_feature_vector(
            cell_count=50000,
            target_y_plus=30.0,
            final_residuals={"Ux": 1e-5, "Uy": 1e-6, "p": 1e-4},
            cl_coarse=1.2,
            cd_coarse=0.03,
            umax_coarse=1.8,
            cp_select_xc={0.0: 1.0, 0.25: -0.5, 0.5: -0.8, 0.75: -0.3, 1.0: 0.0},
        )
        assert names == DEFAULT_FEATURE_NAMES
        assert arr.shape == (len(DEFAULT_FEATURE_NAMES),)
        assert arr[names.index("cell_count")] == 50000.0
        assert arr[names.index("cl_coarse")] == 1.2
        assert arr[names.index("cp_xc_0.25")] == -0.5

    def test_build_minimal_vector(self):
        arr, names = build_feature_vector(cl_coarse=0.8)
        assert arr[names.index("cl_coarse")] == 0.8
        assert np.isnan(arr[names.index("cell_count")])

    def test_build_target_vector(self):
        arr, names = build_target_vector(delta_cl=0.01, delta_cd=0.001)
        assert names == ["delta_cl", "delta_cd"]
        assert arr[0] == 0.01
        assert arr[1] == 0.001

    def test_custom_feature_names(self):
        custom = ["cl_coarse", "cd_coarse"]
        arr, names = build_feature_vector(
            cl_coarse=0.5, cd_coarse=0.02, feature_names=custom
        )
        assert names == custom
        assert arr.shape == (2,)

    def test_extract_from_manifest(self):
        manifest = {
            "cell_count": 30000,
            "final_residuals": {"Ux": 1e-4, "Uy": 2e-5, "p": 3e-4},
        }
        coarse_qoi = {"cl": 1.0, "cd": 0.02, "umax": 1.5}
        cp = {0.0: 1.0, 0.5: -0.6, 0.75: -0.2}

        arr, names = extract_features_from_manifest_and_qoi(manifest, coarse_qoi, cp)
        assert arr[names.index("cell_count")] == 30000.0
        assert arr[names.index("cl_coarse")] == 1.0
        assert arr[names.index("cp_xc_0.50")] == -0.6

    def test_extract_from_manifest_missing_residuals(self):
        manifest = {"cell_count": 10000}
        coarse_qoi = {"cl": 0.5}
        arr, names = extract_features_from_manifest_and_qoi(manifest, coarse_qoi)
        assert arr[names.index("cl_coarse")] == 0.5
        assert np.isnan(arr[names.index("residual_Ux")])

    def test_extract_manifest_none_residuals(self):
        manifest = {"cell_count": 10000, "final_residuals": None}
        coarse_qoi = {"cl": 0.5}
        arr, names = extract_features_from_manifest_and_qoi(manifest, coarse_qoi)
        assert arr[names.index("cell_count")] == 10000.0


# ---- Model Tests ----


class TestMeshSurrogateModel:
    """Tests for MeshSurrogateModel fit/predict."""

    @pytest.fixture
    def synthetic_data(self):
        """Generate synthetic mesh convergence data."""
        rng = np.random.RandomState(42)
        n = 100
        cell_count = rng.randint(10000, 100000, n)
        target_y_plus = rng.uniform(1, 60, n)
        cl = rng.uniform(0.3, 1.5, n)

        # Create synthetic CD that depends on CL and cell count
        cd = 0.01 + 0.02 * cl + 0.5 / np.sqrt(cell_count) + rng.normal(0, 0.001, n)

        # Targets: delta from a known function
        delta_cl = 0.05 / np.sqrt(cell_count / 1000) + rng.normal(0, 0.002, n)
        delta_cd = 0.001 / np.sqrt(cell_count / 1000) + rng.normal(0, 0.0001, n)

        X = np.column_stack([cell_count, target_y_plus, cl, cd])
        Y = np.column_stack([delta_cl, delta_cd])
        feature_names = ["cell_count", "target_y_plus", "cl_coarse", "cd_coarse"]
        target_names = ["delta_cl", "delta_cd"]
        return X, Y, feature_names, target_names

    def test_fit_basic(self, synthetic_data):
        X, Y, feature_names, target_names = synthetic_data
        model = MeshSurrogateModel(
            config=TrainingConfig(
                model_class="lightgbm",
                target_qoi=target_names,
                hyperparams={"n_estimators": 50, "num_leaves": 15},
            ),
            model_version="test-v1",
        )
        model.fit(X, Y, feature_names, target_names)
        assert len(model._models) == 2
        assert "delta_cl" in model._models
        assert model._feature_names == feature_names

    def test_predict_basic(self, synthetic_data):
        X, Y, feature_names, target_names = synthetic_data
        model = MeshSurrogateModel(
            config=TrainingConfig(
                model_class="lightgbm",
                target_qoi=target_names,
                hyperparams={"n_estimators": 50, "num_leaves": 15},
            ),
        )
        model.fit(X, Y, feature_names, target_names)

        pred = model.predict(
            X[0:1],
            coarse_qoi={"cl": 0.5, "cd": 0.02},
            run_id="test_run",
        )

        assert pred.run_id == "test_run"
        assert "delta_cl" in pred.predicted_delta
        assert "delta_cd" in pred.predicted_delta
        assert "cl" in pred.corrected_qoi
        assert "cd" in pred.corrected_qoi
        # Corrected values should be near coarse + delta
        assert abs(
            pred.corrected_qoi["cl"] - (0.5 + pred.predicted_delta["delta_cl"])
        ) < 1e-10

    def test_fit_with_nan_targets(self, synthetic_data):
        X, Y, feature_names, target_names = synthetic_data
        # Inject NaN into delta_cd
        Y[50:60, 1] = np.nan

        model = MeshSurrogateModel(
            config=TrainingConfig(
                model_class="lightgbm",
                target_qoi=target_names,
                hyperparams={"n_estimators": 50, "num_leaves": 15},
            ),
        )
        model.fit(X, Y, feature_names, target_names)
        # Should still train both models (NaN rows are skipped)
        assert "delta_cl" in model._models
        assert "delta_cd" in model._models

    def test_cross_validate(self, synthetic_data):
        X, Y, feature_names, target_names = synthetic_data
        model = MeshSurrogateModel(
            config=TrainingConfig(
                model_class="lightgbm",
                target_qoi=target_names,
                cv_folds=3,
                hyperparams={"n_estimators": 30, "num_leaves": 10},
            ),
        )
        model._target_names = target_names
        cv_results = model.cross_validate(X, Y, target_names)
        assert "delta_cl" in cv_results
        assert "rmse" in cv_results["delta_cl"]
        # RMSE should be finite
        assert not np.isnan(cv_results["delta_cl"]["rmse"])
        assert cv_results["delta_cl"]["rmse"] > 0

    def test_predict_single_sample(self, synthetic_data):
        X, Y, feature_names, target_names = synthetic_data
        model = MeshSurrogateModel(
            config=TrainingConfig(
                model_class="lightgbm",
                target_qoi=target_names,
                hyperparams={"n_estimators": 50, "num_leaves": 15},
            ),
        )
        model.fit(X, Y, feature_names, target_names)

        # 1D input should be reshaped
        pred = model.predict(X[0], coarse_qoi={"cl": 0.8, "cd": 0.03}, run_id="r1d")
        assert pred.run_id == "r1d"
        assert "cl" in pred.corrected_qoi

    def test_training_config_target_order(self):
        """Target output order follows TrainingConfig.target_qoi."""
        cfg = TrainingConfig(target_qoi=["cd", "cl"])
        assert cfg.target_qoi == ["cd", "cl"]


# ---- I/O Tests ----


class TestModelIO:
    """Tests for model serialization and dataset I/O."""

    @pytest.fixture
    def trained_model(self):
        """Create a simple trained model for I/O testing."""
        rng = np.random.RandomState(0)
        X = rng.randn(20, 3)
        Y = np.column_stack([X[:, 0] * 0.5, X[:, 1] * 0.3])
        model = MeshSurrogateModel(
            config=TrainingConfig(
                target_qoi=["delta_cl", "delta_cd"],
                hyperparams={"n_estimators": 20, "num_leaves": 5},
            ),
            model_version="io-test",
        )
        model.fit(X, Y, ["f0", "f1", "f2"], ["delta_cl", "delta_cd"])
        return model

    def test_save_and_load(self, trained_model):
        meta = SurrogateMeta(
            model_version="io-test",
            model_class="lightgbm",
            feature_names=["f0", "f1", "f2"],
            target_names=["delta_cl", "delta_cd"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "model"
            save_model(trained_model, meta, out_dir)

            assert (out_dir / "model.pkl").exists()
            assert (out_dir / "meta.json").exists()

            loaded_model, loaded_meta = load_model(out_dir)
            assert loaded_meta.model_version == "io-test"
            assert loaded_meta.feature_names == ["f0", "f1", "f2"]
            assert loaded_model.model_version == "io-test"

    def test_load_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "empty_model"
            out_dir.mkdir(parents=True, exist_ok=True)
            with pytest.raises(FileNotFoundError):
                load_model(out_dir)

    def test_model_persistence_predictions(self, trained_model):
        """Ensure predictions are consistent after save/load roundtrip."""
        meta = SurrogateMeta(
            model_version="io-test",
            model_class="lightgbm",
            feature_names=["f0", "f1", "f2"],
            target_names=["delta_cl", "delta_cd"],
        )

        X_test = np.array([[1.0, 0.5, -0.5]])
        pred_before = trained_model.predict(
            X_test, coarse_qoi={"cl": 0.5, "cd": 0.02}, run_id="r"
        )

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "model"
            save_model(trained_model, meta, out_dir)
            loaded_model, _ = load_model(out_dir)
            pred_after = loaded_model.predict(
                X_test, coarse_qoi={"cl": 0.5, "cd": 0.02}, run_id="r"
            )

        assert pred_before.predicted_delta == pred_after.predicted_delta
        assert pred_before.corrected_qoi == pred_after.corrected_qoi

    def test_save_dataset_csv(self):
        X = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        Y = [[0.1], [0.2], [0.3]]
        feature_names = ["f1", "f2"]
        target_names = ["delta_cl"]
        run_ids = ["r1", "r2", "r3"]

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "dataset.csv"
            result = save_dataset_csv(X, Y, feature_names, target_names, csv_path, run_ids)
            assert result.exists()

            content = result.read_text()
            assert "run_id" in content
            assert "f1,f2,delta_cl" in content
            assert "r1" in content


class TestDiscoverRuns:
    """Tests for run discovery."""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = discover_runs_with_levels(Path(tmp))
            assert runs == []

    def test_discover_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)

            manifest = {
                "run_id": "run_001",
                "case_id": "naca0012",
                "cell_count": 50000,
                "final_residuals": {"Ux": 1e-5, "Uy": 1e-6, "p": 1e-4},
                "solver": "openfoam",
            }
            (run_dir / "run_manifest.json").write_text(json.dumps(manifest))

            runs = discover_runs_with_levels(root)
            assert len(runs) == 1
            assert runs[0]["run_id"] == "run_001"
            assert runs[0]["cell_count"] == 50000
            assert runs[0]["case_id"] == "naca0012"

    def test_discover_filter_by_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, case_id in enumerate(["naca0012", "flatplate", "naca0012"]):
                run_dir = root / "runs" / f"run_{i:03d}"
                run_dir.mkdir(parents=True)
                manifest = {
                    "run_id": f"run_{i:03d}",
                    "case_id": case_id,
                    "cell_count": 10000 + i,
                    "final_residuals": {},
                    "solver": "openfoam",
                }
                (run_dir / "run_manifest.json").write_text(json.dumps(manifest))

            runs = discover_runs_with_levels(root, case_id="naca0012")
            assert len(runs) == 2
            assert all(r["case_id"] == "naca0012" for r in runs)

    def test_skip_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "bad_run"
            run_dir.mkdir(parents=True)
            (run_dir / "run_manifest.json").write_text("not valid json")

            runs = discover_runs_with_levels(root)
            assert runs == []  # invalid JSON should be skipped


# ---- Integration Tests ----


class TestEndToEnd:
    """End-to-end tests for the full ML surrogate workflow."""

    def test_train_predict_save_roundtrip(self):
        """Full cycle: train on synthetic data, predict, save, load, predict again."""
        rng = np.random.RandomState(123)
        n = 50

        # Synthetic mesh data resembling NACA0012 runs
        cell_counts = rng.randint(20000, 120000, n)
        cl_coarse = 0.3 + 0.08 * cell_counts / 100000 + rng.normal(0, 0.01, n)
        cd_coarse = 0.015 + 0.005 / np.sqrt(cell_counts / 1000) + rng.normal(0, 0.0005, n)

        # Targets: fine-grid corrections
        delta_cl_true = 0.03 / np.sqrt(cell_counts / 5000) + rng.normal(0, 0.001, n)
        delta_cd_true = 0.002 / np.sqrt(cell_counts / 5000) + rng.normal(0, 0.0001, n)

        X = np.column_stack([cell_counts, cl_coarse, cd_coarse])
        Y = np.column_stack([delta_cl_true, delta_cd_true])
        feature_names = ["cell_count", "cl_coarse", "cd_coarse"]
        target_names = ["delta_cl", "delta_cd"]

        # Train
        cfg = TrainingConfig(
            model_class="lightgbm",
            target_qoi=target_names,
            hyperparams={"n_estimators": 60, "num_leaves": 15},
            cv_folds=3,
        )
        model = MeshSurrogateModel(config=cfg, model_version="e2e-v1")
        model.fit(X, Y, feature_names, target_names)

        # Cross-validate
        cv_results = model.cross_validate(X, Y, target_names)
        assert cv_results["delta_cl"]["rmse"] > 0
        assert cv_results["delta_cd"]["rmse"] > 0

        # Predict
        X_new = np.array([[80000, 0.5, 0.025]])
        pred = model.predict(
            X_new,
            coarse_qoi={"cl": 0.5, "cd": 0.025},
            run_id="new_run",
        )
        assert "cl" in pred.corrected_qoi
        # Correction should move in the right direction (reduce error)
        # For large cell_count, delta should be relatively small
        assert abs(pred.predicted_delta["delta_cl"]) < 0.1

        # Save and reload
        meta = SurrogateMeta(
            model_version="e2e-v1",
            model_class="lightgbm",
            feature_names=feature_names,
            target_names=target_names,
            cv_rmse=model.cv_rmse,
            cv_r2=model.cv_r2,
            training_config=cfg.model_dump(),
            num_samples=n,
            trained_on_run_ids=[f"run_{i:03d}" for i in range(n)],
        )

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "model"
            save_model(model, meta, out_dir)

            loaded_model, loaded_meta = load_model(out_dir)
            assert loaded_meta.model_version == "e2e-v1"
            assert loaded_meta.num_samples == n

            # Predictions should match after reload
            pred2 = loaded_model.predict(
                X_new,
                coarse_qoi={"cl": 0.5, "cd": 0.025},
                run_id="new_run",
            )
            assert pred2.predicted_delta == pred.predicted_delta


class TestFeatureCompleteness:
    """Verify all required features from the plan are present."""

    def test_default_features_match_plan(self):
        """Plan specifies: cell_count, target_y_plus, residuals(Ux/Uy/p),
        cl/cd/umax coarse, Cp at x/c=[0,0.25,0.5,0.75,1.0]."""
        expected = [
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
        assert expected == DEFAULT_FEATURE_NAMES
        assert len(DEFAULT_FEATURE_NAMES) == 13
