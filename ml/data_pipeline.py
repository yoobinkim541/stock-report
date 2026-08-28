"""ml/data_pipeline.py — 실시장 데이터 파이프라인 (MVP)

데이터 소스:
  - 유니버스  : Wikipedia (S&P500 / NASDAQ100 현재 구성종목)
  - 가격      : yfinance (일봉 5년, 캐시 1일)
  - Fear/Greed: 자체 proxy — VIX + QQQ모멘텀 + 신용스프레드 + 안전자산 강세
  - 매크로    : yfinance (^VIX, ^TNX, HYG, LQD, IEF, TLT)

주의(survivorship bias): 유니버스가 *현재* 구성종목 기준이라 과거 시점에
  탈락·상장폐지된 종목이 빠져 있다. 학습·백테스트는 '살아남은' 종목만 보므로
  과거 성과(CAGR·Sharpe)가 상향 편향될 수 있다 — 정성 추정으로 연 +1~3%p
  CAGR 과대평가 가능(학계 SP500/NASDAQ 연구 통상 범위, 본 유니버스 미측정).
  실거래·학습은 어차피 생존 종목 대상이라 운용엔 영향이 적으나, 보고되는
  백테스트 수치는 낙관 쪽으로 읽을 것. (리포트에 명시 필요)

공개 API:
  fetch_universe(mode)         → list[str] 티커
  fetch_prices(tickers, days)  → dict[str, pd.DataFrame]  (OHLCV)
  build_fear_greed_proxy(days) → pd.Series  (0=극도공포, 100=극도탐욕)
  get_fg_proxy_score()         → float  (오늘 proxy 점수, 캐시 1h, 빠름)
  build_stock_features(ticker, prices, market) → pd.DataFrame
  build_ml_dataset(mode, days) → dict  {features, returns, market, universe}
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import numpy as np
import pandas as pd
import requests

from ml.strategy_studio.contracts import DataSnapshot, DataStamp

logger = logging.getLogger(__name__)

CACHE_DIR   = Path(os.path.expanduser("~/reports/ml-cache"))
HEADERS     = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
PRICE_TTL_H = 6   # 가격 캐시 유효시간 (시간)

# yfinance 배치 다운로드 견고성 파라미터
PRICE_MAX_RETRIES   = 3            # 배치별 최대 재시도 횟수
PRICE_BACKOFF_BASE  = 2.0          # 지수 백오프 기준 (2s·4s·8s)
PRICE_SHRINK_STEPS  = (20, 10, 5)  # 반복 실패 시 배치 크기 동적 축소 단계


# ── 데이터 시점·provenance ───────────────────────────────────────────────────

def _metadata_value(frame: pd.DataFrame, *names: str) -> object | None:
    attrs = frame.attrs if isinstance(frame.attrs, Mapping) else {}
    for name in names:
        value = attrs.get(name)
        if not _is_missing_value(value) and str(value).strip() != "":
            return value
    return None


def _timestamp_or_none(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    try:
        if bool(pd.isna(parsed)):
            return None
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed


def _timestamp_text(value: object, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    try:
        invalid = bool(pd.isna(parsed))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if invalid:
        raise ValueError(f"{field_name} must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.isoformat()


def _column_lookup(frame: pd.DataFrame) -> dict[str, object]:
    return {str(column).strip().lower().replace(" ", "_"): column for column in frame.columns}


def _row_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number >= 0 else None


def _is_missing_value(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _snapshot_stamps(value: object) -> DataSnapshot | None:
    if isinstance(value, DataSnapshot):
        return value
    if isinstance(value, Mapping):
        try:
            return DataSnapshot.from_dict(value)
        except (TypeError, ValueError):
            return None
    return None


def normalize_data_snapshot(
    frame: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    timeframe: str,
    session: str,
    adjustment: str,
    received_at: object | None = None,
    available_at: object | None = None,
    raw_ref: str | None = None,
    quality: str | None = None,
) -> DataSnapshot:
    """Normalize a price frame into deterministic, point-in-time metadata.

    The market index is the event time.  Receipt and availability remain
    missing unless the collector or source explicitly supplies them; no event
    time or wall-clock value is fabricated as transport metadata.  A supplied
    ``frame.attrs`` value is retained after ISO normalization.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    # DataStamp performs the required textual validation without changing the
    # legacy frame.  The local values are used below so no caller-owned attrs
    # or columns are mutated.
    symbol_text = str(symbol or "").strip()
    source_text = str(source or "").strip()
    timeframe_text = str(timeframe or "").strip()
    session_text = str(session or "").strip()
    adjustment_text = str(adjustment or "").strip()
    if not symbol_text:
        raise ValueError("symbol is required")
    if not source_text:
        raise ValueError("source is required")
    if not timeframe_text:
        raise ValueError("timeframe is required")
    if not session_text:
        raise ValueError("session is required")
    if not adjustment_text:
        raise ValueError("adjustment is required")

    supplied_received = received_at if received_at is not None else _metadata_value(frame, "received_at", "retrieved_at", "fetched_at")
    received_text = _timestamp_text(supplied_received, "received_at") if supplied_received is not None else None
    supplied_available = available_at if available_at is not None else _metadata_value(frame, "available_at")
    available_text = _timestamp_text(supplied_available, "available_at") if supplied_available is not None else None
    raw_reference = raw_ref if raw_ref is not None else _metadata_value(frame, "raw_ref", "raw_path", "source_ref")
    raw_text = str(raw_reference).strip() if raw_reference is not None and str(raw_reference).strip() else None
    explicit_quality = quality if quality is not None else _metadata_value(frame, "quality")
    columns = _column_lookup(frame)
    records: list[tuple[pd.Timestamp, int, pd.Series, str]] = []
    warnings: list[str] = []
    original_events: list[pd.Timestamp] = []
    has_invalid_stamp = False
    for position, (raw_index, row) in enumerate(frame.iterrows()):
        event = _timestamp_or_none(raw_index)
        if event is None:
            warnings.append("invalid_event_timestamp")
            has_invalid_stamp = True
            continue
        original_events.append(event)
        raw_event = pd.Timestamp(raw_index)
        if raw_event.tzinfo is None:
            raw_event = raw_event.tz_localize("UTC")
        records.append((event, position, row, raw_event.isoformat()))
    if any(right < left for left, right in zip(original_events, original_events[1:])):
        warnings.append("timestamp_order_non_monotonic")
    records.sort(key=lambda item: (item[0].value, item[1]))
    seen: set[int] = set()
    stamps: list[DataStamp] = []
    has_missing_value = False
    has_invalid_value = False
    for event, _, row, event_text in records:
        event_key = int(event.value)
        if event_key in seen:
            warnings.append("duplicate_event_timestamp")
            continue
        seen.add(event_key)
        values: dict[str, float | None] = {}
        for field_name in ("open", "high", "low", "close", "volume"):
            column = columns.get(field_name)
            if column is None:
                values[field_name] = None
                continue
            raw_value = row[column]
            value = _row_number(raw_value)
            values[field_name] = value
            if _is_missing_value(raw_value):
                has_missing_value = True
            elif value is None:
                has_invalid_value = True
        row_received = row[columns["received_at"]] if "received_at" in columns else received_text
        row_available = row[columns["available_at"]] if "available_at" in columns else available_text
        if _is_missing_value(row_received):
            row_received = received_text
        if _is_missing_value(row_available):
            row_available = available_text
        try:
            stamp = DataStamp(
                symbol=symbol_text,
                timestamp=event_text,
                source=source_text,
                timeframe=timeframe_text,
                quality=str(explicit_quality or "complete").strip().lower() or "unknown",
                session=session_text,
                adjustment=adjustment_text,
                received_at=row_received,
                available_at=row_available,
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                volume=values["volume"],
            )
        except (TypeError, ValueError) as exc:
            # Keep the event in the audit trail without coercing an invalid
            # price into a plausible numeric value.
            warnings.append(f"invalid_stamp:{exc}")
            has_invalid_stamp = True
            stamp = DataStamp(
                symbol=symbol_text,
                timestamp=event_text,
                source=source_text,
                timeframe=timeframe_text,
                quality="invalid",
                session=session_text,
                adjustment=adjustment_text,
                received_at=None,
                available_at=None,
                metadata={"normalization_error": str(exc)},
            )
        stamps.append(stamp)
    observed_quality = (
        "missing" if not stamps else
        "invalid" if has_invalid_value or has_invalid_stamp else
        "incomplete" if has_missing_value else
        "complete"
    )
    snapshot_quality = _merge_snapshot_quality(explicit_quality, observed_quality)
    if stamps and any(stamp.received_at is None for stamp in stamps):
        warnings.append("received_at_missing")
    if stamps and any(stamp.available_at is None for stamp in stamps):
        warnings.append("available_at_missing")
    snapshot = DataSnapshot(
        data_stamps=stamps,
        raw_ref=raw_text,
        quality=snapshot_quality,
        warnings=list(dict.fromkeys(warnings)),
    )
    return snapshot


def _merge_snapshot_quality(explicit_quality: object | None, observed_quality: str) -> str:
    """Keep source quality while preventing observed defects from being hidden."""

    explicit = str(explicit_quality or "").strip().lower()
    if not explicit:
        return observed_quality
    if observed_quality == "invalid" or explicit in {"invalid", "error"}:
        return "invalid"
    if observed_quality == "missing" or explicit in {"missing", "empty"}:
        return "missing"
    if observed_quality == "incomplete" or explicit in {"incomplete", "partial"}:
        return "incomplete"
    return explicit


def source_freshness(
    snapshot: DataSnapshot,
    *,
    evaluation_at: object | None = None,
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Return deterministic freshness status for the latest usable stamp."""

    if not isinstance(snapshot, DataSnapshot):
        raise TypeError("snapshot must be a DataSnapshot")
    warnings = list(snapshot.warnings or [])
    if any(stamp.received_at is None for stamp in snapshot.data_stamps):
        warnings.append("received_at_missing")
    if any(stamp.available_at is None for stamp in snapshot.data_stamps):
        warnings.append("available_at_missing")
    latest = snapshot.latest_transport_at
    evaluation = _timestamp_or_none(evaluation_at)
    limit: float | None
    try:
        limit = None if max_age_seconds is None else float(max_age_seconds)
    except (TypeError, ValueError):
        limit = None
        warnings.append("freshness_limit_invalid")
    if limit is not None and (not np.isfinite(limit) or limit < 0):
        limit = None
        warnings.append("freshness_limit_invalid")
    if latest is None:
        warnings.append("freshness_timestamp_missing")
        status = "unknown"
        age = None
    elif evaluation is None:
        warnings.append("freshness_evaluation_missing")
        status = "unknown"
        age = None
    else:
        latest_dt = _timestamp_or_none(latest)
        age = (evaluation - latest_dt).total_seconds() if latest_dt is not None else None
        if age is None:
            warnings.append("freshness_timestamp_invalid")
            status = "unknown"
        elif age < 0:
            warnings.append("future_timestamp")
            status = "invalid"
        elif limit is None:
            warnings.append("freshness_limit_missing")
            status = "unknown"
        elif age > limit:
            warnings.append("data_stale")
            status = "stale"
        else:
            status = "fresh"
    if snapshot.quality in {"stale", "expired"}:
        warnings.append("data_stale")
        status = "stale" if status == "fresh" else status
    elif snapshot.quality in {"invalid", "missing"}:
        warnings.append(f"data_quality_{snapshot.quality}")
        status = "invalid" if snapshot.quality == "invalid" else "unknown"
    return {
        "status": status,
        "as_of": snapshot.event_end,
        "received_at": snapshot.latest_received_at,
        "available_at": snapshot.latest_available_at,
        "age_seconds": age,
        "max_age_seconds": limit,
        "warnings": list(dict.fromkeys(str(value) for value in warnings)),
    }


def source_coverage(
    snapshots: DataSnapshot | Iterable[DataSnapshot],
    *,
    expected_symbols: Iterable[str] | None = None,
    expected_start: object | None = None,
    expected_end: object | None = None,
    evaluation_at: object | None = None,
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Summarize symbol coverage and freshness without hiding missing inputs."""

    if isinstance(snapshots, DataSnapshot):
        values = [snapshots]
    else:
        values = list(snapshots)
    if any(not isinstance(snapshot, DataSnapshot) for snapshot in values):
        raise TypeError("snapshots must contain DataSnapshot values")
    values.sort(key=lambda snapshot: (snapshot.snapshot_id or "", snapshot.event_start or ""))
    expected = sorted({str(symbol).strip().upper() for symbol in (expected_symbols or []) if str(symbol).strip()})
    start = _timestamp_or_none(expected_start)
    end = _timestamp_or_none(expected_end)
    warnings: list[str] = []
    sources: dict[str, dict[str, Any]] = {}
    observed: set[str] = set()
    grouped: dict[str, list[DataStamp]] = {}
    source_refs: dict[str, str | None] = {}
    source_qualities: dict[str, list[str]] = {}
    source_warnings: dict[str, list[str]] = {}
    for snapshot in values:
        stamps = [
            stamp for stamp in snapshot.data_stamps
            if (start is None or (_timestamp_or_none(stamp.timestamp) or start) >= start)
            and (end is None or (_timestamp_or_none(stamp.timestamp) or end) <= end)
        ]
        for stamp in stamps:
            grouped.setdefault(stamp.source, []).append(stamp)
            observed.add(stamp.symbol)
            source_refs.setdefault(stamp.source, snapshot.raw_ref)
            source_qualities.setdefault(stamp.source, []).append(snapshot.quality)
            source_warnings.setdefault(stamp.source, []).extend(snapshot.warnings or [])
    for source, source_stamps in sorted(grouped.items()):
        source_symbols = sorted({stamp.symbol for stamp in source_stamps})
        qualities = source_qualities.get(source, [])
        aggregate_quality = _aggregate_snapshot_quality(qualities)
        source_snapshot = DataSnapshot(
            source_stamps,
            source_refs.get(source),
            aggregate_quality,
            warnings=list(dict.fromkeys(source_warnings.get(source, []))),
        )
        freshness = source_freshness(source_snapshot, evaluation_at=evaluation_at, max_age_seconds=max_age_seconds)
        source_expected = expected or source_symbols
        missing = sorted(set(source_expected) - set(source_symbols))
        ratio = len(set(source_symbols) & set(source_expected)) / len(source_expected) if source_expected else 1.0
        source_info = {
            "source": source,
            "symbols": source_symbols,
            "symbol_count": len(source_symbols),
            "observations": len(source_stamps),
            "coverage_ratio": ratio,
            "missing_symbols": missing,
            "quality": source_snapshot.quality,
            "raw_ref": source_snapshot.raw_ref,
            "received_at": source_snapshot.latest_received_at,
            "available_at": source_snapshot.latest_available_at,
            "freshness": freshness,
            "event_start": source_snapshot.event_start,
            "event_end": source_snapshot.event_end,
        }
        sources[source] = source_info
        warnings.extend(f"{source}:{warning}" for warning in freshness["warnings"])
        warnings.extend(f"{source}:missing_symbol:{symbol}" for symbol in missing)
    missing_symbols = sorted(set(expected) - observed)
    coverage_ratio = len(observed & set(expected)) / len(expected) if expected else (1.0 if observed or not values else 0.0)
    if missing_symbols:
        warnings.extend(f"missing_symbol:{symbol}" for symbol in missing_symbols)
    if not values:
        warnings.append("source_snapshot_missing")
    unhealthy_qualities = {"invalid", "incomplete", "missing", "stale", "expired", "unknown"}
    unhealthy_sources = any(info["quality"] in unhealthy_qualities for info in sources.values())
    unhealthy_freshness = any(info["freshness"]["status"] != "fresh" for info in sources.values())
    missing_required_metadata = any(
        warning.rsplit(":", 1)[-1] in {"received_at_missing", "available_at_missing"}
        for warning in warnings
    )
    ok = bool(values) and bool(sources) and not missing_symbols and not unhealthy_sources and not unhealthy_freshness and not missing_required_metadata
    freshness_statuses = [str(info["freshness"]["status"]) for info in sources.values()]
    freshness_status = next(
        (value for value in ("invalid", "stale", "unknown", "fresh") if value in freshness_statuses),
        "unknown",
    )
    status = "ok" if ok else "unknown" if any(
        info["freshness"]["status"] == "unknown" for info in sources.values()
    ) or not sources else "incomplete"
    return {
        "expected_symbols": expected,
        "observed_symbols": sorted(observed),
        "missing_symbols": missing_symbols,
        "coverage_ratio": coverage_ratio,
        "source_count": len(sources),
        "sources": sources,
        "warnings": list(dict.fromkeys(warnings)),
        "freshness_status": freshness_status,
        "status": status,
        "ok": ok,
    }


def _aggregate_snapshot_quality(qualities: Iterable[str]) -> str:
    """Select the most conservative quality label from source snapshots."""

    values = {str(value).strip().lower() for value in qualities if str(value).strip()}
    for quality in ("invalid", "missing", "stale", "expired", "incomplete", "unknown", "complete"):
        if quality in values:
            return quality
    return "unknown"


class PITUniverseResult(list[str]):
    """List-compatible point-in-time membership result with visible diagnostics."""

    __slots__ = ("status", "warnings", "diagnostics", "invalid_rows")

    def __init__(
        self,
        members: Iterable[str],
        *,
        status: str,
        warnings: Iterable[str] = (),
        diagnostics: Iterable[Mapping[str, Any]] = (),
        invalid_rows: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(members)
        self.status = str(status)
        self.warnings = list(dict.fromkeys(str(value) for value in warnings))
        self.diagnostics = [dict(value) for value in diagnostics]
        self.invalid_rows = [dict(value) for value in invalid_rows]

    @property
    def members(self) -> list[str]:
        return list(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": list(self),
            "status": self.status,
            "warnings": list(self.warnings),
            "diagnostics": [dict(value) for value in self.diagnostics],
            "invalid_rows": [dict(value) for value in self.invalid_rows],
        }


def point_in_time_universe(symbols: pd.DataFrame, as_of: pd.Timestamp) -> PITUniverseResult:
    """Return members and diagnostics for inclusive effective intervals."""

    if not isinstance(symbols, pd.DataFrame):
        raise TypeError("symbols must be a pandas DataFrame")
    required = {"symbol", "effective_from"}
    missing = sorted(required - set(symbols.columns))
    if missing:
        raise ValueError(f"universe membership metadata missing: {', '.join(missing)}")
    cutoff = _timestamp_or_none(as_of)
    if cutoff is None:
        raise ValueError("as_of must be a valid timestamp")
    members: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(symbols.iterrows()):
        raw_symbol = row.get("symbol")
        if _is_missing_value(raw_symbol):
            diagnostics.append({"row": position, "symbol": None, "reason": "symbol_missing"})
            continue
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            diagnostics.append({"row": position, "symbol": None, "reason": "symbol_missing"})
            continue
        raw_start = row.get("effective_from")
        start = _membership_boundary(row.get("effective_from"))
        if _is_missing_value(raw_start):
            diagnostics.append({"row": position, "symbol": symbol, "reason": "effective_from_missing"})
            continue
        if start is None:
            diagnostics.append({"row": position, "symbol": symbol, "reason": "effective_from_invalid"})
            continue
        has_end = "effective_to" in symbols.columns and not _is_missing_value(row.get("effective_to"))
        raw_end = row.get("effective_to") if "effective_to" in symbols.columns else None
        end = _membership_boundary(raw_end, end=True) if has_end else None
        if has_end and end is None:
            diagnostics.append({"row": position, "symbol": symbol, "reason": "effective_to_invalid"})
            continue
        if end is not None and end < start:
            diagnostics.append({"row": position, "symbol": symbol, "reason": "effective_interval_reversed"})
            continue
        if start <= cutoff and (end is None or cutoff <= end):
            members.add(symbol)
    diagnostic_warnings = [str(value["reason"]) for value in diagnostics]
    status = "ok" if not diagnostics else "warning" if members else "unknown"
    return PITUniverseResult(
        sorted(members),
        status=status,
        warnings=diagnostic_warnings,
        diagnostics=diagnostics,
        invalid_rows=diagnostics,
    )


def _membership_boundary(value: object, *, end: bool = False) -> pd.Timestamp | None:
    parsed = _timestamp_or_none(value)
    if parsed is None:
        return None
    # Date-only interval endpoints are calendar dates, not instants at midnight.
    text = str(value).strip() if isinstance(value, str) else ""
    if end and len(text) == 10 and text[4] == "-" and text[7] == "-":
        return parsed + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return parsed


def _attach_snapshot_metadata(
    frame: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    timeframe: str,
    session: str,
    adjustment: str,
    received_at: object | None = None,
    available_at: object | None = None,
) -> pd.DataFrame:
    """Attach provenance while returning the collector's original frame."""

    snapshot = normalize_data_snapshot(
        frame,
        symbol=symbol,
        source=source,
        timeframe=timeframe,
        session=session,
        adjustment=adjustment,
        received_at=received_at,
        available_at=available_at,
    )
    frame.attrs["data_snapshot"] = snapshot
    frame.attrs["provenance"] = snapshot.to_provenance().get("data", {})
    return frame

# 포트폴리오 보유 종목 (universe 'portfolio' 모드) — 단일 소스에서 파생
try:
    from portfolio_universe import load_portfolio_tickers as _load_portfolio_tickers
    PORTFOLIO_TICKERS = _load_portfolio_tickers()
except Exception:
    PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "NVDA", "GOOGL", "SAP", "UNH", "SGOV", "SPMO"]

# 미국 시가총액 상위 100개 (섹터 다변화, 2025-26 기준)
US_TOP100 = [
    # 빅테크 / AI / 클라우드
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "CRM",  # ticker-ok 시장 유니버스
    "ADBE", "INTU", "NOW", "SNOW", "PLTR", "UBER", "SHOP",  # ticker-ok 시장 유니버스
    # 반도체 (미국 + 대만)
    "TSM", "QCOM", "AMD", "INTC", "TXN", "AMAT", "KLAC", "MU", "ASML", "LRCX", "MRVL", "ON",
    # 금융 (은행·결제·자산운용)
    "BRK-B", "JPM", "V", "MA", "BAC", "GS", "MS", "WFC",
    "AXP", "C", "COF", "SCHW", "CME", "BLK", "SPGI", "ICE",
    # 헬스케어 / 바이오 / 보험
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG",
    "PFE", "GILD", "REGN", "MDT", "CVS", "CI", "ZTS", "BMY",
    # 소비재 / 유통 / 미디어
    "WMT", "COST", "HD", "MCD", "KO", "PEP", "NKE",
    "SBUX", "TGT", "LOW", "DIS", "CMCSA",
    # 에너지
    "XOM", "CVX", "COP", "SLB", "EOG",
    # 산업재 / 항공방산 / 물류
    "GE", "CAT", "HON", "RTX", "LMT", "BA", "UPS", "DE", "ETN",
    # 통신
    "T", "VZ",
    # 부동산 / 인프라 / 유틸리티
    "NEE", "PLD", "AMT",
    # 소재 / 화학
    "LIN",
    # 기타
    "NFLX", "ACN", "PYPL", "CB", "F", "GM", "AMGN",
]
# 하위 호환: 기존 US_TOP50 참조 코드를 위한 별칭
US_TOP50 = US_TOP100

# 한국 시가총액 상위 10개 (KOSPI, 2025-26 기준)
# 표시명: {티커: (한글명, 영문명, 섹터)}
KR_TOP10_META: dict[str, tuple[str, str, str]] = {
    "005930.KS": ("삼성전자",       "Samsung Electronics", "반도체"),
    "000660.KS": ("SK하이닉스",     "SK Hynix",            "반도체"),
    "373220.KS": ("LG에너지솔루션", "LG Energy Solution",  "2차전지"),
    "207940.KS": ("삼성바이오로직스","Samsung Biologics",   "바이오"),
    "005380.KS": ("현대차",         "Hyundai Motor",       "자동차"),
    "005490.KS": ("포스코홀딩스",   "POSCO Holdings",      "철강"),
    "035420.KS": ("NAVER",          "NAVER",               "IT"),
    "035720.KS": ("카카오",         "Kakao",               "IT"),
    "000270.KS": ("기아",           "Kia",                 "자동차"),
    "006400.KS": ("삼성SDI",        "Samsung SDI",         "2차전지"),
}
KR_TOP10 = list(KR_TOP10_META.keys())

# 한국 시가총액 상위 30개 (KR 전용 ML 학습 폭 확보 — KR_TOP10 + 20)
KR_TOP30 = KR_TOP10 + [
    "105560.KS", "055550.KS", "012330.KS", "028260.KS", "066570.KS",
    "051910.KS", "096770.KS", "032830.KS", "015760.KS", "017670.KS",
    "030200.KS", "086790.KS", "000810.KS", "009150.KS", "010130.KS",
    "011200.KS", "018260.KS", "034730.KS", "011070.KS", "003670.KS",
]
# KR 벤치마크 지수 (초과수익·베타 기준) — KOSPI 종합
KR_BENCHMARK = "^KS11"

# Fear/Greed proxy 재료
_MACRO_TICKERS = ["^VIX", "^TNX", "QQQ", "SPY", "HYG", "LQD", "IEF", "TLT", "ACWI"]


# ── 캐시 유틸 ─────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    from ml._safe_cache import harden_cache_dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    harden_cache_dir(CACHE_DIR)  # 0700 best-effort — 타 사용자 파일 주입 방지
    h = hashlib.md5(key.encode()).hexdigest()[:8]
    return CACHE_DIR / f"{key[:40]}_{h}.pkl"


def _load_cache(key: str, ttl_hours: float) -> pd.DataFrame | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=ttl_hours):
        return None
    # 안전 로더: 심링크·소유자 검증 후 역직렬화(실패 시 None=캐시 미스)
    from ml._safe_cache import safe_unpickle
    return safe_unpickle(path)


def _save_cache(key: str, df: pd.DataFrame) -> None:
    try:
        import pickle
        _cache_path(key).write_bytes(pickle.dumps(df))
    except Exception as e:
        logger.warning("캐시 저장 실패: %s", e)


# ── 유니버스 ──────────────────────────────────────────────────────────────────

def fetch_universe(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all",
                  "us_top50", "kr_top10", "kr30", "watch"] = "nasdaq100",
) -> list[str]:
    """종목 유니버스 반환.

    mode:
      portfolio  — 현재 보유 포트폴리오 (9종목, 빠름)
      us_top50   — 미국 시가총액 상위 50개 (하드코딩, 안정적)
      kr_top10   — 한국 시가총액 상위 10개 KOSPI (.KS 티커)
      watch      — 포트폴리오 + us_top50 + kr_top10 전체 감시 대상
      nasdaq100  — Wikipedia NASDAQ100 (약 101종목)
      sp500      — Wikipedia S&P500 (약 503종목)
      all        — NASDAQ100 + S&P500 합집합

    survivorship bias: 모든 모드가 *현재* 시점 구성종목을 반환한다 (point-in-time
    재구성 아님). 과거 탈락·상폐 종목이 빠져 백테스트 CAGR 이 상향 편향될 수 있고
    (정성 추정 연 +1~3%p), 학습 역시 생존 종목만 본다. 운용엔 영향이 작지만
    보고 수치는 낙관 쪽으로 해석할 것. (모듈 상단 docstring 참조)
    """
    if mode == "portfolio":
        return list(PORTFOLIO_TICKERS)
    if mode in ("us_top50", "us_top100"):
        return list(US_TOP100)
    if mode == "kr_top10":
        return list(KR_TOP10)
    if mode == "kr30":
        return list(KR_TOP30)
    if mode == "watch":
        combined = list(PORTFOLIO_TICKERS) + list(US_TOP50) + list(KR_TOP10)
        return list(dict.fromkeys(combined))   # 순서 유지 중복 제거

    tickers: list[str] = []
    if mode in ("nasdaq100", "all"):
        tickers += _fetch_nasdaq100()
    if mode in ("sp500", "all"):
        tickers += _fetch_sp500()
    return sorted(set(tickers))


def _fetch_nasdaq100() -> list[str]:
    cache_key = "universe_nasdaq100"
    cached = _load_cache(cache_key, ttl_hours=24)
    if cached is not None:
        return cached["ticker"].tolist()

    try:
        r = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=HEADERS, timeout=15)
        tables = pd.read_html(io.StringIO(r.text), flavor="lxml")
        for t in tables:
            for col in ("Ticker", "Symbol"):
                if col in t.columns:
                    tickers = [s for s in t[col].tolist() if isinstance(s, str) and s.isalpha()]
                    df = pd.DataFrame({"ticker": tickers})
                    _save_cache(cache_key, df)
                    logger.info("NASDAQ100 유니버스: %d종목", len(tickers))
                    return tickers
    except Exception as e:
        logger.warning("NASDAQ100 유니버스 로드 실패: %s", e)

    # fallback: 핵심 30종목
    return ["AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","COST",  # ticker-ok NASDAQ100 시장 유니버스
            "NFLX","ASML","AMD","QCOM","INTC","INTU","AMAT","MU","LRCX","MRVL",
            "PANW","CDNS","SNPS","FTNT","KLAC","MCHP","ADI","ON","MPWR","TEAM","ZM"]


def _fetch_sp500() -> list[str]:
    cache_key = "universe_sp500"
    cached = _load_cache(cache_key, ttl_hours=24)
    if cached is not None:
        return cached["ticker"].tolist()

    try:
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=HEADERS, timeout=15,
        )
        tables = pd.read_html(io.StringIO(r.text), flavor="lxml")
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        df = pd.DataFrame({"ticker": tickers})
        _save_cache(cache_key, df)
        logger.info("S&P500 유니버스: %d종목", len(tickers))
        return tickers
    except Exception as e:
        logger.warning("S&P500 유니버스 로드 실패: %s", e)
        return []


# ── 가격 데이터 ───────────────────────────────────────────────────────────────

def _store_batch_result(
    raw: pd.DataFrame,
    batch: list[str],
    days: int,
    result: dict[str, pd.DataFrame],
) -> None:
    """yf.download 응답을 종목별로 분해해 result/캐시에 적재.

    종목 단위 try/except 로 부분 실패를 격리한다 (한 종목 파싱 실패가
    같은 배치의 다른 종목을 막지 않음).
    """
    if raw is None or len(raw) == 0:
        return
    received_at = datetime.now(timezone.utc)
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in batch:
            try:
                df = raw.xs(ticker, axis=1, level=1).dropna(how="all").copy()
                df.index = pd.to_datetime(df.index)
                if len(df) > 10:
                    _attach_snapshot_metadata(
                        df, symbol=ticker, source="yfinance", timeframe="1d",
                        session="regular", adjustment="adjusted", received_at=received_at,
                    )
                    result[ticker] = df
                    _save_cache(f"price_{ticker}_{days}d", df)
            except Exception:
                pass
    else:
        # 단일 종목 반환 형태
        ticker = batch[0]
        try:
            df = raw.dropna(how="all").copy()
            df.index = pd.to_datetime(df.index)
            if len(df) > 10:
                _attach_snapshot_metadata(
                    df, symbol=ticker, source="yfinance", timeframe="1d",
                    session="regular", adjustment="adjusted", received_at=received_at,
                )
                result[ticker] = df
                _save_cache(f"price_{ticker}_{days}d", df)
        except Exception:
            pass


def _download_batch_with_retry(
    yf,
    batch: list[str],
    period: str,
    days: int,
    result: dict[str, pd.DataFrame],
    batch_seq: int,
) -> bool:
    """단일 배치를 지수 백오프로 재시도하며 다운로드.

    재시도 사이 대기: PRICE_BACKOFF_BASE^attempt 초 (2·4·8s) + 고정 지터.
    지터는 batch_seq 기반 결정론적 값(0~0.5s) — random 미사용으로 재현 가능.

    Returns:
        True  — 배치에서 1종목 이상 적재 성공
        False — 모든 재시도 실패 (적재 0종목)
    """
    before = len(result)
    # 배치 순번 기반 고정 지터 (0.0 ~ 0.45s, 동시 배치 thundering-herd 완화)
    jitter = (batch_seq % 10) / 20.0

    for attempt in range(PRICE_MAX_RETRIES):
        try:
            raw = yf.download(
                batch, period=period, auto_adjust=True,
                progress=False, threads=True,
            )
            _store_batch_result(raw, batch, days, result)
            if len(result) > before:
                return True
            # 예외 없이 빈 응답 — 재시도로 회복될 여지가 있어 동일 백오프 적용
            logger.warning("배치 빈 응답 %s (시도 %d/%d)", batch, attempt + 1, PRICE_MAX_RETRIES)
        except Exception as e:
            logger.warning("배치 다운로드 실패 %s (시도 %d/%d): %s",
                           batch, attempt + 1, PRICE_MAX_RETRIES, e)

        # 마지막 시도가 아니면 백오프 후 재시도
        if attempt < PRICE_MAX_RETRIES - 1:
            delay = PRICE_BACKOFF_BASE ** (attempt + 1) + jitter
            time.sleep(delay)

    return len(result) > before


def fetch_prices(
    tickers: list[str],
    days: int = 1260,   # 약 5년
    batch_size: int = 20,
) -> dict[str, pd.DataFrame]:
    """yfinance로 OHLCV 다운로드. 종목별 캐시 적용.

    견고성:
      - 배치별 지수 백오프 재시도 (최대 PRICE_MAX_RETRIES회, 2·4·8s + 고정 지터)
      - 반복 실패 시 배치 크기 동적 축소 (20→10→5) 후 재시도
      - 종목 단위 부분 실패 격리 (_store_batch_result)
      - 캐시 우선 사용 (TTL 내 캐시는 네트워크 호출 없이 반환)

    Returns:
        {ticker: DataFrame(Date, Open, High, Low, Close, Volume)}
    """
    import yfinance as yf

    result: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []

    for ticker in tickers:
        key = f"price_{ticker}_{days}d"
        cached = _load_cache(key, ttl_hours=PRICE_TTL_H)
        if cached is not None:
            result[ticker] = cached
        else:
            to_fetch.append(ticker)

    if to_fetch:
        logger.info("가격 다운로드: %d종목", len(to_fetch))
        period = f"{days // 252 + 1}y"

        # 1차 배치 크기는 호출자 지정값, 이후 축소 단계는 PRICE_SHRINK_STEPS 기준
        batch_seq = 0
        for i in range(0, len(to_fetch), batch_size):
            batch = to_fetch[i : i + batch_size]
            ok = _download_batch_with_retry(yf, batch, period, days, result, batch_seq)
            batch_seq += 1

            # 배치 전체 실패 시 더 작은 단위로 쪼개 재시도 (부분 회복 시도).
            # 각 축소 단계는 '아직 미확보' 종목만 더 작은 서브배치로 재시도하고,
            # 전부 확보될 때까지 다음(더 작은) 단계로 계속 내려간다.
            if not ok and len(batch) > PRICE_SHRINK_STEPS[-1]:
                for sub_size in PRICE_SHRINK_STEPS:
                    remaining = [t for t in batch if t not in result]
                    if not remaining:
                        break  # 전 종목 확보 완료
                    if sub_size >= len(remaining):
                        continue  # 현재 미확보 수보다 큰 분할은 의미 없음
                    logger.warning("배치 축소 재시도: %d종목 → %d씩 분할", len(remaining), sub_size)
                    for j in range(0, len(remaining), sub_size):
                        sub = remaining[j : j + sub_size]
                        _download_batch_with_retry(yf, sub, period, days, result, batch_seq)
                        batch_seq += 1

    loaded = len(result)
    requested = len(tickers)
    failed = requested - loaded
    if failed > 0:
        logger.warning("가격 로드 실패 종목 %d/%d개 (캐시·재시도·축소 후에도 미확보)",
                       failed, requested)
    logger.info("가격 로드 완료: %d/%d종목", loaded, requested)
    return result


# ── Fear / Greed Proxy ────────────────────────────────────────────────────────

def build_fear_greed_proxy(days: int = 1260) -> pd.Series:
    """자체 Fear/Greed proxy 지수 (0=극도공포, 100=극도탐욕).

    구성 요소 (각 0~100으로 정규화 후 평균):
      1. VIX 역수         — VIX 낮을수록 탐욕
      2. QQQ 125일 모멘텀 — 상승 추세일수록 탐욕
      3. 신용 스프레드    — HYG/IEF 비율 높을수록 탐욕 (정크 수요 강)
      4. 안전자산 강세    — TLT/SPY 비율 낮을수록 탐욕
      5. SPY RSI(14)      — RSI 높을수록 탐욕
    """
    cache_key = f"fear_greed_{days}d"
    cached = _load_cache(cache_key, ttl_hours=PRICE_TTL_H)
    if cached is not None:
        return cached["fg_score"]

    prices = fetch_prices(_MACRO_TICKERS, days=days)

    def _close(ticker: str) -> pd.Series | None:
        df = prices.get(ticker)
        return df["Close"] if df is not None and "Close" in df.columns else None

    def _rank_normalize(s: pd.Series, window: int = 252) -> pd.Series:
        """252일 롤링 백분위 → 0~100"""
        return s.rolling(window, min_periods=60).rank(pct=True) * 100

    components: list[pd.Series] = []

    # 1. VIX 역수 (VIX 높으면 공포)
    vix = _close("^VIX")
    if vix is not None:
        components.append(_rank_normalize(-vix).rename("inv_vix"))

    # 2. QQQ 125일 모멘텀
    qqq = _close("QQQ")
    if qqq is not None:
        mom = qqq / qqq.shift(125) - 1
        components.append(_rank_normalize(mom).rename("qqq_mom"))

    # 3. 신용 스프레드 (HYG/IEF — 정크 대 국채)
    hyg, ief = _close("HYG"), _close("IEF")
    if hyg is not None and ief is not None:
        credit = (hyg / ief).dropna()
        aligned = credit.reindex(hyg.index)
        components.append(_rank_normalize(aligned).rename("credit_demand"))

    # 4. 안전자산 역수 (TLT/SPY 높으면 공포)
    tlt, spy = _close("TLT"), _close("SPY")
    if tlt is not None and spy is not None:
        safe_haven = (tlt / spy).dropna()
        aligned = safe_haven.reindex(tlt.index)
        components.append(_rank_normalize(-aligned).rename("inv_safe_haven"))

    # 5. SPY RSI(14)
    if spy is not None:
        delta = spy.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        components.append(rsi.rename("spy_rsi"))

    if not components:
        logger.warning("Fear/Greed proxy 구성 실패 — 빈 시리즈 반환")
        return pd.Series(dtype=float, name="fg_score")

    # 공통 날짜 기준 평균
    df_all = pd.concat(components, axis=1).dropna(how="all")
    fg = df_all.mean(axis=1).rename("fg_score").clip(0, 100)

    _save_cache(cache_key, fg.to_frame())
    return fg


def get_fg_proxy_score() -> float:
    """오늘 Fear/Greed proxy 점수 반환 (0=극도공포, 100=극도탐욕).

    1년치 데이터만 사용해 빠르게 계산. 캐시 1시간.
    네트워크/계산 실패 시 -1 반환.
    """
    cache_key = "fg_proxy_today"
    cached = _load_cache(cache_key, ttl_hours=1.0)
    if cached is not None and "score" in cached.columns:
        return float(cached["score"].iloc[0])

    try:
        fg = build_fear_greed_proxy(days=252)
        if fg.empty:
            return -1.0
        score = float(fg.dropna().iloc[-1])
        import pickle
        _cache_path(cache_key).write_bytes(pickle.dumps(
            pd.DataFrame({"score": [score]})
        ))
        return score
    except Exception as e:
        logger.warning("get_fg_proxy_score 실패: %s", e)
        return -1.0


# ── 종목별 피처 ───────────────────────────────────────────────────────────────

def build_stock_features(
    ticker: str,
    price_df: pd.DataFrame,
    market_features: pd.DataFrame,
    qqq_close: pd.Series | None = None,
    sector_id: int = 0,
) -> pd.DataFrame:
    """단일 종목 전체 피처 생성.

    피처 그룹 (features.py + 추가):
      기술적     : 이동평균(SMA/EMA), 오실레이터(RSI/MACD/Stochastic/Williams%R/CCI)
                   Bollinger, 모멘텀(6개 기간), 이격도(20/60/120), 가격가속도(감마)
                   실현변동성, ATR, VoV(변동성의변동성)
      일목균형표 : 원시값 4개 + 신호 6개 (구름위치, TK크로스, 기준선이격)
      MA크로스   : 골든크로스, EMA단기강세, SMA20/50 위치
      거래량     : OBV, CMF, 거래량비율, 거래량Z-score
      52주       : 고저 대비 위치
      종목고유   : QQQ 초과모멘텀(60d), beta_60d, 섹터ID, 생존편향페널티
      시장공통   : fg_score, vix (market_features에서 병합)
    """
    from ml.features import (
        compute_features, stochastic, williams_r, cci, disparity,
        obv, cmf, price_acceleration, vol_of_vol,
        ichimoku_signals, ma_cross_signals,
    )

    if len(price_df) < 60:
        return pd.DataFrame()

    close = price_df["Close"].copy()

    # OHLCV → features.py compute_features 호환 포맷
    df_feat = price_df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })

    # ── 기술적 피처 전체 세트 ──────────────────────────────────────────────
    tech = compute_features(df_feat, include_ichimoku=True, include_atr=True)

    # ── 종목 고유 피처 ─────────────────────────────────────────────────────
    extra = pd.DataFrame(index=close.index)

    # QQQ 대비 초과 모멘텀 (60일)
    if qqq_close is not None:
        qqq_r = qqq_close.reindex(close.index).ffill()
        extra["excess_mom_60d"] = (close / close.shift(60) - 1) - (qqq_r / qqq_r.shift(60) - 1)
        extra["excess_mom_20d"] = (close / close.shift(20) - 1) - (qqq_r / qqq_r.shift(20) - 1)
        # QQQ 대비 베타 (60일 롤링)
        rets    = close.pct_change()
        qqq_ret = qqq_r.pct_change()
        cov = rets.rolling(60).cov(qqq_ret)
        var = qqq_ret.rolling(60).var().replace(0, np.nan)
        extra["beta_60d"]  = cov / var
        extra["beta_20d"]  = rets.rolling(20).cov(qqq_ret) / qqq_ret.rolling(20).var().replace(0, np.nan)
        # 감마: 베타의 변화율 (베타 가속도)
        extra["beta_gamma"] = extra["beta_60d"].diff(20)
    else:
        for col in ("excess_mom_60d", "excess_mom_20d", "beta_60d", "beta_20d", "beta_gamma"):
            extra[col] = np.nan

    extra["sector_id"] = float(sector_id)

    # ── 합산 ──────────────────────────────────────────────────────────────
    feat = pd.concat([tech, extra], axis=1)
    feat = feat.join(market_features, how="left")
    return feat.dropna(how="all")


# ── 섹터 매핑 ────────────────────────────────────────────────────────────────

_SECTOR_LABELS = {
    "Technology": 1, "Communication Services": 2, "Consumer Discretionary": 3,
    "Health Care": 4, "Financials": 5, "Industrials": 6,
    "Consumer Staples": 7, "Energy": 8, "Materials": 9,
    "Real Estate": 10, "Utilities": 11,
}


def _get_sector_map(tickers: list[str]) -> dict[str, int]:
    """yfinance info로 섹터 조회 → 정수 매핑. 실패 시 0."""
    cache_key = "sector_map_" + hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:8]
    cached = _load_cache(cache_key, ttl_hours=168)  # 1주일 캐시
    if cached is not None and "ticker" in cached.columns:
        return dict(zip(cached["ticker"], cached["sector_id"]))

    import yfinance as yf
    result: dict[str, int] = {}
    for ticker in tickers:
        try:
            sector = yf.Ticker(ticker).info.get("sector", "") or ""
            result[ticker] = _SECTOR_LABELS.get(sector, 0)
        except Exception:
            result[ticker] = 0

    df = pd.DataFrame({"ticker": list(result.keys()), "sector_id": list(result.values())})
    _save_cache(cache_key, df)
    return result


# ── 메인 데이터셋 빌더 ────────────────────────────────────────────────────────

def _membership_frame_from_intervals(intervals: object) -> pd.DataFrame:
    """Convert provider intervals to the PIT helper's explicit row contract."""

    if intervals is None:
        return pd.DataFrame(columns=["symbol", "effective_from", "effective_to"])
    if not isinstance(intervals, Mapping):
        raise TypeError("membership intervals must be a mapping")
    records: list[dict[str, object]] = []
    for raw_symbol, raw_entries in sorted(intervals.items(), key=lambda item: str(item[0]).upper()):
        if raw_entries is None:
            continue
        if isinstance(raw_entries, Mapping):
            entries = [raw_entries]
        elif isinstance(raw_entries, tuple) and len(raw_entries) == 2 and not isinstance(raw_entries[0], (list, tuple, Mapping)):
            entries = [raw_entries]
        elif isinstance(raw_entries, (list, tuple)):
            entries = list(raw_entries)
        else:
            raise TypeError(f"membership intervals for {raw_symbol!r} must be a list")
        for entry in entries:
            if isinstance(entry, Mapping):
                start = entry.get("effective_from", entry.get("start"))
                end = entry.get("effective_to", entry.get("end"))
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                start, end = entry
            else:
                raise ValueError(f"membership interval for {raw_symbol!r} must contain start and end")
            records.append({
                "symbol": raw_symbol,
                "effective_from": start,
                "effective_to": end,
            })
    return pd.DataFrame(records, columns=["symbol", "effective_from", "effective_to"])

def index_multitf_rsi(close: "pd.Series") -> "pd.DataFrame":
    """지수(벤치마크) 일봉/주봉/월봉 RSI(14) — 일별 인덱스로 정렬.

    룩어헤드 방지: 일봉 RSI 는 당일 종가까지(시점 정합), 주/월봉 RSI 는 **직전 완성 봉**만
    사용(shift(1)) 후 ffill — 진행 중인 주/월의 미완성 종가를 쓰지 않는다.
    """
    from ml.features import rsi
    out = pd.DataFrame(index=close.index)
    c = close.dropna()
    out["idx_rsi_d"] = rsi(c, 14).reindex(close.index)
    wk = rsi(c.resample("W").last(), 14).shift(1)        # 직전 완성 주봉
    out["idx_rsi_w"] = wk.reindex(close.index, method="ffill")
    mo = rsi(c.resample("ME").last(), 14).shift(1)       # 직전 완성 월봉
    out["idx_rsi_m"] = mo.reindex(close.index, method="ffill")
    return out


def build_ml_dataset(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all", "kr_top10", "kr30"] = "nasdaq100",
    days: int = 1260,
    forward_days: int = 20,
    benchmark_ticker: str = "QQQ",
    survivorship_free: bool = False,
) -> dict:
    """ML 학습용 데이터셋 구성.

    benchmark_ticker: 초과수익·베타 기준 지수(미국=QQQ, 한국=^KS11 KOSPI).
                      excess/beta/excess_mom 피처와 라벨이 이 벤치마크 대비로 계산됨.

    Returns:
        features  : pd.DataFrame  (date × ticker → flat index, 피처 컬럼)
        returns   : pd.Series     (forward_days 후 수익률, 타겟)
        excess    : pd.Series     (벤치마크 대비 초과수익률, 타겟)
        universe  : list[str]
        fg_score  : pd.Series     (Fear/Greed proxy)
        meta      : dict          (mode, days, forward_days, benchmark, bias_warning)
    """
    logger.info("ML 데이터셋 구성 시작 (mode=%s, days=%d, fwd=%d일, bench=%s)",
                mode, days, forward_days, benchmark_ticker)

    # 생존편향 제거: 현재 구성종목 대신 시점별 멤버십(편출·상폐분 포함). 美 S&P500 = fja05680.
    membership_frame: pd.DataFrame | None = None
    membership_requested = bool(survivorship_free and mode in ("sp500", "all"))
    membership_warnings: list[str] = []
    membership_diagnostics: list[dict[str, Any]] = []
    membership_runtime_error = False
    membership_used = False
    if membership_requested:
        try:
            from providers import index_membership as _im
            from datetime import date as _date, timedelta as _td
            _start = (_date.today() - _td(days=int(days * 1.6))).isoformat()
            raw_intervals = _im.membership_intervals("sp500")
            membership_frame = _membership_frame_from_intervals(raw_intervals)
            if membership_frame.empty:
                raise ValueError("membership intervals are empty")
            validation = point_in_time_universe(membership_frame, pd.Timestamp(_start, tz="UTC"))
            membership_diagnostics.extend(validation.diagnostics)
            if validation.diagnostics or validation.status != "ok":
                membership_warnings.append("membership_metadata_invalid")
                raise ValueError("membership intervals contain invalid rows")
            universe = _im.members_in_window("sp500", _start)
            if not universe:
                raise ValueError("membership universe is empty")
            logger.info("생존편향 제거 유니버스(시점별 멤버십): %d종목 (현재구성 아님)", len(universe))
        except Exception as e:
            logger.warning("멤버십 유니버스 실패 — 현재구성 폴백: %s", e)
            membership_frame = None
            membership_warnings.append("membership_fallback_current_universe")
            universe = fetch_universe(mode)
    else:
        universe = fetch_universe(mode)
    logger.info("유니버스: %d종목", len(universe))

    # 가격 다운로드 (벤치마크 포함)
    all_tickers = list(set(universe + [benchmark_ticker, "QQQ", "SPY", "^VIX", "HYG", "LQD", "IEF", "TLT"]))
    prices = fetch_prices(all_tickers, days=days)
    price_snapshots = {
        ticker: snapshot
        for ticker, frame in sorted(prices.items())
        if (snapshot := _snapshot_stamps(frame.attrs.get("data_snapshot"))) is not None
    }
    coverage = source_coverage(price_snapshots.values(), expected_symbols=sorted(all_tickers))

    # Fear/Greed proxy
    fg = build_fear_greed_proxy(days=days)

    # 시장 공통 피처 (Fear/Greed + VIX + 매크로)
    vix_df = prices.get("^VIX")
    market_feat = fg.to_frame("fg_score")
    if vix_df is not None:
        market_feat["vix"] = vix_df["Close"]

    market_feat = market_feat.ffill(limit=5)
    # 참고: 매크로 피처(수익률곡선·크레딧·달러 등)는 종목 간 동일값이므로
    # 크로스섹셔널 Ranker에 포함하지 않음. LeverageModel/MetaAllocator에서 별도 사용.

    # 벤치마크 선행 수익률 (초과수익·베타 계산용 — 미국 QQQ / 한국 KOSPI)
    bench_df = prices.get(benchmark_ticker)
    qqq_close = bench_df.get("Close") if bench_df is not None else None
    if qqq_close is None:
        logger.warning("벤치마크 %s 가격 없음 — 초과수익이 절대수익으로 폴백", benchmark_ticker)
    else:
        # 지수 다중 타임프레임 RSI(일/주/월)를 시장공통 피처로 추가(전 종목 broadcast)
        try:
            market_feat = market_feat.join(index_multitf_rsi(qqq_close), how="left").ffill(limit=5)
        except Exception as e:
            logger.warning("지수 다중TF RSI 생성 실패: %s", e)

    all_features: list[pd.DataFrame] = []
    all_returns:  list[pd.Series]    = []
    all_excess:   list[pd.Series]    = []

    # 섹터 매핑 (GICS 11개 섹터 정수 인코딩)
    sector_map = _get_sector_map(universe)

    for ticker in universe:
        df = prices.get(ticker)
        if df is None or len(df) < 126:
            continue

        sector_id = sector_map.get(ticker, 0)
        feat = build_stock_features(ticker, df, market_feat,
                                    qqq_close=qqq_close, sector_id=sector_id)
        if feat.empty:
            continue

        # 생존편향 제거: 이 종목이 실제 지수 멤버였던 날짜 표본만(편입 전·편출 후 제외 = point-in-time)
        if membership_frame is not None:
            membership_used = True
            try:
                keep: list[bool] = []
                for value in feat.index:
                    pit = point_in_time_universe(membership_frame, pd.Timestamp(value))
                    membership_diagnostics.extend(pit.diagnostics)
                    if pit.status != "ok":
                        raise ValueError("membership PIT result is not ok")
                    keep.append(ticker in pit)
                feat = feat[keep]
                if feat.empty:
                    continue
            except Exception as exc:
                membership_runtime_error = True
                membership_warnings.append("membership_filter_failed")
                logger.warning("PIT 멤버십 필터 실패 — 해당 표본은 폴백: %s", exc)
                membership_frame = None

        # forward 수익은 갭 없는 원 가격 인덱스에서 계산한 뒤 feat.index 로 정렬 —
        # survivorship_free 로 멤버십 구간을 필터하면 feat.index 에 공백이 생겨 위치기반 pct_change 가
        # '편출 직전→수년 뒤 재편입' 수익을 라벨로 잡는 왜곡이 난다(감사 확정). 종목·벤치 동일 처리로 대칭.
        fwd_ret = df["Close"].pct_change(forward_days).shift(-forward_days).reindex(feat.index)

        # QQQ 초과수익
        if qqq_close is not None:
            qqq_fwd = qqq_close.pct_change(forward_days).shift(-forward_days).reindex(feat.index)
            excess = fwd_ret - qqq_fwd
        else:
            excess = fwd_ret.copy()

        # MultiIndex (date, ticker)
        feat.index = pd.MultiIndex.from_arrays(
            [feat.index, [ticker] * len(feat)], names=["date", "ticker"]
        )
        fwd_ret.index = feat.index
        excess.index  = feat.index

        all_features.append(feat)
        all_returns.append(fwd_ret)
        all_excess.append(excess)

    if not all_features:
        logger.warning("유효 종목 없음 — 빈 데이터셋 반환")
        survivorship_state = _survivorship_state(
            membership_requested,
            membership_frame,
            membership_used,
            membership_runtime_error,
            membership_diagnostics,
            membership_warnings,
        )
        return {"features": pd.DataFrame(), "returns": pd.Series(), "excess": pd.Series(),
                "universe": [], "fg_score": fg, "meta": {
                    "source_coverage": coverage,
                    "data_snapshots": {ticker: snapshot.to_dict() for ticker, snapshot in price_snapshots.items()},
                    **survivorship_state,
                }}

    features = pd.concat(all_features)
    returns  = pd.concat(all_returns).rename("fwd_return")
    excess   = pd.concat(all_excess).rename("fwd_excess")
    features.attrs["source_coverage"] = coverage
    features.attrs["data_snapshots"] = {
        ticker: snapshot.to_dict() for ticker, snapshot in price_snapshots.items()
    }

    survivorship_state = _survivorship_state(
        membership_requested,
        membership_frame,
        membership_used,
        membership_runtime_error,
        membership_diagnostics,
        membership_warnings,
    )

    logger.info(
        "데이터셋 완성: %d행 × %d피처 | 종목 %d개",
        len(features), features.shape[1],
        features.index.get_level_values("ticker").nunique(),
    )

    return {
        "features": features,
        "returns":  returns,
        "excess":   excess,
        "universe": universe,
        "fg_score": fg,
        "meta": {
            "mode": mode,
            "days": days,
            "forward_days": forward_days,
            "benchmark": benchmark_ticker,
            **survivorship_state,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_coverage": coverage,
            "data_snapshots": {ticker: snapshot.to_dict() for ticker, snapshot in price_snapshots.items()},
        },
    }


def _survivorship_state(
    requested: bool,
    membership_frame: pd.DataFrame | None,
    used: bool,
    runtime_error: bool,
    diagnostics: Iterable[Mapping[str, Any]],
    warnings: Iterable[str],
) -> dict[str, Any]:
    """Build explicit survivorship metadata for both normal and fallback paths."""

    diagnostic_rows = [dict(row) for row in diagnostics]
    if not requested:
        return {
            "survivorship_free": False,
            "survivorship_status": "not_requested",
            "survivorship_warnings": [],
            "survivorship_diagnostics": [],
            "bias_warning": "현재 구성종목 기준 — survivorship bias 있음",
        }
    if membership_frame is not None and used and not runtime_error and not diagnostic_rows:
        return {
            "survivorship_free": True,
            "survivorship_status": "applied",
            "survivorship_warnings": [],
            "survivorship_diagnostics": [],
            "bias_warning": "시점별 멤버십 적용 — survivorship bias 제거(美 상폐주 가격은 무료 공백으로 부분)",
        }
    warning_values = list(dict.fromkeys([
        *[str(value) for value in warnings],
        "survivorship_free_unknown",
        "membership_metadata_incomplete",
    ]))
    return {
        "survivorship_free": False,
        "survivorship_status": "unknown",
        "survivorship_warnings": warning_values,
        "survivorship_diagnostics": diagnostic_rows,
        "bias_warning": "시점별 멤버십 메타데이터 불명확 — survivorship bias 제거 여부 알 수 없음; 현재 구성종목 폴백",
    }


# ── sweet_spot 호환 실데이터 빌더 ──────────────────────────────────────────────

def build_real_sweetspot_data(
    asset_ticker: str = "QQQ",
    days: int = 756,
) -> dict:
    """실시장 데이터를 sweet_spot.optimize_sweet_spot() 호환 포맷으로 반환.

    generate_synthetic_market_data()와 동일한 키 구조:
      close      — pd.Series  (asset 종가)
      spy_close  — pd.Series  (SPY 종가)
      qqq_close  — pd.Series  (QQQ 종가)
      features   — pd.DataFrame  (8개 피처)

    피처:
      momentum    — 20일 수익률
      momentum_60 — 60일 수익률 (중기 트렌드)
      volatility  — 20일 실현변동성
      sentiment   — (RSI14 - 50) / 50  ([-1, 1])
      above_ma200 — 200일 MA 위 여부 (0/1)
      vix_norm    — VIX 백분위 역수 (높을수록 탐욕)
      credit_sprd — HYG/IEF 비율 정규화 (높을수록 신용 낙관)
      fg_proxy    — Fear/Greed proxy 백분위 (0~1)
    """
    macro_tickers = list({asset_ticker, "SPY", "QQQ", "^VIX", "HYG", "IEF"})
    prices  = fetch_prices(macro_tickers, days=days + 60)   # 60일 여유

    def _close(t: str) -> pd.Series | None:
        df = prices.get(t)
        return df["Close"] if df is not None and "Close" in df.columns else None

    asset = _close(asset_ticker)
    spy   = _close("SPY")
    qqq   = _close("QQQ")
    vix   = _close("^VIX")
    hyg   = _close("HYG")
    ief   = _close("IEF")

    if asset is None:
        raise ValueError(f"{asset_ticker} 가격 조회 실패")

    # 공통 날짜 인덱스
    idx = asset.dropna().index
    for s in (spy, qqq):
        if s is not None:
            idx = idx.intersection(s.dropna().index)
    idx = idx[-days:]   # 최신 days일만 사용

    asset = asset.reindex(idx)
    spy   = spy.reindex(idx)  if spy  is not None else asset.copy().rename("SPY")
    qqq   = qqq.reindex(idx)  if qqq  is not None else asset.copy().rename("QQQ")

    # 기본 피처
    mom   = asset.pct_change(20).fillna(0)
    mom60 = asset.pct_change(60).fillna(0)
    vol   = asset.pct_change().rolling(20, min_periods=5).std().fillna(0)

    delta = asset.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    sent  = ((rsi - 50) / 50).fillna(0)

    ma200       = asset.rolling(200, min_periods=100).mean()
    above_ma200 = (asset > ma200).astype(float).fillna(0.5)

    # VIX 백분위 역수 (낮은 VIX = 낙관 → 1에 가까움)
    if vix is not None:
        vix_r = vix.reindex(idx).ffill()
        vix_norm = 1 - vix_r.rolling(252, min_periods=60).rank(pct=True).fillna(0.5)
    else:
        vix_norm = pd.Series(0.5, index=idx)

    # 신용 스프레드 (HYG/IEF 비율 백분위)
    if hyg is not None and ief is not None:
        hyg_r  = hyg.reindex(idx).ffill()
        ief_r  = ief.reindex(idx).ffill()
        credit = (hyg_r / ief_r).rolling(252, min_periods=60).rank(pct=True).fillna(0.5)
    else:
        credit = pd.Series(0.5, index=idx)

    # Fear/Greed proxy (0~100 → 0~1)
    try:
        fg = build_fear_greed_proxy(days=days + 60)
        fg_aligned = fg.reindex(idx).ffill().fillna(50.0) / 100.0
    except Exception:
        fg_aligned = pd.Series(0.5, index=idx)

    features = pd.DataFrame({
        "momentum":    mom,
        "momentum_60": mom60,
        "volatility":  vol,
        "sentiment":   sent,
        "above_ma200": above_ma200,
        "vix_norm":    vix_norm,
        "credit_sprd": credit,
        "fg_proxy":    fg_aligned,
    }, index=idx).fillna(0)

    return {
        "close":     asset.rename(asset_ticker),
        "spy_close": spy.rename("SPY"),
        "qqq_close": qqq.rename("QQQ"),
        "features":  features,
    }
