from __future__ import annotations


def _len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _paper_decision_count(paper: dict) -> int:
    total = 0
    for key in ("kr", "us", "combined"):
        item = paper.get(key) if isinstance(paper, dict) else None
        if isinstance(item, dict):
            total += _len(item.get("decisions"))
    return total


def build_usage_summary(pack: dict, *, wiki_pages: list[dict] | None = None,
                        intent: dict | None = None, engine: str = "pending") -> dict:
    sources = pack.get("sources") or {}
    market_snapshot = pack.get("market_snapshot") or {}
    quote_count = _len(market_snapshot.get("quotes"))
    event_count = _len(sources.get("events"))
    return {
        "intent": (intent or {}).get("name") or "general",
        "events": event_count,
        "wiki": _len(wiki_pages),
        "realtime": quote_count,
        "logs": _paper_decision_count(pack.get("paper") or {}),
        "engine": engine,
        "retrieval": {
            "quote": "ok" if quote_count else "unavailable",
            "news": "ok" if event_count else "unavailable",
            "broker": "ok" if (market_snapshot.get("broker") or {}).get("ok") else "unavailable",
        },
    }


def format_usage_lines(summary: dict) -> list[str]:
    retrieval = summary.get("retrieval") or {}
    return [
        f"맥락: 시장 events {summary.get('events', 0)} / wiki {summary.get('wiki', 0)} / 실시간 {summary.get('realtime', 0)} / 로그 {summary.get('logs', 0)}",
        f"엔진: {summary.get('engine') or 'pending'}",
        "수집: "
        f"quote {retrieval.get('quote', 'unavailable')}, "
        f"news {retrieval.get('news', 'unavailable')}, "
        f"broker {retrieval.get('broker', 'unavailable')}",
    ]
