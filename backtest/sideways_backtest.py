#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/sideways_backtest.py — 횡보 조건부 전략 검증 (Phase 1B · ★GO/NO-GO 게이트).

질문: **횡보 감지 시 인컴/저레버리지로 틸트하면 상시 바벨 대비 ★목적함수(아웃퍼폼+MDD≤)가 개선되나?**

설계(정직):
- 무룩어헤드: 일별 목표비중을 shift(1) 적용(당일 시그널→익일 반영). regime_series 도 과거만.
- **비용 반영**: turnover × COST_BPS(5bps). (현 backtest 엔 비용 없음 — 평균회귀/틸트가 무료처럼 보이는 것 차단.)
- **인컴 슬리브**: QQQI 상장 2024 → 장기 프록시 **QYLD**(커버드콜, 2014~). QQQI 고유는 단구간 별도.
- **현금**: SHV(≈SGOV/현금, 장기) 프록시.
- baseline = 낙폭기반 바벨(QQQ/QLD/TQQQ/현금). treatment = 동일 + **횡보일엔 인컴/현금 틸트**(calm/choppy).
- 3대 지표: ①전기간 baseline vs treatment vs QQQ(★objective) ②횡보구간 한정 서브성과 ③오판비용(횡보라
  했으나 향후 20일 강추세였던 구간의 기회비용).

실행: uv run python backtest/sideways_backtest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COST_BPS = 5.0
# 낙폭(rolling 252d 고점 대비) → (QQQ, QLD, TQQQ, CASH) — backtest/backtest.py ALLOC 미러
def _phase_alloc(dd: float) -> tuple:
    if dd <= -0.30: return (0.20, 0.20, 0.60, 0.00)
    if dd <= -0.20: return (0.45, 0.35, 0.20, 0.00)
    if dd <= -0.15: return (0.52, 0.45, 0.00, 0.03)
    if dd <= -0.10: return (0.65, 0.28, 0.00, 0.07)
    if dd <= -0.05: return (1.00, 0.00, 0.00, 0.00)
    return (0.92, 0.00, 0.00, 0.08)            # neutral/near-high

# 횡보 틸트 → (QQQ, QLD, TQQQ, CASH, INCOME) — 레버리지 0·인컴/현금↑
_SIDE_ALLOC = {
    "sideways_calm":   (0.55, 0.0, 0.0, 0.25, 0.20),
    "sideways_choppy": (0.45, 0.0, 0.0, 0.40, 0.15),
}


def _fetch(tickers, start="2014-01-01"):
    import yfinance as yf
    import pandas as pd
    out = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(start=start, auto_adjust=True)["Close"].dropna()
            if getattr(h.index, "tz", None) is not None:
                h.index = h.index.tz_localize(None)
            if len(h):
                out[t] = h
        except Exception:
            pass
    return pd.DataFrame(out).dropna(how="all").ffill()


def _metrics(nav):
    from ml.adaptive import reward
    import numpy as np
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    mdd = reward.max_drawdown(list(nav.values))
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * (252 ** 0.5)) if sd > 0 else 0.0
    return {"total_ret": round(total * 100, 1), "mdd": round(mdd * 100, 1), "sharpe": round(sharpe, 2)}


def _simulate(prices, weights, cost_bps=COST_BPS):
    """prices: DataFrame(date×inst), weights: DataFrame(date×inst) 목표비중. nav 반환(shift(1)·비용)."""
    rets = prices.pct_change().fillna(0.0)
    w = weights.reindex(columns=prices.columns).fillna(0.0).shift(1).fillna(0.0)   # 익일 반영
    gross = (w * rets).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * (cost_bps / 10000.0)
    return (1.0 + net).cumprod(), net


def run(start="2014-01-01"):
    import numpy as np
    import pandas as pd
    from ml.regime_classifier import regime_series, rolling_drawdown
    from ml.adaptive import reward

    px = _fetch(["QQQ", "QLD", "TQQQ", "SHV", "QYLD"], start)
    if "QQQ" not in px or len(px) < 600:
        return {"error": "데이터 부족", "have": list(px.columns)}
    insts = ["QQQ", "QLD", "TQQQ", "SHV", "QYLD"]
    px = px[[c for c in insts if c in px.columns]].dropna()
    qqq = px["QQQ"]
    dd = rolling_drawdown(qqq)
    regime = regime_series(qqq)

    # baseline / treatment 목표비중 DataFrame
    base_w, treat_w = [], []
    for d in px.index:
        a = _phase_alloc(float(dd.loc[d]))                       # (QQQ,QLD,TQQQ,CASH)
        b = {"QQQ": a[0], "QLD": a[1], "TQQQ": a[2], "SHV": a[3], "QYLD": 0.0}
        base_w.append(b)
        reg = regime.loc[d]
        if reg in _SIDE_ALLOC:
            s = _SIDE_ALLOC[reg]
            t = {"QQQ": s[0], "QLD": s[1], "TQQQ": s[2], "SHV": s[3], "QYLD": s[4]}
        else:
            t = dict(b)
        treat_w.append(t)
    base_w = pd.DataFrame(base_w, index=px.index)
    treat_w = pd.DataFrame(treat_w, index=px.index)

    base_nav, _ = _simulate(px, base_w)
    treat_nav, _ = _simulate(px, treat_w)
    qqq_nav = qqq / qqq.iloc[0]

    bm, tm, qm = _metrics(base_nav), _metrics(treat_nav), _metrics(qqq_nav)
    # ★objective: treatment vs baseline (틸트의 한계효과) + vs QQQ
    obj_vs_base = reward.objective_score((tm["total_ret"] - bm["total_ret"]) / 100,
                                         tm["mdd"] / 100, bm["mdd"] / 100)
    side_days = int(regime.str.startswith("sideways").sum())

    # ②횡보구간 한정 서브성과(틸트가 발동했을 때 baseline 대비)
    side_mask = regime.str.startswith("sideways").shift(1).fillna(False)
    b_ret = base_nav.pct_change().fillna(0.0)
    t_ret = treat_nav.pct_change().fillna(0.0)
    sub_base = float((1 + b_ret[side_mask]).prod() - 1) * 100
    sub_treat = float((1 + t_ret[side_mask]).prod() - 1) * 100

    # ③오판비용: 횡보라 했으나 향후 20일 |QQQ수익|>8%(강추세)였던 날의 틸트 손실(treat-base 일수익 합)
    fwd20 = qqq.shift(-20) / qqq - 1.0
    false_pos = regime.str.startswith("sideways") & (fwd20.abs() > 0.08)
    fp_mask = false_pos.shift(1).fillna(False)
    fp_cost = float((t_ret[fp_mask] - b_ret[fp_mask]).sum()) * 100
    fp_days = int(false_pos.sum())

    return {
        "period": f"{px.index[0].date()}~{px.index[-1].date()} ({len(px)}d)",
        "sideways_days": side_days, "sideways_pct": round(100 * side_days / len(px), 1),
        "baseline": bm, "treatment": tm, "qqq_buyhold": qm,
        "objective_treat_vs_base": (None if obj_vs_base is None else round(obj_vs_base, 4)),
        "sideways_subperiod": {"baseline_ret": round(sub_base, 1), "treatment_ret": round(sub_treat, 1)},
        "false_positive": {"days": fp_days, "tilt_cost_pct": round(fp_cost, 2)},
        "verdict": _verdict(bm, tm, qm, obj_vs_base, sub_base, sub_treat),
    }


def _verdict(bm, tm, qm, obj, sub_base, sub_treat) -> str:
    ret_better = tm["total_ret"] >= bm["total_ret"] - 1.0   # 수익 비슷+ 이상
    mdd_better = tm["mdd"] <= bm["mdd"]
    side_help = sub_treat >= sub_base
    if (tm["total_ret"] > bm["total_ret"] and mdd_better) or (mdd_better and ret_better and side_help):
        return "GO ✅ — 틸트가 수익≥ + MDD↓ (★목적함수 충족 방향)"
    if mdd_better and side_help:
        return "조건부 — MDD↓·횡보구간 우위지만 전기간 수익 트레이드오프 (shadow 권장)"
    return "NO-GO ❌ — 틸트가 baseline 대비 개선 없음 (감지·리포트만, 라이브 배분 미반영)"


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
