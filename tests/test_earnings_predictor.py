#!/usr/bin/env python3
"""test_earnings_predictor.py — 실적 서프라이즈 예측 G3 (무네트워크, 합성)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_event_features():
    from ml.earnings_predictor import event_features
    f = event_features([5.0, -2.0, 3.0], mom_20d=0.05, vol_20d=0.02)
    assert f["prior_n"] == 3.0
    assert abs(f["prior_surprise_mean"] - 2.0) < 1e-6        # (5-2+3)/3
    assert f["prior_beat_rate"] == round(2 / 3, 3)           # 2/3 양수
    assert f["last_surprise"] == 3.0 and f["mom_20d"] == 0.05
    f0 = event_features([])
    assert f0["prior_n"] == 0.0 and f0["prior_surprise_mean"] is None


def test_train_learns_signal():
    import numpy as np
    from ml import earnings_predictor as ep
    rng = np.random.default_rng(7)
    rows, labels = [], []
    for _ in range(320):
        pbr = float(rng.random())
        mom = float(rng.normal(0, 0.03))
        rows.append({"features": {"prior_n": 8.0, "prior_surprise_mean": pbr * 5 - 2,
                                  "prior_surprise_std": 3.0, "prior_beat_rate": pbr,
                                  "last_surprise": pbr * 5 - 2, "mom_20d": mom, "vol_20d": 0.02,
                                  "revision_momentum": None}})
        labels.append(1 if pbr + 8 * mom + rng.normal(0, 0.08) > 0.5 else 0)   # 과거beat율+모멘텀 임계
    res = ep.train(rows, labels)
    assert res["model"] is not None, res.get("reason")
    assert res["oos_auc"] > 0.65                              # 신호 있는 합성 → 파이프라인 학습 검증


def test_train_coldstart_holds():
    from ml import earnings_predictor as ep
    rows = [{"features": {c: 0.0 for c in ep.FEATURE_COLS}} for _ in range(30)]
    res = ep.train(rows, [0] * 30)
    assert res["model"] is None and "보류" in res["reason"]


def test_predict_none_neutral():
    from ml import earnings_predictor as ep
    assert ep.predict_beat(None, [{"features": {}}]) == [0.5]    # 모델 없음 → 중립


def test_save_load_roundtrip(tmp_path):
    import numpy as np
    from ml import earnings_predictor as ep
    rng = np.random.default_rng(3)
    rows, labels = [], []
    for _ in range(220):
        pbr = float(rng.random())
        feats = {c: 0.0 for c in ep.FEATURE_COLS}
        feats["prior_beat_rate"] = pbr
        rows.append({"features": feats})
        labels.append(1 if pbr + rng.normal(0, 0.05) > 0.5 else 0)
    res = ep.train(rows, labels)
    assert res["model"] is not None
    p = tmp_path / "m.pkl"
    ep.save_model(res["model"], p)
    m2 = ep.load_model(p)
    assert m2 is not None
    assert ep.predict_beat(res["model"], rows[:3]) == ep.predict_beat(m2, rows[:3])   # 동일 예측
    assert ep.load_model(tmp_path / "none.pkl") is None        # 없는 파일 → None


def test_earnings_loaders_reject_symlink(tmp_path):
    """earnings 모델 로더가 심링크 캐시를 거부하는지 (safe_unpickle 배선 — 감사 확정 회귀)."""
    import os
    import pickle
    from pathlib import Path
    import ml.earnings_predictor as ep
    import ml.earnings_move_predictor as emp

    real = tmp_path / "real.pkl"
    with open(real, "wb") as f:
        pickle.dump({"kind": "dummy_model"}, f)
    link = tmp_path / "link.pkl"
    os.symlink(real, link)

    # 심링크 → safe_unpickle 거부 → None (raw pickle.load 였으면 로드됐음)
    assert ep.load_model(Path(link)) is None
    assert emp.load_model(Path(link)) is None
    # 소유자 검증 통과하는 정상 파일은 로드
    assert ep.load_model(Path(real)) == {"kind": "dummy_model"}
    assert emp.load_model(Path(real)) == {"kind": "dummy_model"}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
