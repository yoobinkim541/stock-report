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


def _replay_error(exc: Exception) -> str:
    text = str(exc)
    known = {
        "invalid order": "주문 수량과 가격을 확인하세요.",
        "bracket requires a long entry": "손절·목표 주문은 매수에만 연결됩니다.",
        "bracket stop must be below target": "손절가는 목표가보다 낮아야 합니다.",
        "bracket stop must remain below target": "손절가는 목표가보다 낮아야 합니다.",
        "bracket target must remain above stop": "목표가는 손절가보다 높아야 합니다.",
        "order not found": "주문을 찾지 못했습니다.",
        "only pending orders can be cancelled": "대기 중인 주문만 취소할 수 있습니다.",
        "order price is not editable": "이 주문은 가격을 바꿀 수 없습니다.",
        "invalid replay rule packet": "규칙이 올바르지 않습니다.",
        "rule packet symbol does not match replay session": "규칙의 종목이 리플레이 종목과 다릅니다.",
        "rule packet timeframe does not match replay session": "규칙의 시간 단위가 리플레이와 다릅니다.",
        "unsupported replay rule packet kind": "지원하지 않는 규칙입니다.",
        "unsupported replay rule action": "지원하지 않는 규칙 동작입니다.",
    }
    if text in known:
        return known[text]
    if text.startswith("invalid number"):
        return "숫자 값을 확인하세요."
    if text.startswith("duplicate order id"):
        return "같은 주문 번호가 이미 있습니다."
    return "요청을 처리하지 못했습니다. 입력값을 확인한 뒤 다시 시도하세요."


_RULE_KIND = {"strategy_spec": "전략", "condition": "차트 조건"}


def _rule_kind(value: object) -> str:
    return _RULE_KIND.get(str(value or "").strip(), "규칙")


_ORDER_TYPE = {"market": "시장가", "limit": "지정가", "stop": "스탑"}
_ORDER_SIDE = {"buy": "매수", "sell": "매도"}
_ORDER_STATUS = {"pending": "대기", "filled": "체결", "cancelled": "취소", "canceled": "취소"}
_EVENT_TYPE = {
    "order_submitted": "주문 제출",
    "order_cancelled": "주문 취소",
    "order_canceled": "주문 취소",
    "order_filled": "주문 체결",
    "fill": "체결",
    "rule_decision": "규칙 판단",
}
_EVENT_REASON = {"shares": "수량", "user": "사용자", "stop": "손절", "target": "목표"}


def _account_money(symbol: str, value: float) -> str:
    import ticker_names
    unit = ticker_names.macro_unit(symbol) or ("₩" if ticker_names.is_kr_listing(symbol) else "$")
    if unit == "₩":
        return f"{unit}{value:,.0f}"
    if unit == "%":
        return f"{value:,.2f}{unit}"
    return f"{unit}{value:,.2f}"


def _pending_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        price = row.get("price")
        out.append({
            "유형": _ORDER_TYPE.get(str(row.get("type") or ""), "주문"),
            "방향": _ORDER_SIDE.get(str(row.get("side") or ""), "—"),
            "수량": row.get("qty"),
            "가격": "—" if price in (None, "") else price,
            "상태": _ORDER_STATUS.get(str(row.get("status") or ""), "대기"),
        })
    return out


def _pending_choice_label(row: Mapping[str, Any]) -> str:
    price = row.get("price")
    price_text = "시장가" if price in (None, "") else str(price)
    side = _ORDER_SIDE.get(str(row.get("side") or ""), "주문")
    kind = _ORDER_TYPE.get(str(row.get("type") or ""), "주문")
    return f"{side} {kind} {row.get('qty')}주 · {price_text}"


def _cancel_labels(rows: list[Mapping[str, Any]]) -> dict[str, str]:
    base = {str(row.get("id")): _pending_choice_label(row) for row in rows}
    counts: dict[str, int] = {}
    for label in base.values():
        counts[label] = counts.get(label, 0) + 1
    if any(count > 1 for count in counts.values()):
        return {order_id: f"{label} · {order_id[:6]}" for order_id, label in base.items()}
    return base




def _event_rows(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for event in events:
        reason = event.get("reason")
        out.append({
            "유형": _EVENT_TYPE.get(str(event.get("type") or ""), "기록"),
            "주문": event.get("order_id") or "—",
            "사유": _EVENT_REASON.get(str(reason or ""), "—" if not reason or str(reason).isascii() else str(reason)),
            "커서": event.get("cursor", "—"),
        })
    return out

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
        was_playing = bool(st.session_state.get("_chart_replay_playing"))
        st.session_state.pop(f"{key_prefix}_replay_session_id", None)
        st.session_state.pop(f"{key_prefix}_replay_playing", None)
        st.session_state["_chart_replay_playing"] = False
        if was_playing:
            st.rerun()
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
        if int(session["cursor"]) >= len(frame) - 1 and playing:
            st.session_state[playing_key] = False
            st.session_state["_chart_replay_playing"] = False
            st.rerun()
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
        "symbol": symbol,
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
    symbol = str(context.get("symbol") or "")
    if symbol:
        from dashboard.ticker_strip import ticker_strip_text
        st.caption(ticker_strip_text(symbol, last))

    replay_tab, orders_tab, positions_tab, strategy_tab, events_tab, diagnostics_tab = st.tabs([
        "리플레이", "주문", "포지션", "전략", "이벤트", "진단",
    ])
    with replay_tab:
        metrics = session.get("metrics") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("순자산", _account_money(symbol, float(metrics.get("nav") or 0)))
        c2.metric("현금", _account_money(symbol, float(session.get("cash") or 0)))
        c3.metric("총 익스포저", _account_money(symbol, float(metrics.get("gross_exposure") or 0)))
        c4.metric("최대 낙폭", f"{float(metrics.get('max_drawdown') or 0) * 100:.2f}%")
        s1, s2, s3, s4, save_settings = st.columns([1, 1, 1, 1, 0.75], vertical_alignment="bottom")
        fees = s1.number_input("수수료(bp)", min_value=0.0, value=float(session["settings"]["fees_bps"]), key=f"{prefix}_fees", help="1bp는 0.01%")
        slippage = s2.number_input("슬리피지(bp)", min_value=0.0, value=float(session["settings"]["slippage_bps"]), key=f"{prefix}_slippage", help="1bp는 0.01%")
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
        # 폼 안 위젯은 제출 전에 리런되지 않는다. 유형·토글을 폼에 두면 주문가·손절가가 꺼진 채다.
        type_label = st.segmented_control(
            "유형", ["시장가", "지정가", "스탑"], default="시장가", key=f"{prefix}_order_type",
        ) or "시장가"
        side_label = st.segmented_control(
            "방향", ["매수", "매도"], default="매수", key=f"{prefix}_order_side",
        ) or "매수"
        order_type = {"시장가": "market", "지정가": "limit", "스탑": "stop"}[type_label]
        side = {"매수": "buy", "매도": "sell"}[side_label]
        bracket_on = bool(st.toggle(
            "손절·목표 동시 설정", value=False, disabled=side != "buy", key=f"{prefix}_bracket",
        )) and side == "buy"
        with st.form(f"{prefix}_order_form", border=False):
            o1, o2 = st.columns(2)
            qty = o1.number_input("수량", min_value=1, value=1, step=1)
            price = o2.number_input(
                "주문가", min_value=0.000001, value=last, disabled=order_type == "market",
            )
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
                st.error(_replay_error(exc))
        pending = [row for row in session.get("orders") or [] if row.get("status") == "pending"]
        if pending:
            st.dataframe(pd.DataFrame(_pending_rows(pending)), width="stretch", hide_index=True)
            cancel_labels = _cancel_labels(pending)
            selected = st.selectbox(
                "취소할 주문", list(cancel_labels),
                format_func=lambda order_id: cancel_labels.get(order_id, order_id),
                key=f"{prefix}_cancel_order")
            if st.button("선택 주문 취소", key=f"{prefix}_cancel_btn"):
                try:
                    _save(record, chart_replay.cancel_order(session, selected), "cancel-order")
                    st.rerun()
                except ValueError as exc:
                    st.error(_replay_error(exc))
        else:
            st.caption("대기 주문 없음")

    with positions_tab:
        positions = session.get("positions") or {}
        if positions:
            st.dataframe(pd.DataFrame([
                {"종목": symbol, "수량": value.get("qty"), "평단": value.get("avg_price")}
                for symbol, value in positions.items()
            ]), width="stretch", hide_index=True)
            position = positions.get(session["symbol"])
            if position:
                held_qty = int(position["qty"])
                cols = st.columns(3)
                for column, ratio, label in zip(cols, (0.25, 0.5, 1.0), ("25% 청산", "50% 청산", "전량 청산")):
                    qty = held_qty if ratio == 1 else min(held_qty, max(1, round(held_qty * ratio)))
                    partial_blocked = ratio < 1 and qty >= held_qty
                    shown = label if ratio == 1 or partial_blocked else (label if qty == round(held_qty * ratio) else f"{qty}주 청산")
                    if column.button(
                        shown,
                        key=f"{prefix}_exit_{ratio}",
                        width="stretch",
                        disabled=partial_blocked,
                        help="보유 수량이 적어 이 비율로는 나눌 수 없습니다." if partial_blocked else None,
                    ):
                        _submit(record, session, {"type": "market", "side": "sell", "qty": qty})
        else:
            st.caption("보유 포지션 없음")

    with strategy_tab:
        attached = session.get("rule_packet")
        handoff = st.session_state.get("_chart_replay_handoff")
        shared_condition = context.get("shared_condition")
        if isinstance(attached, Mapping):
            st.caption(f"연결됨 · {attached.get('name')} · {_rule_kind(attached.get('kind'))}")
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
                    st.error(_replay_error(exc))
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
                    st.error(_replay_error(exc))
        decisions = [event for event in session.get("events") or [] if event.get("type") == "rule_decision"]
        if decisions:
            st.json(decisions[-1], expanded=False)
        elif not attached and not handoff and not shared_condition:
            st.caption("연결 가능한 전략 또는 차트 조건 없음")

    with events_tab:
        rows = list(reversed(session.get("events") or []))
        st.dataframe(pd.DataFrame(_event_rows(rows)), width="stretch", hide_index=True)

    with diagnostics_tab:
        st.json({
            "세션": record["id"],
            "개정": record["revision"],
            "종목": session["symbol"],
            "봉": session["timeframe"],
            "커서": session["cursor"],
            "보이는 봉": int(session["cursor"]) + 1,
            "전체 봉": len(frame),
            "기준 시각": str(context.get("as_of")),
            "충돌 정책": "손절 우선",
            "체결 정책": "다음 봉",
        }, expanded=True)
