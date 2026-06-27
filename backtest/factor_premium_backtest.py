#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/factor_premium_backtest.py — Tier 4 팩터 프리미엄 틸트 ★GO/NO-GO 게이트.

질문: 롱온리 팩터 ETF(밸류·소형·퀄리티·모멘텀·최소변동) 틸트가 시장(SPY) 대비 초과수익을
      **비용·DSR 다중검정·약세구간**을 견디며 내나? (롱숏 FF 아님 — 투자가능 ETF 로 정직.)

설계(정직):
- 팩터별 초과수익(factor−SPY) → validate_strategy(psr_excess·DSR; n_trials=팩터+블렌드 deflate) +
  pbo_cscv(팩터 행렬) + ★objective(MDD>지수×1.3 실격) + **약세슬라이스**(시장 낙폭>10% 한정 초과Sharpe —
  강세장서만 되는 팩터 적발).
- 짧은 ETF 이력(2013+) 강세장 편중은 DSR √(T−1) 자기페널티 + 약세슬라이스로 보정(별도 페널티 주입 X).

★개념 분리: `ml/risk_model.factor_betas`(QQQ/TLT *노출 측정*·표시)와 **별개**. 이건 팩터 *프리미엄 틸트*(처방).
판정·표시·shadow 전용 — 배분 불변·자동집행 0(실계좌 수동). 모멘텀류는 SPMO 로 이미 일부 보유.
실행: uv run python backtest/factor_premium_backtest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FACTOR_ETFS = {"momentum": "MTUM", "quality": "QUAL", "value": "VLUE",
               "size": "IWM", "minvol": "USMV"}
MARKET = "SPY"
PSR_GATE = float(os.getenv("TIER4_PSR_GATE", "0.95"))
DSR_GATE = float(os.getenv("TIER4_DSR_GATE", "0.95"))
PBO_MAX = float(os.getenv("TIER4_PBO_MAX", "0.50"))
MIN_DAYS = 1000
TRADING_DAYS = 252


# ── 순수 코어 (테스트 폐형해) ────────────────────────────────────────────

def excess_series(factor_ret, market_ret):
    """팩터−시장 초과수익 (길이 교집합)."""
    import numpy as np
    f = np.asarray(factor_ret, dtype=float)
    m = np.asarray(market_ret, dtype=float)
    n = min(len(f), len(m))
    return f[-n:] - m[-n:]


def factor_sleeve_net(factor_ret, cost_bps: float = 0.0):
    """롱온리 ETF 슬리브 수익 — 매수후보유라 회전≈0(ETF expense 는 가격에 이미 반영). 페널티 주입 X."""
    import numpy as np
    return np.asarray(factor_ret, dtype=float) - cost_bps / 1e4 * 0.0


def blend_returns(factor_rets):
    """팩터 동일가중 블렌드 (교집합 일자)."""
    import pandas as pd
    df = pd.concat(list(factor_rets), axis=1).dropna()
    return df.mean(axis=1)


# ── 데이터·지표 ──────────────────────────────────────────────────────────

def _fetch(symbol: str):
    import yfinance as yf
    h = yf.Ticker(symbol).history(period="max", auto_adjust=True)["Close"].dropna()
    if getattr(h.index, "tz", None) is not None:
        h.index = h.index.tz_localize(None)
    return h


def _metrics(nav):
    from ml.adaptive import reward
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    mdd = reward.max_drawdown(list(nav.values))
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * (TRADING_DAYS ** 0.5)) if sd > 0 else 0.0
    return {"total_ret": round(total * 100, 1), "mdd": round(mdd * 100, 1), "sharpe": round(sharpe, 2)}


# ── 게이트 판정 ──────────────────────────────────────────────────────────

def _factor_passes(f: dict, pbo) -> bool:
    return (pbo is not None and pbo <= PBO_MAX
            and f.get("obj") is not None and f["obj"] > 0
            and (f.get("dsr") or 0) >= DSR_GATE
            and (f.get("psr_excess") or 0) >= PSR_GATE)


def decide_verdict(factors_out: list, pbo) -> dict:
    """팩터별 게이트 → 종합 판정. GO=≥1 통과, 조건부=통과하나 약세슬라이스 음수, NO-GO=통과 0."""
    go = [f["factor"] for f in factors_out if _factor_passes(f, pbo)]
    nogo = [f["factor"] for f in factors_out if f["factor"] not in go]
    conditional = [f["factor"] for f in factors_out
                   if _factor_passes(f, pbo) and (f.get("bear_excess_sharpe") or 0) < 0]
    if go:
        note = f"보상 팩터: {', '.join(go)} (모멘텀류는 SPMO 기보유 — 중복틸트 주의·수동집행)"
        if conditional:
            note += f" / ⚠️조건부(강세장 한정·약세서 음수): {', '.join(conditional)}"
        return {"verdict": "GO", "go_factors": go, "nogo_factors": nogo, "note": note}
    return {"verdict": "NO-GO", "go_factors": [], "nogo_factors": nogo,
            "note": ("전 팩터 SPY 대비 초과수익이 비용·DSR 다중검정 게이트 미충족(프리미엄 쇠퇴) "
                     "→ 틸트 미반영. 모멘텀 노출은 SPMO 로 유지")}


def run():
    import numpy as np
    import pandas as pd
    from ml.regime_classifier import rolling_drawdown
    from ml.adaptive import reward
    from ml.validation import validate_strategy, pbo_cscv

    mkt_px = _fetch(MARKET)
    if mkt_px is None or len(mkt_px) < MIN_DAYS:
        return {"error": "시장 데이터 부족"}
    fac_px = {}
    for key, etf in FACTOR_ETFS.items():
        px = _fetch(etf)
        if px is not None and len(px) >= MIN_DAYS:
            fac_px[key] = px
    if not fac_px:
        return {"error": "팩터 데이터 부족"}

    idx = mkt_px.index
    for px in fac_px.values():
        idx = idx.intersection(px.index)
    if len(idx) < MIN_DAYS:
        return {"error": "공통 이력 부족", "n": len(idx)}

    mkt = mkt_px.reindex(idx)
    mkt_ret = mkt.pct_change().dropna()
    mkt_m = _metrics(mkt / mkt.iloc[0])
    dd = rolling_drawdown(mkt).reindex(mkt_ret.index).fillna(0.0)
    bear_mask = dd <= -0.10

    fac_ret = {k: fac_px[k].reindex(idx).pct_change().dropna() for k in fac_px}
    blend = blend_returns([fac_ret[k] for k in fac_ret])

    # 다중검정 sr_variance: 팩터+블렌드 per-period 초과Sharpe 분산
    excess_pp = []
    for r in list(fac_ret.values()) + [blend]:
        ex = pd.Series(excess_series(r.values, mkt_ret.values))
        excess_pp.append(float(ex.mean() / ex.std()) if ex.std() > 0 else 0.0)
    sr_var = float(pd.Series(excess_pp).var(ddof=1))
    n_trials = len(fac_ret) + 1

    factors_out, excess_matrix = [], []
    for k, r in fac_ret.items():
        m = _metrics((1 + r).cumprod())
        val = validate_strategy(r.values, benchmark_returns=mkt_ret.values,
                                n_trials=n_trials, sr_variance=sr_var)
        obj = reward.objective_score((m["total_ret"] - mkt_m["total_ret"]) / 100,
                                     m["mdd"] / 100, mkt_m["mdd"] / 100)
        ex = pd.Series(r.values - mkt_ret.values, index=mkt_ret.index)
        ex_bear = ex[bear_mask]
        bear_sr = (float(ex_bear.mean() / ex_bear.std() * (TRADING_DAYS ** 0.5))
                   if len(ex_bear) > 2 and ex_bear.std() > 0 else 0.0)
        factors_out.append({
            "factor": k, "etf": FACTOR_ETFS[k], "total_ret": m["total_ret"], "mdd": m["mdd"],
            "excess_sharpe": val.get("excess_sharpe"), "psr_excess": val.get("psr_excess"),
            "dsr": val.get("dsr"), "obj": (None if obj is None else round(obj, 4)),
            "bear_excess_sharpe": round(bear_sr, 2),
        })
        excess_matrix.append(r.values - mkt_ret.values)

    pbo_res = pbo_cscv(np.column_stack(excess_matrix), n_splits=10)
    pbo = None if pbo_res is None else round(pbo_res["pbo"], 3)
    return {
        "period": f"{idx[0].date()}~{idx[-1].date()} ({len(idx)}d)",
        "market": MARKET, "market_cagr_ret": mkt_m["total_ret"],
        "factors": factors_out, "pbo": pbo,
        **decide_verdict(factors_out, pbo),
    }


def run_all():
    return run()


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), ensure_ascii=False, indent=2, default=str))
