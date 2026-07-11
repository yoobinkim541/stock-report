import numpy as np
import pytest

from ml import gbdt_adapters as gbdt


def test_normalize_backend_aliases():
    assert gbdt.normalize_backend("lgbm") == "lightgbm"
    assert gbdt.normalize_backend("xgb") == "xgboost"
    assert gbdt.normalize_backend("scikit-learn") == "sklearn"


def test_normalize_backend_rejects_unknown():
    with pytest.raises(ValueError, match="unknown GBDT backend"):
        gbdt.normalize_backend("catboost")


def test_backend_available_returns_bool():
    assert isinstance(gbdt.backend_available("lightgbm"), bool)
    assert isinstance(gbdt.backend_available("xgboost"), bool)
    assert isinstance(gbdt.backend_available("sklearn"), bool)


def test_feature_importance_sorted_and_named():
    class Model:
        feature_importances_ = np.array([1.0, 5.0, 2.0])

    out = gbdt.feature_importance(Model(), ["a", "b", "c"])
    assert list(out.index) == ["b", "c", "a"]
    assert out.name == "importance"


def test_feature_importance_zero_fallback():
    out = gbdt.feature_importance(object(), ["a", "b"])
    assert list(out.index) == ["a", "b"]
    assert out.tolist() == [0.0, 0.0]


def test_ranker_model_detection_by_class_name():
    LGBMRanker = type("LGBMRanker", (), {})
    XGBRanker = type("XGBRanker", (), {})
    Other = type("Other", (), {})
    assert gbdt.is_ranker_model(LGBMRanker())
    assert gbdt.is_ranker_model(XGBRanker())
    assert not gbdt.is_ranker_model(Other())


def test_xgboost_regressor_adapter_smoke_if_installed():
    if not gbdt.backend_available("xgboost"):
        pytest.skip("xgboost not installed")

    X = np.array([[0.0, 0.0], [1.0, 0.2], [2.0, 0.4], [3.0, 0.6]], dtype=float)
    y = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    model = gbdt.make_regressor("xgboost", n_estimators=5, max_depth=2, n_jobs=1)
    gbdt.fit_regressor_model(model, X, y, ["a", "b"], "xgboost")

    preds = np.asarray(model.predict(X)).ravel()
    assert preds.shape == (4,)
    assert np.all(np.isfinite(preds))
