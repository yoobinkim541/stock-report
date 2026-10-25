"""
oos_eval.py — 날짜별 top-K 바스켓 OOS 평가 (KR/US 모의 정책 공용).

배치(plan_rebalance)는 **결정일마다** 정책점수 상위 max_positions 를 균등 비중으로 고른다.
OOS 평가도 같은 단위로 한다: 결정일 d 의 후보를 점수순으로 정렬해 상위 K 를 고르고,
그 바스켓의 평균 수익을 d 의 관측 하나로 삼아 **하나의 OOS 수익 시계열**을 만든다.

- n_candidates: 평가에 들어간 후보 행 수, n_selected: 실제 성과에 쓰인 행 수,
  n_days: 평가일(= 시계열 관측) 수, n_independent: 보유기간이 겹치지 않는 평가일 수.
  최소 표본 게이트는 행 수가 아니라 평가일(n)에 적용한다.
- MDD: 평가일 순서대로 바스켓 수익(net 절대수익 = fwd_excess + index_ret)을 복리로 이은
  포트폴리오 곡선의 MDD. 지수 MDD 는 같은 평가일의 지수 수익으로 같은 방식의 곡선을 만들어
  계산한다(index_curve_mdd) — 두 값의 단위가 같다. 보유기간이 겹치는 코호트를 순차
  복리하므로 절대 크기는 실제 계좌 MDD 와 다르지만 전략·지수에 같은 구성을 쓴다.
  지수 수익이 없는 날이 있으면 초과수익 곡선으로 대체(mdd_basis='excess').
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from ml.adaptive import reward as _reward


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _day(row: dict) -> str:
    return str(row.get("date") or "")[:10]


def _by_day(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        if _finite(row.get("fwd_excess")):
            out.setdefault(_day(row), []).append(row)
    return out


def _day_index_return(rows: list[dict]) -> float | None:
    vals = [float(r["index_ret"]) for r in rows if _finite(r.get("index_ret"))]
    return sum(vals) / len(vals) if vals else None


def _curve_mdd(returns: list[float]) -> float:
    nav, navs = 1.0, [1.0]
    for ret in returns:
        nav *= 1.0 + ret
        navs.append(nav)
    return _reward.max_drawdown(navs)


def independent_days(days: list[str], horizon_days: int) -> list[str]:
    """보유기간이 겹치지 않는 평가일(앞에서부터 탐욕 선택).

    horizon_days 거래일 ≈ ceil(horizon·7/5) 달력일로 근사한다. 날짜를 해석할 수 없으면
    서로 독립이라고 가정하지 않고 첫 날 하나만 센다.
    """
    ordered = sorted(d for d in days if d)
    if horizon_days <= 1:
        return ordered
    span = timedelta(days=math.ceil(horizon_days * 7 / 5))
    picked: list[str] = []
    last = None
    for d in ordered:
        try:
            cur = date.fromisoformat(d)
        except ValueError:
            return ordered[:1]
        if last is None or cur - last >= span:
            picked.append(d)
            last = cur
    return picked


def dated_topk_eval(rows: list[dict], score_fn, max_positions: int, *, horizon_days: int) -> dict:
    """결정일별 top-K 균등 바스켓 → OOS 시계열 평가.

    score_fn(row) -> float. 같은 점수는 ticker 순으로 결정적 정렬.
    반환 {"excess", "mdd", "n"(=n_days), "n_days", "n_candidates", "n_selected",
          "n_independent", "series", "dates", "mdd_basis"}.
    """
    k = max(1, int(max_positions))
    grouped = _by_day(rows)
    dates = sorted(grouped)
    if not dates:
        return {"excess": 0.0, "mdd": 1.0, "n": 0, "n_days": 0, "n_candidates": 0,
                "n_selected": 0, "n_independent": 0, "series": [], "dates": [],
                "mdd_basis": "none"}
    series: list[float] = []
    absolute: list[float | None] = []
    n_selected = 0
    for d in dates:
        ranked = sorted(grouped[d], key=lambda r: (-float(score_fn(r)), str(r.get("ticker") or "")))
        sel = ranked[:k]
        n_selected += len(sel)
        excess = sum(float(r["fwd_excess"]) for r in sel) / len(sel)
        idx = _day_index_return(grouped[d])
        series.append(excess)
        absolute.append(None if idx is None else excess + idx)
    if all(v is not None for v in absolute):
        mdd, basis = _curve_mdd([float(v) for v in absolute]), "absolute"
    else:
        mdd, basis = _curve_mdd(series), "excess"
    return {
        "excess": round(sum(series) / len(series), 6),
        "mdd": round(mdd, 6),
        "n": len(dates),
        "n_days": len(dates),
        "n_candidates": sum(len(v) for v in grouped.values()),
        "n_selected": n_selected,
        "n_independent": len(independent_days(dates, horizon_days)),
        "series": [round(v, 8) for v in series],
        "dates": dates,
        "mdd_basis": basis,
    }


def index_curve_mdd(rows: list[dict]) -> float | None:
    """dated_topk_eval 과 같은 평가일(fwd_excess 가 있는 결정일)의 지수 수익 곡선 MDD.

    지수 수익이 없는 평가일이 하나라도 있으면 None(호출자가 보수 기본값 사용)."""
    grouped = _by_day(rows)
    if not grouped:
        return None
    rets = [_day_index_return(grouped[d]) for d in sorted(grouped)]
    if any(v is None for v in rets):
        return None
    return round(_curve_mdd([float(v) for v in rets]), 6)
