#!/usr/bin/env python3
"""test_earnings_move.py — 실적후 주가반응 예측 G4 (무네트워크, 합성).

변동폭(magnitude)은 예측 가능(vol·과거반응) → 나이브 대비 skill 검증. 방향은 약함(정직 — 미검증).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_event_features():
    from ml.earnings_move_predictor import event_features
    f = event_features([0.05, 0.03, 0.04], [1, 0, 1], [2.0, -1.0, 3.0], mom_20d=0.01, vol_20d=0.02)
    assert abs(f["hist_avg_abs_move"] - 0.04) < 1e-6
    assert abs(f["hist_drift_persist"] - round(2 / 3, 3)) < 1e-6
    assert abs(f["prior_surprise_mean"] - round(4 / 3, 3)) < 1e-6
    f0 = event_features([], [], [])
    assert f0["hist_avg_abs_move"] is None


def test_train_magnitude_skill():
    import numpy as np
    from ml import earnings_move_predictor as mp
    rng = np.random.default_rng(11)
    rows, mag, dirn = [], [], []
    for _ in range(220):
        vol = abs(float(rng.normal(0.02, 0.01)))
        havg = abs(float(rng.normal(0.04, 0.02)))
        m = 0.02 + 1.0 * vol + 0.5 * havg + abs(float(rng.normal(0, 0.004)))   # 변동폭 ~ vol+과거반응
        rows.append({"features": {"hist_avg_abs_move": havg, "hist_drift_persist": 0.5,
                                  "prior_surprise_mean": 1.0, "mom_20d": float(rng.normal(0, 0.03)),
                                  "vol_20d": vol, "beat_prob": None, "iv_expected_move": None}})
        mag.append(m)
        dirn.append(1 if rng.random() > 0.5 else 0)
    res = mp.train(rows, mag, dirn)
    assert res["mag_model"] is not None, res.get("reason")
    assert res["mag_skill"] > 0.1            # 변동폭은 나이브 평균예측보다 유의하게 우수


def test_train_coldstart_holds():
    from ml import earnings_move_predictor as mp
    rows = [{"features": {c: 0.0 for c in mp.FEATURE_COLS}} for _ in range(40)]
    res = mp.train(rows, [0.03] * 40, [1] * 40)
    assert res["mag_model"] is None and "보류" in res["reason"]


def test_predict_none_safe():
    from ml import earnings_move_predictor as mp
    out = mp.predict({"mag_model": None}, [{"features": {}}])
    assert out == [{"expected_abs_move": None, "p_up": 0.5}]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
