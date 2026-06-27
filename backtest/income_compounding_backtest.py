#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/income_compounding_backtest.py — Tier 5 인컴 복리 재투자 ★GO/NO-GO 게이트.

질문 (분리):
- Q1 재투자 vs 현금비축 → 양 드리프트면 재투자 우월(산술·게이트 불요). 격차만 수치화(규율).
- Q2 ★커버드콜 인컴 엔진(QQQI; 장기프록시 QYLD)이 총수익 보유(QQQ) 대비 복리에 유리한가? → 게이트.

설계(정직):
- QYLD total(분배 재투자 반영) vs QQQ total. 세금: 분배 연 DIV_TAX(15.4%) 차감 vs QQQ 양도세 이연(never-sell=0).
- validate_strategy(초과PSR/DSR vs QQQ) 세전·세후 + ★objective(아웃퍼폼 최우선) + pbo.
- QYLD 는 QQQI 보다 상방캡 큼 → 드래그 **과대평가(보수적)**. 강세장 편중·ROC 전액과세 단순화 명시.
판정·표시 전용·배분 불변·자동집행 0. 사용자=젊음·장기·인컴 불필요 → 복리 우선.
실행: uv run python backtest/income_compounding_backtest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIV_TAX = float(os.getenv("TIER5_DIV_TAX", "0.154"))
PSR_GATE = float(os.getenv("TIER5_PSR_GATE", "0.95"))
DSR_GATE = float(os.getenv("TIER5_DSR_GATE", "0.95"))
PBO_MAX = float(os.getenv("TIER5_PBO_MAX", "0.50"))
INCOME_PROXY = "QYLD"
MARKET = "QQQ"
CASH = "SHV"
MIN_DAYS = 1000
TRADING_DAYS = 252


# ── 순수 코어 (테스트 폐형해) ────────────────────────────────────────────

def reinvest_nav(total_ret):
    """분배 재투자 NAV = ∏(1+total)."""
    import numpy as np
    return np.cumprod(1.0 + np.asarray(total_ret, dtype=float))


def cash_hoard_nav(price_ret, div_yield, cash_ret):
    """인컴 미재투자·현금(cash_ret) 비축. 자본=price 슬리브 + 누적현금. NAV(start≈1)."""
    import numpy as np
    p = np.asarray(price_ret, dtype=float)
    d = np.asarray(div_yield, dtype=float)
    c = np.asarray(cash_ret, dtype=float)
    equity, cash, out = 1.0, 0.0, []
    for i in range(len(p)):
        cash *= (1.0 + c[i])
        cash += equity * d[i]                 # 분배는 자본가치 기준 → 현금으로
        equity *= (1.0 + p[i])
        out.append(equity + cash)
    return np.array(out)


def after_tax_total_ret(price_ret, div_yield, tax: float):
    """분배에 세금 차감 후 재투자한 총수익 = (1+price)(1+div(1−tax))−1. tax=0→total 항등, div=0→price."""
    import numpy as np
    p = np.asarray(price_ret, dtype=float)
    d = np.asarray(div_yield, dtype=float)
    return (1.0 + p) * (1.0 + d * (1.0 - tax)) - 1.0


def decide_verdict(d: dict) -> dict:
    """게이트 판정. d: {after_tax_psr, after_tax_dsr, obj, pbo, reinvest_vs_hoard_gap, after_tax_gap}."""
    engine_go = (d.get("pbo") is not None and d["pbo"] <= PBO_MAX
                 and d.get("obj") is not None and d["obj"] > 0
                 and (d.get("after_tax_dsr") or 0) >= DSR_GATE
                 and (d.get("after_tax_psr") or 0) >= PSR_GATE)
    gap = d.get("reinvest_vs_hoard_gap", 0.0)
    if engine_go:
        return {"verdict": "GO",
                "note": ("커버드콜 인컴이 세후에도 총수익(QQQ) 대비 복리 우위 — 희귀(장기 횡보 등). "
                         f"단 재투자>현금비축(+{gap:.0f}%) 규율 유지·shadow 권장")}
    return {"verdict": "NO-GO",
            "note": (f"커버드콜 인컴 엔진(QQQI)은 총수익(QQQ) 대비 복리 열위(세후 CAGR 격차 "
                     f"{d.get('after_tax_gap', 0):+.1f}%p) — 상방캡 + 배당과세. 방어/심리 기능이지 "
                     f"복리 엣지 아님. 단 보유 시 배당 **재투자**가 현금비축보다 +{gap:.0f}% 우월(비축 금물)")}


# ── 데이터·지표 ──────────────────────────────────────────────────────────

def _fetch(symbol: str, adjust: bool = True):
    import yfinance as yf
    h = yf.Ticker(symbol).history(period="max", auto_adjust=adjust)["Close"].dropna()
    if getattr(h.index, "tz", None) is not None:
        h.index = h.index.tz_localize(None)
    return h


def _metrics(nav):
    from ml.adaptive import reward
    import pandas as pd
    s = pd.Series(nav)
    rets = s.pct_change().dropna()
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    cagr = float((s.iloc[-1] / s.iloc[0]) ** (TRADING_DAYS / len(rets)) - 1.0) if len(rets) else 0.0
    mdd = reward.max_drawdown(list(s.values))
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * (TRADING_DAYS ** 0.5)) if sd > 0 else 0.0
    return {"total_ret": round(total * 100, 1), "cagr": round(cagr * 100, 1),
            "mdd": round(mdd * 100, 1), "sharpe": round(sharpe, 2)}


def run():
    import numpy as np
    import pandas as pd
    from ml.adaptive import reward
    from ml.validation import validate_strategy, pbo_cscv

    qy_t = _fetch(INCOME_PROXY, adjust=True)
    qy_p = _fetch(INCOME_PROXY, adjust=False)
    qqq = _fetch(MARKET, adjust=True)
    shv = _fetch(CASH, adjust=True)
    for s in (qy_t, qy_p, qqq, shv):
        if s is None or len(s) < MIN_DAYS:
            return {"error": "데이터 부족"}
    idx = qy_t.index.intersection(qy_p.index).intersection(qqq.index).intersection(shv.index)
    if len(idx) < MIN_DAYS:
        return {"error": "공통 이력 부족", "n": len(idx)}

    qy_total = qy_t.reindex(idx).pct_change().dropna()
    qy_price = qy_p.reindex(idx).pct_change().dropna()
    qqq_ret = qqq.reindex(idx).pct_change().dropna()
    cash_ret = shv.reindex(idx).pct_change().dropna()
    div_yield = (1.0 + qy_total) / (1.0 + qy_price) - 1.0          # 분배수익률 추출
    aftertax = pd.Series(after_tax_total_ret(qy_price.values, div_yield.values, DIV_TAX), index=qy_total.index)

    rein = _metrics(reinvest_nav(qy_total.values))
    qqqm = _metrics(reinvest_nav(qqq_ret.values))
    hoard = _metrics(cash_hoard_nav(qy_price.values, div_yield.values, cash_ret.values))
    atax = _metrics(reinvest_nav(aftertax.values))

    # 다중검정: reinvest·aftertax·hoard 초과 vs QQQ
    variants = {"reinvest": qy_total.values, "aftertax": aftertax.values,
                "hoard_excess_proxy": qy_price.values}
    excess_pp = []
    for v in variants.values():
        ex = pd.Series(v - qqq_ret.values)
        excess_pp.append(float(ex.mean() / ex.std()) if ex.std() > 0 else 0.0)
    sr_var = float(pd.Series(excess_pp).var(ddof=1))
    n_trials = len(variants)

    val_pre = validate_strategy(qy_total.values, benchmark_returns=qqq_ret.values, n_trials=n_trials, sr_variance=sr_var)
    val_post = validate_strategy(aftertax.values, benchmark_returns=qqq_ret.values, n_trials=n_trials, sr_variance=sr_var)
    obj = reward.objective_score((rein["total_ret"] - qqqm["total_ret"]) / 100, rein["mdd"] / 100, qqqm["mdd"] / 100)

    M = np.column_stack([qy_total.values - qqq_ret.values,
                         aftertax.values - qqq_ret.values,
                         qy_price.values - qqq_ret.values])
    pbo_res = pbo_cscv(M, n_splits=10)
    pbo = None if pbo_res is None else round(pbo_res["pbo"], 3)

    reinvest_nav_end = float(reinvest_nav(qy_total.values)[-1])
    hoard_nav_end = float(cash_hoard_nav(qy_price.values, div_yield.values, cash_ret.values)[-1])
    d = {
        "after_tax_psr": val_post.get("psr_excess"), "after_tax_dsr": val_post.get("dsr"),
        "obj": (None if obj is None else round(obj, 4)), "pbo": pbo,
        "reinvest_vs_hoard_gap": round((reinvest_nav_end / hoard_nav_end - 1.0) * 100, 1),
        "after_tax_gap": round(atax["cagr"] - qqqm["cagr"], 1),
    }
    return {
        "period": f"{idx[0].date()}~{idx[-1].date()} ({len(idx)}d)",
        "qqq_total_return": qqqm, "qyld_reinvest": rein, "qyld_after_tax": atax, "qyld_cash_hoard": hoard,
        "excess_psr_pretax": val_pre.get("psr_excess"), "excess_psr_aftertax": val_post.get("psr_excess"),
        "pbo": pbo, "reinvest_vs_hoard_gap_pct": d["reinvest_vs_hoard_gap"],
        "pretax_cagr_gap": round(rein["cagr"] - qqqm["cagr"], 1), "aftertax_cagr_gap": d["after_tax_gap"],
        **decide_verdict(d),
    }


def run_all():
    return run()


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), ensure_ascii=False, indent=2, default=str))
