"""한국 모의에 넣는 장점 앙상블.

소유자 2026-11-12: 2019-2026 낙폭 0.4786 이 지수 0.4576 을 2.1%p 넘은 것은 교체 거절
사유에서 뺐다. 초과 3.6672 는 현재 가격축 0.0933 보다 크다. 이 여유는 이 앙상블만
쓴다. should_adopt 전역 규칙은 그대로다. 미국 앙상블은 대상이 아니다.

슬리브 목록은 2018년 이전에 고정했다. 비중은 직전 월말까지의 36개월 목적함수만 쓴다.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

OWNER_MDD_SLACK = 0.03
STALE_DAYS = 45


def owner_adopts(held_mdd: float, bench_mdd: float, held_excess: float,
                 current_excess: float, n: int, min_samples: int) -> bool:
    """이 앙상블만. 지수 낙폭 + 3%p 이내이고 현재 믹스보다 초과가 크면 채택."""
    if n < min_samples or held_excess <= 0.0 or held_excess <= current_excess:
        return False
    if bench_mdd is None or bench_mdd < 0:
        return False
    return float(held_mdd) <= float(bench_mdd) + OWNER_MDD_SLACK


def active_mark(marks, asof) -> pd.Timestamp | None:
    """끝난 달의 마지막 월말. 이번 달 중간의 마지막 거래일은 월말이 아니다."""
    asof = pd.Timestamp(asof).normalize()
    month_start = asof.replace(day=1)
    prev = [pd.Timestamp(t).normalize() for t in marks if pd.Timestamp(t).normalize() < month_start]
    return max(prev) if prev else None


def name_weights(sleeve_weights: dict, picks: dict,
                 within: dict | None = None,
                 exposure: dict | None = None) -> dict[str, float]:
    """슬리브 비중 × 슬리브 안 비중 × 노출. 합이 1 미만이면 나머지는 현금.

    고른 종목이 없는 슬리브 비중은 재배분하지 않는다. 그 수익 시계열도 현금이다.
    """
    within = within or {}
    exposure = exposure or {}
    out: dict[str, float] = {}
    for sleeve, raw in sleeve_weights.items():
        sw = float(raw or 0.0)
        if sw <= 0.0:
            continue
        names = [str(c) for c in (picks.get(sleeve) or [])]
        if not names:
            continue
        scale = float(exposure.get(sleeve, 1.0) or 0.0)
        if scale <= 0.0:
            continue
        local = within.get(sleeve)
        total = sum(float((local or {}).get(c, 0.0) or 0.0) for c in names) if local else 0.0
        use_local = local is not None and total > 0.0
        for code in names:
            part = float(local[code]) / total if use_local else 1.0 / len(names)
            if part <= 0.0:
                continue
            out[code] = out.get(code, 0.0) + sw * scale * part
    return out


def vol_exposure(returns: pd.Series, asof, *, target: float = 0.12, lookback: int = 63) -> float:
    """전일까지 실현변동으로 노출. 1 초과 레버리지는 없다."""
    s = returns.dropna().sort_index()
    s = s[s.index < pd.Timestamp(asof)]
    if len(s) < lookback:
        return 1.0
    rv = float(s.iloc[-lookback:].std() * np.sqrt(252.0))
    if not rv or rv != rv or rv <= 0.0:
        return 1.0
    return float(min(1.0, max(0.0, target / rv)))


def invvol_within(ret: pd.DataFrame, names: list[str], mark, lookback: int = 60) -> dict[str, float]:
    """월말 mark 까지의 60일 역변동 비중. 시뮬레이터와 같은 창이다."""
    cols = [c for c in names if c in ret.columns]
    if not cols:
        return {}
    vol = ret.loc[:mark, cols].iloc[-lookback:].std().replace(0, np.nan)
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if float(inv.sum()) <= 0.0:
        return {c: 1.0 / len(cols) for c in cols}
    w = inv / inv.sum()
    return {c: float(w[c]) for c in cols}


def _runners():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bt = os.path.join(root, "backtest")
    if bt not in sys.path:
        sys.path.insert(0, bt)
    import kr_policy_backtest as kb
    import x_strategy_backtest as xb
    return kb, xb


def _closes(codes: set[str], year: int) -> dict[str, float]:
    from providers.kr_market_data import _marcap_year, norm_code
    df = _marcap_year(year)
    if df is None or "Close" not in getattr(df, "columns", []):
        return {}
    want = {norm_code(c) for c in codes}
    work = df.copy()
    work["_code"] = work["Code"].map(norm_code)
    sub = work[work["_code"].isin(want)].sort_values("Date")
    if sub.empty:
        return {}
    last = sub.groupby("_code", sort=False).tail(1)
    out = {}
    for code, px in zip(last["_code"], last["Close"]):
        if px is not None and float(px) > 0:
            out[str(code)] = float(px)
    return out


def current_book(asof=None, *, start_year: int = 2001) -> dict:
    """마지막 완료 월말의 슬리브 비중으로 종목 목표 비중을 만든다. 실패 시 ok=False."""
    asof = pd.Timestamp(asof or pd.Timestamp.now()).normalize()
    try:
        kb, xb = _runners()
        from ml.x_strategies import KR_ADVANTAGE_SLEEVES, current_weights, picks_for
        panels = kb.build_panels(start_year, int(asof.year))
        if not panels or panels["ret"].empty:
            return {"ok": False, "error": "marcap 패널 없음"}
        last = pd.Timestamp(panels["ret"].index.max()).normalize()
        if (asof - last).days > STALE_DAYS:
            return {"ok": False, "error": f"패널이 오래됨 {last.date()}"}
        frames = xb.build_frames(panels)
        marks = [t for t, _, _ in frames]
        mark = active_mark(marks, asof)
        if mark is None:
            return {"ok": False, "error": "적용할 월말이 없다"}
        px_w = current_weights()
        picks = {name: {} for name in KR_ADVANTAGE_SLEEVES}
        for t, frame, state in frames:
            for name in KR_ADVANTAGE_SLEEVES:
                picks[name][t] = picks_for(name, frame, state, kb.TOP_K, px_w)
        ret, adj = panels["ret"], panels["adj"]
        series = {
            name: xb._simulate(name, ret, adj, picks[name])
            for name in KR_ADVANTAGE_SLEEVES
        }
        bench = kb.cap_benchmark(panels, marks)
        _blend, sleeve_frame = xb.blend_sleeves(
            series, bench, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS,
        )
        if sleeve_frame.empty or mark not in sleeve_frame.index:
            return {"ok": False, "error": f"월말 비중 없음 {mark.date()}"}
        sleeve_w = {name: float(sleeve_frame.loc[mark, name]) for name in KR_ADVANTAGE_SLEEVES}
        held_picks = {name: picks[name].get(mark, []) for name in KR_ADVANTAGE_SLEEVES}
        within = {}
        if held_picks.get("invvol_current"):
            within["invvol_current"] = invvol_within(ret, held_picks["invvol_current"], mark)
        exposure = {}
        if "barroso_vol_mom12" in series and len(series["barroso_vol_mom12"]):
            exposure["barroso_vol_mom12"] = vol_exposure(series["barroso_vol_mom12"], asof)
        raw = name_weights(sleeve_w, held_picks, within, exposure)
        from providers.kr_market_data import norm_code
        weights = {}
        for code, w in raw.items():
            weights[norm_code(code)] = weights.get(norm_code(code), 0.0) + float(w)
        prices = _closes(set(weights), int(last.year))
        return {
            "ok": True,
            "asof": str(mark.date()),
            "weights": {k: round(v, 6) for k, v in weights.items() if v > 0},
            "prices": prices,
            "sleeves": {k: round(v, 4) for k, v in sleeve_w.items()},
            "cash": round(max(0.0, 1.0 - sum(weights.values())), 4),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
