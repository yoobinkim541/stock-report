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


# ── ★PEAD 축 (어닝 드리프트 — 순수·무네트워크) ────────────────────────────────

def _closes(base_date="2026-05-01", n=40, jump_at=10, jump=0.0):
    import pandas as pd
    idx = pd.bdate_range(base_date, periods=n)
    px = [100.0] * n
    for i in range(jump_at, n):
        px[i] = 100.0 * (1 + jump)
    return pd.Series(px, index=idx)


def test_pead_axis_beat_recent_above_half():
    ev = [{"date": "2026-06-01", "surprise_pct": 8.0}]
    v = up.pead_axis(ev, None, asof="2026-06-10")
    assert v is not None and v > 0.5


def test_pead_axis_miss_below_half_and_decay():
    ev = [{"date": "2026-06-01", "surprise_pct": -8.0}]
    fresh = up.pead_axis(ev, None, asof="2026-06-05")
    old = up.pead_axis(ev, None, asof="2026-07-20")
    assert fresh is not None and fresh < 0.5
    assert old is not None and abs(old - 0.5) < abs(fresh - 0.5)   # 감쇠 → 0.5 수렴


def test_pead_axis_none_when_stale_or_empty():
    ev = [{"date": "2026-01-02", "surprise_pct": 10.0}]
    assert up.pead_axis(ev, None, asof="2026-06-30") is None       # >60일
    assert up.pead_axis([], None, asof="2026-06-30") is None
    assert up.pead_axis([{"date": "2026-06-01", "surprise_pct": None}], None,
                        asof="2026-06-10") is None


def test_pead_axis_reaction_amplifies():
    import pandas as pd
    idx = pd.bdate_range("2026-05-20", periods=20)
    up_px = pd.Series([100.0] * 9 + [106.0] * 11, index=idx)       # 실적일 +6% 반응
    flat = pd.Series(100.0, index=idx)
    ev = [{"date": str(idx[8].date()), "surprise_pct": 5.0}]
    asof = str(idx[-1].date())
    with_r = up.pead_axis(ev, up_px, asof=asof)
    no_r = up.pead_axis(ev, flat, asof=asof)
    assert with_r > no_r > 0.5                                     # 상승반응이 축을 증폭


def test_pead_in_default_policy_zero_weight():
    assert up.DEFAULT_POLICY["w_pead"] == 0.0 and "w_pead" in up.BOUNDS
    # 가중 0 → score 무영향 (수집 전용)
    f = {"ranker": 1.0, "pead": 1.0}
    assert up.score(f, up.DEFAULT_POLICY) == pytest.approx(1.0)


# ── US 가격축 shadow 게이트 (공용 axes_shadow 경유) ───────────────────────────

def test_us_axes_shadow_applied_when_enabled(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    monkeypatch.setenv("ADAPTIVE_US_AXES_ENABLED", "true")
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "policy_weights": {"w_hi52": 0.21, "w_lowvol": 0.14,
                                                "w_mom12": 0.0, "w_mom": 0.0}}))
    out = up._apply_axes_shadow(dict(up.DEFAULT_POLICY), path=str(p))
    assert out["w_hi52"] == pytest.approx(0.21) and out["w_lowvol"] == pytest.approx(0.14)
    assert out["w_ranker"] == up.DEFAULT_POLICY["w_ranker"]        # 비가격축 불변
    monkeypatch.delenv("ADAPTIVE_US_AXES_ENABLED")
    same = up._apply_axes_shadow(dict(up.DEFAULT_POLICY), path=str(p))
    assert same == dict(up.DEFAULT_POLICY)                          # env off → 불변
