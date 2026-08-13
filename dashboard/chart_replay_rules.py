"""Strategy Studio and shared-condition handoff for chart replay sessions."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pandas as pd

from agent_console import chart_alert_runner
from dashboard import chart_conditions, chart_replay
from ml.strategy_studio import StrategySpec, compile_strategy, strategy_spec_hash
from ohlc_utils import normalize_ohlc_frame


RULE_PACKET_VERSION = 1
_OPERATOR_MAP = {
    "<": "less_than", "lt": "less_than",
    "<=": "less_or_equal", "lte": "less_or_equal",
    ">": "greater_than", "gt": "greater_than",
    ">=": "greater_or_equal", "gte": "greater_or_equal",
    "=": "equal", "==": "equal", "eq": "equal",
    "!=": "not_equal", "ne": "not_equal",
    "cross_above": "crossing_up", "crosses_above": "crossing_up",
    "cross_below": "crossing_down", "crosses_below": "crossing_down",
}


def _packet_id(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def condition_packet(
    condition: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    action: str = "enter_long",
    position_pct: float = 1.0,
) -> dict[str, Any]:
    normalized = chart_conditions.normalize_condition(condition)
    errors = chart_conditions.validate_condition(normalized)
    if errors:
        raise ValueError("; ".join(errors))
    action = str(action or "enter_long").strip().lower()
    if action not in {"enter_long", "exit_all", "trim_half"}:
        raise ValueError("unsupported replay rule action")
    position_pct = float(position_pct)
    if not 0 < position_pct <= 1:
        raise ValueError("position_pct must be within (0, 1]")
    packet = {
        "version": RULE_PACKET_VERSION,
        "kind": "condition",
        "name": chart_conditions.explain_condition(normalized),
        "symbol": str(symbol or "").upper().strip(),
        "timeframe": str(timeframe or "").lower().strip(),
        "condition": normalized,
        "action": action,
        "sizing": {"type": "fixed_pct", "position_pct": position_pct},
    }
    packet["id"] = _packet_id(packet)
    return packet


def strategy_packet(spec: Mapping[str, Any] | StrategySpec) -> dict[str, Any]:
    strategy = StrategySpec.from_dict(spec)
    payload = strategy.to_dict()
    packet = {
        "version": RULE_PACKET_VERSION,
        "kind": "strategy_spec",
        "name": strategy.name,
        "symbol": strategy.base_symbol,
        "timeframe": strategy.timeframe,
        "spec_hash": strategy_spec_hash(strategy),
        "spec": payload,
        "sizing": copy.deepcopy(strategy.sizing),
        "costs": copy.deepcopy(strategy.costs),
    }
    packet["id"] = _packet_id(packet)
    return packet


def attach_rule_packet(session: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(packet))
    if int(payload.get("version") or 0) != RULE_PACKET_VERSION or not payload.get("id"):
        raise ValueError("invalid replay rule packet")
    if str(payload.get("symbol") or "").upper() != str(session.get("symbol") or "").upper():
        raise ValueError("rule packet symbol does not match replay session")
    if str(payload.get("timeframe") or "").lower() != str(session.get("timeframe") or "").lower():
        raise ValueError("rule packet timeframe does not match replay session")
    out = copy.deepcopy(dict(session))
    if payload.get("kind") == "strategy_spec":
        costs = payload.get("costs") or {}
        out = chart_replay.update_settings(out, {
            "fees_bps": float(costs.get("fees_bps") or 0.0),
            "slippage_bps": float(costs.get("slippage_bps") or 0.0) + float(costs.get("spread_bps") or 0.0),
        })
    out["rule_packet"] = payload
    out["events"].append({
        "type": "rule_packet_attached", "cursor": int(out.get("cursor") or 0),
        "rule_packet_id": payload["id"], "kind": payload.get("kind"),
        "name": payload.get("name"),
    })
    return out


def detach_rule_packet(session: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(session))
    packet = out.pop("rule_packet", None)
    if packet:
        out["events"].append({
            "type": "rule_packet_detached", "cursor": int(out.get("cursor") or 0),
            "rule_packet_id": packet.get("id"),
        })
    return out


def _rule_condition(node: Mapping[str, Any], *, symbol: str, timeframe: str) -> dict[str, Any]:
    if "all" in node:
        return {"op": "all", "children": [
            _rule_condition(child, symbol=symbol, timeframe=timeframe)
            for child in node.get("all") or [] if isinstance(child, Mapping)
        ]}
    if "any" in node:
        return {"op": "any", "children": [
            _rule_condition(child, symbol=symbol, timeframe=timeframe)
            for child in node.get("any") or [] if isinstance(child, Mapping)
        ]}
    if isinstance(node.get("not"), Mapping):
        return {"op": "none", "children": [
            _rule_condition(node["not"], symbol=symbol, timeframe=timeframe),
        ]}
    field = str(node.get("field") or node.get("left") or "close").strip()
    operator = _OPERATOR_MAP.get(str(node.get("op") or node.get("operator") or "").lower().strip())
    if operator is None:
        raise ValueError(f"unsupported strategy rule operator: {node.get('op') or node.get('operator')}")
    ref = node.get("ref") or node.get("right") or node.get("compare_to")
    value: Any = node.get("value")
    if isinstance(ref, str) and ref.strip():
        value = {
            "field": ref.strip(),
            "symbol": str(node.get("ref_symbol") or node.get("symbol") or symbol).upper().strip(),
            "timeframe": timeframe,
        }
    return {
        "type": "price" if field.lower() in {"open", "high", "low", "close", "volume", "price"} else "indicator",
        "symbol": str(node.get("symbol") or symbol).upper().strip(),
        "timeframe": timeframe,
        "field": field,
        "operator": operator,
        "value": value,
        "confirmation": "bar_close",
        "session": "regular",
    }


def _context_values(compiled, symbol: str, timestamp: pd.Timestamp) -> dict[str, Any]:
    values: dict[str, Any] = {"time": pd.Timestamp(timestamp).isoformat()}
    for field, series in (compiled.contexts.get(symbol) or {}).items():
        if timestamp not in series.index:
            continue
        value = series.loc[timestamp]
        if pd.notna(value):
            values[str(field)] = float(value)
    return values


def _strategy_evaluations(packet: Mapping[str, Any], frame: pd.DataFrame) -> list[dict[str, Any]]:
    spec = StrategySpec.from_dict(packet["spec"])
    symbol = str(packet["symbol"])
    store = pd.DataFrame(index=frame.index)
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column in frame:
            store[f"{symbol}__{column.lower()}"] = pd.to_numeric(frame[column], errors="coerce")
    compiled = compile_strategy(spec, store)
    if compiled.errors:
        return [{"bucket": "compile", "label": spec.name, "matched": False,
                 "status": "unknown", "reason": "; ".join(compiled.errors), "trace": []}]
    current_time, previous_time = frame.index[-1], frame.index[-2]
    contexts = {(symbol, spec.timeframe): {
        "previous": _context_values(compiled, symbol, previous_time),
        "current": _context_values(compiled, symbol, current_time),
        "as_of": pd.Timestamp(current_time).isoformat(),
        "confirmed": True,
    }}
    evaluations: list[dict[str, Any]] = []
    for bucket in ("entry", "exit", "trim"):
        for index, rule in enumerate(spec.rules.get(bucket) or []):
            if not isinstance(rule, Mapping):
                continue
            try:
                condition = _rule_condition(rule, symbol=symbol, timeframe=spec.timeframe)
                result = chart_conditions.evaluate_condition(
                    condition, contexts, now=pd.Timestamp(current_time).isoformat(),
                )
                evaluations.append({
                    "bucket": bucket,
                    "label": str(rule.get("label") or rule.get("name") or f"{bucket}-{index + 1}"),
                    "matched": bool(result.get("matched")),
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "trace": copy.deepcopy(result.get("trace") or []),
                })
            except ValueError as exc:
                evaluations.append({
                    "bucket": bucket, "label": f"{bucket}-{index + 1}",
                    "matched": False, "status": "unknown", "reason": str(exc), "trace": [],
                })
    return evaluations


def _condition_evaluations(packet: Mapping[str, Any], frame: pd.DataFrame) -> list[dict[str, Any]]:
    symbol, timeframe = str(packet["symbol"]), str(packet["timeframe"])
    rule = {"symbol": symbol, "timeframe": timeframe, "condition": packet["condition"]}
    contexts = chart_alert_runner.build_condition_contexts(rule, {(symbol, timeframe): frame})
    result = chart_conditions.evaluate_condition(
        packet["condition"], contexts, now=pd.Timestamp(frame.index[-1]).isoformat(),
    )
    bucket = {"enter_long": "entry", "exit_all": "exit", "trim_half": "trim"}[packet["action"]]
    return [{
        "bucket": bucket, "label": packet.get("name"),
        "matched": bool(result.get("matched")), "status": result.get("status"),
        "reason": result.get("reason"), "trace": copy.deepcopy(result.get("trace") or []),
    }]


def _decision(evaluations: list[dict[str, Any]], held: int) -> str | None:
    matched = {row["bucket"] for row in evaluations if row.get("matched")}
    if held > 0 and "exit" in matched:
        return "exit_all"
    if held > 0 and "trim" in matched:
        return "trim_half"
    if held <= 0 and "entry" in matched:
        return "enter_long"
    return None


def evaluate_and_apply(session: Mapping[str, Any], bars) -> dict[str, Any]:
    out = copy.deepcopy(dict(session))
    packet = out.get("rule_packet")
    if not isinstance(packet, Mapping):
        return out
    cursor = int(out.get("cursor") or 0)
    if any(
        event.get("type") == "rule_decision"
        and event.get("rule_packet_id") == packet.get("id")
        and int(event.get("cursor") or 0) == cursor
        for event in out.get("events") or []
    ):
        return out
    frame = normalize_ohlc_frame(bars)
    if frame is None or frame.empty or cursor >= len(frame):
        return out
    visible = frame.iloc[: cursor + 1].copy()
    if len(visible) < 2:
        evaluations = [{"bucket": "input", "label": "warmup", "matched": False,
                        "status": "unknown", "reason": "at least two bars are required", "trace": []}]
    elif packet.get("kind") == "strategy_spec":
        evaluations = _strategy_evaluations(packet, visible)
    elif packet.get("kind") == "condition":
        evaluations = _condition_evaluations(packet, visible)
    else:
        raise ValueError("unsupported replay rule packet kind")
    symbol = str(out["symbol"])
    held = int(((out.get("positions") or {}).get(symbol) or {}).get("qty") or 0)
    decision = _decision(evaluations, held)
    as_of = pd.Timestamp(visible.index[-1]).isoformat()
    out["events"].append({
        "type": "rule_decision", "cursor": cursor, "as_of": as_of,
        "rule_packet_id": packet["id"], "decision": decision,
        "evaluations": evaluations,
    })
    if decision is None:
        return out
    if decision == "enter_long":
        nav = float((out.get("metrics") or {}).get("nav") or out.get("cash") or 0.0)
        position_pct = float((packet.get("sizing") or {}).get("position_pct") or 1.0)
        # Leave room for next-open gaps, configured slippage, and fees so a
        # nominal 100% target is not mechanically rejected at execution.
        cost_buffer = (
            float((out.get("settings") or {}).get("fees_bps") or 0.0)
            + float((out.get("settings") or {}).get("slippage_bps") or 0.0)
        ) / 10_000.0
        execution_buffer = max(0.005, cost_buffer) if position_pct >= 0.99 else cost_buffer
        budget = max(0.0, nav * min(position_pct, 1.0) * (1.0 - execution_buffer))
        qty = int(budget / float(visible.iloc[-1]["Close"]))
        if qty <= 0:
            return out
        order = {"id": f"rule:{packet['id']}:{cursor}:entry", "type": "market", "side": "buy", "qty": qty}
    elif decision == "exit_all":
        order = {"id": f"rule:{packet['id']}:{cursor}:exit", "type": "market", "side": "sell", "qty": held}
    else:
        order = {"id": f"rule:{packet['id']}:{cursor}:trim", "type": "market", "side": "sell", "qty": max(1, held // 2)}
    return chart_replay.submit_order(out, order)
