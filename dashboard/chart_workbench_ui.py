"""Shared Streamlit controls for the versioned chart analysis workbench."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

import pandas as pd
import streamlit as st

from dashboard import chart_conditions, chart_document, chart_studies


CHART_TYPE_GROUPS = {
    "가격": ("candlestick", "hollow_candle", "bars", "high_low"),
    "평활": ("line", "area", "baseline", "heikin_ashi"),
    "가격 변환": ("renko", "kagi", "line_break", "range"),
}
CHART_TYPE_LABELS = {
    "candlestick": "캔들",
    "hollow_candle": "할로우 캔들",
    "bars": "OHLC 바",
    "high_low": "고저 바",
    "line": "라인",
    "area": "영역",
    "baseline": "베이스라인",
    "heikin_ashi": "하이킨아시",
    "renko": "렌코",
    "kagi": "카기",
    "line_break": "라인 브레이크",
    "range": "레인지",
}
_TF_LABELS = {"5m": "5분", "1h": "1시간", "2h": "2시간", "4h": "4시간", "1d": "일", "1wk": "주", "1mo": "월"}
_SESSION_LABELS = {"regular": "정규장", "extended": "정규+시간외", "all": "전체 세션"}
_OPERATOR_LABELS = {
    "greater_than": "초과",
    "less_than": "미만",
    "crossing_up": "상향 돌파",
    "crossing_down": "하향 돌파",
}


def _chart_group(chart_type: str) -> str:
    return next((name for name, values in CHART_TYPE_GROUPS.items() if chart_type in values), "가격")


def render_chart_toolbar(document, *, key_prefix: str = "chart") -> dict[str, Any]:
    """Render compact chart controls and return a normalized document copy."""
    doc = chart_document.normalize_chart_document(document)
    c1, c2, c3, c4, c5 = st.columns([1.0, 1.35, 1.0, 1.2, 0.75], vertical_alignment="bottom")
    group = c1.selectbox(
        "차트 계열",
        list(CHART_TYPE_GROUPS),
        index=list(CHART_TYPE_GROUPS).index(_chart_group(doc["chart"]["type"])),
        key=f"{key_prefix}_chart_group",
    )
    choices = list(CHART_TYPE_GROUPS[group])
    current = doc["chart"]["type"] if doc["chart"]["type"] in choices else choices[0]
    chart_type = c2.selectbox(
        "차트",
        choices,
        index=choices.index(current),
        format_func=lambda value: CHART_TYPE_LABELS[value],
        key=f"{key_prefix}_chart_type_{group}",
    )
    timeframe = c3.selectbox(
        "봉",
        list(_TF_LABELS),
        index=list(_TF_LABELS).index(doc["timeframe"]),
        format_func=_TF_LABELS.get,
        key=f"{key_prefix}_timeframe",
    )
    session = c4.selectbox(
        "세션",
        list(_SESSION_LABELS),
        index=list(_SESSION_LABELS).index(doc["session"]["policy"]),
        format_func=_SESSION_LABELS.get,
        key=f"{key_prefix}_session",
    )
    scale = c5.segmented_control(
        "가격축",
        ["linear", "log"],
        default=doc["scale"]["type"],
        format_func=lambda value: "선형" if value == "linear" else "로그",
        key=f"{key_prefix}_scale",
    ) or doc["scale"]["type"]

    doc["chart"]["type"] = chart_type
    doc["timeframe"] = timeframe
    doc["session"]["policy"] = session
    doc["scale"]["type"] = scale

    params: dict[str, Any] = {}
    if chart_type in {"renko", "kagi"}:
        p1, p2 = st.columns(2)
        parameter_name = "box_size" if chart_type == "renko" else "reversal"
        auto_size = p1.toggle(
            "ATR 자동 크기",
            value=parameter_name not in doc["chart"].get("params", {}),
            key=f"{key_prefix}_auto_size_{chart_type}",
        )
        size = p2.number_input(
            "박스 크기" if chart_type == "renko" else "반전 크기",
            min_value=0.000001,
            value=float(doc["chart"].get("params", {}).get(parameter_name) or 1.0),
            key=f"{key_prefix}_{parameter_name}",
            disabled=auto_size,
        )
        if not auto_size:
            params[parameter_name] = size
    elif chart_type == "line_break":
        params["lines"] = st.number_input(
            "반전 라인 수",
            min_value=1,
            max_value=20,
            value=int(doc["chart"].get("params", {}).get("lines") or 3),
            key=f"{key_prefix}_lines",
        )
    elif chart_type == "range":
        p1, p2 = st.columns(2)
        auto_size = p1.toggle(
            "ATR 자동 크기",
            value="range_size" not in doc["chart"].get("params", {}),
            key=f"{key_prefix}_auto_size_range",
        )
        range_size = p2.number_input(
            "레인지 크기",
            min_value=0.000001,
            value=float(doc["chart"].get("params", {}).get("range_size") or 1.0),
            key=f"{key_prefix}_range_size",
            disabled=auto_size,
        )
        if not auto_size:
            params["range_size"] = range_size
    doc["chart"]["params"] = params
    return chart_document.normalize_chart_document(doc)


def render_series_manager(document, *, key_prefix: str = "chart") -> dict[str, Any]:
    """Keep series visible and editable without nesting another card surface."""
    doc = chart_document.normalize_chart_document(document)
    st.markdown("##### 시리즈")
    visible = []
    for spec in doc.get("series") or []:
        series_id = str(spec.get("id") or "series")
        label = f"{spec.get('symbol')} · {spec.get('kind')}"
        enabled = st.toggle(label, value=bool(spec.get("visible", True)), key=f"{key_prefix}_series_{series_id}")
        updated = copy.deepcopy(spec)
        updated["visible"] = enabled
        visible.append(updated)
    a1, a2 = st.columns([1.8, 0.7], vertical_alignment="bottom")
    peer = a1.text_input("비교 심볼", key=f"{key_prefix}_series_symbol", placeholder="예: QQQ, 005930.KS")
    if a2.button("추가", key=f"{key_prefix}_series_add", width="stretch") and peer.strip():
        symbol = peer.strip().upper()
        if symbol not in {str(item.get("symbol") or "").upper() for item in visible}:
            visible.append({
                "id": f"compare-{len(visible)}",
                "kind": "benchmark",
                "symbol": symbol,
                "axis": "primary",
                "normalization": "visible_start",
                "visible": True,
            })
    doc["series"] = visible
    return chart_document.normalize_chart_document(doc)


def render_study_manager(document, *, key_prefix: str = "chart") -> dict[str, Any]:
    doc = chart_document.normalize_chart_document(document)
    catalog = chart_studies.study_catalog()
    selected = st.multiselect(
        "인디케이터",
        options=[definition.id for definition in catalog],
        default=[str(item.get("study_id")) for item in doc.get("studies") or [] if item.get("study_id")],
        format_func=lambda value: next(definition.label for definition in catalog if definition.id == value),
        key=f"{key_prefix}_studies",
    )
    doc["studies"] = [{"id": f"study-{index}", "study_id": value, "parameters": {}, "visible": True} for index, value in enumerate(selected, 1)]
    return doc


def condition_from_draft(*, symbol: str, timeframe: str, field: str, operator: str,
                         value: float, confirmation: str, session: str,
                         leaf_type: str = "price") -> dict[str, Any]:
    return {
        "op": "all",
        "children": [{
            "type": str(leaf_type).strip().lower(),
            "symbol": str(symbol).strip().upper(),
            "timeframe": str(timeframe).strip().lower(),
            "field": str(field).strip().lower(),
            "operator": str(operator).strip().lower(),
            "value": float(value),
            "confirmation": str(confirmation).strip().lower(),
            "session": str(session).strip().lower(),
        }],
    }


def render_condition_builder(document, *, key_prefix: str = "chart") -> dict[str, Any] | None:
    doc = chart_document.normalize_chart_document(document)
    st.markdown("##### 조건 알림")
    type_labels = {
        "price": "가격",
        "indicator": "인디케이터",
        "fundamental": "펀더멘털",
        "relative_performance": "상대성과",
    }
    fields = {
        "price": ["close", "high", "low", "volume"],
        "indicator": ["rsi_14", "macd", "macd_signal", "vwap", "atr_14", "volume_zscore_20"],
        "fundamental": ["per", "forward_pe", "pbr", "psr", "ev_ebitda", "roe", "net_margin", "target_upside_pct"],
        "relative_performance": ["relative_return_20d", "relative_return_60d", "relative_momentum_20d"],
    }
    c0, c1, c2, c3, c4 = st.columns([1.0, 1.15, 0.8, 1.1, 0.9], vertical_alignment="bottom")
    leaf_type = c0.selectbox(
        "유형", list(type_labels), format_func=type_labels.get,
        key=f"{key_prefix}_condition_type",
    )
    field = c1.selectbox("필드", fields[leaf_type], key=f"{key_prefix}_condition_field_{leaf_type}")
    operator = c2.selectbox(
        "조건",
        list(_OPERATOR_LABELS),
        format_func=_OPERATOR_LABELS.get,
        key=f"{key_prefix}_condition_operator",
    )
    value = c3.number_input("값", value=0.0, key=f"{key_prefix}_condition_value")
    confirmation = c4.selectbox(
        "확정",
        ["bar_close", "intrabar"],
        format_func=lambda item: "봉 마감" if item == "bar_close" else "장중",
        key=f"{key_prefix}_condition_confirmation",
    )
    condition = condition_from_draft(
        symbol=doc["symbol"], timeframe=doc["timeframe"], field=field,
        operator=operator, value=value, confirmation=confirmation,
        session=doc["session"]["policy"], leaf_type=leaf_type,
    )
    errors = chart_conditions.validate_condition(condition)
    if errors:
        st.error(" · ".join(errors))
        return None
    requirements = sorted(chart_conditions.condition_requirements(
        condition, default_symbol=doc["symbol"], default_timeframe=doc["timeframe"],
    ))
    st.caption(chart_conditions.explain_condition(condition))
    st.caption("필요 데이터: " + ", ".join(f"{symbol} {timeframe}" for symbol, timeframe in requirements))
    return condition


def render_analysis_rail(snapshot: Mapping[str, Any]) -> None:
    """Always-visible compact rail; each tab degrades independently."""
    st.markdown("##### 분석 레일")
    quality = snapshot.get("data_quality") or {}
    st.caption(
        f"{snapshot.get('symbol')} · 기준 {snapshot.get('benchmark')} · "
        f"{quality.get('source', 'unknown')} · {quality.get('freshness', 'unknown')} · {quality.get('as_of') or '시각 미상'}"
    )
    tabs = st.tabs(["추세", "패턴", "멀티봉", "계절성", "상대강도", "펀더멘털", "알림", "데이터"])
    trend = snapshot.get("trend") or {}
    with tabs[0]:
        by_kind = trend.get("by_kind") or {}
        cols = st.columns(4)
        cols[0].metric("전체", trend.get("count", 0))
        cols[1].metric("지지", by_kind.get("support", 0))
        cols[2].metric("저항", by_kind.get("resistance", 0))
        cols[3].metric("채널", by_kind.get("channel", 0))
        for item in (trend.get("items") or [])[:4]:
            st.caption(f"{item.get('label')} · 점수 {(item.get('meta') or {}).get('score', '—')}")
    with tabs[1]:
        rows = snapshot.get("patterns") or []
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("패턴 후보 없음")
    with tabs[2]:
        rows = (snapshot.get("multi_timeframe") or {}).get("rows") or []
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("멀티봉 데이터 없음")
    with tabs[3]:
        rows = (snapshot.get("seasonality") or {}).get("months") or []
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("계절성 표본 부족")
    with tabs[4]:
        rs = snapshot.get("relative_strength") or {}
        cols = st.columns(3)
        cols[0].metric("국면", rs.get("quadrant", "—"))
        cols[1].metric("60일 RS", f"{float(rs.get('relative_strength_60d', 0)) * 100:.1f}%" if rs.get("ok") else "—")
        cols[2].metric("20일 모멘텀", f"{float(rs.get('relative_momentum_20d', 0)) * 100:.1f}%" if rs.get("ok") else "—")
    with tabs[5]:
        fundamentals = snapshot.get("fundamentals") or {}
        st.json(fundamentals) if fundamentals else st.caption("펀더멘털 데이터 없음")
    with tabs[6]:
        alerts = snapshot.get("alerts") or []
        st.dataframe(pd.DataFrame(alerts), hide_index=True, width="stretch") if alerts else st.caption("등록된 조건 알림 없음")
    with tabs[7]:
        st.json({"quality": quality, "errors": snapshot.get("errors") or {}})


def render_exports(document, hist, snapshot, *, key_prefix: str = "chart") -> None:
    """Export reproducible source bars and the renderer-neutral document."""
    e1, e2, e3 = st.columns(3)
    document_text = json.dumps(document, ensure_ascii=False, indent=2, default=str)
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    e1.download_button("차트 설정", document_text, "chart-document.json", "application/json", key=f"{key_prefix}_export_document")
    e2.download_button("분석 스냅샷", snapshot_text, "chart-analysis.json", "application/json", key=f"{key_prefix}_export_snapshot")
    if isinstance(hist, pd.DataFrame):
        e3.download_button("가격 데이터", hist.to_csv(), "chart-bars.csv", "text/csv", key=f"{key_prefix}_export_bars")
