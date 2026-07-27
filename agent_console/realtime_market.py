from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from . import market_snapshot_store


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
        out = {
            "symbol": symbol,
            "market": _market_for(symbol),
            "price": float(price),
            "volume": float(volume) if volume is not None else None,
            "ts": ts,
            "age_s": max(0, int(now - ts)),
            "source": f"rest_cache:{source}" if source not in {"kis_ws", "kis_rest"} else source,
            "fresh": True,
        }
        if entry.get("session"):
            out["session"] = str(entry.get("session"))
        return out
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
    micro = market_snapshot_store.load_market_microstructure()
    indices = micro.get("indices") or {}
    investor_flow = micro.get("investor_flow") or {}
    k200_futures = micro.get("k200_futures") or None
    breadth = micro.get("breadth") or micro.get("advancers_decliners") or None
    quotes = []
    missing = []
    for symbol in _symbols(symbols):
        quote = _quote_from_cache(symbol, now=now, max_age_s=max_age_s) or _quote_from_kis(symbol, now=now)
        if quote:
            quotes.append(quote)
        else:
            missing.append({"symbol": symbol, "market": _market_for(symbol), "reason": "fresh quote unavailable"})
    fx = _fx_snapshot() or (micro.get("fx") if isinstance(micro.get("fx"), dict) else None)
    populated_fields = set()
    if (indices.get("kospi") or {}).get("price") is not None:
        populated_fields.add("kospi_index")
    if (indices.get("kosdaq") or {}).get("price") is not None:
        populated_fields.add("kosdaq_index")
    if investor_flow:
        populated_fields.add("investor_flow")
    if k200_futures:
        populated_fields.add("k200_futures")
    if breadth:
        populated_fields.add("advancers_decliners")
    unavailable = [
        {"field": field, "reason": reason}
        for field, reason in UNAVAILABLE_FIELDS
        if field not in populated_fields
    ]
    if fx is None:
        unavailable.append({"field": "usdkrw", "reason": "Toss FX opt-in/key 미연결 또는 조회 실패"})
    has_micro = bool(indices or investor_flow or k200_futures or breadth)
    status = "partial" if quotes or fx or has_micro else "unavailable"
    return {
        "ok": bool(quotes or fx or has_micro),
        "status": status,
        "as_of": micro.get("as_of") or datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "max_age_s": max_age_s,
        "quotes": quotes,
        "fx": fx,
        "indices": indices,
        "investor_flow": investor_flow,
        "k200_futures": k200_futures,
        "breadth": breadth,
        "microstructure_source": micro.get("source"),
        "missing_quotes": missing,
        "unavailable": unavailable,
        "sources": {
            "cache": "providers.realtime_quotes",
            "rest_fallback": "providers.kis_quote" if os.getenv("AGENT_CONSOLE_KIS_REST_FALLBACK", "0").lower() in {"1", "true", "yes", "on"} else "disabled",
            "fx": "providers.toss_api" if os.getenv("AGENT_CONSOLE_TOSS_FX_ENABLED", "0").lower() in {"1", "true", "yes", "on"} else "disabled",
        },
    }


def _fmt_signed(value, *, suffix: str = "") -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:g}{suffix}"


def _fmt_krw_net(value) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num / 100000000:.0f}억"


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
        session = f" · {row.get('session')}" if row.get("session") else ""
        lines.append(f"- {symbol}: {price:g} · {src}{session} · {age}s 전{tail}")
    fx = snapshot.get("fx")
    if isinstance(fx, dict) and fx.get("rate"):
        lines.append(f"- {fx.get('pair', 'USD/KRW')}: {float(fx['rate']):,.2f} · {fx.get('source', 'fx')}")
    indices = snapshot.get("indices") or {}
    for key, label in (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ"), ("kospi200", "KOSPI200")):
        row = indices.get(key) or {}
        if row.get("price") is not None:
            lines.append(f"- {label}: {float(row['price']):,.2f} · {_fmt_signed(row.get('change_pct'), suffix='%')}")
    flow = snapshot.get("investor_flow") or {}
    kospi_flow = flow.get("kospi") or flow.get("KOSPI") or {}
    if kospi_flow:
        lines.append(
            "- KOSPI 수급: "
            f"외국인 {_fmt_krw_net(kospi_flow.get('foreign_net'))} · "
            f"기관 {_fmt_krw_net(kospi_flow.get('institution_net'))}"
        )
    fut = snapshot.get("k200_futures") or {}
    if fut:
        price = f"{float(fut['price']):,.2f}" if fut.get("price") is not None else "—"
        lines.append(
            f"- K200 선물: {price} · {_fmt_signed(fut.get('change_pct'), suffix='%')} · "
            f"외국인 {_fmt_signed(fut.get('foreign_net'), suffix='계약')}"
        )
    breadth = snapshot.get("breadth") or {}
    if breadth:
        adv = breadth.get("advancers")
        dec = breadth.get("decliners")
        unchanged = breadth.get("unchanged")
        tail = f" · 보합 {unchanged}" if unchanged is not None else ""
        lines.append(f"- 시장 폭: 상승 {adv if adv is not None else '—'} · 하락 {dec if dec is not None else '—'}{tail}")
    unavailable = snapshot.get("unavailable") or []
    if unavailable:
        labels = ", ".join(str(row.get("field")) for row in unavailable[:6] if row.get("field"))
        if labels:
            lines.append(f"- 미연결/부족 필드: {labels}")
    if len(lines) == 1 and not quotes:
        lines.append("- 신선한 실시간 시세가 없습니다.")
    return lines
