#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports import source_collector


def probe() -> dict:
    providers = {}
    for name, fetch in (
        ("polymarket", source_collector.fetch_polymarket_events),
        ("kalshi", source_collector.fetch_kalshi_events),
    ):
        source_collector._LAST_ERRORS.pop(name, None)
        source_collector._SOURCE_AVAILABILITY.pop(name, None)
        try:
            events = fetch()
        except Exception as exc:
            events = []
            error = str(exc)[:500]
        else:
            error = str(source_collector._LAST_ERRORS.get(name) or "")
        state = source_collector._SOURCE_AVAILABILITY.get(name) or {}
        availability = str(state.get("availability") or ("error" if error else "available"))
        observed = [
            str(row.get("observed_at") or row.get("collected_at") or row.get("published_at") or "")
            for row in events
        ]
        transports = sorted({
            str((row.get("metrics") or {}).get("transport") or row.get("transport") or "direct")
            for row in events
        })
        providers[name] = {
            "ok": availability == "available",
            "count": len(events),
            "availability": availability,
            "error": str(state.get("availability_reason") or error),
            "freshest_observed_at": max(observed, default=""),
            "transports": transports,
        }
    available = [name for name, row in providers.items() if row["availability"] == "available"]
    return {
        "ok": bool(available),
        "availability": "available" if len(available) == len(providers) else ("degraded" if available else "unavailable"),
        "providers": providers,
    }


def main() -> int:
    result = probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
