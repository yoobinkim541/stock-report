#!/usr/bin/env python3
"""test_us_policy.py — US 모의 선택 정책 (무네트워크·폐형해)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import us_policy as up


def test_score_weighted_average():
    f = {"ranker": 1.0, "value": 0.0, "quality": 0.0, "mom": 0.0, "conf": 0.0}
    assert up.score(f, up.DEFAULT_POLICY) == pytest.approx(0.40)   # w_ranker


def test_score_renormalizes_missing_components():
    assert up.score({"ranker": 1.0}, up.DEFAULT_POLICY) == pytest.approx(1.0)  # 사용분만 정규화


def test_score_empty_is_zero():
    assert up.score({}, up.DEFAULT_POLICY) == 0.0


def test_extract_features_value_quality():
    f = up.extract_features({"confidence": 80}, {"per": 10, "pbr": 2, "roe": 30},
                            {"price_info": {"1mo_change_pct": 0}})
    assert f["value"] == pytest.approx(0.775, abs=1e-3)   # 저PER/저PBR
    assert f["quality"] == pytest.approx(1.0, abs=1e-3)   # ROE 30%
    assert f["mom"] == pytest.approx(0.5) and f["conf"] == pytest.approx(0.8)


def test_extract_features_graceful_defaults():
    f = up.extract_features(None, None, None)
    assert f == {"value": 0.5, "quality": 0.5, "mom": 0.5, "conf": 0.5}


def test_load_params_has_weights():
    p = up.load_params()
    assert all(k in p for k in ("w_ranker", "w_value", "w_quality", "w_mom", "w_conf"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
