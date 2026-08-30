from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import safe_io
from . import chart_alert_dispatcher, chart_alert_runner, storage
from dashboard import chart_conditions


LoadBarsFn = Callable[..., Any]

_LOCK_TARGET = os.path.expanduser("~/.cache/stock-report/chart_alert_worker")


def _lock_target() -> str:
    """읽기 전용 홈 디렉터리에서도 워커 중복 실행 락을 확보한다."""
    default_target = os.path.expanduser("~/.cache/stock-report/chart_alert_worker")
    # 기존 호출부/테스트가 모듈 상수를 명시한 경우 환경변수보다 우선한다.
    if _LOCK_TARGET != default_target:
        return _LOCK_TARGET
    configured = os.getenv("AGENT_CONSOLE_CHART_ALERT_LOCK", "").strip()
    if configured:
        return os.path.expanduser(configured)
    runtime_dir = os.getenv("AGENT_CONSOLE_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        runtime_dir = os.getenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", "").strip()
    if runtime_dir:
        return str(Path(runtime_dir).expanduser() / "chart_alert_worker")
    return _LOCK_TARGET


def run_chart_alert_cycle(
    *,
    workspace_id: str | None = None,
    symbols: list[str] | None = None,
    notify: bool = False,
    load_bars_fn: LoadBarsFn | None = None,
    send_fn: chart_alert_dispatcher.SendFn | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Load market bars for enabled chart alert rules, evaluate them, and optionally notify.

    겹쳐 도는 두 사이클이 "once" 룰의 last_state 를 동시에 미체결로 읽고 둘 다
    dispatch 해 중복 알림을 보내는 race(감사 #16) 방지 — 비차단 락으로 겹치면
    이번 호출은 통째로 스킵(룰 조회도 시작 안 함).
    """
    try:
        with safe_io.file_write_lock(_lock_target(), timeout=0):
            return _run_chart_alert_cycle_locked(
                workspace_id=workspace_id, symbols=symbols, notify=notify,
                load_bars_fn=load_bars_fn, send_fn=send_fn, limit=limit,
            )
    except safe_io.LockTimeout:
        return {
            "ok": False,
            "workspace_id": str(workspace_id).strip() if workspace_id else "",
            "rule_count": 0,
            "event_count": 0,
            "events": [],
            "evaluations": [],
            "missing_bars": [],
            "notification": {"attempted": 0, "delivered": 0, "failed": 0, "failures": []},
            "reason": "already_running",
        }


def _run_chart_alert_cycle_locked(
    *,
    workspace_id: str | None,
    symbols: list[str] | None,
    notify: bool,
    load_bars_fn: LoadBarsFn | None,
    send_fn: chart_alert_dispatcher.SendFn | None,
    limit: int,
) -> dict[str, Any]:
    rules = storage.list_chart_alert_rules(
        workspace_id=str(workspace_id).strip() if workspace_id else None,
        enabled=True,
        limit=limit,
    )
    symbol_filter = {str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()}
    if symbol_filter:
        rules = [rule for rule in rules if str(rule.get("symbol") or "").upper().strip() in symbol_filter]

    loader = load_bars_fn or load_chart_alert_bars
    requirements: set[tuple[str, str]] = set()
    for rule in rules:
        symbol = str(rule.get("symbol") or "").upper().strip()
        timeframe = str(rule.get("timeframe") or "1d").lower().strip() or "1d"
        try:
            requirements.update(chart_conditions.condition_requirements(
                rule.get("condition") or {}, default_symbol=symbol, default_timeframe=timeframe,
            ))
        except ValueError:
            requirements.add((symbol, timeframe))

    bars_by_key: dict[tuple[str, str], Any] = {}
    missing_bars: list[dict[str, str]] = []
    for symbol, timeframe in sorted(requirements):
        if not symbol:
            continue
        try:
            bars = _call_loader(loader, symbol, timeframe)
        except Exception:
            bars = None
        if _bars_available(bars):
            bars_by_key[(symbol, timeframe)] = bars
        else:
            missing_bars.append({"symbol": symbol, "timeframe": timeframe})

    evaluations: list[dict[str, Any]] = []
    events = chart_alert_runner.evaluate_alert_rules(
        rules, bars_by_key, state_sink=evaluations,
    )
    for state in evaluations:
        rule_id = str(state.get("rule_id") or "").strip()
        if rule_id:
            storage.update_chart_alert_state(rule_id, state)

    notification = {"attempted": 0, "delivered": 0, "failed": 0, "failures": []}
    if notify and events:
        notification = chart_alert_dispatcher.dispatch_alert_events(events, send_fn=send_fn)

    result = {
        "ok": True,
        "workspace_id": str(workspace_id).strip() if workspace_id else "",
        "rule_count": len(rules),
        "event_count": len(events),
        "events": events,
        "evaluations": evaluations,
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


def _call_loader(loader: LoadBarsFn, symbol: str, timeframe: str):
    """Use one-argument loaders only in the explicit legacy compatibility path."""
    try:
        signature = inspect.signature(loader)
        positional = [
            parameter for parameter in signature.parameters.values()
            if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
        ]
        variadic = any(
            parameter.kind == parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        one_argument = not variadic and len(positional) == 1
    except (TypeError, ValueError):
        one_argument = False
    return loader(symbol) if one_argument else loader(symbol, timeframe)


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
