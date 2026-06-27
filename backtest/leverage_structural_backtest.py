#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/leverage_structural_backtest.py — Tier 3 구조적 레버리지 ★GO/NO-GO 게이트.

질문: 분산책에 **구조적 base 레버리지(1.2~1.5x) + 바벨 폭락 디리스크**를 적용하면,
      **낙폭예산 50% 안**에서 장기 복리(★objective)가 개선되나? (LETF 감쇠·파이낸싱 비용 반영)

설계(정직):
- 비용 = **일일리셋 LETF 합성** — 감쇠는 합성서 자연발생(페널티 주입 금지):
  synth(r,L) = L·r − (L−1)·(fin_d + expense_d).  L=1 → r 항등.
  fin = (RF_t + SPREAD)/252 (RF=^IRX 일별·폴백 3%), expense 0.9%/252 × (L−1).
- 전략 = 바벨 버킷(backtest.py ALLOC) — **base_lev 는 near-high 버킷에만** 적용, 깊은 낙폭 버킷은
  동일(=레버리지 고유 MDD 기여만 격리). 그리드 {1.0,1.2,1.3,1.4,1.5}.
- 게이트 = Tier2(DSR·psr_excess·PBO) + 낙폭예산 + ★objective. **SPY(1993+)·QQQ(1999+) 양쪽** 통과해야 GO.
- 무룩어헤드: 낙폭 rolling, 비중 당일 시그널→당일(버킷은 과거 가격 기반 dd). 턴오버 비용 5bps.

실행: uv run python backtest/leverage_structural_backtest.py
주의: 판정·검증용. reco 는 GO 시에만, 표시(advisory)까지 — 자동집행 0(실계좌 수동).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RF_FALLBACK = float(os.getenv("TIER3_RF_FALLBACK", "0.03"))
SPREAD = float(os.getenv("TIER3_LETF_SPREAD", "0.005"))
EXPENSE = float(os.getenv("TIER3_LETF_EXPENSE", "0.009"))
BUDGET = float(os.getenv("TIER3_BUDGET", "0.50"))
COST_BPS = 5.0
GRID = [1.0, 1.2, 1.3, 1.4, 1.5]
TRADING_DAYS = 252


# ── 순수 코어 (테스트 폐형해) ────────────────────────────────────────────

def synth_letf_daily(r, L: float, fin_daily, expense: float = EXPENSE):
    """일일리셋 합성 레버리지 수익. L=1 → r 항등. 감쇠는 합성서 자연발생(페널티 주입 X)."""
    return L * r - (L - 1.0) * (fin_daily + expense / TRADING_DAYS)


def _lev_weights(eff: float) -> dict:
    """목표 유효레버리지 eff → instrument 비중 (under 1x·QLD 2x·cash 0)."""
    if eff <= 0.0:
        return {"cash": 1.0}
    if eff <= 1.0:
        return {"under": eff, "cash": 1.0 - eff}
    return {"under": 2.0 - eff, "QLD": eff - 1.0}             # eff∈(1,2]


def _near_high_weights(base_lev: float) -> dict:
    """near-high 비중 (= _lev_weights(base_lev))."""
    return _lev_weights(base_lev)


def _effective_leverage(dd: float, base_lev: float) -> float:
    """낙폭 → 목표 유효레버리지. **폭락엔 디리스크(감액) — 증액 아님.**

    (★중요: '폭락 증액'(레버리지로 물타기)은 닷컴·2008 같은 다년 크래시에서 파산 — 백테스트 실증.
     레버리지를 안전하게 드는 정공법은 낙폭 깊어질수록 *감액*하는 서킷브레이커.)
    """
    if dd <= -0.30:
        return 0.2
    if dd <= -0.20:
        return 0.4
    if dd <= -0.15:
        return min(base_lev, 0.6)
    if dd <= -0.10:
        return min(base_lev, 0.85)
    if dd <= -0.05:
        return min(base_lev, 1.0)
    return base_lev                                           # near-high: 구조적 base 레버리지


def _bucket_weights(dd: float, base_lev: float) -> dict:
    """낙폭 → 비중. base_lev 는 near-high 에만; 폭락엔 양쪽 동일 디리스크(레버리지 고유 MDD 격리)."""
    return _lev_weights(_effective_leverage(dd, base_lev))


# ── 데이터 ───────────────────────────────────────────────────────────────

def _fetch(symbol: str):
    import yfinance as yf
    h = yf.Ticker(symbol).history(period="max", auto_adjust=True)["Close"].dropna()
    if getattr(h.index, "tz", None) is not None:
        h.index = h.index.tz_localize(None)
    return h


def _rf_series(index):
    """일별 무위험(cash 수익)·파이낸싱(레버리지 비용) 시리즈. ^IRX 폴백 RF_FALLBACK."""
    import pandas as pd
    import yfinance as yf
    try:
        irx = yf.Ticker("^IRX").history(period="max", auto_adjust=False)["Close"].dropna()
        if getattr(irx.index, "tz", None) is not None:
            irx.index = irx.index.tz_localize(None)
        irx = (irx / 100.0).reindex(index).ffill().bfill().clip(0.0, 0.20)
    except Exception:
        irx = pd.Series(RF_FALLBACK, index=index)
    irx = irx.fillna(RF_FALLBACK)
    return irx / TRADING_DAYS, (irx + SPREAD) / TRADING_DAYS    # (cash_daily, fin_daily)


def _metrics(nav):
    from ml.adaptive import reward
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    mdd = reward.max_drawdown(list(nav.values))
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * (TRADING_DAYS ** 0.5)) if sd > 0 else 0.0
    return {"total_ret": round(total * 100, 1), "mdd": round(mdd * 100, 1), "sharpe": round(sharpe, 2)}


def build_nav(r, dd, instruments, base_lev: float, cost_bps: float = COST_BPS):
    """바벨 버킷(base_lev near-high) 일별 NAV + net 수익. instruments: DataFrame(under/QLD/TQQQ/cash)."""
    import pandas as pd
    w = pd.DataFrame([_bucket_weights(float(x), base_lev) for x in dd.values],
                     index=dd.index).reindex(columns=instruments.columns).fillna(0.0)
    w = w.shift(1).fillna(0.0)                    # ★무룩어헤드: 전일 낙폭 시그널 → 당일 비중
    gross = (w * instruments).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * (cost_bps / 1e4)
    return (1.0 + net).cumprod(), net


# ── 게이트 ───────────────────────────────────────────────────────────────

def run(proxy: str = "SPY"):
    import numpy as np
    import pandas as pd
    from ml.regime_classifier import rolling_drawdown
    from ml.adaptive import reward
    from ml.validation import validate_strategy, pbo_cscv

    px = _fetch(proxy)
    if px is None or len(px) < 1000:
        return {"error": "데이터 부족", "proxy": proxy}
    r = px.pct_change().dropna()
    dd = rolling_drawdown(px).reindex(r.index).fillna(0.0)
    cash_d, fin_d = _rf_series(r.index)
    instruments = pd.DataFrame({
        "under": r,
        "QLD": synth_letf_daily(r, 2.0, fin_d),
        "TQQQ": synth_letf_daily(r, 3.0, fin_d),
        "cash": cash_d,
    })

    navs, nets, metr = {}, {}, {}
    for L in GRID:
        nav, net = build_nav(r, dd, instruments, L)
        navs[L], nets[L], metr[L] = nav, net, _metrics(nav)

    base_net = nets[1.0]
    base_m = metr[1.0]
    # 다중검정: 그리드 5개 시도 → per-period Sharpe 분산
    sweep_pp = [float(nets[L].mean() / nets[L].std()) if nets[L].std() > 0 else 0.0 for L in GRID]
    sr_var = float(pd.Series(sweep_pp).var(ddof=1))

    grid_out = []
    for L in GRID:
        m = metr[L]
        budget_ok = m["mdd"] <= BUDGET * 100
        obj = reward.objective_score((m["total_ret"] - base_m["total_ret"]) / 100,
                                     m["mdd"] / 100, base_m["mdd"] / 100)
        val = (validate_strategy(nets[L].values, benchmark_returns=base_net.values,
                                 n_trials=len(GRID), sr_variance=sr_var) if L != 1.0 else None)
        grid_out.append({
            "L": L, "total_ret": m["total_ret"], "mdd": m["mdd"], "sharpe": m["sharpe"],
            "budget_ok": budget_ok,
            "obj": (None if obj is None else round(obj, 4)),
            "psr_excess": (val or {}).get("psr_excess"),
            "dsr": (val or {}).get("dsr"),
        })

    M = np.column_stack([nets[L].values for L in GRID])
    pbo_res = pbo_cscv(M, n_splits=10)
    return {
        "proxy": proxy,
        "period": f"{px.index[0].date()}~{px.index[-1].date()} ({len(r)}d)",
        "baseline_1.0x": base_m,
        "grid": grid_out,
        "pbo": (None if pbo_res is None else round(pbo_res["pbo"], 3)),
        "best_L": _best_L(grid_out, pbo_res),
    }


def _best_L(grid_out, pbo_res):
    """예산·objective·DSR·psr_excess 모두 충족 최대 L (PBO 양호 시). 없으면 None."""
    pbo_ok = (pbo_res is not None and pbo_res["pbo"] <= 0.5)
    if not pbo_ok:
        return None
    passing = [g["L"] for g in grid_out if g["L"] != 1.0 and g["budget_ok"]
               and g.get("mdd", 100) <= (BUDGET * 100 - 3.0)        # 예산 3%p 안전마진 (오버레이·갭리스크)
               and g["obj"] is not None and g["obj"] > 0
               and (g["dsr"] or 0) >= 0.95 and (g["psr_excess"] or 0) >= 0.95]
    return max(passing) if passing else None


def decide_verdict(results: dict) -> dict:
    """SPY·QQQ 결과 → 종합 판정. GO 는 양 프록시 동일 L* 통과 시."""
    spy, qqq = results.get("SPY", {}), results.get("QQQ", {})
    ls, lq = spy.get("best_L"), qqq.get("best_L")
    # 예산 초과 여부(어느 프록시든 base조차 초과면 강한 NO-GO 신호)
    budget_breach = any(
        all(not g["budget_ok"] for g in res.get("grid", []) if g["L"] > 1.0)
        for res in (spy, qqq) if res.get("grid")
    )
    if ls and lq:
        common = min(ls, lq)                       # 양 프록시 모두 통과하는 보수적 L
        return {"verdict": "GO", "reco_L": common,
                "note": f"SPY L*={ls}·QQQ L*={lq} → 보수적 reco {common}x (낙폭예산 cap 추가적용)"}
    # 한쪽만 통과 or 예산 경계 → 조건부
    if ls or lq:
        return {"verdict": "조건부", "reco_L": None,
                "note": f"프록시 불일치(SPY {ls}·QQQ {lq}) — shadow 권장, 라이브 미반영"}
    return {"verdict": "NO-GO", "reco_L": None,
            "note": ("구조적 레버리지가 낙폭예산 50%·DSR/PBO 게이트 미충족"
                     + (" (예산 초과 — 디리스크가 레버리지 MDD 못 되삼)" if budget_breach else "")
                     + " → crash-only 유지, 라이브 미반영")}


def run_all():
    results = {p: run(p) for p in ("SPY", "QQQ")}
    return {"results": results, "verdict": decide_verdict(results)}


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), ensure_ascii=False, indent=2, default=str))
