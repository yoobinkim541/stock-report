from __future__ import annotations

import argparse
import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import safe_io
from dashboard import chart_data_policy, chart_document
from . import chart_alert_dispatcher, chart_alert_runner, storage


LoadBarsFn = Callable[..., Any]
LoadFundamentalsFn = Callable[[str], Any]

_LOCK_TARGET = os.path.expanduser("~/.cache/stock-report/chart_alert_worker")
_UNDELIVERED = ("pending", "failed")
_EMPTY_NOTIFICATION = {"attempted": 0, "delivered": 0, "failed": 0, "failures": [], "results": []}


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


def cycle_lock(timeout: float = 0.0):
    """One evaluate→deliver pass at a time — 워커 크론·배치 API 가 공유하는 단일 직렬화 지점.

    ``timeout`` 안에 못 잡으면 ``safe_io.LockTimeout`` (기본 0 = 즉시).
    """
    return safe_io.file_write_lock(_lock_target(), timeout=timeout)


def run_chart_alert_cycle(
    *,
    workspace_id: str | None = None,
    symbols: list[str] | None = None,
    notify: bool = False,
    load_bars_fn: LoadBarsFn | None = None,
    send_fn: chart_alert_dispatcher.SendFn | None = None,
    limit: int = 200,
    load_fundamentals_fn: LoadFundamentalsFn | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Load market bars for enabled chart alert rules, evaluate them, and optionally notify.

    겹쳐 도는 두 사이클이 "once" 룰의 last_state 를 동시에 미체결로 읽고 둘 다
    dispatch 해 중복 알림을 보내는 race(감사 #16) 방지 — 비차단 락으로 겹치면
    이번 호출은 통째로 스킵(룰 조회도 시작 안 함).
    """
    try:
        with cycle_lock(timeout=0):
            return _run_chart_alert_cycle_locked(
                workspace_id=workspace_id, symbols=symbols, notify=notify,
                load_bars_fn=load_bars_fn, send_fn=send_fn, limit=limit,
                load_fundamentals_fn=load_fundamentals_fn, as_of=as_of,
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
            "notification": dict(_EMPTY_NOTIFICATION),
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
    load_fundamentals_fn: LoadFundamentalsFn | None,
    as_of: str | None,
) -> dict[str, Any]:
    as_of_text = as_of or datetime.now(timezone.utc).isoformat(timespec="seconds")
    result: dict[str, Any] = {
        "ok": True,
        "workspace_id": str(workspace_id).strip() if workspace_id else "",
        "as_of": as_of_text,
        "rule_count": 0,
        "event_count": 0,
        "events": [],
        "evaluations": [],
        "evaluation_summary": {},
        "missing_bars": [],
        "missing_fundamentals": [],
        "notification": dict(_EMPTY_NOTIFICATION),
    }
    try:
        rules = storage.list_chart_alert_rules(
            workspace_id=result["workspace_id"] or None,
            enabled=True,
            limit=limit,
        )
        symbol_filter = {str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()}
        if symbol_filter:
            rules = [rule for rule in rules if str(rule.get("symbol") or "").upper().strip() in symbol_filter]
        result["rule_count"] = len(rules)

        bars_by_key, result["missing_bars"] = _load_rule_bars(rules, load_bars_fn or load_chart_alert_bars)
        fundamentals, result["missing_fundamentals"] = load_rule_fundamentals(rules, load_fundamentals_fn)
        result.update(evaluate_and_deliver(
            rules, bars_by_key, as_of=as_of_text, notify=notify, send_fn=send_fn,
            fundamentals_by_symbol=fundamentals,
        ))
        result["status"] = _run_status(result)
    except Exception as exc:
        # 부분 실패·예외도 감사 기록을 남긴다(평가 state 는 이미 저장됐을 수 있다).
        result["ok"] = False
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"

    saved_run = storage.save_chart_alert_run({
        "workspace_id": result["workspace_id"],
        "status": result["status"],
        "rule_count": result["rule_count"],
        "event_count": result["event_count"],
        "missing_bars": result["missing_bars"],
        "notification": result["notification"],
        "result": result,
    })
    result["run_id"] = saved_run["id"]
    result["created_at"] = saved_run["created_at"]
    return result


def evaluate_and_deliver(
    rules: list[dict[str, Any]],
    bars_by_key: dict[Any, Any],
    *,
    as_of: str,
    notify: bool,
    send_fn: chart_alert_dispatcher.SendFn | None = None,
    fundamentals_by_symbol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate rules, persist pending events before sending, then deliver with per-event acks.

    Caller must hold :func:`cycle_lock`.

    - notify=False 는 평가 전용이다. 평가 감사(matched/trace)는 저장하지만 이벤트를
      기록하지 않고 once 완료·repeating 무장·pending 을 바꾸지 않는다. 다음 notify 사이클이
      같은 봉이면 같은 event ID 로 발송한다.
    - notify=True: 새 이벤트를 pending 으로 먼저 저장 → 평가 state 저장 → 아직 delivered 가
      아닌 이 규칙들의 모든 이벤트(이전 사이클 실패·중단분 포함)를 발송 → 성공 ack 만
      delivered. once 는 delivered 뒤에만 triggered=True(+triggered_condition_hash)로 완료한다.
    - 미발송 이벤트는 현재 조건 해시의 것만 발송한다. 수정 전 조건의 이벤트는
      ``superseded``, 근거 봉이 이제 오래된(chart_data_policy.bar_staleness) 이벤트는
      ``expired`` 로 닫고 보내지 않는다 — 어느 쪽도 once 를 완료시키지 않는다.
    - 같은 event ID 가 이미 delivered 면 다시 보내지 않는다(sender 성공 후 ack 저장 전에
      중단되면 다음 사이클에 한 번 더 갈 수 있다 — at-least-once).
    """
    evaluations: list[dict[str, Any]] = []
    chart_alert_runner.evaluate_alert_rules(
        rules, bars_by_key, as_of=as_of, fundamentals_by_symbol=fundamentals_by_symbol,
        state_sink=evaluations,
    )
    rules_by_id = {str(rule.get("id") or ""): rule for rule in rules or []}
    hashes = {rule_id: chart_alert_runner.rule_condition_hash(rule) for rule_id, rule in rules_by_id.items()}
    rule_ids = [str(state.get("rule_id") or "") for state in evaluations if state.get("rule_id")]
    once_ids = [rule_id for rule_id in rule_ids if _frequency(rules_by_id.get(rule_id)) == "once"]
    undelivered_by_rule: dict[str, list[dict[str, Any]]] = {}
    delivered_once: set[str] = set()
    if notify and rule_ids:
        for row in _sendable_events(rule_ids, rules_by_id, hashes, as_of):
            undelivered_by_rule.setdefault(row["rule_id"], []).append(row)
        if once_ids:
            delivered_once = {
                row["rule_id"]
                for row in storage.list_chart_alert_events(rule_ids=once_ids, statuses=("delivered",))
                if _event_hash(row) == hashes.get(row["rule_id"])
            }

    events: list[dict[str, Any]] = []
    for state in evaluations:
        rule_id = str(state.get("rule_id") or "")
        rule = rules_by_id.get(rule_id) or {}
        previous = rule.get("last_state") if isinstance(rule.get("last_state"), dict) else {}
        event = state.get("event") if isinstance(state.get("event"), dict) else None
        if not notify:
            _keep_delivery_fields(state, previous)
            if event is not None:
                state["delivery"] = "not_requested"
                events.append(event)
            continue
        if event is None:
            continue
        if _frequency(rule) == "once":
            if rule_id in delivered_once:
                # ack 저장 뒤 state 갱신 전에 중단된 once: 이미 보냈으므로 완료로 복구한다.
                state.update({
                    "triggered": True, "reason": "already_triggered",
                    "triggered_condition_hash": hashes.get(rule_id),
                })
                state.pop("event", None)
                continue
            other = [row for row in undelivered_by_rule.get(rule_id, []) if row["event_id"] != event["event_id"]]
            if other:
                # once 는 미발송 이벤트 하나만 재시도한다 — 새 봉의 다른 event 를 더하지 않는다.
                state["pending_event_id"] = other[0]["event_id"]
                state["delivery"] = "pending_previous_event"
                state.pop("event", None)
                continue
        row = storage.record_chart_alert_event(event)
        if row["status"] == "delivered":
            state["delivery"] = "already_delivered"
            continue
        if row["status"] not in _UNDELIVERED:
            # 같은 근거 봉의 이벤트가 이미 superseded/expired 로 닫혔다 — 다시 열지 않는다.
            state["delivery"] = row["status"]
            continue
        state["pending_event_id"] = event["event_id"]
        state["delivery"] = "pending"
        events.append(event)

    for state in evaluations:
        if state.get("rule_id"):
            storage.update_chart_alert_state(state["rule_id"], state)

    notification = dict(_EMPTY_NOTIFICATION)
    if notify and rule_ids:
        queue = _sendable_events(rule_ids, rules_by_id, hashes, as_of)
        if queue:
            notification = chart_alert_dispatcher.dispatch_alert_events(
                [row["event"] for row in queue], send_fn=send_fn,
            )
            states_by_id = {str(state.get("rule_id") or ""): state for state in evaluations}
            for outcome in notification["results"]:
                saved = storage.mark_chart_alert_event(
                    outcome["event_id"], delivered=outcome["delivered"], error=outcome["error"],
                )
                state = states_by_id.get(saved["rule_id"])
                if state is None:
                    continue
                if outcome["delivered"]:
                    state["delivered_event_id"] = saved["event_id"]
                    if state.get("pending_event_id") == saved["event_id"]:
                        state.pop("pending_event_id", None)
                    if _frequency(rules_by_id.get(saved["rule_id"])) == "once":
                        state["triggered"] = True
                        state["triggered_condition_hash"] = _event_hash(saved)
                    state["delivery"] = "delivered"
                else:
                    state["delivery"] = "failed"
                storage.update_chart_alert_state(saved["rule_id"], state)

    return {
        "event_count": len(events),
        "events": events,
        "evaluations": evaluations,
        "evaluation_summary": _evaluation_summary(evaluations),
        "notification": notification,
    }


def _event_hash(row: dict[str, Any]) -> str | None:
    event = row.get("event") if isinstance(row.get("event"), dict) else {}
    return str(event.get("condition_hash") or "") or None


def _sendable_events(rule_ids, rules_by_id, hashes, as_of) -> list[dict[str, Any]]:
    """Undelivered events still valid for the rule's current condition and still current data.

    Events of an edited-away condition are closed as ``superseded``; events whose basis
    bar is now stale (same freshness rule as evaluation) are closed as ``expired``.
    """
    sendable: list[dict[str, Any]] = []
    superseded: list[str] = []
    expired: list[str] = []
    for row in storage.list_chart_alert_events(rule_ids=rule_ids, statuses=_UNDELIVERED):
        rule = rules_by_id.get(row["rule_id"]) or {}
        current_hash = hashes.get(row["rule_id"])
        if current_hash is None:
            continue  # 조건을 해석할 수 없는 규칙의 이벤트는 판단 없이 보류한다.
        if _event_hash(row) != current_hash:
            superseded.append(row["event_id"])
        elif _event_is_stale(row["event"], rule, as_of):
            expired.append(row["event_id"])
        else:
            sendable.append(row)
    if superseded:
        storage.close_chart_alert_events(superseded, status="superseded", reason="rule condition changed")
    if expired:
        storage.close_chart_alert_events(expired, status="expired", reason="basis bar is stale")
    return sendable


def _event_is_stale(event: dict[str, Any], rule: dict[str, Any], as_of: str) -> bool:
    bar_time = event.get("bar_time")
    if not bar_time:
        return False
    symbol = str(event.get("symbol") or rule.get("symbol") or "").upper().strip()
    timeframe = str(event.get("timeframe") or rule.get("timeframe") or "1d").lower().strip() or "1d"
    market = chart_document.default_chart_document(symbol)["market"]
    return chart_data_policy.bar_staleness(bar_time, as_of, market=market, timeframe=timeframe) is not None


def _keep_delivery_fields(state: dict[str, Any], previous: dict[str, Any]) -> None:
    if state.get("condition_hash"):
        previous = chart_alert_runner.revision_state(previous, state["condition_hash"])
    state["triggered"] = bool(previous.get("triggered"))
    # 이번 평가의 matched 로 무장을 다시 유도하지 않도록 이전 무장 상태를 명시해 둔다.
    state["armed"] = chart_alert_runner.previous_armed(previous)
    for key in ("pending_event_id", "delivered_event_id", "triggered_condition_hash"):
        if previous.get(key):
            state[key] = previous[key]
        else:
            state.pop(key, None)


def _evaluation_summary(evaluations: list[dict[str, Any]]) -> dict[str, int]:
    """matched / not matched / data-unavailable / unsupported / error counts kept apart."""
    summary = {"matched": 0, "not_matched": 0, "unknown": 0, "unsupported": 0, "error": 0, "suppressed": 0}
    for state in evaluations:
        status = str(state.get("status") or "")
        key = {"true": "matched", "false": "not_matched"}.get(status, status)
        summary[key if key in summary else "unknown"] += 1
    return summary


def _run_status(result: dict[str, Any]) -> str:
    summary = result.get("evaluation_summary") or {}
    notification = result.get("notification") or {}
    degraded = (
        int(notification.get("failed") or 0) > 0
        or int(summary.get("unknown") or 0) > 0
        or int(summary.get("unsupported") or 0) > 0
        or int(summary.get("error") or 0) > 0
        or bool(result.get("missing_bars"))
        or bool(result.get("missing_fundamentals"))
    )
    return "partial" if degraded else "ok"


def _frequency(rule: dict[str, Any] | None) -> str:
    return str((rule or {}).get("frequency") or "").lower().strip()


def _load_rule_bars(rules: list[dict[str, Any]], loader: LoadBarsFn):
    requirements: set[tuple[str, str]] = set()
    for rule in rules:
        try:
            requirements.update(chart_alert_runner.alert_requirements(rule))
        except ValueError:
            symbol = str(rule.get("symbol") or "").upper().strip()
            timeframe = str(rule.get("timeframe") or "1d").lower().strip() or "1d"
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
    return bars_by_key, missing_bars


def load_rule_fundamentals(rules: list[dict[str, Any]], loader: LoadFundamentalsFn | None = None):
    """Load fundamentals only for symbols referenced by fundamental leaves."""
    symbols: set[str] = set()
    for rule in rules or []:
        try:
            symbols.update(chart_alert_runner.fundamental_symbols(rule))
        except ValueError:
            continue
    loader = loader or load_chart_alert_fundamentals
    fundamentals: dict[str, Any] = {}
    missing: list[str] = []
    for symbol in sorted(symbols):
        try:
            payload = loader(symbol)
        except Exception:
            payload = None
        if isinstance(payload, dict) and any(value is not None for value in payload.values()):
            fundamentals[symbol] = payload
        else:
            missing.append(symbol)
    return fundamentals, missing


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


# 경보 펀더멘털 필드 → 대시보드 공급 경로(views.valuation / views.financials)의 위치.
_FUNDAMENTAL_SOURCES: dict[str, tuple[str, str]] = {
    "per": ("metrics", "per"),
    "forward_pe": ("metrics", "forward_pe"),
    "pbr": ("metrics", "pbr"),
    "psr": ("metrics", "psr"),
    "roe": ("metrics", "roe"),
    "target_upside_pct": ("consensus", "target_upside_pct"),
    "net_margin": ("trends", "net_margin"),
}


def load_chart_alert_fundamentals(symbol: str) -> dict[str, Any]:
    """Default fundamentals loader shared with the dashboard valuation panels."""
    from dashboard import cached

    valuation = cached.valuation(symbol) or {}
    financials = cached.financials(symbol) or {}
    sections = {
        "metrics": valuation.get("metrics") or {},
        "consensus": valuation.get("consensus") or {},
        "trends": financials.get("trends") or {},
    }
    return {
        field: (sections[section] or {}).get(key)
        for field, (section, key) in _FUNDAMENTAL_SOURCES.items()
    }


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
