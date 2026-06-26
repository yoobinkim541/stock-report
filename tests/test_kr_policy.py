#!/usr/bin/env python3
"""test_kr_policy.py — KR 선택 정책 점수 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import kr_policy                       # noqa: E402
from ml.adaptive import policy as pol          # noqa: E402


def test_extract_features_point_in_time_normalized():
    fund = {"total_score": 80}
    sig = {"overall_signal": "Positive", "price_info": {"1mo_change_pct": 20}}
    dec = {"confidence": 70}
    f = kr_policy.extract_features(fund, sig, dec)
    assert f["fund"] == 0.8
    assert f["signal"] == 1.0
    assert f["conf"] == 0.7
    assert f["mom"] == 1.0          # +20% → 상단
    # 결정성: 같은 입력 같은 출력
    assert kr_policy.extract_features(fund, sig, dec) == f


def test_extract_handles_missing():
    f = kr_policy.extract_features(None, None, None)
    assert set(f) == {"fund", "signal", "conf", "mom"}
    assert f["signal"] == 0.5      # 기본 Neutral


def test_score_weighted_average():
    feats = {"fund": 1.0, "signal": 1.0, "conf": 1.0, "mom": 1.0, "ranker": 1.0}
    params = {"w_ranker": 0.4, "w_fund": 0.2, "w_signal": 0.2, "w_conf": 0.1, "w_mom": 0.1}
    assert kr_policy.score(feats, params) == pytest.approx(1.0)   # 전부 1 → 1
    feats0 = {k: 0.0 for k in ("fund", "signal", "conf", "mom", "ranker")}
    assert kr_policy.score(feats0, params) == 0.0


def test_score_renormalizes_when_ranker_missing():
    # ranker 누락 → 나머지 가중으로 재정규화 (graceful)
    feats = {"fund": 1.0, "signal": 0.0, "conf": 0.0, "mom": 0.0}   # ranker 없음
    params = {"w_ranker": 0.4, "w_fund": 0.2, "w_signal": 0.2, "w_conf": 0.1, "w_mom": 0.1}
    # 사용 가중 합 0.6, fund만 1 → 0.2/0.6 = 0.333
    assert kr_policy.score(feats, params) == pytest.approx(0.2 / 0.6)


def test_policy_clamp_via_adaptive(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    p = kr_policy.get_policy()
    saved = p.save({"w_ranker": 5.0})       # 범위 밖 → 1.0 클램프
    assert saved["w_ranker"] == 1.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
