from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

KALSHI_MARKETS_URL = "https://external-api.kalshi.com/trade-api/v2/markets"
KALSHI_SERIES_URL = "https://external-api.kalshi.com/trade-api/v2/series"
KALSHI_SOURCE_URL = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_DEFAULT_KEYWORDS = [
    "fed",
    "federal reserve",
    "interest rate",
    "rates",
    "inflation",
    "cpi",
    "unemployment",
    "jobs",
    "gdp",
    "recession",
    "tariff",
    "treasury",
    "debt ceiling",
    "government shutdown",
    "oil",
    "bitcoin",
    "ethereum",
    "nvidia",
    "semiconductor",
    "artificial intelligence",
    "china",
    "taiwan",
    "russia",
    "ukraine",
    "israel",
    "iran",
    "election",
    "president",
    "s&p 500",
    "nasdaq",
]


def _as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _matches_keywords(row: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("title", "subtitle", "yes_sub_title", "no_sub_title", "ticker", "event_ticker")
    ).lower()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(str(word).strip().lower())}(?![a-z0-9])", haystack)
        for word in keywords
        if str(word).strip()
    )


def _probability(row: dict) -> tuple[float | None, float | None, float | None]:
    bid = _as_float(row.get("yes_bid_dollars"))
    ask = _as_float(row.get("yes_ask_dollars"))
    last = _as_float(row.get("last_price_dollars"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        probability = (bid + ask) / 2
    else:
        probability = last
    if probability is None or probability < 0 or probability > 1:
        probability = None
    return (
        None if probability is None else round(probability, 4),
        None if bid is None else round(bid, 4),
        None if ask is None else round(ask, 4),
    )


def _default_keywords() -> list[str]:
    raw = (os.getenv("STOCK_COLLECTOR_KALSHI_KEYWORDS") or "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()] if raw else KALSHI_DEFAULT_KEYWORDS


def _relevant_series(row: dict, keywords: list[str]) -> bool:
    category = str(row.get("category") or "").strip().lower()
    if any(blocked in category for blocked in ("sport", "entertainment", "culture", "game")):
        return False
    text = " ".join([
        str(row.get("title") or ""),
        category,
        " ".join(str(tag) for tag in row.get("tags") or []),
    ]).lower()
    if any(token in category for token in ("economic", "financial", "politic", "crypto", "technology", "energy")):
        return True
    return any(str(keyword).strip().lower() in text for keyword in keywords if str(keyword).strip())


def _discover_series_markets(
    *,
    get: Callable,
    keywords: list[str],
    min_volume: float,
    limit: int,
) -> list[dict]:
    response = get(
        KALSHI_SERIES_URL,
        params={"include_volume": True},
        headers={"User-Agent": "stock-report/1.0 (+yoobinkim2006@gmail.com)"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    series = [row for row in (payload.get("series") or []) if isinstance(row, dict) and _relevant_series(row, keywords)]
    series.sort(key=lambda row: _as_float(row.get("volume_fp") or row.get("volume")) or 0.0, reverse=True)
    max_series = max(1, min(int(os.getenv("STOCK_COLLECTOR_KALSHI_MAX_SERIES", "12")), 30))

    def fetch_one(meta: dict) -> tuple[dict, list[dict]]:
        ticker = str(meta.get("ticker") or "").strip().upper()
        if not ticker:
            return meta, []
        market_response = get(
            KALSHI_MARKETS_URL,
            params={"limit": 100, "status": "open", "mve_filter": "exclude", "series_ticker": ticker},
            headers={"User-Agent": "stock-report/1.0 (+yoobinkim2006@gmail.com)"},
            timeout=15,
        )
        market_response.raise_for_status()
        market_payload = market_response.json()
        return meta, [row for row in (market_payload.get("markets") or []) if isinstance(row, dict)]

    rows: list[dict] = []
    selected = series[:max_series]
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(selected)))) as executor:
        futures = [executor.submit(fetch_one, meta) for meta in selected]
        for future in as_completed(futures):
            try:
                meta, markets = future.result()
            except (requests.RequestException, ValueError, TypeError):
                continue
            for market in markets:
                enriched = dict(market)
                if not enriched.get("title"):
                    enriched["title"] = str(meta.get("title") or "")
                if (_as_float(enriched.get("volume_fp")) or 0.0) < min_volume:
                    continue
                rows.append(enriched)
    rows.sort(key=lambda row: _as_float(row.get("volume_fp")) or 0.0, reverse=True)
    return rows[:limit]


def _normalize_market(row: dict, *, observed_at: str) -> dict | None:
    market_ticker = str(row.get("ticker") or "").strip().upper()
    if not market_ticker:
        return None
    probability, bid, ask = _probability(row)
    if probability is None:
        return None
    title = str(row.get("title") or row.get("yes_sub_title") or market_ticker).strip()
    event_ticker = str(row.get("event_ticker") or "").strip().upper()
    volume = _as_float(row.get("volume_fp")) or 0.0
    volume_24h = _as_float(row.get("volume_24h_fp")) or 0.0
    liquidity = _as_float(row.get("liquidity_dollars"))
    open_interest = _as_float(row.get("open_interest_fp"))
    url_key = event_ticker or market_ticker
    url = f"https://kalshi.com/markets/{quote(url_key.lower())}"
    body = (
        f"Kalshi implied probability for '{title}' is {probability * 100:.1f}% Yes. "
        f"Bid {bid or 0:.3f}; ask {ask or 0:.3f}; volume {volume:.0f}; "
        f"open interest {open_interest or 0:.0f}. Prediction-market prices are crowd-implied "
        "probabilities, not verified facts."
    )
    event = {
        "source": "kalshi",
        "source_url": KALSHI_SOURCE_URL,
        "type": "prediction_market",
        "record_kind": "observation",
        "entity_id": f"kalshi:{market_ticker}",
        "observed_at": observed_at,
        "transport": "direct",
        "title": f"{title}: Yes {probability * 100:.1f}%",
        "url": url,
        "published_at": str(row.get("open_time") or row.get("created_time") or ""),
        "body_raw": body,
        "body": body,
        "body_excerpt": body[:500],
        "tickers": [],
        "tags": ["prediction_market", "kalshi"],
        "markets": ["prediction_market"],
        "metrics": {
            "market_ticker": market_ticker,
            "event_ticker": event_ticker,
            "yes_probability": probability,
            "yes_bid": bid,
            "yes_ask": ask,
            "last_price": _as_float(row.get("last_price_dollars")),
            "volume": float(volume),
            "volume_24h": float(volume_24h),
            "liquidity": liquidity,
            "open_interest": open_interest,
            "close_time": str(row.get("close_time") or ""),
            "transport": "direct",
        },
        "raw_payload": {"market": row},
    }
    try:
        from reports.source_collector import _classify_event

        return _classify_event(event)
    except ImportError:
        return event


def fetch_kalshi_events(
    limit: int = 80,
    *,
    min_volume: float | None = None,
    keywords: list[str] | None = None,
    get: Callable | None = None,
    max_pages: int = 3,
) -> list[dict]:
    get = get or requests.get
    limit = max(1, min(int(limit or 80), 500))
    min_volume = (
        float(os.getenv("STOCK_COLLECTOR_KALSHI_MIN_VOLUME", "1000"))
        if min_volume is None
        else float(min_volume)
    )
    keywords = _default_keywords() if keywords is None else keywords
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = ""
    events: list[dict] = []
    pages = max(1, min(int(max_pages or 1), 10))

    for _page in range(pages):
        params = {
            "limit": min(1000, max(100, limit * 4)),
            "status": "open",
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        response = get(
            KALSHI_MARKETS_URL,
            params=params,
            headers={"User-Agent": "stock-report/1.0 (+yoobinkim2006@gmail.com)"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Kalshi markets response must be an object")
        for row in payload.get("markets") or []:
            if not isinstance(row, dict) or not _matches_keywords(row, keywords):
                continue
            volume = _as_float(row.get("volume_fp")) or 0.0
            if volume < min_volume:
                continue
            normalized = _normalize_market(row, observed_at=observed_at)
            if normalized:
                events.append(normalized)
            if len(events) >= limit:
                return events
        cursor = str(payload.get("cursor") or "").strip()
        if not cursor:
            break
    if not events:
        discovered = _discover_series_markets(
            get=get,
            keywords=keywords,
            min_volume=min_volume,
            limit=limit,
        )
        for row in discovered:
            normalized = _normalize_market(row, observed_at=observed_at)
            if normalized:
                events.append(normalized)
    return events[:limit]
