"""
oos_eval.py — 날짜별 top-K 바스켓 OOS 평가 (KR/US 모의 정책 공용).

배치(plan_rebalance)는 **결정일마다** 정책점수 상위 max_positions 를 균등 비중으로 고른다.
OOS 평가도 같은 단위로 한다: 결정일 d 의 후보를 점수순으로 정렬해 상위 K 바스켓을 만든다.

- excess / n: 모든 평가일 바스켓 평균 초과수익의 평균(챔피언 비교용 점추정)과 평가일 수(최소 표본
  게이트 단위). n_candidates: 평가에 들어간 후보 행 수, n_selected: 성과에 쓰인 행 수.
- 비중첩 코호트(통계 증거): 평가일 바스켓을 시작순으로 훑어, 직전에 채택한 코호트의 **실제 라벨
  종료일**(outcome 의 exit_date — 가격 자료의 거래일이므로 휴장·거래정지가 반영됨) 이후에 시작하는
  코호트만 고른다(nonoverlapping_units). series 는 바로 이 코호트들의 바스켓 초과수익이고
  n_independent = len(series), series_ids 가 각 관측의 입력 행 id 다 — 보고 표본 = 검정 입력.
  종료일·보유 경로가 없는 행(legacy)이 낀 코호트는 독립 증거로 쓰지 않는다(n_unresolved_days).
- MDD: 위 코호트를 차례로 보유(진입일 종가 진입·라벨 종료일 청산·사이는 현금)하는 포트폴리오의
  **일별 mark-to-market** 곡선 MDD. 각 종목의 보유기간 경로(outcome.path)를 균등 비중으로 합치므로
  보유 중 낙폭이 그대로 반영된다(왕복비용 = gross_excess − fwd_excess 를 진입 시 차감).
  index_mdd 는 같은 코호트·같은 비중·같은 날짜로 지수를 보유한 곡선의 MDD 다.
  코호트가 하나도 없으면 mdd/index_mdd = None(mdd_basis='unavailable') — 위험 증거 없음 → 채택 보류.
  종료 수익률만으로 곡선을 합성하지 않는다(보유 중 낙폭이 사라져 위험 게이트가 무력화됨).
"""
from __future__ import annotations

import math

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


def row_id(row: dict) -> str:
    """학습행 식별자 — 결정 id, 없으면 결정일:티커."""
    for key in ("id", "decision_id"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return f"{_day(row)}:{row.get('ticker') or ''}"


def holding_window(stock, index, horizon: int) -> dict | None:
    """공통 거래일로 정렬된 종목·지수 종가(pd.Series) → 보유 horizon 거래일 outcome 필드.

    path = [[거래일, 종목/진입가, 지수/진입가], ...] (진입일 포함 horizon+1 점) — OOS 위험곡선의 근거.
    entry_date/exit_date 는 실제 가격 자료의 거래일이다. 기간 미달(미성숙)이면 None.
    """
    import pandas as pd
    if stock is None or index is None or len(stock) <= horizon or len(index) <= horizon:
        return None
    sw, kw = stock.iloc[:horizon + 1], index.iloc[:horizon + 1]
    svals = [float(x) for x in sw.values]
    kvals = [float(x) for x in kw.values]
    s0, k0 = svals[0], kvals[0]
    if not (s0 > 0 and k0 > 0):
        return None
    days = [pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in sw.index]
    path = [[d, round(a / s0, 6), round(b / k0, 6)] for d, a, b in zip(days, svals, kvals)]
    return {"stock_ret": svals[-1] / s0 - 1.0, "idx_ret": kvals[-1] / k0 - 1.0,
            "stock_mdd": _reward.max_drawdown(svals), "idx_mdd": _reward.max_drawdown(kvals),
            "entry_date": days[0], "exit_date": days[-1], "path": path}


def _member_path(row: dict) -> list[tuple[str, float, float]] | None:
    """outcome.path 검증 → [(거래일, 종목가치, 지수가치)]. 형식 위반·exit_date 불일치면 None."""
    path = row.get("path")
    exit_day = str(row.get("exit_date") or "")[:10]
    if not isinstance(path, list) or len(path) < 2 or not exit_day:
        return None
    out: list[tuple[str, float, float]] = []
    prev = ""
    for point in path:
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            return None
        day, s, i = str(point[0])[:10], point[1], point[2]
        if not day or day <= prev or not _finite(s) or not _finite(i) or s <= 0 or i <= 0:
            return None
        out.append((day, float(s), float(i)))
        prev = day
    if out[-1][0] != exit_day:
        return None
    return out


def _round_trip_cost(row: dict) -> float:
    gross, net = row.get("gross_excess"), row.get("fwd_excess")
    return max(0.0, float(gross) - float(net)) if _finite(gross) and _finite(net) else 0.0


def _cohort_curve(members: list[tuple[list, float]]) -> list[tuple[str, float, float]]:
    """균등 비중 코호트 [(path, 왕복비용)] → [(거래일, 바스켓 가치, 지수 바스켓 가치)].

    진입 전 구성원은 현금 1, 진입 후 path 가치(비용 차감), 청산 후 마지막 가치로 고정."""
    days = sorted({d for path, _ in members for d, _, _ in path})
    weight = 1.0 / len(members)
    cursor = [0] * len(members)
    last = [(1.0, 1.0)] * len(members)
    out = []
    for day in days:
        bs = bi = 0.0
        for j, (path, cost) in enumerate(members):
            while cursor[j] < len(path) and path[cursor[j]][0] <= day:
                _, s, i = path[cursor[j]]
                last[j] = (s - cost, i)
                cursor[j] += 1
            bs += weight * last[j][0]
            bi += weight * last[j][1]
        out.append((day, bs, bi))
    return out


def nonoverlapping_units(units: list[tuple[str, str | None, object]]) -> tuple[list, int]:
    """[(시작일, 라벨 종료일|None, payload)] → (비중첩 채택 목록, 종료 미상으로 건너뛴 수).

    시작순 탐욕 선택. 직전 채택 단위의 종료일 이후(시작일 ≥ 종료일 — 종가 기준 수익 구간이
    겹치지 않음)에 시작하는 단위만 채택한다. 종료일이 없거나 시작일보다 앞서면 독립 증거로 쓰지
    않고 건너뛴다(그 단위의 기간을 모르므로 다음 단위의 기준으로도 쓰지 않는다)."""
    picked: list = []
    unresolved = 0
    last_end = None
    for start, end, payload in sorted(units, key=lambda u: (u[0], u[1] or "")):
        if last_end is not None and start < last_end:
            continue
        if not end or end < start:
            unresolved += 1
            continue
        picked.append((start, end, payload))
        last_end = end
    return picked, unresolved


def dated_topk_eval(rows: list[dict], score_fn, max_positions: int) -> dict:
    """결정일별 top-K 균등 바스켓 OOS 평가.

    score_fn(row) -> float. 같은 점수는 ticker 순으로 결정적 정렬.
    반환 {"excess", "mdd", "index_mdd", "mdd_basis", "n"(=n_days), "n_days", "n_candidates",
          "n_selected", "n_independent", "series", "series_days", "series_ids",
          "n_unresolved_days", "dates"}.
    """
    k = max(1, int(max_positions))
    grouped = _by_day(rows)
    dates = sorted(grouped)
    if not dates:
        return {"excess": 0.0, "mdd": None, "index_mdd": None, "mdd_basis": "unavailable",
                "n": 0, "n_days": 0, "n_candidates": 0, "n_selected": 0, "n_independent": 0,
                "series": [], "series_days": [], "series_ids": [], "n_unresolved_days": 0,
                "dates": []}
    day_excess: list[float] = []
    units: list[tuple[str, str | None, dict]] = []
    n_selected = 0
    for d in dates:
        ranked = sorted(grouped[d], key=lambda r: (-float(score_fn(r)), str(r.get("ticker") or "")))
        sel = ranked[:k]
        n_selected += len(sel)
        excess = sum(float(r["fwd_excess"]) for r in sel) / len(sel)
        day_excess.append(excess)
        paths = [_member_path(r) for r in sel]
        start, end, members = d, None, None
        if all(p is not None and p[0][0] >= d for p in paths):
            start = min(p[0][0] for p in paths)
            end = max(p[-1][0] for p in paths)
            members = [(p, _round_trip_cost(r)) for p, r in zip(paths, sel)]
        units.append((start, end, {"day": d, "excess": excess, "members": members,
                                   "ids": [row_id(r) for r in sel]}))
    picked, unresolved = nonoverlapping_units(units)
    mdd = index_mdd = None
    if picked:
        nav_s = nav_i = 1.0
        s_curve, i_curve = [1.0], [1.0]
        for _, _, unit in picked:
            curve = _cohort_curve(unit["members"])
            s_curve += [nav_s * bs for _, bs, _ in curve]
            i_curve += [nav_i * bi for _, _, bi in curve]
            nav_s *= curve[-1][1]
            nav_i *= curve[-1][2]
        mdd = round(_reward.max_drawdown(s_curve), 6)
        index_mdd = round(_reward.max_drawdown(i_curve), 6)
    return {
        "excess": round(sum(day_excess) / len(day_excess), 6),
        "mdd": mdd,
        "index_mdd": index_mdd,
        "mdd_basis": "holding_path" if picked else "unavailable",
        "n": len(dates),
        "n_days": len(dates),
        "n_candidates": sum(len(v) for v in grouped.values()),
        "n_selected": n_selected,
        "n_independent": len(picked),
        "series": [round(unit["excess"], 8) for _, _, unit in picked],
        "series_days": [unit["day"] for _, _, unit in picked],
        "series_ids": [unit["ids"] for _, _, unit in picked],
        "n_unresolved_days": unresolved,
        "dates": dates,
    }
