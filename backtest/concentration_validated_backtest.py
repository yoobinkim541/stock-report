#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/concentration_validated_backtest.py — Tier 6 검증된 집중 ★GO/NO-GO 게이트 (마지막).

질문: 선택 스킬 없이 집중(랜덤 K)하면 분산을 이기나? → **집중 = 엣지의 증폭기, 엣지원천 아님.**
      세션이 선택 엣지 전무 입증(KR랭커·팩터·횡보·평균회귀 NO-GO) → 무스킬 집중 = 보상없는
      idiosyncratic 위험(Sharpe↓·분산↑, 기대수익 동일). 예상 NO-GO.

설계(정직):
- 유니버스 = 9 섹터 SPDR(상폐 없음 → **생존편향 0**). 섹터<종목 분산 → 집중패널티 **보수적 하한**.
- 몬테카를로(seed 고정·재현): K∈{3,5} 랜덤부분집합 N_SAMPLES vs 분산 EW.
- **다중검정 rigor**: MC N draw = n_trials → 최고 부분집합조차 DSR deflate 시 붕괴(운) = "집중 승자는 노이즈".
판정·표시 전용·배분 불변·자동집행 0. 검증된 집중은 Tier3 구조레버리지(분산코어)뿐.
실행: uv run python backtest/concentration_validated_backtest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECTORS = ["XLK", "XLV", "XLF", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU"]
SEED = int(os.getenv("TIER6_SEED", "6"))
N_SAMPLES = int(os.getenv("TIER6_MC_SAMPLES", "500"))
KS = [3, 5]
PSR_GATE = float(os.getenv("TIER6_PSR_GATE", "0.95"))
DSR_GATE = float(os.getenv("TIER6_DSR_GATE", "0.95"))
PBO_MAX = float(os.getenv("TIER6_PBO_MAX", "0.50"))
MIN_DAYS = 1000
TRADING_DAYS = 252


# ── 순수 코어 (테스트 폐형해) ────────────────────────────────────────────

def equal_weight_returns(rets_df):
    """분산 EW 수익 = 열 평균."""
    return rets_df.mean(axis=1)


def random_subset_indices(n: int, k: int, n_samples: int, seed: int):
    """재현가능 랜덤 K-부분집합 인덱스(중복 제거). 같은 seed → 동일 결과."""
    import numpy as np
    rng = np.random.default_rng(seed)
    seen, res, tries = set(), [], 0
    while len(res) < n_samples and tries < n_samples * 30:
        idx = tuple(sorted(int(x) for x in rng.choice(n, k, replace=False)))
        tries += 1
        if idx not in seen:
            seen.add(idx)
            res.append(idx)
    return res


def subset_returns(rets_df, idx_tuple):
    """선택 K종목 동일가중 수익."""
    return rets_df.iloc[:, list(idx_tuple)].mean(axis=1)


def concentration_penalty(div_sharpe: float, conc_sharpes) -> dict:
    """집중 Sharpe 분포 vs 분산: 중앙Δ·분산 못이길 비율·분산폭."""
    import numpy as np
    a = np.asarray(conc_sharpes, dtype=float)
    return {"median_delta": round(float(np.median(a) - div_sharpe), 3),
            "frac_worse": round(float((a < div_sharpe).mean()), 3),
            "p10": round(float(np.percentile(a, 10)), 2),
            "p90": round(float(np.percentile(a, 90)), 2)}


def decide_verdict(d: dict) -> dict:
    """집중 게이트 판정 (K=3 기준). conc_go = 모집단·다중검정 모두 통과 시만."""
    conc_go = (d.get("pbo") is not None and d["pbo"] <= PBO_MAX
               and d.get("median_obj") is not None and d["median_obj"] > 0
               and (d.get("best_dsr") or 0) >= DSR_GATE
               and (d.get("best_psr") or 0) >= PSR_GATE
               and d.get("frac_worse", 1.0) < 0.5)
    if conc_go:
        return {"verdict": "GO",
                "note": "무스킬 집중이 분산을 이김 — 희귀(선택엣지 존재 의미). shadow 권장"}
    beat = round((1.0 - d.get("frac_worse", 1.0)) * 100)
    return {"verdict": "NO-GO",
            "note": (f"집중(랜덤K)은 분산 대비 중앙 Sharpe Δ{d.get('median_delta')}·분산 이길확률 {beat}%·"
                     f"과적합확률(PBO) {d.get('pbo')} — 무스킬 집중은 보상없는 idiosyncratic 위험(중앙값이 "
                     f"분산에 지고 승자도 OOS 미지속). 최고 1개는 사후 운(1/{N_SAMPLES}, 사전선택 불가). "
                     f"종목집중 미반영 — 검증된 집중은 구조레버리지(Tier3)뿐, 현 책 단일종목 집중은 분산 권고")}


# ── 데이터·게이트 ────────────────────────────────────────────────────────

def _fetch(symbol: str):
    import yfinance as yf
    h = yf.Ticker(symbol).history(period="max", auto_adjust=True)["Close"].dropna()
    if getattr(h.index, "tz", None) is not None:
        h.index = h.index.tz_localize(None)
    return h


def _sharpe(r) -> float:
    sd = float(r.std())
    return float(r.mean() / sd * (TRADING_DAYS ** 0.5)) if sd > 0 else 0.0


def run():
    import numpy as np
    import pandas as pd
    from ml.adaptive import reward
    from ml.validation import validate_strategy, pbo_cscv

    px = {}
    for s in SECTORS:
        h = _fetch(s)
        if h is not None and len(h) > 500:
            px[s] = h
    if len(px) < 5:
        return {"error": "데이터 부족"}
    df = pd.DataFrame(px).dropna()
    if len(df) < MIN_DAYS:
        return {"error": "공통 이력 부족", "n": len(df)}

    rets = df.pct_change().dropna()
    n = rets.shape[1]
    div_r = equal_weight_returns(rets)
    div_sharpe = _sharpe(div_r)
    div_nav = (1 + div_r).cumprod()
    div_mdd = reward.max_drawdown(list(div_nav.values))
    div_total = float(div_nav.iloc[-1] - 1.0)

    by_K = {}
    for K in KS:
        subs = random_subset_indices(n, K, N_SAMPLES, SEED + K)
        sharpes, objs, excess_pp, best = [], [], [], None
        for idx in subs:
            cr = subset_returns(rets, idx)
            sh = _sharpe(cr)
            nav = (1 + cr).cumprod()
            md = reward.max_drawdown(list(nav.values))
            tot = float(nav.iloc[-1] - 1.0)
            ob = reward.objective_score(tot - div_total, md, div_mdd)
            sharpes.append(sh)
            objs.append(ob if ob is not None else None)
            ex = cr.values - div_r.values
            excess_pp.append(float(ex.mean() / ex.std()) if ex.std() > 0 else 0.0)
            if best is None or sh > best[0]:
                best = (sh, cr)
        valid_objs = [o for o in objs if o is not None]
        median_obj = float(np.median(valid_objs)) if valid_objs else None
        sr_var = float(pd.Series(excess_pp).var(ddof=1))
        val = validate_strategy(best[1].values, benchmark_returns=div_r.values,
                                n_trials=len(subs), sr_variance=sr_var)
        by_K[K] = {"penalty": concentration_penalty(div_sharpe, sharpes),
                   "median_obj": (None if median_obj is None else round(median_obj, 3)),
                   "best_sharpe": round(best[0], 2),
                   "best_psr": val.get("psr_excess"), "best_dsr": val.get("dsr")}

    # PBO: K=3 부분집합 표본 초과수익 행렬
    sub3 = random_subset_indices(n, 3, min(12, N_SAMPLES), SEED + 99)
    M = np.column_stack([subset_returns(rets, idx).values - div_r.values for idx in sub3])
    pbo_res = pbo_cscv(M, n_splits=10)
    pbo = None if pbo_res is None else round(pbo_res["pbo"], 3)

    k3 = by_K[3]
    d = {"pbo": pbo, "median_obj": k3["median_obj"], "best_dsr": k3["best_dsr"],
         "best_psr": k3["best_psr"], "median_delta": k3["penalty"]["median_delta"],
         "frac_worse": k3["penalty"]["frac_worse"]}
    return {
        "period": f"{df.index[0].date()}~{df.index[-1].date()} ({len(df)}d)",
        "universe": "9 sector SPDR (survivorship-free)",
        "diversified_sharpe": round(div_sharpe, 2), "diversified_mdd": round(div_mdd * 100, 1),
        "by_K": by_K, "pbo": pbo,
        **decide_verdict(d),
    }


def run_all():
    return run()


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), ensure_ascii=False, indent=2, default=str))
