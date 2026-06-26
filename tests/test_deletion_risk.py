#!/usr/bin/env python3
"""test_deletion_risk.py — 부실 퇴출 예측 모델 (무네트워크, 합성).

검증: 피처 엔지니어링 무룩어헤드 · 라벨 horizon · 학습셋 OOS AUC(신호 있는 합성) · 콜드스타트 보류 ·
predict None 안전(회피 안 함).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_build_features_no_lookahead():
    from ml import deletion_risk as dr
    series = [{"date": f"2020-{m:02d}-01", "marcap": 1e12 * (1 - 0.02 * m),
               "rank": 100 + m * 5, "close": 100 - m, "amount": 1e9 * (1 - 0.03 * m)}
              for m in range(1, 14)]              # 13개월
    feats = dr.build_features(series)
    assert len(feats) == 13
    last = feats[-1]["features"]
    assert last["rank_chg_6m"] is not None and last["rank_chg_12m"] is not None
    assert last["rank_chg_6m"] > 0               # 순위 악화(증가) = 시총 축소
    assert feats[0]["features"]["rank_chg_6m"] is None    # 초기엔 6개월 lookback 없음


def test_label_distress_horizon():
    from ml import deletion_risk as dr
    dmap = {"111111": {"date": "2020-12-01"}}
    assert dr.label_distress("111111", "2020-06-01", dmap, horizon_m=12) == 1   # 6개월 후 → 라벨 1
    assert dr.label_distress("111111", "2019-06-01", dmap, horizon_m=12) == 0   # 18개월 후 → horizon 밖
    assert dr.label_distress("999999", "2020-06-01", dmap) == 0                 # 상폐 안 됨
    assert dr.label_distress("111111", "2021-06-01", dmap) == 0                 # 이미 지난 상폐


def test_train_deletion_model_learns_signal():
    import numpy as np
    from ml import deletion_risk as dr
    rng = np.random.default_rng(42)
    rows, labels = [], []
    for _ in range(400):
        rank = float(rng.integers(1, 500))
        rank_chg = float(rng.normal(0, 30))
        risk = (rank / 500) * 0.5 + (max(0.0, rank_chg) / 100) * 0.5     # 작은캡 + 순위악화 = 위험
        rows.append({"features": {
            "log_marcap": -rank / 100, "rank": rank, "rank_chg_6m": rank_chg,
            "rank_chg_12m": rank_chg * 1.5, "marcap_chg_6m": -risk, "ret_6m": -risk,
            "ret_12m": -risk, "amount_chg_6m": -risk, "log_amount": 10 - rank / 100,
            "near_boundary": min(1.0, rank / 300)}})
        labels.append(1 if risk + rng.normal(0, 0.04) > 0.4 else 0)      # 임계 + 소량 노이즈
    res = dr.train_deletion_model(rows, labels)
    assert res["model"] is not None, res.get("reason")
    assert res["oos_auc"] > 0.7                  # 신호 있는 합성 → AUC 유의 (파이프라인 학습 검증)
    assert "feature_importance" in res


def test_train_coldstart_holds():
    from ml import deletion_risk as dr
    rows = [{"features": {c: 0.0 for c in dr.FEATURE_COLS}} for _ in range(50)]
    res = dr.train_deletion_model(rows, [0] * 50)
    assert res["model"] is None and "보류" in res["reason"]   # 표본 부족 → 보류


def test_predict_risk_none_safe():
    from ml import deletion_risk as dr
    rows = [{"features": {c: 1.0 for c in dr.FEATURE_COLS}}]
    assert dr.predict_risk(None, rows) == [0.0]   # 모델 없음 → 위험 0(회피 안 함)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
