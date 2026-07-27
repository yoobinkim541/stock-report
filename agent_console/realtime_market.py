from __future__ import annotations

import os
import time
from datetime import datetime, timezone


DEFAULT_SYMBOLS = ("005930", "000660", "QQQ", "SPY", "HYG", "LQD")
UNAVAILABLE_FIELDS = (
    ("kospi_index", "KRX/증권사 지수 현재가 미연결"),
    ("kosdaq_index", "KRX/증권사 지수 현재가 미연결"),
    ("investor_flow", "투자자별 장중 수급 API 미연결"),
    ("k200_futures", "KOSPI200 선물 장중 API 미연결"),
    ("advancers_decliners", "상승/하락 종목 수 API 미연결"),
)


def _symbols(symbols: list[str] | None = None) -> list[str]:
    raw = symbols
    if raw is None:
        env = os.getenv("AGENT_CONSOLE_MARKET_SYMBOLS", "")
        raw = [x.strip() for x in env.split(",") if x.strip()] or list(DEFAULT_SYMBOLS)
    out: list[str] = []
    for value in raw:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return out[:24]


def _market_for(symbol: str) -> str:
    return "KR" if symbol.isdigit() and len(symbol) == 6 else "US"


def _quote_from_cache(symbol: str, *, now: float, max_age_s: int) -> dict | None:
    try:
        from providers import realtime_quotes

        price = realtime_quotes.get_price(symbol, max_age_s=max_age_s)
        if price is None:
            return None
        volume = realtime_quotes.get_volume(symbol, max_age_s=max_age_s)
        entry = realtime_quotes._entry(symbol, max_age_s) or {}
        ts = float(entry.get("ts") or now)
        source = str(entry.get("src") or entry.get("source") or "realtime_cache")
        return {
            "symbol": symbol,
            "market": _market_for(symbol),
            "price": float(price),
            "volume": float(volume) if volume is not None else None,
            "ts": ts,
            "age_s": max(0, int(now - ts)),
            "source": f"rest_cache:{source}" if source not in {"kis_ws", "kis_rest"} else source,
            "fresh": True,
        }
    except Exception:
        return None


def _quote_from_kis(symbol: str, *, now: float) -> dict | None:
    if os.getenv("AGENT_CONSOLE_KIS_REST_FALLBACK", "0").lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from providers import kis_quote

        row = kis_quote.get_quote(symbol, market=_market_for(symbol))
        if not row or not row.get("price"):
            return None
        ts = float(row.get("ts") or now)
        return {
            "symbol": symbol,
            "market": _market_for(symbol),
            "price": float(row["price"]),
            "volume": float(row["volume"]) if row.get("volume") is not None else None,
            "ts": ts,
            "age_s": max(0, int(now - ts)),
            "source": str(row.get("source") or "kis_rest"),
            "fresh": True,
        }
    except Exception:
        return None


def _fx_snapshot() -> dict | None:
    if os.getenv("AGENT_CONSOLE_TOSS_FX_ENABLED", "0").lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from providers import toss_api

        rate = toss_api.exchange_rate("USD", "KRW")
        if rate:
            return {"pair": "USD/KRW", "rate": float(rate), "source": "toss_api"}
    except Exception:
        return None
    return None


def build_market_snapshot(symbols: list[str] | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    max_age_s = int(os.getenv("AGENT_CONSOLE_MARKET_STALE_S", "90"))
    quotes = []
    missing = []
    for symbol in _symbols(symbols):
        quote = _quote_from_cache(symbol, now=now, max_age_s=max_age_s) or _quote_from_kis(symbol, now=now)
        if quote:
            quotes.append(quote)
        else:
            missing.append({"symbol": symbol, "market": _market_for(symbol), "reason": "fresh quote unavailable"})
    fx = _fx_snapshot()
    unavailable = [{"field": field, "reason": reason} for field, reason in UNAVAILABLE_FIELDS]
    if fx is None:
        unavailable.append({"field": "usdkrw", "reason": "Toss FX opt-in/key 미연결 또는 조회 실패"})
    status = "partial" if quotes or fx else "unavailable"
    return {
        "ok": bool(quotes or fx),
        "status": status,
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "max_age_s": max_age_s,
        "quotes": quotes,
        "fx": fx,
        "missing_quotes": missing,
        "unavailable": unavailable,
        "sources": {
            "cache": "providers.realtime_quotes",
            "rest_fallback": "providers.kis_quote" if os.getenv("AGENT_CONSOLE_KIS_REST_FALLBACK", "0").lower() in {"1", "true", "yes", "on"} else "disabled",
            "fx": "providers.toss_api" if os.getenv("AGENT_CONSOLE_TOSS_FX_ENABLED", "0").lower() in {"1", "true", "yes", "on"} else "disabled",
        },
    }


def compact_snapshot_lines(snapshot: dict) -> list[str]:
    snapshot = snapshot or {}
    lines = [
        f"- 상태: {snapshot.get('status') or 'unavailable'} · 시점 {snapshot.get('as_of') or 'unknown'}",
    ]
    quotes = snapshot.get("quotes") or []
    for row in quotes[:12]:
        symbol = row.get("symbol")
        price = row.get("price")
        src = row.get("source") or "unknown"
        age = row.get("age_s")
        volume = row.get("volume")
        tail = f" · 거래량 {volume:,.0f}" if isinstance(volume, (int, float)) else ""
        lines.append(f"- {symbol}: {price:g} · {src} · {age}s 전{tail}")
    fx = snapshot.get("fx")
    if isinstance(fx, dict) and fx.get("rate"):
        lines.append(f"- {fx.get('pair', 'USD/KRW')}: {float(fx['rate']):,.2f} · {fx.get('source', 'fx')}")
    unavailable = snapshot.get("unavailable") or []
    if unavailable:
        labels = ", ".join(str(row.get("field")) for row in unavailable[:6] if row.get("field"))
        if labels:
            lines.append(f"- 미연결/부족 필드: {labels}")
    if len(lines) == 1 and not quotes:
        lines.append("- 신선한 실시간 시세가 없습니다.")
    return lines
