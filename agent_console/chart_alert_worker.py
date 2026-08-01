from __future__ import annotations

import argparse
import json
from typing import Any, Callable

import pandas as pd

from . import chart_alert_dispatcher, chart_alert_runner, storage


LoadBarsFn = Callable[[str, str], Any]


def run_chart_alert_cycle(
    *,
    workspace_id: str | None = None,
    symbols: list[str] | None = None,
    notify: bool = False,
    load_bars_fn: LoadBarsFn | None = None,
    send_fn: chart_alert_dispatcher.SendFn | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Load market bars for enabled chart alert rules, evaluate them, and optionally notify."""
    rules = storage.list_chart_alert_rules(
        workspace_id=str(workspace_id).strip() if workspace_id else None,
        enabled=True,
        limit=limit,
    )
    symbol_filter = {str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()}
    if symbol_filter:
        rules = [rule for rule in rules if str(rule.get("symbol") or "").upper().strip() in symbol_filter]

    loader = load_bars_fn or load_chart_alert_bars
    events: list[dict[str, Any]] = []
    missing_bars: list[dict[str, str]] = []
    for timeframe, tf_rules in _rules_by_timeframe(rules).items():
        bars_by_symbol: dict[str, Any] = {}
        for symbol in _symbols_for_rules(tf_rules):
            bars = loader(symbol, timeframe)
            if _bars_available(bars):
                bars_by_symbol[symbol] = bars
            else:
                missing_bars.append({"symbol": symbol, "timeframe": timeframe})
        if bars_by_symbol:
            events.extend(chart_alert_runner.evaluate_alert_rules(tf_rules, bars_by_symbol))

    for event in events:
        alert_id = str(event.get("alert_id") or "").strip()
        if not alert_id:
            continue
        storage.update_chart_alert_state(alert_id, {
            "triggered": True,
            "event": event,
            "last_price": event.get("current_price"),
            "last_checked_at": event.get("as_of"),
        })

    notification = {"attempted": 0, "delivered": 0, "failed": 0, "failures": []}
    if notify and events:
        notification = chart_alert_dispatcher.dispatch_alert_events(events, send_fn=send_fn)

    result = {
        "ok": True,
        "workspace_id": str(workspace_id).strip() if workspace_id else "",
        "rule_count": len(rules),
        "event_count": len(events),
        "events": events,
        "missing_bars": missing_bars,
        "notification": notification,
    }
    saved_run = storage.save_chart_alert_run({
        "workspace_id": result["workspace_id"],
        "status": "ok",
        "rule_count": result["rule_count"],
        "event_count": result["event_count"],
        "missing_bars": result["missing_bars"],
        "notification": result["notification"],
        "result": result,
    })
    result["run_id"] = saved_run["id"]
    result["created_at"] = saved_run["created_at"]
    return result


def load_chart_alert_bars(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Default chart-alert bar loader shared with the dashboard chart stack."""
    symbol = str(symbol or "").upper().strip()
    timeframe = str(timeframe or "1d").lower().strip() or "1d"
    if not symbol:
        return None
    from dashboard import cached

    if timeframe == "1d":
        return cached.ohlc(symbol, period="max")
    return cached.ohlc_tf(symbol, timeframe)


def _rules_by_timeframe(rules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in rules or []:
        timeframe = str(rule.get("timeframe") or "1d").lower().strip() or "1d"
        grouped.setdefault(timeframe, []).append(rule)
    return grouped


def _symbols_for_rules(rules: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    for rule in rules or []:
        symbol = str(rule.get("symbol") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _bars_available(bars: Any) -> bool:
    if bars is None or getattr(bars, "empty", False):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate saved chart alerts and optionally send notifications.")
    parser.add_argument("--workspace-id", default=None, help="Restrict evaluation to one chart workspace.")
    parser.add_argument("--symbol", action="append", default=[], help="Restrict evaluation to one symbol. Repeatable.")
    parser.add_argument("--notify", action="store_true", help="Send triggered chart alerts through the configured channel.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum enabled alert rules to inspect.")
    args = parser.parse_args(argv)
    result = run_chart_alert_cycle(
        workspace_id=args.workspace_id,
        symbols=args.symbol or [],
        notify=bool(args.notify),
        limit=int(args.limit or 200),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
