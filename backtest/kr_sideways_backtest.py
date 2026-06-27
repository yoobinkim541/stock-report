#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/kr_sideways_backtest.py — KR(KOSPI) 횡보→현금 디리스크 정밀 검증 (★GO/NO-GO 게이트).

질문: KOSPI 횡보 감지 시 현금화하면 buy&hold 대비 ★목적함수(아웃퍼폼 + MDD≤지수)가 개선되나?
      **KR 거래비용(매도 증권거래세 + 수수료)을 현실 반영**해도 유효한가?

설계(정직):
- 무룩어헤드: regime_series(^KS11)는 과거만, 비중은 shift(1)(당일 시그널 → 익일 반영).
- **KR 비대칭 비용**: 청산(매도) ~20bps(증권거래세 ≈0.18% + 수수료), 편입(매수) ~2bps(수수료).
  현금-디리스크는 진입=청산 / 이탈=편입이라 토글마다 매도세가 붙는다 → 잦은 전환이 MDD 이득을
  깎는지 검증한다. (US 5bps 대칭과 달리 KR은 매도세가 핵심 — 무비용 crude 체크가 놓친 부분.)
- 현금 일수익 = 0 (KR 무위험 캐리 무시 — 전략에 **보수적**. 그럼에도 이기면 robust.)
- baseline = KOSPI buy&hold(=벤치마크), treatment = 횡보일 현금(나머지 풀투자).
- 3대 지표: ①전기간 ★objective(treatment vs baseline) ②횡보구간 한정 서브성과(맞췄을 때 이득)
  ③오판비용(횡보라 했으나 향후 20일 강세였던 구간의 기회비용).
- **비용 민감도**: 매도세 0 / 20 / 30bps 로 robustness — 0에서만 이기면 함정(crude 의 오류).

주의: 지수 레벨 신호 검증이다. KR 모의는 5종목 선택이라, 통과 시 "KOSPI 횡보 → 모의 익스포저
      축소(현금 틸트)" **오버레이**로 반영한다 (개별 종목 평균회귀 아님 — 평균회귀는 현금보다 열위).

실행: uv run python backtest/kr_sideways_backtest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KR_SELL_BPS = 20.0   # 청산: 증권거래세(≈0.18%) + 수수료 — 보수적 반올림
KR_BUY_BPS = 2.0     # 편입: 수수료만


def _fetch(symbol="^KS11", start="2010-01-01"):
    import yfinance as yf
    h = yf.Ticker(symbol).history(start=start, auto_adjust=True)["Close"].dropna()
    if getattr(h.index, "tz", None) is not None:
        h.index = h.index.tz_localize(None)
    return h


def _metrics(nav):
    from ml.adaptive import reward
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    mdd = reward.max_drawdown(list(nav.values))
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * (252 ** 0.5)) if sd > 0 else 0.0
    return {"total_ret": round(total * 100, 1), "mdd": round(mdd * 100, 1), "sharpe": round(sharpe, 2)}


def _simulate(close, weight, sell_bps=KR_SELL_BPS, buy_bps=KR_BUY_BPS):
    """단일 위험자산(KOSPI)+현금. weight∈{0,1} 시계열. shift(1) + KR 비대칭 비용. nav 반환."""
    ret = close.pct_change().fillna(0.0)
    w = weight.shift(1).fillna(0.0)                    # 당일 시그널 → 익일 반영
    dw = w.diff().fillna(w)                            # 첫날은 0→w 전환
    buys = dw.clip(lower=0.0)
    sells = (-dw).clip(lower=0.0)
    cost = buys * (buy_bps / 1e4) + sells * (sell_bps / 1e4)
    net = w * ret - cost
    return (1.0 + net).cumprod()


def run(start="2010-01-01", symbol="^KS11"):
    import pandas as pd
    from ml.regime_classifier import regime_series

    close = _fetch(symbol, start)
    if close is None or len(close) < 600:
        return {"error": "데이터 부족", "have": (0 if close is None else len(close))}

    regime = regime_series(close)
    sideways = regime.str.startswith("sideways")

    base_w = pd.Series(1.0, index=close.index)                 # buy&hold
    treat_w = pd.Series(1.0, index=close.index)
    treat_w[sideways] = 0.0                                     # 횡보일 현금

    base_nav = _simulate(close, base_w)
    bm = _metrics(base_nav)

    # 비용 민감도 — 매도세 0/20/30bps
    sens = {}
    for sb in (0.0, KR_SELL_BPS, 30.0):
        tnav = _simulate(close, treat_w, sell_bps=sb)
        sens[f"sell_{int(sb)}bps"] = _metrics(tnav)

    treat_nav = _simulate(close, treat_w)                      # 기준 비용(20bps)
    tm = _metrics(treat_nav)

    from ml.adaptive import reward
    obj = reward.objective_score((tm["total_ret"] - bm["total_ret"]) / 100,
                                 tm["mdd"] / 100, bm["mdd"] / 100)

    # ②횡보구간 한정: 디리스크가 발동했을 때 baseline 대비
    side_mask = sideways.shift(1).fillna(False)
    b_ret = base_nav.pct_change().fillna(0.0)
    t_ret = treat_nav.pct_change().fillna(0.0)
    sub_base = float((1 + b_ret[side_mask]).prod() - 1) * 100
    sub_treat = float((1 + t_ret[side_mask]).prod() - 1) * 100

    # ③오판비용: 횡보라 했으나 향후 20일 KOSPI +8% 이상 강세였던 날의 디리스크 손실
    fwd20 = close.shift(-20) / close - 1.0
    false_pos = sideways & (fwd20 > 0.08)
    fp_mask = false_pos.shift(1).fillna(False)
    fp_cost = float((t_ret[fp_mask] - b_ret[fp_mask]).sum()) * 100

    side_days = int(sideways.sum())
    from ml.validation import validate_strategy
    _sweep_pp = [sens[k]["sharpe"] / (252 ** 0.5) for k in sens]   # 비용스윕 = 3 trials
    _validation = validate_strategy(
        treat_nav.pct_change().dropna(),
        benchmark_returns=base_nav.pct_change().dropna(),
        n_trials=3, sr_variance=float(pd.Series(_sweep_pp).var(ddof=1)),
    )
    return {
        "symbol": symbol,
        "period": f"{close.index[0].date()}~{close.index[-1].date()} ({len(close)}d)",
        "sideways_days": side_days, "sideways_pct": round(100 * side_days / len(close), 1),
        "baseline_buyhold": bm,
        "treatment_cash_in_sideways": tm,
        "cost_sensitivity": sens,
        "objective_treat_vs_base": (None if obj is None else round(obj, 4)),
        "validation": _validation,
        "sideways_subperiod": {"baseline_ret": round(sub_base, 1), "treatment_ret": round(sub_treat, 1)},
        "false_positive": {"days": int(false_pos.sum()), "tilt_cost_pct": round(fp_cost, 2)},
        "verdict": _verdict(bm, tm, sens, sub_base, sub_treat),
    }


def _verdict(bm, tm, sens, sub_base, sub_treat) -> str:
    mdd_better = tm["mdd"] < bm["mdd"]
    ret_ok = tm["total_ret"] >= bm["total_ret"] - 2.0          # 디리스크는 수익 약간 양보 허용
    side_help = sub_treat >= sub_base
    # 비용 robustness: 20bps·30bps 둘 다에서도 MDD 개선 유지?
    robust = all(s["mdd"] < bm["mdd"] for k, s in sens.items() if k != "sell_0bps")
    if mdd_better and ret_ok and side_help and robust:
        return ("GO ✅ — 현금-디리스크가 MDD↓ + 수익≥(−2%p) + 횡보구간 우위 + 비용(20·30bps) robust "
                "(★목적함수 충족) → 모의 익스포저 오버레이 반영 후보")
    if mdd_better and robust:
        return "조건부 — MDD↓·비용 robust 이나 수익/횡보구간 트레이드오프 (shadow 권장)"
    if mdd_better and not robust:
        return "NO-GO ❌ — 무비용엔 MDD↓이나 KR 매도세 반영 시 이득 소멸 (잦은 토글 비용, crude 함정)"
    return "NO-GO ❌ — 현금-디리스크가 baseline 대비 개선 없음 (감지·리포트만)"


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
