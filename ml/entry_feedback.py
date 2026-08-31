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
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SURFACE = "entry_signals"
# Integer horizons are retained for legacy daily records. String horizons make
# the sampling unit explicit for short intraday and swing evaluations.
HORIZONS = (20, 60, "30m", "1h", "4h", "1d", "3d", "5d", "20d")
SHORT_HORIZONS = {"30m": 30, "1h": 60, "4h": 240}
SWING_HORIZONS = {"1d": 1, "3d": 3, "5d": 5, "20d": 20}
ADJUST_PATH = Path(os.path.expanduser("~/reports/ml-cache/entry_feedback_adjustments.json"))
MIN_ADJUST_SAMPLES = int(os.getenv("ENTRY_FEEDBACK_ADJUST_MIN_SAMPLES", "30"))
MIN_FACTOR_SAMPLES = int(os.getenv("ENTRY_FEEDBACK_FACTOR_MIN_SAMPLES", "8"))
MIN_OOS_SAMPLES = int(os.getenv("ENTRY_FEEDBACK_OOS_MIN_SAMPLES", "10"))
FACTOR_ADJUST_CAP = 0.04
TOTAL_ADJUST_CAP = 0.08
FACTOR_SCORE_PER_R = 0.035


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


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, float(v)))


def _as_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _as_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _decision_id(date: str, source: str, universe: str, ticker: str) -> str:
    return f"{date}:{_slug(source)}:{_slug(universe)}:{ticker}"


def pre_signal_factors(obj: dict) -> list[str]:
    """추천 시점에 이미 알 수 있는 조건 태그만 추출한다."""
    obj_dict = obj if isinstance(obj, dict) else {}
    f = obj_dict.get("features") or obj_dict
    tags: list[str] = []
    technical = str(f.get("technical_rating") or "")
    pivot_position = str(f.get("pivot_position") or "")
    win20 = _as_float(f.get("win_prob_20d"))
    win60 = _as_float(f.get("win_prob_60d"))
    reward_risk = _as_float(f.get("reward_risk") if "reward_risk" in f else obj_dict.get("reward_risk"))
    drawdown = _as_float(f.get("drawdown"))

    if "매도" in technical:
        tags.append("technical_conflict")
    if "매수" in technical:
        tags.append("technical_confirmed")
    if pivot_position in ("below_p", "below_s1"):
        tags.append("pivot_not_recovered")
    if pivot_position in ("above_p", "above_r1"):
        tags.append("pivot_confirmed")
    if _as_float(f.get("mom_20d")) < 0 and _as_float(f.get("mom_60d")) < 0:
        tags.append("falling_momentum")
    if _as_float(f.get("vix")) >= 28:
        tags.append("high_vix")
    if _as_int(f.get("n_similar")) < 20:
        tags.append("small_sample")
    if win60 < win20:
        tags.append("weak_60d_confirmation")
    if reward_risk >= 2.0:
        tags.append("strong_reward_risk")
    if win20 >= 0.65:
        tags.append("high_win_prob")
    if drawdown <= -0.30:
        tags.append("severe_drawdown")
    return tags


def _reward_risk(score) -> tuple[float, float]:
    risk = abs(score.downside_p25_20d) if score.downside_p25_20d < 0 else 0.0
    risk = max(risk, 0.03)
    reward = max(score.expected_ret_20d, 0.0)
    rr = reward / risk if risk > 0 else 0.0
    return round(risk, 5), round(rr, 4)


def _horizon_key(horizon: int | str) -> str:
    text = str(horizon).strip()
    return str(int(text)) if text.isdigit() else text.lower()


def _horizon_value(horizon: int | str) -> int | str:
    key = _horizon_key(horizon)
    return int(key) if key.isdigit() else key


def _evaluation_profile(horizon: int | str) -> str:
    return "short" if _horizon_key(horizon) in SHORT_HORIZONS else "swing"


def _session_for_decision(timestamp: str, market: str) -> str:
    text = str(timestamp or "")
    if "T" in text:
        try:
            hour = int(text.split("T", 1)[1][0:2])
            return "regular" if 9 <= hour < 16 else "extended"
        except (ValueError, IndexError):
            pass
    return "regular" if market == "KR" else "unknown"


def score_to_decision(score, *, source: str, universe: str, date: str | None = None,
                      snapshot_ts: str | None = None, session: str | None = None,
                      evaluation_profile: str = "daily", event_id: str | None = None,
                      event_type: str | None = None, model_version: str | None = None,
                      parameter_version: str | None = None, feature_version: str | None = None,
                      freshness_seconds: int | float | None = None) -> dict:
    """EntryScore를 학습용 point-in-time decision 레코드로 변환."""
    from ml.entry_analyzer import trade_level_values

    d = date or _today_kst()
    stamp = str(snapshot_ts or _now_kst())
    buy_lo, target, stop = trade_level_values(score)
    risk_pct, rr = _reward_risk(score)
    market = _market(score.ticker, score.currency)
    profile = str(evaluation_profile or "daily").strip().lower()
    current_session = str(session or _session_for_decision(stamp, market)).strip().lower()
    current_event_type = str(event_type or ("enter" if score.signal == "enter" else "signal"))
    if profile == "daily":
        decision_id = _decision_id(d, source, universe, score.ticker)
    else:
        decision_id = event_id or (
            f"{d}:{_slug(current_session)}:{_slug(source)}:{_slug(universe)}:{score.ticker}:{_slug(current_event_type)}"
        )
    metadata = {
        "model_version": model_version or os.getenv("ENTRY_MODEL_VERSION", "entry-v1"),
        "parameter_version": parameter_version or os.getenv("ENTRY_PARAMETER_VERSION", "default"),
        "feature_version": feature_version or os.getenv("ENTRY_FEATURE_VERSION", "default"),
        "evaluation_profile": profile,
        "session": current_session,
        "freshness_seconds": freshness_seconds,
    }
    return {
        "id": decision_id,
        "date": d,
        "snapshot_ts": stamp,
        "event_id": decision_id,
        "event_type": current_event_type,
        "evaluation_profile": profile,
        "session": current_session,
        "model_version": metadata["model_version"],
        "parameter_version": metadata["parameter_version"],
        "feature_version": metadata["feature_version"],
        "freshness_seconds": freshness_seconds,
        "metadata": metadata,
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
        "raw_score": float(score.raw_score) if score.raw_score is not None else float(score.score),
        "feedback_adjustment": float(score.feedback_adjustment or 0.0),
        "feedback_factors": list(score.feedback_factors or []),
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
            "reward_risk": rr,
        },
        "reasons": list(score.reasons or []),
    }


def record_entry_scores(scores: Iterable, *, source: str = "auto_watch",
                        universe: str = "watch", ledger=None,
                        evaluation_profile: str = "daily", session: str | None = None,
                        event_id: str | None = None, event_type: str | None = None,
                        model_version: str | None = None, parameter_version: str | None = None,
                        feature_version: str | None = None,
                        freshness_seconds: int | float | None = None) -> int:
    """분석된 EntryScore 전체를 일 1회/종목 단위로 불변 저장. 신규 기록 수 반환."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    existing = {d.get("id") for d in ledger.read_decisions()}
    added = 0
    for score in scores or []:
        rec = score_to_decision(
            score, source=source, universe=universe,
            evaluation_profile=evaluation_profile, session=session,
            event_id=event_id, event_type=event_type,
            model_version=model_version, parameter_version=parameter_version,
            feature_version=feature_version, freshness_seconds=freshness_seconds,
        )
        if rec["id"] not in existing:
            ledger.log_decision(rec)
            existing.add(rec["id"])
            added += 1
    if added:
        logger.info("진입 후보 스냅샷 저장: %d건 (%s/%s)", added, source, universe)
    return added


def _outcome_id(decision_id: str, horizon: int | str) -> str:
    return f"{decision_id}:h{_horizon_key(horizon)}"


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


def _elapsed_minutes(origin: pd.Timestamp, current: pd.Timestamp) -> int | None:
    try:
        if origin.tzinfo is not None and current.tzinfo is None:
            current = current.tz_localize(origin.tzinfo)
        elif origin.tzinfo is None and current.tzinfo is not None:
            current = current.tz_localize(None)
        return max(0, int(round((current - origin).total_seconds() / 60)))
    except (TypeError, ValueError, AttributeError):
        return None


def _first_touch_details(window: pd.DataFrame, target: float | None,
                         stop: float | None) -> tuple[str, str | None, float | None, int | None, int | None]:
    """목표/무효화선 경로와 각 레벨까지의 시간을 계산한다.

    같은 캔들에서 목표와 손절이 모두 닿으면 경로 판정은 보수적으로 stop
    우선으로 유지한다. 다만 백테스트 진단을 위해 목표·손절의 최초 터치
    시간은 각각 기록한다.
    """
    if window is None or len(window) <= 1:
        return "none", None, None, None, None
    origin = pd.Timestamp(window.index[0])
    path_result = "none"
    path_date = None
    path_price = None
    target_minutes = None
    stop_minutes = None
    for idx, row in window.iloc[1:].iterrows():
        hi = float(row.get("High", row.get("Close")))
        lo = float(row.get("Low", row.get("Close")))
        stamp = pd.Timestamp(idx)
        day = stamp.strftime("%Y-%m-%d")
        elapsed = _elapsed_minutes(origin, stamp)
        hit_stop = bool(stop and lo <= stop)
        hit_target = bool(target and hi >= target)
        if hit_stop and stop_minutes is None:
            stop_minutes = elapsed
        if hit_target and target_minutes is None:
            target_minutes = elapsed
        if path_result == "none" and (hit_stop or hit_target):
            # Intrabar 순서를 알 수 없으므로 같은 봉은 stop 우선이다.
            path_result = "stop" if hit_stop else "target"
            path_date = day
            path_price = float(stop if hit_stop else target)
    return path_result, path_date, path_price, target_minutes, stop_minutes


def _first_touch(window: pd.DataFrame, target: float | None, stop: float | None) -> tuple[str, str | None, float | None]:
    """목표/무효화선 첫 터치의 기존 3개 값 호환 래퍼."""
    return _first_touch_details(window, target, stop)[:3]


def _intraday_steps(horizon: int | str) -> int | None:
    return SHORT_HORIZONS.get(_horizon_key(horizon))


def _swing_steps(horizon: int | str) -> int:
    key = _horizon_key(horizon)
    return SWING_HORIZONS.get(key, int(key) if key.isdigit() else 20)


def _intraday_symbol(symbol: str, market: str) -> str:
    """intraday provider용 심볼 정규화. 지수·매크로 심볼은 suffix를 붙이지 않는다."""
    symbol = str(symbol or "").strip()
    if str(market or "").upper() != "KR":
        return symbol
    if symbol.startswith("^") or symbol.endswith((".KS", ".KQ", "=X", "-USD")):
        return symbol
    return f"{symbol}.KS"


def _intraday_frame(symbol: str, date: str, *, market: str) -> pd.DataFrame:
    """자체 1분봉을 우선 사용하고 없을 때만 기존 공급자 fetch를 시도한다."""
    try:
        from providers.intraday_bars import load_bars

        frame = load_bars(symbol, date, interval="1m", session="all")
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            return frame
    except Exception:
        pass
    try:
        from ml.intraday_signal import fetch_intraday
        ticker = _intraday_symbol(symbol, market)
        frame = fetch_intraday(ticker, interval="1m", days=7)
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _parse_snapshot_timestamp(value: object):
    text = str(value or "").strip()
    if text.endswith(" KST"):
        text = text[:-4] + "+09:00"
    try:
        return pd.Timestamp(text) if text else None
    except (TypeError, ValueError):
        return None


def _default_intraday_result(decision: dict, horizon: int | str) -> dict | None:
    steps = _intraday_steps(horizon)
    if steps is None:
        return None
    ticker = str(decision.get("ticker") or "")
    benchmark = str(decision.get("benchmark") or _benchmark(decision))
    market = str(decision.get("market") or _market(ticker)).upper()
    date = str(decision.get("date") or _today_kst())[:10]
    df = _intraday_frame(ticker, date, market=market)
    bm = _intraday_frame(benchmark, date, market="US" if market != "KR" else "KR")
    if df.empty or bm.empty:
        return None
    df = df.sort_index()
    bm = bm.sort_index()
    snapshot = _parse_snapshot_timestamp(decision.get("snapshot_ts"))
    def after_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
        if snapshot is None:
            return frame
        start = snapshot
        try:
            if getattr(start, "tzinfo", None) is None and getattr(frame.index, "tz", None) is not None:
                start = start.tz_localize(frame.index.tz)
            elif getattr(start, "tzinfo", None) is not None and getattr(frame.index, "tz", None) is None:
                start = start.tz_localize(None)
            return frame[frame.index >= start]
        except (TypeError, ValueError):
            return frame.iloc[0:0]
    df = after_snapshot(df)
    bm = after_snapshot(bm)
    if len(df) <= steps or len(bm) <= steps:
        return None
    window, bwindow = df.iloc[:steps + 1], bm.iloc[:steps + 1]
    entry, exit_ = float(window["Close"].iloc[0]), float(window["Close"].iloc[-1])
    bentry, bexit = float(bwindow["Close"].iloc[0]), float(bwindow["Close"].iloc[-1])
    target = _as_float(decision.get("target_price"), 0.0) or None
    stop = _as_float(decision.get("stop_price"), 0.0) or None
    path_result, path_date, path_price, time_to_target, time_to_stop = _first_touch_details(
        window, target, stop
    )
    stock_prices = [float(x) for x in window["Close"].tolist()]
    return {
        "entry_date": pd.Timestamp(window.index[0]).isoformat(),
        "exit_date": pd.Timestamp(window.index[-1]).isoformat(),
        "entry_price_actual": entry, "exit_price": exit_,
        "benchmark_entry": bentry, "benchmark_exit": bexit,
        "stock_ret": exit_ / entry - 1.0 if entry > 0 else 0.0,
        "benchmark_ret": bexit / bentry - 1.0 if bentry > 0 else 0.0,
        "fwd_mdd": _max_drawdown(stock_prices),
        "idx_fwd_mdd": _max_drawdown([float(x) for x in bwindow["Close"].tolist()]),
        "mfe": max(stock_prices) / entry - 1.0 if entry > 0 else 0.0,
        "mae": min(stock_prices) / entry - 1.0 if entry > 0 else 0.0,
        "path_result": path_result, "path_date": path_date, "path_price": path_price,
        "time_to_target": time_to_target, "time_to_stop": time_to_stop,
        "time_to_target_minutes": time_to_target, "time_to_stop_minutes": time_to_stop,
    }


def _default_price_result(decision: dict, horizon: int | str) -> dict | None:
    """결정일 이후 market-aware horizon 수익률/경로 결과 계산."""
    if _intraday_steps(horizon) is not None:
        return _default_intraday_result(decision, horizon)
    from ml.data_pipeline import fetch_prices

    ticker = decision.get("ticker")
    benchmark = decision.get("benchmark") or _benchmark(decision)
    if not ticker:
        return None
    steps = _swing_steps(horizon)
    prices = fetch_prices([ticker, benchmark], days=max(756, steps * 4 + 80))
    df = prices.get(ticker)
    bm = prices.get(benchmark)
    if df is None or bm is None or len(df) <= steps or len(bm) <= steps:
        return None

    start = pd.Timestamp(decision.get("date"))
    df = df.sort_index()
    bm = bm.sort_index()
    fut = df[df.index >= start]
    bfut = bm[bm.index >= start]
    if len(fut) <= steps or len(bfut) <= steps:
        return None

    window = fut.iloc[:steps + 1]
    bwindow = bfut.iloc[:steps + 1]
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
    path_result, path_date, path_price, time_to_target, time_to_stop = _first_touch_details(
        window, target, stop
    )
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
        "mfe": max(float(x) for x in window["Close"].tolist()) / entry - 1.0 if entry > 0 else 0.0,
        "mae": min(float(x) for x in window["Close"].tolist()) / entry - 1.0 if entry > 0 else 0.0,
        "path_result": path_result,
        "path_date": path_date,
        "path_price": path_price,
        "time_to_target": time_to_target,
        "time_to_stop": time_to_stop,
        "time_to_target_minutes": time_to_target,
        "time_to_stop_minutes": time_to_stop,
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


def _is_pending_result(result: object) -> bool:
    return not isinstance(result, dict) or str(result.get("status") or "").lower() in {
        "pending", "quality_error", "stale", "unavailable"
    }


def build_outcome(decision: dict, horizon: int | str, result: dict) -> dict:
    """Build one immutable outcome row; return a pending row for bad data."""
    if _is_pending_result(result) or any(
        result.get(key) is None for key in ("stock_ret", "benchmark_ret", "entry_price_actual", "exit_price")
    ):
        return {
            "decision_id": _outcome_id(decision.get("id", "unknown"), horizon),
            "base_decision_id": decision.get("id"), "ticker": decision.get("ticker"),
            "horizon": _horizon_value(horizon), "status": "pending",
            "quality_reason": (result or {}).get("status") if isinstance(result, dict) else "missing_price_data",
        }
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
    horizon_key = _horizon_value(horizon)
    direction_up = str(decision.get("signal") or "enter").lower() != "avoid"
    direction_hit = stock_ret > 0 if direction_up else stock_ret <= 0
    fee_rate = _as_float(result.get("fee_rate"), _as_float(os.getenv("ENTRY_FEES_RATE"), 0.0005))
    slippage_rate = _as_float(result.get("slippage_rate"), _as_float(os.getenv("ENTRY_SLIPPAGE_RATE"), 0.0005))
    gross_ret = stock_ret
    net_ret = gross_ret - fee_rate - slippage_rate
    mfe = result.get("mfe")
    mae = result.get("mae")
    if mfe is None:
        mfe = max(stock_ret, 0.0)
    if mae is None:
        mae = min(stock_ret, 0.0)
    path_result = result.get("path_result") or "none"
    outcome = {
        "decision_id": _outcome_id(decision["id"], horizon),
        "base_decision_id": decision["id"],
        "ticker": decision.get("ticker"),
        "horizon": horizon_key,
        "evaluation_profile": decision.get("evaluation_profile") or _evaluation_profile(horizon),
        "market": decision.get("market") or _market(str(decision.get("ticker") or "")),
        "status": "matured",
        "matured_at": _today_kst(),
        "entry_date": result.get("entry_date"),
        "exit_date": result.get("exit_date"),
        "entry_price_actual": round(float(result["entry_price_actual"]), 4),
        "exit_price": round(float(result["exit_price"]), 4),
        "fwd_ret": round(stock_ret, 5),
        "benchmark": decision.get("benchmark") or _benchmark(decision),
        "benchmark_ret": round(bench_ret, 5),
        "fwd_excess": round(excess, 5),
        "stock_ret": round(stock_ret, 5),
        "excess_ret": round(excess, 5),
        "direction_hit": direction_hit,
        "fwd_mdd": round(float(result.get("fwd_mdd") or 0), 5),
        "idx_fwd_mdd": round(float(result.get("idx_fwd_mdd") or 0), 5),
        "path_result": path_result,
        "target_first": path_result == "target",
        "stop_first": path_result == "stop",
        "path_date": result.get("path_date"),
        "path_price": result.get("path_price"),
        "mfe": round(_as_float(mfe), 5),
        "mae": round(_as_float(mae), 5),
        "time_to_target": result.get("time_to_target") or result.get("time_to_target_minutes"),
        "time_to_stop": result.get("time_to_stop") or result.get("time_to_stop_minutes"),
        "time_to_target_minutes": result.get("time_to_target_minutes"),
        "time_to_stop_minutes": result.get("time_to_stop_minutes"),
        "fee_rate": round(fee_rate, 6),
        "slippage_rate": round(slippage_rate, 6),
        "gross_ret": round(gross_ret, 5),
        "net_ret": round(net_ret, 5),
        "r_multiple": round(r_multiple, 3),
        "success": success,
    }
    primary, tags, note = _diagnose(decision, outcome)
    outcome.update({"diagnosis": primary, "factor_tags": tags, "learn_note": note})
    return outcome


def backfill_outcomes(*, ledger=None, horizons: tuple[int | str, ...] = HORIZONS,
                      price_fn=None) -> int:
    """성숙한 추천 후보 outcome을 append-only 백필. 신규 outcome 수 반환."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    use_default_price_fn = price_fn is None
    price_fn = price_fn or _default_price_result
    done = {o.get("decision_id") for o in ledger.read_outcomes()}
    added = 0
    for decision in ledger.read_decisions():
        if not decision.get("id") or not decision.get("ticker"):
            continue
        for horizon in horizons:
            if use_default_price_fn and _intraday_steps(horizon) is not None and str(
                decision.get("evaluation_profile") or "daily"
            ).lower() not in {"short", "intraday"}:
                continue
            oid = _outcome_id(decision["id"], horizon)
            if oid in done:
                continue
            result = price_fn(decision, horizon)
            if _is_pending_result(result):
                continue
            outcome = build_outcome(decision, horizon, result)
            if outcome.get("status") != "matured":
                continue
            ledger.log_outcome(outcome)
            done.add(oid)
            added += 1
    if added:
        logger.info("진입 후보 outcome 백필: %d건", added)
    return added


def training_rows(*, ledger=None, horizon: int | str = 20) -> list[dict]:
    """decision + horizon별 outcome 조인."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    decisions = {d.get("id"): d for d in ledger.read_decisions() if d.get("id")}
    rows = []
    suffix = f":h{_horizon_key(horizon)}"
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


def summarize_feedback(rows: list[dict] | None = None, *, horizon: int | str = 20) -> dict:
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
        "avg_stock_ret": round(sum(float(r.get("stock_ret", r.get("fwd_ret")) or 0) for r in rows) / len(rows), 4),
        "avg_net_ret": round(sum(float(r.get("net_ret", r.get("fwd_ret")) or 0) for r in rows) / len(rows), 4),
        "direction_hit_rate": round(sum(bool(r.get("direction_hit", r.get("success"))) for r in rows) / len(rows), 3),
        "avg_mfe": round(sum(float(r.get("mfe") or 0) for r in rows) / len(rows), 4),
        "avg_mae": round(sum(float(r.get("mae") or 0) for r in rows) / len(rows), 4),
        "path_counts": dict(Counter(str(r.get("path_result") or "none") for r in rows)),
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
    "strong_reward_risk": "손익비 강함",
    "high_win_prob": "승률 강함",
    "severe_drawdown": "대폭 하락",
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


def _fit_factor_adjustments(rows: list[dict]) -> dict[str, float]:
    if len(rows) < MIN_FACTOR_SAMPLES:
        return {}
    base_r = sum(_as_float(r.get("r_multiple")) for r in rows) / len(rows)
    factor_rows: dict[str, list[dict]] = {}
    for row in rows:
        for tag in pre_signal_factors(row):
            factor_rows.setdefault(tag, []).append(row)

    adjustments: dict[str, float] = {}
    for tag, tagged in factor_rows.items():
        if len(tagged) < MIN_FACTOR_SAMPLES:
            continue
        tag_r = sum(_as_float(r.get("r_multiple")) for r in tagged) / len(tagged)
        adj = _clamp((tag_r - base_r) * FACTOR_SCORE_PER_R, -FACTOR_ADJUST_CAP, FACTOR_ADJUST_CAP)
        if abs(adj) >= 0.005:
            adjustments[tag] = round(adj, 4)
    return adjustments


def _score_with_adjustment(row: dict, adjustments: dict[str, float]) -> tuple[float, float, list[str]]:
    score = _as_float(row.get("score"))
    tags = pre_signal_factors(row)
    raw_adj = sum(float(adjustments.get(tag, 0.0)) for tag in tags)
    adj = _clamp(raw_adj, -TOTAL_ADJUST_CAP, TOTAL_ADJUST_CAP)
    return round(_clamp(score + adj, 0.0, 1.0), 4), round(adj, 4), tags


def _realized_excess(row: dict) -> float:
    """OOS 비교 지표. 새 원장은 초과수익을 쓰고 legacy 행은 R로 호환한다."""
    for key in ("fwd_excess", "excess_ret", "r_multiple"):
        if row.get(key) is not None:
            return _as_float(row.get(key))
    return 0.0


def _eval_adjustments(rows: list[dict], adjustments: dict[str, float], threshold: float) -> dict:
    selected = []
    for row in rows:
        adj_score, _, _ = _score_with_adjustment(row, adjustments)
        if adj_score >= threshold:
            selected.append(row)
    if not selected:
        return {"excess": 0.0, "mdd": 0.0, "avg_loss": 0.0, "n": 0, "win_rate": 0.0}
    excess_values = [_realized_excess(r) for r in selected]
    wins = sum(1 for r in selected if r.get("success"))
    losses = [value for value in excess_values if value < 0]
    mdd_values = [
        _as_float(row.get("fwd_mdd"), abs(value) if value < 0 else 0.0)
        for row, value in zip(selected, excess_values)
    ]
    return {
        "excess": round(sum(excess_values) / len(excess_values), 4),
        "mdd": round(max([0.0] + mdd_values), 4),
        "avg_loss": round(abs(sum(losses) / len(losses)), 4) if losses else 0.0,
        "n": len(selected),
        "win_rate": round(wins / len(selected), 4),
    }


def _oos_constraints_ok(challenger: dict, champion: dict | None) -> bool:
    """절대 양의 초과수익과 champion 대비 MDD/평균손실 제약을 검사한다."""
    if not challenger:
        return False
    if _as_float(challenger.get("excess")) <= 0:
        return False
    champion = champion or {}
    champion_mdd = max(0.0, _as_float(champion.get("mdd")))
    champion_avg_loss = max(0.0, _as_float(champion.get("avg_loss")))
    if _as_float(challenger.get("mdd")) > champion_mdd:
        return False
    if _as_float(challenger.get("avg_loss")) > champion_avg_loss:
        return False
    return True


def load_feedback_adjustments(path: Path | str | None = None) -> dict:
    model_path = Path(path or ADJUST_PATH)
    try:
        if not model_path.exists():
            return {"adjustments": {}, "meta": {"status": "missing"}}
        model = json.loads(model_path.read_text())
        if not isinstance(model, dict):
            return {"adjustments": {}, "meta": {"status": "invalid"}}
        adjustments = model.get("adjustments") or {}
        if not isinstance(adjustments, dict):
            adjustments = {}
        model["adjustments"] = {
            str(k): _clamp(_as_float(v), -FACTOR_ADJUST_CAP, FACTOR_ADJUST_CAP)
            for k, v in adjustments.items()
        }
        return model
    except Exception as e:
        logger.warning("진입 추천 보정 모델 로드 실패: %s", e)
        return {"adjustments": {}, "meta": {"status": "load_failed", "error": str(e)}}


def apply_score_adjustment(base_score: float, context: dict, *,
                           path: Path | str | None = None,
                           enabled: bool | None = None) -> tuple[float, float, list[str]]:
    """저장된 사후성과 보정치를 현재 추천 점수에 보수적으로 반영."""
    if enabled is None:
        enabled = os.getenv("ENTRY_FEEDBACK_ADJUST_ENABLED", "true").lower() == "true"
    tags = pre_signal_factors(context)
    if not enabled:
        return round(_clamp(base_score, 0.0, 1.0), 4), 0.0, tags
    model = load_feedback_adjustments(path)
    adjustments = model.get("adjustments") or {}
    adj = sum(float(adjustments.get(tag, 0.0)) for tag in tags)
    adj = _clamp(adj, -TOTAL_ADJUST_CAP, TOTAL_ADJUST_CAP)
    return round(_clamp(_as_float(base_score) + adj, 0.0, 1.0), 4), round(adj, 4), tags


def learn_feedback_adjustments(rows: list[dict] | None = None, *,
                               horizon: int = 20,
                               save: bool = True,
                               path: Path | str | None = None) -> dict:
    """사후 성과를 이용해 조건별 점수 보정치를 학습하고, 검증 통과 시 저장."""
    rows = list(rows if rows is not None else training_rows(horizon=horizon))
    rows = [r for r in rows if r.get("r_multiple") is not None]
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("ticker") or "")))
    if len(rows) < MIN_ADJUST_SAMPLES:
        return {
            "adopted": False,
            "reason": f"표본 부족({len(rows)}/{MIN_ADJUST_SAMPLES})",
            "horizon": horizon,
            "n": len(rows),
            "adjustments": {},
        }

    split = max(MIN_FACTOR_SAMPLES, int(len(rows) * 0.6))
    if len(rows) - split < max(5, MIN_FACTOR_SAMPLES // 2):
        split = max(1, len(rows) // 2)
    train = rows[:split]
    oos = rows[split:]
    if len(oos) < MIN_OOS_SAMPLES:
        return {
            "adopted": False,
            "reason": f"OOS 표본 부족({len(oos)}/{MIN_OOS_SAMPLES})",
            "horizon": horizon,
            "n": len(rows),
            "train_n": len(train),
            "oos_n": len(oos),
            "adjustments": {},
        }
    adjustments = _fit_factor_adjustments(train)
    if not adjustments:
        return {
            "adopted": False,
            "reason": "유효한 조건별 보정치 없음",
            "horizon": horizon,
            "n": len(rows),
            "train_n": len(train),
            "oos_n": len(oos),
            "adjustments": {},
        }

    try:
        from ml.entry_analyzer import get_score_params
        threshold = float(get_score_params().get("enter_threshold", 0.62))
    except Exception:
        threshold = 0.62

    champion = _eval_adjustments(oos, {}, threshold)
    challenger = _eval_adjustments(oos, adjustments, threshold)

    try:
        from ml.adaptive.reward import should_adopt
        index_mdd = float(champion.get("mdd") or 0.0)
        min_samples = max(5, min(MIN_FACTOR_SAMPLES, len(oos)))
        # MIN_OOS_SAMPLES applies to the held-out time window. A stricter
        # per-threshold selected-count gate would reject a valid OOS window
        # simply because the challenger is intentionally selective.
        adopted = should_adopt(challenger, champion, index_mdd=index_mdd, min_samples=min_samples)
    except Exception:
        adopted = (
            challenger.get("n", 0) >= MIN_OOS_SAMPLES
            and challenger.get("excess", 0.0) > 0
            and challenger.get("excess", 0.0) > champion.get("excess", 0.0)
            and challenger.get("mdd", 0.0) <= max(champion.get("mdd", 0.0) * 1.25, 1.0)
        )
    # Keep drawdown and average-loss constraints explicit even if the shared
    # reward gate changes implementation details later.
    if adopted and not _oos_constraints_ok(challenger, champion):
        adopted = False

    result = {
        "adopted": bool(adopted),
        "reason": "검증 통과" if adopted else "검증 구간에서 기존 점수 대비 개선 부족",
        "horizon": horizon,
        "n": len(rows),
        "train_n": len(train),
        "oos_n": len(oos),
        "threshold": round(threshold, 4),
        "adjustments": adjustments,
        "champion": champion,
        "challenger": challenger,
    }
    if adopted and save:
        model_path = Path(path or ADJUST_PATH)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "policy_version": f"entry-factor-{_now_kst().replace(':', '').replace('+', '-')}",
            "learned_at": _now_kst(),
            "horizon": _horizon_value(horizon),
            "adjustments": adjustments,
            "meta": {
                "n": len(rows),
                "train_n": len(train),
                "oos_n": len(oos),
                "threshold": round(threshold, 4),
                "champion": champion,
                "challenger": challenger,
                "total_adjust_cap": TOTAL_ADJUST_CAP,
                "factor_adjust_cap": FACTOR_ADJUST_CAP,
            },
        }
        model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        result["path"] = str(model_path)
        logger.info("진입 추천 성과 보정 모델 저장: %s", model_path)
    return result
