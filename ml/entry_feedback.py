"""entry_feedback.py — 진입 후보 스냅샷/사후성과 수집.

목표:
  - 추천 당시의 점수·근거를 point-in-time 원장에 append-only로 저장한다.
  - 20/60거래일이 지난 후보의 실제 수익·초과수익·목표/무효화선 터치 여부를
    별도 outcome 원장에 append-only로 백필한다.
  - 성공/실패 이유 태그를 만들어 다음 임계값 학습과 사람이 읽는 회고에 쓴다.

위치:
  ~/reports/ml-data/entry_signals_decisions.jsonl
  ~/reports/ml-data/entry_signals_outcomes.jsonl
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SURFACE = "entry_signals"
HORIZONS = (20, 60)


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s or "unknown")).strip("_") or "unknown"


def _market(ticker: str, currency: str = "") -> str:
    if ticker.endswith((".KS", ".KQ")) or currency == "KRW":
        return "KR"
    return "US"


def _benchmark(decision: dict) -> str:
    if decision.get("market") == "KR":
        return "^KS11"
    underlying = str(decision.get("underlying") or "")
    if underlying in ("SPY", "QQQ"):
        return underlying
    return "QQQ"


def _sample_quality(n: int) -> str:
    if n >= 50:
        return "high"
    if n >= 20:
        return "medium"
    return "low"


def _decision_id(date: str, source: str, universe: str, ticker: str) -> str:
    return f"{date}:{_slug(source)}:{_slug(universe)}:{ticker}"


def _reward_risk(score) -> tuple[float, float]:
    risk = abs(score.downside_p25_20d) if score.downside_p25_20d < 0 else 0.0
    risk = max(risk, 0.03)
    reward = max(score.expected_ret_20d, 0.0)
    rr = reward / risk if risk > 0 else 0.0
    return round(risk, 5), round(rr, 4)


def score_to_decision(score, *, source: str, universe: str, date: str | None = None) -> dict:
    """EntryScore를 학습용 point-in-time decision 레코드로 변환."""
    from ml.entry_analyzer import trade_level_values

    d = date or _today_kst()
    buy_lo, target, stop = trade_level_values(score)
    risk_pct, rr = _reward_risk(score)
    market = _market(score.ticker, score.currency)
    return {
        "id": _decision_id(d, source, universe, score.ticker),
        "date": d,
        "snapshot_ts": _now_kst(),
        "source": source,
        "universe": universe,
        "ticker": score.ticker,
        "display_name": score.display_name or score.ticker,
        "market": market,
        "benchmark": "^KS11" if market == "KR" else ("SPY" if score.underlying == "SPY" else "QQQ"),
        "category": score.category,
        "currency": score.currency,
        "underlying": score.underlying,
        "signal": score.signal,
        "score": float(score.score),
        "alert_candidate": bool(score.signal == "enter" and score.score >= 0.60),
        "current_price": round(float(score.current_price), 4),
        "buy_low": round(float(buy_lo), 4),
        "target_price": round(float(target), 4),
        "stop_price": round(float(stop), 4),
        "risk_pct": risk_pct,
        "reward_risk": rr,
        "features": {
            "drawdown": float(score.current_drawdown),
            "rsi": float(score.current_rsi),
            "vix": float(score.current_vix),
            "mom_20d": float(score.current_mom_20d),
            "mom_60d": float(score.current_mom_60d),
            "n_similar": int(score.n_similar),
            "sample_quality": _sample_quality(int(score.n_similar)),
            "win_prob_20d": float(score.win_prob_20d),
            "win_prob_60d": float(score.win_prob_60d),
            "expected_ret_20d": float(score.expected_ret_20d),
            "expected_ret_60d": float(score.expected_ret_60d),
            "downside_p25_20d": float(score.downside_p25_20d),
            "upside_p75_20d": float(score.upside_p75_20d),
            "technical_rating": score.technical_rating,
            "technical_score": score.technical_score,
            "pivot_p": score.pivot_p,
            "pivot_position": score.pivot_position,
        },
        "reasons": list(score.reasons or []),
    }


def record_entry_scores(scores: Iterable, *, source: str = "auto_watch",
                        universe: str = "watch", ledger=None) -> int:
    """분석된 EntryScore 전체를 일 1회/종목 단위로 불변 저장. 신규 기록 수 반환."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    existing = {d.get("id") for d in ledger.read_decisions()}
    added = 0
    for score in scores or []:
        rec = score_to_decision(score, source=source, universe=universe)
        if rec["id"] not in existing:
            ledger.log_decision(rec)
            existing.add(rec["id"])
            added += 1
    if added:
        logger.info("진입 후보 스냅샷 저장: %d건 (%s/%s)", added, source, universe)
    return added


def _outcome_id(decision_id: str, horizon: int) -> str:
    return f"{decision_id}:h{int(horizon)}"


def _base_decision_id(outcome_id: str) -> str:
    if ":h" in str(outcome_id):
        return str(outcome_id).rsplit(":h", 1)[0]
    return str(outcome_id)


def _max_drawdown(values: list[float]) -> float:
    peak = None
    mdd = 0.0
    for v in values:
        if not math.isfinite(v):
            continue
        peak = v if peak is None else max(peak, v)
        if peak and peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return abs(mdd)


def _first_touch(window: pd.DataFrame, target: float | None, stop: float | None) -> tuple[str, str | None, float | None]:
    """목표/무효화선 첫 터치. 같은 날 둘 다 닿으면 보수적으로 stop 우선."""
    if window is None or len(window) <= 1:
        return "none", None, None
    for idx, row in window.iloc[1:].iterrows():
        hi = float(row.get("High", row.get("Close")))
        lo = float(row.get("Low", row.get("Close")))
        day = pd.Timestamp(idx).strftime("%Y-%m-%d")
        if stop and lo <= stop:
            return "stop", day, float(stop)
        if target and hi >= target:
            return "target", day, float(target)
    return "none", None, None


def _default_price_result(decision: dict, horizon: int) -> dict | None:
    """결정일 이후 horizon 거래일 수익률/경로 결과 계산. 미성숙이면 None."""
    from ml.data_pipeline import fetch_prices

    ticker = decision.get("ticker")
    benchmark = decision.get("benchmark") or _benchmark(decision)
    if not ticker:
        return None
    prices = fetch_prices([ticker, benchmark], days=max(756, horizon * 4 + 80))
    df = prices.get(ticker)
    bm = prices.get(benchmark)
    if df is None or bm is None or len(df) <= horizon or len(bm) <= horizon:
        return None

    start = pd.Timestamp(decision.get("date"))
    df = df.sort_index()
    bm = bm.sort_index()
    fut = df[df.index >= start]
    bfut = bm[bm.index >= start]
    if len(fut) <= horizon or len(bfut) <= horizon:
        return None

    window = fut.iloc[:horizon + 1]
    bwindow = bfut.iloc[:horizon + 1]
    entry = float(window["Close"].iloc[0])
    exit_ = float(window["Close"].iloc[-1])
    bentry = float(bwindow["Close"].iloc[0])
    bexit = float(bwindow["Close"].iloc[-1])
    target = decision.get("target_price")
    stop = decision.get("stop_price")
    try:
        target = float(target) if target is not None else None
        stop = float(stop) if stop is not None else None
    except Exception:
        target, stop = None, None
    path_result, path_date, path_price = _first_touch(window, target, stop)
    return {
        "entry_date": pd.Timestamp(window.index[0]).strftime("%Y-%m-%d"),
        "exit_date": pd.Timestamp(window.index[-1]).strftime("%Y-%m-%d"),
        "entry_price_actual": entry,
        "exit_price": exit_,
        "benchmark_entry": bentry,
        "benchmark_exit": bexit,
        "stock_ret": exit_ / entry - 1.0 if entry > 0 else 0.0,
        "benchmark_ret": bexit / bentry - 1.0 if bentry > 0 else 0.0,
        "fwd_mdd": _max_drawdown([float(x) for x in window["Close"].tolist()]),
        "idx_fwd_mdd": _max_drawdown([float(x) for x in bwindow["Close"].tolist()]),
        "path_result": path_result,
        "path_date": path_date,
        "path_price": path_price,
    }


def _diagnose(decision: dict, outcome: dict) -> tuple[str, list[str], str]:
    f = decision.get("features") or {}
    tags: list[str] = []
    success = bool(outcome.get("success"))

    if outcome.get("path_result") == "target":
        tags.append("target_hit")
    if outcome.get("path_result") == "stop":
        tags.append("invalidation_broken")
    if outcome.get("fwd_excess", 0.0) > 0:
        tags.append("benchmark_outperformed")
    else:
        tags.append("benchmark_lagged")
    if "매수" in str(f.get("technical_rating") or ""):
        tags.append("technical_confirmed")
    if "매도" in str(f.get("technical_rating") or ""):
        tags.append("technical_conflict")
    if f.get("pivot_position") in ("below_p", "below_s1"):
        tags.append("pivot_not_recovered")
    if f.get("pivot_position") in ("above_p", "above_r1"):
        tags.append("pivot_confirmed")
    if float(f.get("mom_20d") or 0) < 0 and float(f.get("mom_60d") or 0) < 0:
        tags.append("falling_momentum")
    if float(f.get("vix") or 0) >= 28:
        tags.append("high_vix")
    if int(f.get("n_similar") or 0) < 20:
        tags.append("small_sample")
    if float(f.get("win_prob_60d") or 0) < float(f.get("win_prob_20d") or 0):
        tags.append("weak_60d_confirmation")

    if success:
        primary = "목표 도달" if outcome.get("path_result") == "target" else "양수 초과수익"
        note = "통계 신호가 실제 수익/벤치마크 초과로 이어졌습니다."
        if "technical_confirmed" in tags or "pivot_confirmed" in tags:
            note += " 기술/피벗 확인이 성공 쪽에 보탬이 됐습니다."
    else:
        if outcome.get("path_result") == "stop":
            primary = "무효화선 이탈"
        elif outcome.get("fwd_ret", 0.0) > 0 and outcome.get("fwd_excess", 0.0) <= 0:
            primary = "상승했지만 벤치마크 미달"
        else:
            primary = "수익률 부진"
        note = "통계 신호가 실제 성과로 이어지지 않았습니다."
        if "technical_conflict" in tags or "pivot_not_recovered" in tags:
            note += " 기술 추세/피벗 충돌이 주요 의심 요인입니다."
        if "falling_momentum" in tags:
            note += " 중기 모멘텀 약세도 실패 쪽에 기여했을 수 있습니다."
    return primary, tags, note


def build_outcome(decision: dict, horizon: int, result: dict) -> dict:
    stock_ret = float(result["stock_ret"])
    bench_ret = float(result["benchmark_ret"])
    excess = stock_ret - bench_ret
    entry_price = float(decision.get("current_price") or result["entry_price_actual"])
    stop = float(decision.get("stop_price") or entry_price * 0.97)
    risk = max(entry_price - stop, entry_price * 0.03, 1e-9)
    r_multiple = (float(result["exit_price"]) - entry_price) / risk
    success = bool(
        result.get("path_result") == "target"
        or (result.get("path_result") != "stop" and stock_ret > 0 and excess > 0)
    )
    outcome = {
        "decision_id": _outcome_id(decision["id"], horizon),
        "base_decision_id": decision["id"],
        "ticker": decision.get("ticker"),
        "horizon": int(horizon),
        "matured_at": _today_kst(),
        "entry_date": result.get("entry_date"),
        "exit_date": result.get("exit_date"),
        "entry_price_actual": round(float(result["entry_price_actual"]), 4),
        "exit_price": round(float(result["exit_price"]), 4),
        "fwd_ret": round(stock_ret, 5),
        "benchmark": decision.get("benchmark") or _benchmark(decision),
        "benchmark_ret": round(bench_ret, 5),
        "fwd_excess": round(excess, 5),
        "fwd_mdd": round(float(result.get("fwd_mdd") or 0), 5),
        "idx_fwd_mdd": round(float(result.get("idx_fwd_mdd") or 0), 5),
        "path_result": result.get("path_result") or "none",
        "path_date": result.get("path_date"),
        "path_price": result.get("path_price"),
        "r_multiple": round(r_multiple, 3),
        "success": success,
    }
    primary, tags, note = _diagnose(decision, outcome)
    outcome.update({"diagnosis": primary, "factor_tags": tags, "learn_note": note})
    return outcome


def backfill_outcomes(*, ledger=None, horizons: tuple[int, ...] = HORIZONS,
                      price_fn=None) -> int:
    """성숙한 추천 후보 outcome을 append-only 백필. 신규 outcome 수 반환."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    price_fn = price_fn or _default_price_result
    done = {o.get("decision_id") for o in ledger.read_outcomes()}
    added = 0
    for decision in ledger.read_decisions():
        if not decision.get("id") or not decision.get("ticker"):
            continue
        for horizon in horizons:
            oid = _outcome_id(decision["id"], horizon)
            if oid in done:
                continue
            result = price_fn(decision, horizon)
            if result is None:
                continue
            ledger.log_outcome(build_outcome(decision, horizon, result))
            done.add(oid)
            added += 1
    if added:
        logger.info("진입 후보 outcome 백필: %d건", added)
    return added


def training_rows(*, ledger=None, horizon: int = 20) -> list[dict]:
    """decision + horizon별 outcome 조인."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    decisions = {d.get("id"): d for d in ledger.read_decisions() if d.get("id")}
    rows = []
    suffix = f":h{int(horizon)}"
    for outcome in ledger.read_outcomes():
        oid = str(outcome.get("decision_id") or "")
        if not oid.endswith(suffix):
            continue
        base_id = outcome.get("base_decision_id") or _base_decision_id(oid)
        decision = decisions.get(base_id)
        if decision:
            rows.append({**decision, **outcome, "base_decision_id": base_id})
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("ticker") or "")))
    return rows


def summarize_feedback(rows: list[dict] | None = None, *, horizon: int = 20) -> dict:
    rows = list(rows if rows is not None else training_rows(horizon=horizon))
    if not rows:
        return {"horizon": horizon, "n": 0}
    wins = [r for r in rows if r.get("success")]
    losses = [r for r in rows if not r.get("success")]
    fail_tags = Counter(tag for r in losses for tag in (r.get("factor_tags") or []))
    win_tags = Counter(tag for r in wins for tag in (r.get("factor_tags") or []))
    enter_rows = [r for r in rows if r.get("signal") == "enter"]
    return {
        "horizon": horizon,
        "n": len(rows),
        "success_rate": round(len(wins) / len(rows), 3),
        "avg_excess": round(sum(float(r.get("fwd_excess") or 0) for r in rows) / len(rows), 4),
        "avg_r": round(sum(float(r.get("r_multiple") or 0) for r in rows) / len(rows), 3),
        "enter_n": len(enter_rows),
        "enter_success_rate": round(
            sum(1 for r in enter_rows if r.get("success")) / len(enter_rows), 3
        ) if enter_rows else 0.0,
        "top_success_factors": win_tags.most_common(5),
        "top_failure_factors": fail_tags.most_common(5),
    }


_TAG_LABELS = {
    "target_hit": "목표 도달",
    "invalidation_broken": "무효화선 이탈",
    "benchmark_outperformed": "벤치마크 초과",
    "benchmark_lagged": "벤치마크 미달",
    "technical_confirmed": "기술 추세 확인",
    "technical_conflict": "기술 추세 충돌",
    "pivot_not_recovered": "피벗 미회복",
    "pivot_confirmed": "피벗 확인",
    "falling_momentum": "하락 모멘텀",
    "high_vix": "고 VIX",
    "small_sample": "표본 부족",
    "weak_60d_confirmation": "60일 확인 약함",
}


def format_feedback_summary(summary: dict) -> str:
    if not summary or not summary.get("n"):
        return f"{summary.get('horizon', 20)}일: 성숙 표본 없음"
    def _fmt_tags(items):
        if not items:
            return "—"
        return ", ".join(f"{_TAG_LABELS.get(k, k)} {v}" for k, v in items)
    return (
        f"{summary['horizon']}일 표본 {summary['n']}건 · 성공률 {summary['success_rate']*100:.0f}% "
        f"· 평균초과 {summary['avg_excess']*100:+.2f}% · 평균R {summary['avg_r']:+.2f}\n"
        f"성공 요인: {_fmt_tags(summary.get('top_success_factors'))}\n"
        f"실패 요인: {_fmt_tags(summary.get('top_failure_factors'))}"
    )
