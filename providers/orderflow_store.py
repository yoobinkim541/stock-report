"""Versioned, bounded local capture for realtime KIS trade and book events."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import safe_io


EVENT_VERSION = 1
ORDERFLOW_DIR = Path(os.path.expanduser("~/reports/ml-data/orderflow"))
# A busy US session can produce hundreds of megabytes of trades and sampled
# books. Keep a generous daily bound while leaving retention as the long-term
# disk guardrail.
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 14
DEFAULT_LOAD_LIMIT = 10_000
DEFAULT_SCAN_BYTES = 8 * 1024 * 1024


def _number(value: Any, *, positive: bool = False) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or (positive and out <= 0):
        return None
    return out


def _levels(value: Any) -> list[list[float]]:
    out: list[list[float]] = []
    if not isinstance(value, (list, tuple)):
        return out
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _number(row[0], positive=True)
        quantity = _number(row[1])
        if price is not None and quantity is not None and quantity >= 0:
            out.append([price, quantity])
    return out


def _exchange_at(record: Mapping[str, Any], market: str, received_at: float) -> str | None:
    raw_time = "".join(character for character in str(record.get("exchange_time") or "") if character.isdigit())
    if len(raw_time) != 6:
        return None
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    raw_date = "".join(character for character in str(record.get("exchange_date") or "") if character.isdigit())
    if len(raw_date) != 8:
        raw_date = datetime.fromtimestamp(received_at, tz=zone).strftime("%Y%m%d")
    try:
        local = datetime.strptime(raw_date + raw_time, "%Y%m%d%H%M%S").replace(tzinfo=zone)
    except ValueError:
        return None
    return local.isoformat()


def _session_date(market: str, received_at: float, exchange_at: str | None = None) -> str:
    if exchange_at:
        try:
            return datetime.fromisoformat(str(exchange_at)).date().isoformat()
        except ValueError:
            pass
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    return datetime.fromtimestamp(received_at, tz=zone).date().isoformat()


def current_session_date(symbol: str, *, now: float | None = None) -> str:
    normalized = str(symbol or "").strip().upper()
    base = normalized[:-3] if normalized.endswith((".KS", ".KQ")) else normalized
    market = "KR" if base.isdigit() and len(base) == 6 else "US"
    timestamp = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    return _session_date(market, timestamp)


class OrderFlowRecorder:
    """Normalize realtime records while keeping provenance and sampling explicit."""

    def __init__(self, *, book_sample_seconds: float = 1.0):
        self.book_sample_seconds = max(0.0, float(book_sample_seconds))
        self._previous_cumulative: dict[str, float] = {}
        self._last_book_at: dict[str, float] = {}

    def capture(
        self,
        record: Mapping[str, Any],
        *,
        received_at: float,
        market: str,
        delayed: bool = False,
    ) -> dict[str, Any] | None:
        symbol = str(record.get("symbol") or "").strip().upper()
        received = _number(received_at)
        kind = str(record.get("kind") or "").strip().lower()
        if not symbol or received is None:
            return None
        normalized_market = str(market or "").strip().upper()
        exchange_at = record.get("exchange_at") or _exchange_at(record, normalized_market, received)
        common = {
            "version": EVENT_VERSION,
            "symbol": symbol,
            "market": normalized_market,
            "received_at": received,
            "exchange_at": exchange_at,
            "session_date": _session_date(normalized_market, received, exchange_at),
            "source": str(record.get("source") or "kis_ws"),
            "delayed": bool(delayed),
        }
        if kind == "trade":
            price = _number(record.get("price"), positive=True)
            if price is None:
                return None
            cumulative = _number(record.get("volume"))
            provider_size = _number(record.get("trade_size"))
            previous = self._previous_cumulative.get(symbol)
            partial = previous is None and not bool(provider_size and provider_size > 0)
            anomaly = bool(previous is not None and cumulative is not None and cumulative < previous)
            size = 0.0
            method = "unavailable"
            if provider_size is not None and provider_size > 0:
                size = provider_size
                method = "provider_trade_size"
            elif cumulative is not None:
                method = "cumulative_delta"
                if previous is not None and not anomaly:
                    size = max(0.0, cumulative - previous)
            if cumulative is not None:
                self._previous_cumulative[symbol] = cumulative
            return {
                **common,
                "event_type": "trade",
                "price": price,
                "cumulative_volume": cumulative,
                "size": size,
                "volume_method": method,
                "volume_partial": partial,
                "volume_anomaly": anomaly,
            }
        if kind != "ask":
            return None
        previous_at = self._last_book_at.get(symbol)
        if previous_at is not None and received - previous_at < self.book_sample_seconds:
            return None
        bids = _levels(record.get("bids"))
        asks = _levels(record.get("asks"))
        if not bids and not asks:
            return None
        self._last_book_at[symbol] = received
        best_bid = _number(record.get("best_bid"), positive=True) or (bids[0][0] if bids else None)
        best_ask = _number(record.get("best_ask"), positive=True) or (asks[0][0] if asks else None)
        return {
            **common,
            "event_type": "book",
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "depth": max(len(bids), len(asks)),
        }


_SYMBOL_PATH_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def event_path(date_utc: str, symbol: str, base_dir: Path | str | None = None) -> Path:
    date = str(date_utc)
    try:
        if datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
            raise ValueError
    except ValueError as exc:
        raise ValueError("invalid order-flow date") from exc
    normalized_symbol = str(symbol or "").strip().upper()
    if not _SYMBOL_PATH_RE.fullmatch(normalized_symbol):
        raise ValueError("invalid order-flow symbol")
    return Path(base_dir or ORDERFLOW_DIR) / date / f"{normalized_symbol}.jsonl"


def capture_status_path(base_dir: Path | str | None = None) -> Path:
    return Path(base_dir or ORDERFLOW_DIR) / "_capture_status.json"


def write_capture_status(status: Mapping[str, Any], *, base_dir: Path | str | None = None) -> None:
    payload = dict(status)
    payload.setdefault("version", EVENT_VERSION)
    payload.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    safe_io.atomic_write_json(str(capture_status_path(base_dir)), payload)


def load_capture_status(*, base_dir: Path | str | None = None) -> dict[str, Any]:
    path = capture_status_path(base_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def prune_partitions(
    *,
    base_dir: Path | str | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    root = Path(base_dir or ORDERFLOW_DIR)
    days = max(1, int(retention_days))
    today_text = as_of_date or datetime.now(timezone.utc).date().isoformat()
    try:
        today = datetime.strptime(today_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("invalid order-flow retention date") from exc
    cutoff = today - timedelta(days=days - 1)
    deleted = 0
    reclaimed = 0
    if not root.exists():
        return {"deleted_partitions": 0, "reclaimed_bytes": 0, "cutoff": cutoff.isoformat()}
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            partition_date = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if partition_date >= cutoff:
            continue
        reclaimed += sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
        shutil.rmtree(child)
        deleted += 1
    return {"deleted_partitions": deleted, "reclaimed_bytes": reclaimed, "cutoff": cutoff.isoformat()}


def append_events(
    events: Iterable[Mapping[str, Any] | None],
    *,
    base_dir: Path | str | None = None,
    date_utc: str | None = None,
    max_bytes: int | None = None,
) -> int:
    rows = [dict(event) for event in events if isinstance(event, Mapping)]
    if not rows:
        return 0
    forced_date = date_utc
    payload_lines: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        received = _number(row.get("received_at"))
        partition_date = forced_date or str(row.get("session_date") or "")
        if not partition_date and received is not None:
            partition_date = _session_date(str(row.get("market") or "").upper(), received)
        event_path(partition_date, symbol, base_dir)  # validate before writing any partition
        payload_lines.setdefault((partition_date, symbol), []).append(json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), default=str,
        ))
    payloads = {key: "\n".join(lines) + "\n" for key, lines in payload_lines.items()}
    budget = DEFAULT_MAX_BYTES if max_bytes is None else max(0, int(max_bytes))
    for partition_date in {key[0] for key in payloads}:
        root = Path(base_dir or ORDERFLOW_DIR) / partition_date
        current_size = sum(path.stat().st_size for path in root.glob("*.jsonl")) if root.exists() else 0
        payload_size = sum(
            len(payload.encode("utf-8"))
            for (date, _symbol), payload in payloads.items()
            if date == partition_date
        )
        if current_size + payload_size > budget:
            return 0
    for (partition_date, symbol), payload in payloads.items():
        path = event_path(partition_date, symbol, base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
    return len(rows)


def load_event_window(
    symbol: str,
    date_utc: str | None = None,
    *,
    base_dir: Path | str | None = None,
    limit: int = DEFAULT_LOAD_LIMIT,
    max_scan_bytes: int = DEFAULT_SCAN_BYTES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from providers.intraday_bars import base_symbol

    target = base_symbol(symbol)
    date = date_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = event_path(date, target, base_dir)
    bounded_limit = max(0, int(limit))
    scan_budget = max(0, int(max_scan_bytes))
    capture_status = load_capture_status(base_dir=base_dir)
    dropped_by_session = capture_status.get("dropped_by_session")
    if isinstance(dropped_by_session, Mapping):
        session_dropped = int(dropped_by_session.get(date) or 0)
    else:
        session_dropped = int(capture_status.get("dropped_events") or 0)
    if capture_status:
        capture_status = {**capture_status, "session_dropped_events": session_dropped}
    metadata = {
        "file_bytes": 0,
        "scanned_bytes": 0,
        "returned_events": 0,
        "limit": bounded_limit,
        "truncated": False,
        "capture_status": capture_status,
        "capture_complete": (
            session_dropped == 0
            if capture_status else None
        ),
    }
    if not path.exists() or bounded_limit == 0 or scan_budget == 0:
        return [], metadata
    try:
        file_bytes = path.stat().st_size
        scanned_bytes = min(file_bytes, scan_budget)
        start = file_bytes - scanned_bytes
        with path.open("rb") as handle:
            previous_byte = b"\n"
            if start > 0:
                handle.seek(start - 1)
                previous_byte = handle.read(1)
            handle.seek(start)
            payload = handle.read(scanned_bytes)
        if start > 0 and previous_byte != b"\n":
            newline = payload.find(b"\n")
            payload = payload[newline + 1:] if newline >= 0 else b""
        raw_lines = payload.splitlines()
        rows: list[dict[str, Any]] = []
        for raw_line in raw_lines[-bounded_limit:]:
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if row.get("version") == EVENT_VERSION and base_symbol(str(row.get("symbol") or "")) == target:
                rows.append(row)
        metadata.update({
            "file_bytes": file_bytes,
            "scanned_bytes": scanned_bytes,
            "returned_events": len(rows),
            "truncated": bool(start > 0 or len(raw_lines) > bounded_limit),
        })
        return rows, metadata
    except OSError:
        return [], metadata


def load_events(
    symbol: str,
    date_utc: str | None = None,
    *,
    base_dir: Path | str | None = None,
    limit: int = DEFAULT_LOAD_LIMIT,
    max_scan_bytes: int = DEFAULT_SCAN_BYTES,
) -> list[dict[str, Any]]:
    rows, _metadata = load_event_window(
        symbol,
        date_utc,
        base_dir=base_dir,
        limit=limit,
        max_scan_bytes=max_scan_bytes,
    )
    return rows


def coverage(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [event for event in events if isinstance(event, Mapping)]
    trades = [row for row in rows if row.get("event_type") == "trade"]
    books = [row for row in rows if row.get("event_type") == "book"]
    timestamps = [value for row in rows if (value := _number(row.get("received_at"))) is not None]
    sized = [row for row in trades if (_number(row.get("size")) or 0) > 0]
    has_side = bool(sized) and all(row.get("aggressor_side") in {"buy", "sell"} for row in sized)
    return {
        "event_version": EVENT_VERSION,
        "events": len(rows),
        "trade_events": len(trades),
        "book_events": len(books),
        "first_received_at": min(timestamps) if timestamps else None,
        "last_received_at": max(timestamps) if timestamps else None,
        "max_depth": max((int(row.get("depth") or 0) for row in books), default=0),
        "capabilities": {
            "live_dom": bool(books),
            "historical_depth": bool(books),
            "volume_at_price": bool(sized),
            "footprint": has_side,
            "bid_ask_delta": has_side,
            "orderflow_replay": bool(books and trades),
        },
    }
