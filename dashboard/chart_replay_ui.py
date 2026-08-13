"""Compact Streamlit replay controls and persistent paper-trading terminal."""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

import pandas as pd
import streamlit as st

from agent_console import storage
from dashboard import chart_replay, chart_replay_rules
from ohlc_utils import normalize_ohlc_frame


def order_patch_url(session_id: str) -> str:
    base = str(os.getenv("AGENT_CONSOLE_URL") or "").strip()
    if not base:
        port = str(os.getenv("AGENT_CONSOLE_PORT") or "8797").strip() or "8797"
        base = f"http://127.0.0.1:{port}"
    safe_id = str(session_id or "").strip()
    return f"{base.rstrip('/')}/api/chart-replay/sessions/{safe_id}/orders"


def _scope(symbol: str, timeframe: str, workspace_id: str, key_prefix: str) -> str:
    raw = workspace_id or f"{key_prefix}:{symbol}:{timeframe}"
    return "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in raw)


def _restore_cursor(session: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    out = dict(session)
    timestamp = out.get("cursor_timestamp")
    if timestamp:
        index = pd.to_datetime(frame.index, utc=True)
        target = pd.to_datetime(timestamp, utc=True)
        eligible = [idx for idx, value in enumerate(index) if value <= target]
        out["cursor"] = eligible[-1] if eligible else 0
    else:
        out["cursor"] = min(max(int(out.get("cursor") or 0), 0), len(frame) - 1)
    out["cursor_timestamp"] = pd.Timestamp(frame.index[int(out["cursor"])]).isoformat()
    return out


def slice_until(data, as_of):
    if data is None or getattr(data, "empty", True):
        return data
    index = pd.to_datetime(data.index, utc=True)
    cutoff = pd.to_datetime(as_of, utc=True)
    return data.iloc[index <= cutoff].copy()


def records_until(records, as_of) -> list[dict[str, Any]]:
    cutoff = pd.to_datetime(as_of, utc=True)
    out: list[dict[str, Any]] = []
    for raw in records or []:
        value = raw.get("timestamp") or raw.get("date")
        if value is None:
            continue
        try:
            if pd.to_datetime(value, utc=True) <= cutoff:
                out.append(dict(raw))
        except (TypeError, ValueError):
            continue
    return out


def _latest(scope: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
    rows = storage.list_chart_replay_sessions(workspace_id=scope, limit=20)
    return next(
        (row for row in rows if row["symbol"] == symbol and row["timeframe"] == timeframe),
        None,
    )


def _create(frame: pd.DataFrame, symbol: str, timeframe: str, scope: str) -> dict[str, Any]:
    cursor = max(0, len(frame) - min(len(frame), 100))
    session = chart_replay.new_session(
        symbol=symbol,
        timeframe=timeframe,
        cursor=cursor,
        initial_cash=100_000,
        settings={"fees_bps": 1, "slippage_bps": 2, "max_leverage": 1},
        session_id=f"replay-{uuid.uuid4().hex}",
    )
    session["cursor_timestamp"] = pd.Timestamp(frame.index[cursor]).isoformat()
    return storage.save_chart_replay_session(
        session, workspace_id=scope, expected_revision=0,
        request_id=f"create-{uuid.uuid4().hex}",
    )


def _save(record: Mapping[str, Any], session: dict[str, Any], operation: str) -> dict[str, Any]:
    return storage.save_chart_replay_session(
        session,
        workspace_id=str(record.get("workspace_id") or ""),
        expected_revision=int(record.get("revision") or 0),
        request_id=f"{operation}-{uuid.uuid4().hex}",
    )


def advance_with_rules(session: Mapping[str, Any], frame: pd.DataFrame, *, steps: int) -> dict[str, Any]:
    out = dict(session)
    for _ in range(max(0, int(steps))):
        previous_cursor = int(out.get("cursor") or 0)
        out = chart_replay.advance(out, frame, steps=1)
        if int(out.get("cursor") or 0) == previous_cursor:
            break
        out = chart_replay_rules.evaluate_and_apply(out, frame)
    return out


def prepare_replay(
    hist,
    *,
    symbol: str,
    timeframe: str,
    key_prefix: str,
    workspace_id: str = "",
) -> dict[str, Any]:
    frame = normalize_ohlc_frame(hist)
    inactive = {
        "active": False, "frame": frame, "record": None, "session": None,
        "as_of": None, "order_patch_url": None, "revision": None,
    }
    if frame is None or frame.empty:
        return inactive
    symbol = str(symbol or "").upper().strip()
    timeframe = str(timeframe or "").lower().strip()
    scope = _scope(symbol, timeframe, workspace_id, key_prefix)
    active = st.toggle("리플레이", value=False, key=f"{key_prefix}_replay_enabled")
    if not active:
        st.session_state.pop(f"{key_prefix}_replay_session_id", None)
        st.session_state.pop(f"{key_prefix}_replay_playing", None)
        st.session_state["_chart_replay_playing"] = False
        return inactive

    session_key = f"{key_prefix}_replay_session_id"
    session_id = st.session_state.get(session_key)
    record = storage.get_chart_replay_session(session_id) if session_id else None
    if record is None or record["symbol"] != symbol or record["timeframe"] != timeframe:
        record = _latest(scope, symbol, timeframe) or _create(frame, symbol, timeframe, scope)
        st.session_state[session_key] = record["id"]
    session = _restore_cursor(record["session"], frame)

    controls = st.columns([1.25, 0.8, 0.8, 0.8, 1.1, 1.35], vertical_alignment="center")
    controls[0].caption(pd.Timestamp(frame.index[int(session["cursor"])]).strftime("%Y-%m-%d %H:%M"))
    speed = controls[1].selectbox(
        "배속", [1, 2, 5, 10], index=0,
        format_func=lambda value: f"{value}x", key=f"{key_prefix}_replay_speed",
        label_visibility="collapsed",
    )
    playing_key = f"{key_prefix}_replay_playing"
    playing = bool(st.session_state.get(playing_key, False))
    if controls[2].button("일시정지" if playing else "재생", key=f"{key_prefix}_replay_play", width="stretch"):
        st.session_state[playing_key] = not playing
        st.session_state["_chart_replay_playing"] = not playing
        st.rerun()
    if controls[3].button("한 봉", key=f"{key_prefix}_replay_step", width="stretch"):
        record = _save(record, advance_with_rules(session, frame, steps=1), "step")
        st.rerun()
    if controls[4].button(f"{speed}봉 진행", key=f"{key_prefix}_replay_batch", width="stretch"):
        record = _save(record, advance_with_rules(session, frame, steps=int(speed)), "advance")
        st.rerun()
    if controls[5].button("최신 봉으로", key=f"{key_prefix}_replay_live", width="stretch"):
        record = _save(record, advance_with_rules(session, frame, steps=len(frame)), "jump-live")
        st.session_state[playing_key] = False
        st.session_state["_chart_replay_playing"] = False
        st.rerun()

    if playing and int(session["cursor"]) < len(frame) - 1:
        record = _save(record, advance_with_rules(session, frame, steps=int(speed)), "play")
        session = record["session"]
    else:
        session = _restore_cursor(record["session"], frame)
        if int(session["cursor"]) >= len(frame) - 1:
            st.session_state[playing_key] = False
            st.session_state["_chart_replay_playing"] = False
    cursor = int(session["cursor"])
    return {
        "active": True,
        "frame": frame.iloc[: cursor + 1].copy(),
        "full_frame": frame,
        "record": record,
        "session": session,
        "as_of": frame.index[cursor],
        "order_patch_url": order_patch_url(record["id"]),
        "revision": int(record["revision"]),
        "key_prefix": key_prefix,
        "shared_condition": st.session_state.get(f"_chart_condition_{symbol}_{timeframe}"),
    }


def _submit(record: Mapping[str, Any], session: dict[str, Any], order: dict[str, Any]) -> None:
    _save(record, chart_replay.submit_order(session, order), "submit-order")
    st.rerun()


def render_terminal(context: Mapping[str, Any]) -> None:
    if not context.get("active"):
        return
    record = context["record"]
    session = context["session"]
    frame = context["full_frame"]
    prefix = str(context["key_prefix"])
    last = float(frame.iloc[int(session["cursor"])]["Close"])

    replay_tab, orders_tab, positions_tab, strategy_tab, events_tab, diagnostics_tab = st.tabs([
        "Replay", "Orders", "Positions", "Strategy", "Events", "Diagnostics",
    ])
    with replay_tab:
        metrics = session.get("metrics") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("NAV", f"{float(metrics.get('nav') or 0):,.2f}")
        c2.metric("현금", f"{float(session.get('cash') or 0):,.2f}")
        c3.metric("총 익스포저", f"{float(metrics.get('gross_exposure') or 0):,.2f}")
        c4.metric("최대 낙폭", f"{float(metrics.get('max_drawdown') or 0) * 100:.2f}%")
        s1, s2, s3, s4, save_settings = st.columns([1, 1, 1, 1, 0.75], vertical_alignment="bottom")
        fees = s1.number_input("수수료(bps)", min_value=0.0, value=float(session["settings"]["fees_bps"]), key=f"{prefix}_fees")
        slippage = s2.number_input("슬리피지(bps)", min_value=0.0, value=float(session["settings"]["slippage_bps"]), key=f"{prefix}_slippage")
        leverage = s3.number_input("최대 레버리지", min_value=1.0, max_value=5.0, value=float(session["settings"]["max_leverage"]), step=0.1, key=f"{prefix}_leverage")
        maintenance = s4.number_input("유지증거금", min_value=0.01, max_value=1.0, value=float(session["settings"].get("maintenance_margin") or 0.25), step=0.05, key=f"{prefix}_maintenance")
        if save_settings.button("적용", key=f"{prefix}_settings_save", width="stretch"):
            updated = chart_replay.update_settings(session, {
                "fees_bps": fees, "slippage_bps": slippage, "max_leverage": leverage,
                "maintenance_margin": maintenance,
            })
            _save(record, updated, "settings")
            st.rerun()

    with orders_tab:
        with st.form(f"{prefix}_order_form", border=False):
            o1, o2, o3, o4 = st.columns([1, 1, 1, 1.2], vertical_alignment="bottom")
            order_type = o1.segmented_control("유형", ["market", "limit", "stop"], default="market") or "market"
            side = o2.segmented_control("방향", ["buy", "sell"], default="buy") or "buy"
            qty = o3.number_input("수량", min_value=1, value=1, step=1)
            price = o4.number_input("주문가", min_value=0.000001, value=last, disabled=order_type == "market")
            bracket_on = st.toggle("손절·목표 동시 설정", value=False, disabled=side != "buy")
            b1, b2 = st.columns(2)
            stop = b1.number_input("손절가", min_value=0.000001, value=last * 0.98, disabled=not bracket_on)
            target = b2.number_input("목표가", min_value=0.000001, value=last * 1.02, disabled=not bracket_on)
            submitted = st.form_submit_button("주문 제출", width="stretch")
        if submitted:
            order = {"type": order_type, "side": side, "qty": int(qty)}
            if order_type != "market":
                order["price"] = float(price)
            if bracket_on and side == "buy":
                order["bracket"] = {"stop": float(stop), "target": float(target)}
            try:
                _submit(record, session, order)
            except ValueError as exc:
                st.error(str(exc))
        pending = [row for row in session.get("orders") or [] if row.get("status") == "pending"]
        if pending:
            st.dataframe(pd.DataFrame(pending), width="stretch", hide_index=True)
            selected = st.selectbox("취소할 주문", [row["id"] for row in pending], key=f"{prefix}_cancel_order")
            if st.button("선택 주문 취소", key=f"{prefix}_cancel_btn"):
                _save(record, chart_replay.cancel_order(session, selected), "cancel-order")
                st.rerun()
        else:
            st.caption("대기 주문 없음")

    with positions_tab:
        positions = session.get("positions") or {}
        if positions:
            st.dataframe(pd.DataFrame([{"symbol": symbol, **value} for symbol, value in positions.items()]), width="stretch", hide_index=True)
            position = positions.get(session["symbol"])
            if position:
                cols = st.columns(3)
                for column, ratio, label in zip(cols, (0.25, 0.5, 1.0), ("25% 청산", "50% 청산", "전량 청산")):
                    if column.button(label, key=f"{prefix}_exit_{ratio}", width="stretch"):
                        qty = max(1, min(int(position["qty"]), round(int(position["qty"]) * ratio)))
                        _submit(record, session, {"type": "market", "side": "sell", "qty": qty})
        else:
            st.caption("보유 포지션 없음")

    with strategy_tab:
        attached = session.get("rule_packet")
        handoff = st.session_state.get("_chart_replay_handoff")
        shared_condition = context.get("shared_condition")
        if isinstance(attached, Mapping):
            st.caption(f"연결됨 · {attached.get('name')} · {attached.get('kind')}")
            if st.button("규칙 연결 해제", key=f"{prefix}_rule_detach"):
                _save(record, chart_replay_rules.detach_rule_packet(session), "detach-rule")
                st.rerun()
        elif isinstance(handoff, Mapping) and isinstance(handoff.get("packet"), Mapping):
            packet = handoff["packet"]
            st.caption(f"전략 스튜디오 · {packet.get('name')} · {packet.get('symbol')} {packet.get('timeframe')}")
            if st.button("전략 규칙 연결", key=f"{prefix}_rule_attach_strategy", width="stretch"):
                try:
                    attached_session = chart_replay_rules.attach_rule_packet(session, packet)
                    attached_session = chart_replay_rules.evaluate_and_apply(attached_session, frame)
                    _save(record, attached_session, "attach-strategy")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if not attached and isinstance(shared_condition, Mapping):
            st.caption("현재 차트 조건 빌더의 검증된 조건을 연결할 수 있습니다.")
            if st.button("차트 조건 연결", key=f"{prefix}_rule_attach_condition", width="stretch"):
                try:
                    packet = chart_replay_rules.condition_packet(
                        shared_condition, symbol=session["symbol"], timeframe=session["timeframe"],
                    )
                    attached_session = chart_replay_rules.attach_rule_packet(session, packet)
                    attached_session = chart_replay_rules.evaluate_and_apply(attached_session, frame)
                    _save(record, attached_session, "attach-condition")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        decisions = [event for event in session.get("events") or [] if event.get("type") == "rule_decision"]
        if decisions:
            st.json(decisions[-1], expanded=False)
        elif not attached and not handoff and not shared_condition:
            st.caption("연결 가능한 전략 또는 차트 조건 없음")

    with events_tab:
        rows = list(reversed(session.get("events") or []))
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with diagnostics_tab:
        st.json({
            "session_id": record["id"], "revision": record["revision"],
            "symbol": session["symbol"], "timeframe": session["timeframe"],
            "cursor": session["cursor"], "bars_visible": int(session["cursor"]) + 1,
            "bars_total": len(frame), "as_of": str(context.get("as_of")),
            "collision_policy": "stop_first", "fill_policy": "next_bar",
        }, expanded=True)
