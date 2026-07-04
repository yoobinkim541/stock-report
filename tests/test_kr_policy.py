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


# ── ★가격 축 3종 (price_axes — 2026-07 kr_policy_backtest 실증 반영) ──────────

def _series(vals):
    import pandas as pd
    return pd.Series(vals, index=pd.bdate_range("2023-01-02", periods=len(vals)))


def test_price_axes_full_history():
    """253일+ 이력 → mom12·hi52·lowvol 모두 [0,1]. 상승 추세면 hi52≈1·mom12>0.5."""
    up = _series([100 * (1.003 ** i) for i in range(300)])
    ax = kr_policy.price_axes(up)
    assert set(ax) == {"mom12", "hi52", "lowvol"}
    assert 0.0 <= min(ax.values()) and max(ax.values()) <= 1.0
    assert ax["mom12"] > 0.5 and ax["hi52"] > 0.99


def test_price_axes_short_history_graceful():
    assert kr_policy.price_axes(_series([100.0] * 50)) == {}          # <130일 → {}
    ax = kr_policy.price_axes(_series([100.0] * 200))                 # 130~252일 → mom12 없음
    assert "mom12" not in ax and "hi52" in ax


def test_price_axes_lowvol_ranks_calm_above_wild():
    import numpy as np
    rng = np.random.default_rng(11)
    calm = _series(list(100 * np.cumprod(1 + rng.normal(0, 0.005, 300))))
    wild = _series(list(100 * np.cumprod(1 + rng.normal(0, 0.03, 300))))
    assert kr_policy.price_axes(calm)["lowvol"] > kr_policy.price_axes(wild)["lowvol"]


def test_default_policy_has_new_axes_and_score_uses_them():
    for k in ("w_hi52", "w_lowvol", "w_mom12"):
        assert k in kr_policy.DEFAULT_POLICY and k in kr_policy.BOUNDS
    # 새 축만 1.0 이고 나머지 0 인 피처 → 새 축 가중만으로 정규화 점수 산출
    feats = {"hi52": 1.0, "lowvol": 1.0, "mom12": 1.0}
    assert kr_policy.score(feats, kr_policy.DEFAULT_POLICY) == pytest.approx(1.0)
    # 축 미기록(구 원장 행) → 기존 5축만으로 재정규화 (graceful 하위호환)
    old = {"fund": 1.0, "signal": 1.0, "conf": 1.0, "mom": 1.0, "ranker": 1.0}
    assert kr_policy.score(old, kr_policy.DEFAULT_POLICY) == pytest.approx(1.0)


# ── 가격축 shadow 게이트 머지 (_apply_axes_shadow) ────────────────────────────

def _shadow_file(tmp_path, weights, days_old=0):
    import json
    from datetime import datetime, timedelta
    p = tmp_path / "axes_shadow.json"
    asof = (datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M")
    p.write_text(json.dumps({"asof": asof, "chosen": "t", "policy_weights": weights}),
                 encoding="utf-8")
    return str(p)


def test_axes_shadow_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ADAPTIVE_KR_AXES_ENABLED", raising=False)
    p = dict(kr_policy.DEFAULT_POLICY)
    sp = _shadow_file(tmp_path, {"w_hi52": 0.5})
    assert kr_policy._apply_axes_shadow(p, path=sp) == p   # env off → 불변


def test_axes_shadow_applied_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    sp = _shadow_file(tmp_path, {"w_hi52": 0.21, "w_lowvol": 0.14, "w_mom12": 0.0, "w_mom": 0.0})
    out = kr_policy._apply_axes_shadow(dict(kr_policy.DEFAULT_POLICY), path=sp)
    assert out["w_hi52"] == pytest.approx(0.21)
    assert out["w_lowvol"] == pytest.approx(0.14)
    assert out["w_mom"] == 0.0                             # 권고가 0 인 축은 0 으로 교체
    assert out["w_ranker"] == kr_policy.DEFAULT_POLICY["w_ranker"]   # 비가격축 불변


def test_axes_shadow_stale_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    p = dict(kr_policy.DEFAULT_POLICY)
    sp = _shadow_file(tmp_path, {"w_hi52": 0.5}, days_old=kr_policy.AXES_SHADOW_MAX_AGE_D + 5)
    assert kr_policy._apply_axes_shadow(p, path=sp) == p   # stale → 무시


def test_axes_shadow_capped_at_max_share(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    # 가격축 합 2.0 (극단) → 전체의 AXES_MAX_SHARE 이하로 비례 축소 + 클램프
    sp = _shadow_file(tmp_path, {"w_hi52": 1.0, "w_lowvol": 1.0})
    out = kr_policy._apply_axes_shadow(dict(kr_policy.DEFAULT_POLICY), path=sp)
    axes = sum(out[k] for k in ("w_mom12", "w_hi52", "w_lowvol", "w_mom"))
    total = sum(v for k, v in out.items() if k.startswith("w_"))
    assert axes / total <= kr_policy.AXES_MAX_SHARE + 1e-6


def test_axes_shadow_missing_file_graceful(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    p = dict(kr_policy.DEFAULT_POLICY)
    assert kr_policy._apply_axes_shadow(p, path="/nonexistent/x.json") == p
