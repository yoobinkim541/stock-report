"""tests/test_us_policy_backtest.py — US 선택정책 백테스트 순수로직 (무네트워크·합성).

핵심 감사: ①시점 멤버십 마스킹(비멤버 제외) ②커버리지 산출·GO 강등 ③워크포워드 폴드
④kr 프리미티브 재사용 경로 무예외. 가격 조립(build_panels)은 서버 전용 — 여기선 합성 주입.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "backtest"))

import us_policy_backtest as ub


def _panels(n_days=1400, n_codes=25, seed=9, member_until: dict | None = None):
    """합성: 티커 T01..T25, 기본 전기간 멤버. member_until={tk: 'YYYY-MM-DD'} 로 중도 퇴출."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-02", periods=n_days)
    codes = [f"T{i:02d}" for i in range(1, n_codes + 1)]
    ret = pd.DataFrame(rng.normal(0.0005, 0.012, (n_days, n_codes)), index=dates, columns=codes)
    iv = {}
    for c in codes:
        end = (member_until or {}).get(c, "9999-12-31")
        iv[c] = [("2018-01-01", end)]
    return {"ret": ret, "close": (1 + ret).cumprod(), "intervals": iv}


def test_features_masks_non_members():
    """t 시점 비멤버(중도 퇴출)는 유니버스에서 제외 — 시점 멤버십 마스킹."""
    p = _panels(member_until={"T05": "2020-06-30"})
    t = bt_last = ub.month_ends(p["ret"].index)[-2]
    f, cov = ub.features_asof(p, t)
    assert f is not None
    assert "T05" not in f.index                      # 2020-06 퇴출 → 이후 시점 제외
    early = pd.Timestamp("2020-03-31")
    f2, _ = ub.features_asof(p, ub.month_ends(p["ret"].index[p["ret"].index <= early])[-1])
    if f2 is not None:                               # 이력 260일 충족 시
        assert "T05" in f2.index                     # 퇴출 전엔 포함


def test_features_coverage_counts_missing_prices():
    """멤버인데 가격이 없는 티커 → 커버리지 하락으로 정량화."""
    p = _panels()
    p["intervals"]["GHOST"] = [("2018-01-01", "9999-12-31")]   # 가격 없는 멤버(상폐종목 모사)
    t = ub.month_ends(p["ret"].index)[-1]
    f, cov = ub.features_asof(p, t)
    assert f is not None
    assert cov == pytest.approx(25 / 26, abs=1e-6)


def test_degrade_go_when_low_coverage():
    v = {"code": "GO", "label": "✅ GO — ..."}
    out = ub.degrade_for_coverage(v, 0.80)
    assert out["code"] == "OBSERVE" and "커버리지" in out["label"]
    ok = ub.degrade_for_coverage({"code": "GO", "label": "✅"}, 0.95)
    assert ok["code"] == "GO"                        # 커버리지 충족 시 유지
    ng = ub.degrade_for_coverage({"code": "NO-GO", "label": "➖"}, 0.80)
    assert ng["code"] == "NO-GO"                     # NO-GO 는 그대로(주의만 병기)


def test_walk_forward_runs_and_verdict():
    p = _panels()
    dates = p["ret"].index
    rng = np.random.default_rng(1)
    bench = pd.Series(rng.normal(0.0005, 0.01, len(dates)), index=dates)
    wf = ub.walk_forward(p, {"mom12": {"mom12": 1.0}, "lowvol": {"vol_inv": 1.0}},
                         bench, train_years=2)
    assert not wf.get("error"), wf
    assert wf["folds"] and 0.0 < wf["coverage"] <= 1.0
    from kr_policy_backtest import build_verdict
    v = ub.degrade_for_coverage(build_verdict(wf, n_trials=2), wf["coverage"])
    assert v["code"] in ("GO", "OBSERVE", "NO-GO")


def test_recommendation_maps_to_us_axes():
    """kr current_recommendation 재사용 — US config 도 w_* 매핑 산출."""
    from kr_policy_backtest import current_recommendation
    dates = pd.bdate_range("2018-01-02", periods=252 * 6)
    rng = np.random.default_rng(4)
    bench = pd.Series(rng.normal(0.0003, 0.01, len(dates)), index=dates)
    cfg_daily = pd.DataFrame({"hi52": bench + 0.001})
    rec = current_recommendation(cfg_daily, bench, configs={"hi52": {"hi52": 1.0}})
    assert rec and set(rec["policy_weights"]) == {"w_mom12", "w_hi52", "w_lowvol", "w_mom"}
    assert rec["policy_weights"]["w_hi52"] > 0
