#!/usr/bin/env python3
"""phase_ladder_backtest.py — 바벨 Phase 래더 DCA vs 플랫 DCA 10년 비교

Phase 래더(낙폭별 DCA 배율 + 레버리지 전환 + SGOV 실탄)가
단순 매일 QQQ 매수 대비 실제로 우위인지 처음으로 검증한다.

시뮬레이션 규칙 (barbell_strategy BEAR/BULL_PHASES 근사):
  - 매일 소득 1.0 유닛 유입
  - 래더: Phase 배율 m 적용 — m>1 초과분은 SGOV 실탄에서, m<1 잉여는 실탄 적립
  - 매수 대상: Phase 0~1 QQQ / 2~3 QLD / 4 QLD70+TQQQ30 / 5 TQQQ
  - 매도 없음 (적립식) — 두 전략 모두 보유 지속
  - 비교: MOIC(투입 대비 평가), XIRR, 포트폴리오 MDD

실행:
    uv run python backtest/phase_ladder_backtest.py
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Phase 배율·매수대상 (CLAUDE.md IB Phase 테이블)
BEAR_LADDER = [
    # (낙폭 하한, 배율, {티커: 비중})
    (-0.05, 1.0, {"QQQ": 1.0}),
    (-0.10, 1.5, {"QQQ": 1.0}),
    (-0.15, 2.0, {"QLD": 1.0}),
    (-0.20, 2.5, {"QLD": 1.0}),
    (-0.30, 3.0, {"QLD": 0.7, "TQQQ": 0.3}),
    (-1.00, 5.0, {"TQQQ": 1.0}),
]


def _phase_row(dd: float, rsi: float, mom1m: float, vix: float):
    # Bull 체크 (배율 축소 — 매수대상 QQQ 유지)
    if rsi > 75 and mom1m > 0.08 and vix < 15:
        return 0.5, {"QQQ": 1.0}
    if rsi > 70 or mom1m > 0.05:
        return 0.8, {"QQQ": 1.0}
    for floor, mult, alloc in BEAR_LADDER:
        if dd > floor:
            return mult, alloc
    return 5.0, {"TQQQ": 1.0}


def _xirr(dates: pd.DatetimeIndex, flows: np.ndarray, lo=-0.9, hi=2.0) -> float:
    """일별 현금흐름 XIRR (이분법)."""
    t = np.array([(d - dates[0]).days / 365.25 for d in dates])
    def npv(r):
        return float((flows / (1 + r) ** t).sum())
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return float("nan")
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def main() -> int:
    from ml.data_pipeline import fetch_prices

    prices = fetch_prices(["QQQ", "QLD", "TQQQ", "SGOV", "SHV", "^VIX"], days=2520)
    closes = {t: df["Close"].dropna() for t, df in prices.items() if not df.empty}
    qqq = closes["QQQ"]
    idx = qqq.index

    # SGOV 짧은 히스토리 → SHV 연결 (leverage_optimizer와 동일)
    sgov = closes.get("SGOV", pd.Series(dtype=float))
    shv  = closes.get("SHV")
    if shv is not None and len(sgov) and sgov.index[0] > idx[0]:
        scale = float(sgov.iloc[0] / shv.reindex(sgov.index).iloc[0])
        sgov  = pd.concat([shv.loc[:sgov.index[0]] * scale, sgov])
        sgov  = sgov[~sgov.index.duplicated(keep="last")]
    closes["SGOV"] = sgov

    vix   = closes["^VIX"].reindex(idx).ffill().fillna(20)
    dd    = (qqq / qqq.cummax() - 1)
    mom1m = qqq.pct_change(21).fillna(0)
    delta = qqq.diff()
    rsi   = (100 - 100 / (1 + delta.clip(lower=0).rolling(14).mean()
                          / (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan))).fillna(50)

    def px(t, d):
        s = closes[t].reindex(idx).ffill()
        return float(s.loc[d])

    # ── 래더 전략 ──
    shares = {t: 0.0 for t in ("QQQ", "QLD", "TQQQ")}
    sgov_sh, ladder_vals, mults = 0.0, [], []
    # ── 플랫 DCA ──
    flat_sh, flat_vals = 0.0, []

    for d in idx:
        mult, alloc = _phase_row(float(dd.loc[d]), float(rsi.loc[d]),
                                 float(mom1m.loc[d]), float(vix.loc[d]))
        mults.append(mult)
        budget = mult * 1.0
        if mult > 1.0:  # 초과분은 SGOV 실탄 인출 (잔액 한도)
            need = (mult - 1.0) * 1.0
            avail = sgov_sh * px("SGOV", d)
            draw  = min(need, avail)
            sgov_sh -= draw / px("SGOV", d)
            budget   = 1.0 + draw
        elif mult < 1.0:  # 잉여는 실탄 적립
            sgov_sh += (1.0 - mult) / px("SGOV", d)
        for t, w in alloc.items():
            shares[t] += budget * w / px(t, d)
        ladder_vals.append(sum(shares[t] * px(t, d) for t in shares) + sgov_sh * px("SGOV", d))

        flat_sh += 1.0 / px("QQQ", d)
        flat_vals.append(flat_sh * px("QQQ", d))

    n = len(idx)
    contrib = float(n)  # 매일 1.0 유입 (양쪽 동일)
    lad = pd.Series(ladder_vals, index=idx)
    flt = pd.Series(flat_vals, index=idx)

    def report(name, series):
        final = float(series.iloc[-1])
        moic  = final / contrib
        mdd   = float((series / series.cummax() - 1).min())
        flows = np.full(n + 1, -1.0); flows[-1] = final
        dates = idx.append(pd.DatetimeIndex([idx[-1]]))
        irr   = _xirr(dates, flows)
        logger.info("%s: 최종 %.0f (투입 %.0f) MOIC %.2f×  XIRR %.1f%%  MDD %.1f%%",
                    name, final, contrib, moic, irr * 100, mdd * 100)
        return moic, irr, mdd

    logger.info("기간: %s ~ %s (%d일)", idx[0].date(), idx[-1].date(), n)
    logger.info("Phase 배율 분포: %s", pd.Series(mults).value_counts().sort_index().to_dict())
    l = report("Phase 래더", lad)
    f = report("플랫 DCA  ", flt)
    logger.info("래더 우위: MOIC %+.2f×  XIRR %+.1f%%p  MDD %+.1f%%p",
                l[0] - f[0], (l[1] - f[1]) * 100, (l[2] - f[2]) * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
