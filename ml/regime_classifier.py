#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/regime_classifier.py — 추세 vs 횡보 레짐 감지 (Phase 1A).

낙폭 중심 classify_market 이 못 잡는 **추세 없음(횡보/range-bound)** 을 명시적으로 감지한다.

핵심 = Kaufman **Efficiency Ratio(ER)**: 임의 가중합성 대신 단일 원리지표.
    ER(N) = |close[t] − close[t−N]| / Σ_{i}|close[i] − close[i−1]|   ∈ [0,1]
    1에 가까움 = 직선 추세 / 0에 가까움 = 같은 자리서 등락(chop=횡보).

판정(모두 충족, K일 지속): ER < ER_IN(0.30) AND |MA200 20일 기울기(연율)| < SLOPE_MAX(5%)
                          AND drawdown > DD_FLOOR(−10%, 깊은 bear 제외).
서브: realized_vol_20d < 1년 중앙값 → 'sideways_calm'(인컴/캐리), 아니면 'sideways_choppy'(디리스크).
비대칭 이탈(즉시): ER > ER_OUT(0.45) OR |1M 모멘텀| > MOM_EXIT(6%) OR drawdown ≤ DD_FLOOR → 추세/bear 재개.

무룩어헤드: 모든 통계는 과거만(rolling), 상태 전이는 forward-walk(각 시점 과거만 참조).
US=QQQ · KR=^KS11 공용. 결측/오류 시 graceful(None/'unknown').
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 기본 파라미터(원리값 — grid 과최적화 금지) ─────────────────────────────────
# 핵심: 낮은 ER(제자리 등락) + **중기 순진행 없음**(|60일 수익률| 작음) + 지속.
# (MA200 기울기는 진단용으로만 — QQQ 장기상승에선 거의 항상 >5%라 하드게이트로 쓰면 횡보 과소감지.
#  대신 60일 순수익률로 "추세 눌림목 vs 진짜 박스권" 을 직접 구분.)
ER_N = 20            # Efficiency Ratio 창
ER_IN = 0.35         # 이 미만 = chop
ER_OUT = 0.50        # 이 초과 = 추세 재개(즉시 이탈)
RET_WIN = 60         # 중기(≈3개월) 순진행 창
RET_FLAT = 0.08      # |60일 수익률| 이 미만 = 순진행 없음(진입)
RET_EXIT = 0.10      # |60일 수익률| 초과 = 추세 재개(이탈)
DD_FLOOR = -0.10     # 낙폭 이보다 깊으면 bear(횡보 아님)
PERSIST = 15         # 횡보 진입 확인 거래일(브리프 눌림목 배제 — 느린 진입)
MOM_WIN = 21         # 1M 모멘텀 창
MOM_EXIT = 0.08      # |1M 모멘텀| 초과 시 즉시 이탈
MA_WIN = 200
SLOPE_WIN = 20


# ── 순수 통계 (pd.Series in/out) ────────────────────────────────────────────────

def efficiency_ratio(closes, n: int = ER_N):
    """Kaufman ER 시계열 ∈[0,1]. 분모 0(완전 정지) → 0.0."""
    import numpy as np
    net = closes.diff(n).abs()
    vol = closes.diff().abs().rolling(n).sum()
    er = net / vol.replace(0, np.nan)
    return er.fillna(0.0).clip(0.0, 1.0)


def ma_slope_annualized(closes, ma_win: int = MA_WIN, slope_win: int = SLOPE_WIN):
    """MA(ma_win) 의 slope_win 거래일 변화율을 연율화(×252/slope_win). 추세 강도/방향."""
    ma = closes.rolling(ma_win).mean()
    return (ma / ma.shift(slope_win) - 1.0) * (252.0 / slope_win)


def realized_vol(closes, window: int = 20):
    """연율 실현변동성(일수익 std × √252)."""
    return closes.pct_change().rolling(window).std() * (252 ** 0.5)


def rolling_drawdown(closes, window: int = 252):
    """trailing window 최고가 대비 낙폭(음수, 무룩어헤드 rolling max)."""
    peak = closes.rolling(window, min_periods=1).max()
    return closes / peak - 1.0


# ── 레짐 시계열 (백테스트용, 무룩어헤드 forward-walk) ────────────────────────────

def regime_series(closes, *, er_n: int = ER_N, er_in: float = ER_IN, er_out: float = ER_OUT,
                  dd_floor: float = DD_FLOOR, persist: int = PERSIST,
                  mom_win: int = MOM_WIN, mom_exit: float = MOM_EXIT):
    """일별 레짐 라벨 Series ∈ {'sideways_calm','sideways_choppy','trend'}.

    진입 = raw 조건 persist일 연속 / 이탈 = 비대칭(ER↑·모멘텀↑·deep DD 즉시). 각 시점 과거만 사용.
    """
    import pandas as pd
    er = efficiency_ratio(closes, er_n)
    dd = rolling_drawdown(closes)
    rv = realized_vol(closes)
    rv_med = rv.rolling(252, min_periods=60).median()
    mom = (closes / closes.shift(mom_win) - 1.0).abs()
    ret60 = (closes / closes.shift(RET_WIN) - 1.0).abs()        # 중기 순진행

    raw = (er < er_in) & (ret60 < RET_FLAT) & (dd > dd_floor)   # 순간 횡보 조건(순진행 없음)
    exit_now = (er > er_out) | (mom > mom_exit) | (ret60 > RET_EXIT) | (dd <= dd_floor)

    labels = []
    in_side = False
    run = 0
    idx = closes.index
    for i in range(len(idx)):
        r = bool(raw.iloc[i]) if pd.notna(raw.iloc[i]) else False
        x = bool(exit_now.iloc[i]) if pd.notna(exit_now.iloc[i]) else False
        run = run + 1 if r else 0
        if in_side:
            if x:
                in_side = False
        else:
            if run >= persist:        # 느린 진입 확인
                in_side = True
        if in_side:
            calm = pd.notna(rv.iloc[i]) and pd.notna(rv_med.iloc[i]) and rv.iloc[i] < rv_med.iloc[i]
            labels.append("sideways_calm" if calm else "sideways_choppy")
        else:
            labels.append("trend")
    return pd.Series(labels, index=idx, name="regime")


# ── 라이브 단일 시점 (barbell·report 용) ────────────────────────────────────────

def classify_latest(closes, *, drawdown: float | None = None) -> dict:
    """최신 시점 레짐 진단. closes 부족/오류 시 graceful('unknown').

    drawdown 주입 시(barbell 앵커 낙폭) 그것을 deep-bear 게이트에 사용, 미주입 시 rolling 계산.
    반환 {sideways: bool, substate, er, ma_slope, rv, drawdown, reason}.
    """
    out = {"sideways": False, "substate": None, "er": None, "ma_slope": None,
           "rv": None, "drawdown": drawdown, "reason": "데이터 부족"}
    try:
        import pandas as pd
        if closes is None or len(closes) < MA_WIN + SLOPE_WIN:
            return out
        er = float(efficiency_ratio(closes).iloc[-1])
        slope = float(ma_slope_annualized(closes).iloc[-1])
        rv = float(realized_vol(closes).iloc[-1])
        rv_med_s = realized_vol(closes).rolling(252, min_periods=60).median().iloc[-1]
        dd = drawdown if drawdown is not None else float(rolling_drawdown(closes).iloc[-1])
        mom = float((closes / closes.shift(MOM_WIN) - 1.0).iloc[-1])
        ret60 = float((closes / closes.shift(RET_WIN) - 1.0).iloc[-1])
        out.update(er=round(er, 3), ma_slope=round(slope, 4), rv=round(rv, 4),
                   drawdown=round(dd, 4), ret60=round(ret60, 4))

        # 비대칭 이탈 우선(추세/bear 재개면 횡보 아님)
        if er > ER_OUT or abs(mom) > MOM_EXIT or abs(ret60) > RET_EXIT or dd <= DD_FLOOR:
            out["reason"] = f"추세/bear (ER {er:.2f}·1M {mom*100:+.1f}%·3M {ret60*100:+.1f}%·DD {dd*100:.1f}%)"
            return out
        if er < ER_IN and abs(ret60) < RET_FLAT and dd > DD_FLOOR:
            calm = pd.notna(rv_med_s) and rv < float(rv_med_s)
            out["sideways"] = True
            out["substate"] = "sideways_calm" if calm else "sideways_choppy"
            out["reason"] = (f"횡보({out['substate']}) — ER {er:.2f}<{ER_IN}·3M {ret60*100:+.1f}%·"
                             f"낙폭 {dd*100:.1f}% (vol {'저' if calm else '고'})")
        else:
            out["reason"] = f"추세성(ER {er:.2f}·3M {ret60*100:+.1f}%)"
    except Exception as e:
        logger.debug("regime classify 실패: %s", e)
    return out
