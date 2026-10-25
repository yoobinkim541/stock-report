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

from ml.recommendation_contracts import (
    canonical_horizon,
    canonical_outcome_key,
    dedupe_canonical_outcomes,
    finite_json_value,
)

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
# 보정 학습의 시도 이력 표면(ml.adaptive.evolution learning jsonl) — 다중시도 보정(DSR)·시도 지문 근거.
TRIAL_SURFACE = "entry_feedback"


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
    return canonical_horizon(horizon)


def _horizon_value(horizon: int | str) -> int | str:
    return _horizon_key(horizon)


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
                      freshness_seconds: int | float | None = None,
                      selection_status: str | None = None, selection_rank: int | None = None,
                      data_quality: str | None = None, source_coverage: dict | None = None,
                      evidence_refs: list | None = None) -> dict:
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
        "selection_status": selection_status or ("selected" if score.signal == "enter" else "shadow"),
        "selection_rank": selection_rank,
        "data_quality": data_quality or "degraded:source_metadata_not_loaded",
        "source_coverage": source_coverage,
        "evidence_refs": evidence_refs or [],
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
        "selection_status": metadata["selection_status"],
        "selection_rank": selection_rank,
        "data_quality": metadata["data_quality"],
        "source_coverage": source_coverage,
        "evidence_refs": evidence_refs or [],
        "horizon": "1h" if profile in {"short", "intraday"} else "20d",
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
    records = []
    for score in scores or []:
        rec = score_to_decision(
            score, source=source, universe=universe,
            evaluation_profile=evaluation_profile, session=session,
            event_id=event_id, event_type=event_type,
            model_version=model_version, parameter_version=parameter_version,
            feature_version=feature_version, freshness_seconds=freshness_seconds,
        )
        records.append(rec)
    from ml.recommendation_surfaces import snapshot_candidates, decision_metadata

    snapshots = snapshot_candidates(
        SURFACE, records, as_of=records[0]["snapshot_ts"] if records else None,
        selected_tickers={rec["ticker"] for rec in records if rec["signal"] == "enter"},
    )
    for rec, snapshot in zip(records, snapshots):
        rec.update(decision_metadata(snapshot))
        rec["metadata"].update(decision_metadata(snapshot))
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


# ── outcome 시간창: 종목·벤치마크를 같은 시작/종료 시각으로 평가 ─────────────────
# 창 안 기대 봉수(1분봉=경과분+1, 일봉=거래일+1) 대비 실제 봉 비율 하한(종목·벤치마크 각각).
MIN_BAR_COVERAGE = 0.5
# 단기 창의 종료 시각+유예가 지나도 창이 채워지지 않으면(장 경계·결측) invalid. 그 전엔 pending.
INTRADAY_GRACE = pd.Timedelta(days=1)
_MARKET_TZ = {"KR": "Asia/Seoul", "US": "America/New_York"}


def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _invalid_window(reason: str) -> dict:
    """평가 불가가 확정된 창 — build_outcome 이 invalid outcome 으로 남기고 학습에서 제외한다."""
    return {"status": "invalid", "quality_reason": reason, "path_result": "none"}


def _clean_bars(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out.index = pd.DatetimeIndex(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[pd.to_numeric(out["Close"], errors="coerce").notna()]


def _window_payload(window: pd.DataFrame, bwindow: pd.DataFrame, decision: dict, *,
                    stamp_format: str | None) -> dict:
    entry, exit_ = float(window["Close"].iloc[0]), float(window["Close"].iloc[-1])
    bentry, bexit = float(bwindow["Close"].iloc[0]), float(bwindow["Close"].iloc[-1])
    target = decision.get("target_price")
    stop = decision.get("stop_price")
    try:
        target = float(target) if target is not None else None
        stop = float(stop) if stop is not None else None
    except (TypeError, ValueError):
        target, stop = None, None
    path_result, path_date, path_price, time_to_target, time_to_stop = _first_touch_details(
        window, target or None, stop or None
    )
    stock_prices = [float(x) for x in window["Close"].tolist()]

    def fmt(value) -> str:
        stamp = pd.Timestamp(value)
        return stamp.strftime(stamp_format) if stamp_format else stamp.isoformat()

    return {
        "entry_date": fmt(window.index[0]),
        "exit_date": fmt(window.index[-1]),
        "benchmark_entry_date": fmt(bwindow.index[0]),
        "benchmark_exit_date": fmt(bwindow.index[-1]),
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


def _default_intraday_result(decision: dict, horizon: int | str) -> dict | None:
    """단기 horizon = **경과 시간**(분). 종목·벤치마크는 같은 시작/종료 timestamp 로만 비교한다.

    - 시작: snapshot 이후 두 시계열이 모두 가진 첫 봉(종목 첫 봉과 허용오차 이내여야 함).
    - 종료: 시작+horizon 분에서 허용오차(horizon 10%, 최소 1분) 안의 마지막 공통 봉.
    - 창이 아직 안 채워짐 → pending(None). 종료+INTRADAY_GRACE 후에도 미충족·종료봉 결측·
      봉 coverage 부족·시작봉 불일치 → invalid. 봉 개수로 horizon 을 세지 않는다.
    """
    steps = _intraday_steps(horizon)
    if steps is None:
        return None
    ticker = str(decision.get("ticker") or "")
    benchmark = str(decision.get("benchmark") or _benchmark(decision))
    market = str(decision.get("market") or _market(ticker)).upper()
    date = str(decision.get("date") or _today_kst())[:10]
    df = _clean_bars(_intraday_frame(ticker, date, market=market))
    bm = _clean_bars(_intraday_frame(benchmark, date, market="US" if market != "KR" else "KR"))
    if df.empty or bm.empty:
        return None
    tz = df.index.tz or _MARKET_TZ.get(market, "UTC")
    snapshot = _parse_snapshot_timestamp(decision.get("snapshot_ts"))
    try:
        df.index = df.index.tz_localize(tz) if df.index.tz is None else df.index.tz_convert(tz)
        bm.index = bm.index.tz_localize(tz) if bm.index.tz is None else bm.index.tz_convert(tz)
        if snapshot is not None:
            snapshot = snapshot.tz_localize(tz) if snapshot.tzinfo is None else snapshot.tz_convert(tz)
    except (TypeError, ValueError):
        return _invalid_window("timezone_unresolved")
    if snapshot is not None:
        df = df[df.index >= snapshot]
        bm = bm[bm.index >= snapshot]
    span = pd.Timedelta(minutes=steps)
    tolerance = pd.Timedelta(minutes=max(1, round(steps * 0.1)))

    def not_ready(reason: str, expected_end) -> dict | None:
        if expected_end is not None and _now_utc() >= pd.Timestamp(expected_end).tz_convert("UTC") + INTRADAY_GRACE:
            return _invalid_window(reason)
        return None

    origin = snapshot if snapshot is not None else (df.index[0] if not df.empty else None)
    if df.empty or bm.empty:
        return not_ready("no_bars_after_snapshot", None if origin is None else origin + span)
    common = df.index.intersection(bm.index)
    if common.empty:
        return not_ready("no_common_bars", df.index[0] + span)
    anchor = common[0]
    if anchor - df.index[0] > tolerance:
        return _invalid_window("entry_bar_not_aligned")
    end_target = anchor + span
    exits = common[(common >= end_target - tolerance) & (common <= end_target)]
    if exits.empty:
        if df.index[-1] < end_target or bm.index[-1] < end_target:
            return not_ready("window_not_covered", end_target)
        return _invalid_window("exit_bar_missing")
    exit_at = exits[-1]
    window = df.loc[anchor:exit_at]
    bwindow = bm.loc[anchor:exit_at]
    expected = int(round((exit_at - anchor).total_seconds() / 60)) + 1
    coverage = min(len(window), len(bwindow)) / max(expected, 1)
    if coverage < MIN_BAR_COVERAGE:
        return _invalid_window("insufficient_bar_coverage")
    out = _window_payload(window, bwindow, decision, stamp_format=None)
    out["bar_coverage"] = round(coverage, 4)
    return out


def _daily_bars(frame: pd.DataFrame | None, start: pd.Timestamp) -> pd.DataFrame:
    """일봉을 tz 제거·날짜 정규화(벽시계 날짜 보존)한 뒤 start 이후만."""
    out = _clean_bars(frame)
    if out.empty:
        return out
    idx = out.index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")]
    return out[out.index >= start]


def _default_price_result(decision: dict, horizon: int | str) -> dict | None:
    """결정일 이후 market-aware horizon 수익률/경로 결과 계산.

    종목·벤치마크 관측일의 합집합을 거래일 시계로 쓰고 같은 시작일/종료일(시계상 horizon
    거래일 뒤)로만 비교한다. 데이터가 아직 그 날까지 없으면 pending, 한쪽이 그 날 이후
    관측은 있는데 시작/종료일 봉이 없으면(거래정지·결측) invalid, coverage 부족도 invalid.
    """
    if _intraday_steps(horizon) is not None:
        return _default_intraday_result(decision, horizon)
    from ml.data_pipeline import fetch_prices

    ticker = decision.get("ticker")
    benchmark = decision.get("benchmark") or _benchmark(decision)
    if not ticker:
        return None
    steps = _swing_steps(horizon)
    prices = fetch_prices([ticker, benchmark], days=max(756, steps * 4 + 80))
    try:
        start = pd.Timestamp(str(decision.get("date") or "")[:10])
    except (TypeError, ValueError):
        return None
    df = _daily_bars(prices.get(ticker), start)
    bm = _daily_bars(prices.get(benchmark), start)
    if df.empty or bm.empty:
        return None
    calendar = df.index.union(bm.index)
    if len(calendar) <= steps:
        return None
    anchor, end = calendar[0], calendar[steps]
    for frame in (df, bm):
        if anchor not in frame.index:
            return _invalid_window("start_bar_missing")
        if end not in frame.index:
            return None if frame.index[-1] < end else _invalid_window("end_bar_missing")
    window = df.loc[anchor:end]
    bwindow = bm.loc[anchor:end]
    coverage = min(len(window), len(bwindow)) / (steps + 1)
    if coverage < MIN_BAR_COVERAGE:
        return _invalid_window("insufficient_bar_coverage")
    out = _window_payload(window, bwindow, decision, stamp_format="%Y-%m-%d")
    out["bar_coverage"] = round(coverage, 4)
    return out


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
    """Keep immature data pending and retain corrupt evaluable data as invalid."""
    if _is_pending_result(result):
        return {
            "decision_id": _outcome_id(decision.get("id", "unknown"), horizon),
            "base_decision_id": decision.get("id"), "ticker": decision.get("ticker"),
            "horizon": _horizon_value(horizon), "status": "pending",
            "quality_reason": (result or {}).get("status") if isinstance(result, dict) else "missing_price_data",
        }
    window_reason = result.get("quality_reason") if result.get("status") == "invalid" else None
    invalid = [] if window_reason else _invalid_outcome_fields(result, returns=("stock_ret", "benchmark_ret"))
    if result.get("status") == "invalid" and not window_reason:
        invalid.append("status")
    if invalid or window_reason:
        return finite_json_value({
            **result, "decision_id": _outcome_id(decision.get("id", "unknown"), horizon),
            "base_decision_id": decision.get("id"), "ticker": decision.get("ticker"),
            "horizon": _horizon_value(horizon), "status": "invalid", "success": None,
            "fwd_ret": result.get("stock_ret"), "fwd_excess": None,
            "quality_reason": window_reason or "invalid_fields:" + ",".join(invalid),
            "diagnosis": "invalid_outcome_window" if window_reason else "invalid_price_or_path_data",
        })
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
        **{key: result[key] for key in ("benchmark_entry", "benchmark_exit") if key in result},
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
    return finite_json_value(outcome)


def _invalid_outcome_fields(row, *, returns):
    invalid = []
    for key in (*returns, "entry_price_actual", "exit_price"):
        value = _as_float(row.get(key), float("nan"))
        if isinstance(row.get(key), bool) or not math.isfinite(value) or (
            key in {"entry_price_actual", "exit_price"} and value <= 0
        ):
            invalid.append(key)
    for key in ("benchmark_entry", "benchmark_exit"):
        if key in row:
            value = _as_float(row.get(key), float("nan"))
            if isinstance(row.get(key), bool) or not math.isfinite(value) or value <= 0:
                invalid.append(key)
    if row.get("path_result") not in {"none", "target", "stop"}:
        invalid.append("path_result")
    return invalid


def backfill_outcomes(*, ledger=None, horizons: tuple[int | str, ...] = HORIZONS,
                      price_fn=None) -> int:
    """성숙한 추천 후보 outcome을 append-only 백필. 신규 outcome 수 반환."""
    from ml.adaptive import Ledger

    ledger = ledger or Ledger(SURFACE)
    use_default_price_fn = price_fn is None
    price_fn = price_fn or _default_price_result
    done = {
        canonical_outcome_key(
            o.get("base_decision_id") or _base_decision_id(str(o.get("decision_id") or "")),
            o.get("horizon") or (
                str(o.get("decision_id") or "").rsplit(":h", 1)[1]
                if ":h" in str(o.get("decision_id") or "") else ""
            ),
        )
        for o in ledger.read_outcomes()
    }
    added = 0
    for decision in ledger.read_decisions():
        if not decision.get("id") or not decision.get("ticker"):
            continue
        for horizon in horizons:
            if use_default_price_fn and _intraday_steps(horizon) is not None and str(
                decision.get("evaluation_profile") or "daily"
            ).lower() not in {"short", "intraday"}:
                continue
            outcome_key = canonical_outcome_key(decision["id"], horizon)
            if outcome_key in done:
                continue
            result = price_fn(decision, horizon)
            if _is_pending_result(result):
                continue
            outcome = build_outcome(decision, horizon, result)
            if outcome.get("status") not in {"matured", "invalid"}:
                continue
            ledger.log_outcome(outcome)
            done.add(outcome_key)
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
    target_horizon = _horizon_key(horizon)
    outcomes = dedupe_canonical_outcomes(ledger.read_outcomes())
    for outcome in outcomes:
        if (outcome.get("status") not in {None, "matured"}
                or not isinstance(outcome.get("success"), bool)
                or _invalid_outcome_fields(outcome, returns=("fwd_ret", "fwd_excess", "benchmark_ret"))):
            continue
        oid = str(outcome.get("decision_id") or "")
        base_id = outcome.get("base_decision_id") or _base_decision_id(oid)
        outcome_horizon = outcome.get("horizon")
        if outcome_horizon is None and ":h" in oid:
            outcome_horizon = oid.rsplit(":h", 1)[1]
        if canonical_outcome_key(base_id, outcome_horizon or "")[1] != target_horizon:
            continue
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
    def finite_metric(row: dict, *keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
        return None

    def average(*keys: str) -> float:
        values = [value for row in rows if (value := finite_metric(row, *keys)) is not None]
        return sum(values) / len(values) if values else 0.0

    return {
        "horizon": horizon,
        "n": len(rows),
        "success_rate": round(len(wins) / len(rows), 3),
        "avg_excess": round(average("fwd_excess", "excess_ret"), 4),
        "avg_r": round(average("r_multiple"), 3),
        "avg_stock_ret": round(average("stock_ret", "fwd_ret"), 4),
        "avg_net_ret": round(average("net_ret", "fwd_ret"), 4),
        "direction_hit_rate": round(sum(bool(r.get("direction_hit", r.get("success"))) for r in rows) / len(rows), 3),
        "avg_mfe": round(average("mfe"), 4),
        "avg_mae": round(average("mae"), 4),
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


def _label_window(row: dict) -> tuple[str, str | None]:
    """라벨 구간(진입 거래일, 종료 거래일|None). 종료일이 없으면 기간 미상(독립 증거로 못 씀)."""
    start = str(row.get("entry_date") or row.get("date") or "")[:10]
    end = str(row.get("exit_date") or "")[:10] or None
    return start, end


def _nonoverlapping_series(selected: list[dict]) -> dict:
    """선택 행 → 결정일 바스켓 → 실제 라벨 종료일 기준 비중첩 관측열(통계 게이트 입력)."""
    from ml.adaptive.oos_eval import nonoverlapping_units

    by_day: dict[str, list[dict]] = {}
    for row in selected:
        by_day.setdefault(str(row.get("date") or "")[:10], []).append(row)
    units = []
    for day, members in by_day.items():
        windows = [_label_window(r) for r in members]
        start = min(w[0] for w in windows) if all(w[0] for w in windows) else day
        end = None if any(w[1] is None for w in windows) else max(w[1] for w in windows)
        if start < day:                       # 결정일 이전 진입 기록 = 손상 → 기간 미상
            start, end = day, None
        value = sum(_realized_excess(r) for r in members) / len(members)
        units.append((start, end, (day, value)))
    picked, unresolved = nonoverlapping_units(units)
    return {
        "series": [round(value, 8) for _, _, (_, value) in picked],
        "series_days": [day for _, _, (day, _) in picked],
        "n_independent": len(picked),
        "n_unresolved_days": unresolved,
    }


def _eval_adjustments(rows: list[dict], adjustments: dict[str, float], threshold: float) -> dict:
    selected = []
    for row in rows:
        adj_score, _, _ = _score_with_adjustment(row, adjustments)
        if adj_score >= threshold:
            selected.append(row)
    if not selected:
        return {"excess": 0.0, "mdd": 0.0, "avg_loss": 0.0, "n": 0, "win_rate": 0.0,
                "series": [], "series_days": [], "n_independent": 0, "n_unresolved_days": 0}
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
        **_nonoverlapping_series(selected),
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


_DECISION_TIME_KEYS = ("decision_ts", "snapshot_ts", "date", "asof_date")
_OUTCOME_AVAILABLE_KEYS = ("available_at", "outcome_available_at", "matured_at", "exit_date")


def _utc_value(value, *, end_of_day: bool = False):
    from ml.purged_split import utc_timestamp

    text = str(value or "").strip()
    if text.endswith(" KST"):
        stamp = _parse_snapshot_timestamp(text)
        return None if stamp is None else utc_timestamp(stamp.to_pydatetime())
    return utc_timestamp(text, end_of_day=end_of_day)


def _decision_time(row: dict):
    """결정 시각(UTC). 첫 번째로 존재하는 키가 결정 — 해석 불가면 None(추정 금지)."""
    for key in _DECISION_TIME_KEYS:
        if row.get(key) is not None:
            return _utc_value(row.get(key))
    return None


def _outcome_available_time(row: dict):
    """라벨이 실제로 알려진 시각(UTC) = exit/성숙/가용 시각 중 가장 늦은 값. 없거나 해석 불가면 None."""
    values = [row.get(key) for key in _OUTCOME_AVAILABLE_KEYS if row.get(key) is not None]
    if not values:
        return None
    parsed = [_utc_value(value, end_of_day=True) for value in values]
    if any(stamp is None for stamp in parsed):
        return None
    return max(parsed)


def learn_feedback_adjustments(rows: list[dict] | None = None, *,
                               horizon: int = 20,
                               save: bool = True,
                               path: Path | str | None = None,
                               trial_log: dict | None = None) -> dict:
    """사후 성과를 이용해 조건별 점수 보정치를 학습하고, 검증 통과 시 저장.

    train/OOS 경계는 결정일 단위(같은 날 다종목이 양쪽에 걸치지 않음)이며, train 라벨의
    이용가능 시각(exit/성숙)이 첫 OOS 결정 시각 이전인 행만 학습한다(ml.purged_split).

    채택 계약(모두 충족):
    - 챔피언 = 지금 실제 점수에 적용 중인 저장 모델의 보정치(없으면 무보정). 같은 OOS 행·같은 문턱으로
      평가해 challenger 가 절대 양의 초과수익 + 챔피언 초과 + MDD/평균손실 ≤ 챔피언.
    - 통계 게이트(ml.adaptive.learner.adoption_evidence): challenger 선택 행의 결정일 바스켓을 실제
      라벨 종료일 기준으로 겹치지 않게 고른 관측열의 PSR/DSR. 시도 지문 = (보정치, 문턱, horizon,
      OOS 행 집합), trial_log 는 TRIAL_SURFACE 학습 이력(None 이면 파일에서 읽음).
    """
    from ml.adaptive import learner
    from ml.purged_split import purged_day_holdout_split

    rows = list(rows if rows is not None else training_rows(horizon=horizon))
    rows = [r for r in rows if r.get("r_multiple") is not None]
    # (결정일, 결정시각 UTC, 행) — 결정일은 원장의 거래일(date), 없으면 UTC 날짜.
    timed = []
    for row in rows:
        decided = _decision_time(row)
        if decided is not None:
            day = str(row.get("date") or "")[:10] or decided.date().isoformat()
            timed.append((day, decided, row))
    excluded_n = len(rows) - len(timed)
    timed.sort(key=lambda item: (item[1], str(item[2].get("ticker") or "")))
    rows = [row for _, _, row in timed]
    if len(rows) < MIN_ADJUST_SAMPLES:
        return {
            "adopted": False,
            "reason": f"표본 부족({len(rows)}/{MIN_ADJUST_SAMPLES})",
            "horizon": horizon,
            "n": len(rows),
            "excluded_n": excluded_n,
            "adjustments": {},
        }

    split = max(MIN_FACTOR_SAMPLES, int(len(rows) * 0.6))
    if len(rows) - split < max(5, MIN_FACTOR_SAMPLES // 2):
        split = max(1, len(rows) // 2)
    train_t, oos_t, purged_t = purged_day_holdout_split(
        timed, holdout_size=len(rows) - split,
        day_of=lambda item: item[0],
        decision_at=lambda item: item[1],
        available_at=lambda item: _outcome_available_time(item[2]),
    )
    train = [row for _, _, row in train_t]
    oos = [row for _, _, row in oos_t]
    split_meta = {
        "horizon": horizon,
        "n": len(rows),
        "train_n": len(train),
        "oos_n": len(oos),
        "purged_n": len(purged_t),
        "excluded_n": excluded_n,
        "train_last_date": max((day for day, _, _ in train_t), default=None),
        "oos_first_date": min((day for day, _, _ in oos_t), default=None),
        "oos_start_at": min((decided for _, decided, _ in oos_t), default=None),
    }
    if split_meta["oos_start_at"] is not None:
        split_meta["oos_start_at"] = split_meta["oos_start_at"].isoformat()
    if len(oos) < MIN_OOS_SAMPLES:
        return {
            "adopted": False,
            "reason": f"OOS 표본 부족({len(oos)}/{MIN_OOS_SAMPLES})",
            **split_meta,
            "adjustments": {},
        }
    if len(train) < MIN_FACTOR_SAMPLES:
        return {
            "adopted": False,
            "reason": f"purge 후 학습 표본 부족({len(train)}/{MIN_FACTOR_SAMPLES})",
            **split_meta,
            "adjustments": {},
        }
    adjustments = _fit_factor_adjustments(train)
    if not adjustments:
        return {
            "adopted": False,
            "reason": "유효한 조건별 보정치 없음",
            **split_meta,
            "adjustments": {},
        }

    try:
        from ml.entry_analyzer import get_score_params
        threshold = float(get_score_params().get("enter_threshold", 0.62))
    except Exception:
        threshold = 0.62

    champion_model = load_feedback_adjustments(path)
    champion_adjustments = dict(champion_model.get("adjustments") or {})
    champion = _eval_adjustments(oos, champion_adjustments, threshold)
    challenger = _eval_adjustments(oos, adjustments, threshold)

    try:
        from ml.adaptive.reward import should_adopt
        index_mdd = float(champion.get("mdd") or 0.0)
        min_samples = max(5, min(MIN_FACTOR_SAMPLES, len(oos)))
        # MIN_OOS_SAMPLES applies to the held-out time window. A stricter
        # per-threshold selected-count gate would reject a valid OOS window
        # simply because the challenger is intentionally selective.
        objective_ok = should_adopt(challenger, champion, index_mdd=index_mdd, min_samples=min_samples)
    except Exception:
        objective_ok = (
            challenger.get("n", 0) >= MIN_OOS_SAMPLES
            and challenger.get("excess", 0.0) > 0
            and challenger.get("excess", 0.0) > champion.get("excess", 0.0)
            and challenger.get("mdd", 0.0) <= max(champion.get("mdd", 0.0) * 1.25, 1.0)
        )
    # Keep drawdown and average-loss constraints explicit even if the shared
    # reward gate changes implementation details later.
    if objective_ok and not _oos_constraints_ok(challenger, champion):
        objective_ok = False
    trial_key = learner.trial_fingerprint(
        {"adjustments": adjustments, "threshold": round(threshold, 6), "horizon": str(horizon)}, oos)
    evidence = learner.adoption_evidence(
        challenger, trial_key=trial_key,
        trial_log=trial_log if trial_log is not None else learner.trial_history(TRIAL_SURFACE))
    adopted = bool(objective_ok and evidence["ok"])
    if adopted:
        reason = "검증 통과"
    elif not objective_ok:
        reason = "검증 구간에서 기존 점수 대비 개선 부족"
    else:
        reason = (f"통계 게이트 미통과 ({evidence['reason']} · 비중첩 {evidence['n_independent']}"
                  f" · 시도 {evidence['n_trials']} · PSR {evidence['psr']})")

    result = {
        "adopted": adopted,
        "reason": reason,
        **split_meta,
        "threshold": round(threshold, 4),
        "adjustments": adjustments,
        "champion_adjustments": champion_adjustments,
        "champion": champion,
        "challenger": challenger,
        "evidence": evidence,
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
                "purged_n": split_meta["purged_n"],
                "train_last_date": split_meta["train_last_date"],
                "oos_first_date": split_meta["oos_first_date"],
                "threshold": round(threshold, 4),
                "champion_adjustments": champion_adjustments,
                "champion": champion,
                "challenger": challenger,
                "psr": evidence["psr"],
                "n_independent": evidence["n_independent"],
                "n_trials": evidence["n_trials"],
                "trial_key": evidence["trial_key"],
                "total_adjust_cap": TOTAL_ADJUST_CAP,
                "factor_adjust_cap": FACTOR_ADJUST_CAP,
            },
        }
        model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        result["path"] = str(model_path)
        logger.info("진입 추천 성과 보정 모델 저장: %s", model_path)
    return result
