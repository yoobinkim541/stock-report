"""Streamlit renderer for saved chart workspaces."""
from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard import cached, chart_workspace, charts, data, theme

_PERIOD_DAYS = {"3mo": 90, "6mo": 180, "1y": 365, "5y": 1825, "전체": None}
_TF_LABEL = {
    "5m": "5분",
    "1h": "1시간",
    "2h": "2시간",
    "4h": "4시간",
    "1d": "1일",
    "1wk": "주",
    "1mo": "월",
}
_KIND_LABEL = {"line": "라인", "candle": "캔들", "heikin_ashi": "HA"}


def _layout_columns(layout: str):
    if layout in {"2v", "2h"}:
        return st.columns(2, gap="small")
    if layout in {"2x2", "3+1"}:
        return st.columns(2, gap="small")
    if layout == "2x3":
        return st.columns(3, gap="small")
    return [st.container()]


def _panel_count(layout: str) -> int:
    return chart_workspace.LAYOUTS.get(layout, 1)


def _caption_panel(panel: dict[str, Any]) -> str:
    top = ", ".join(panel.get("top_indicators") or []) or "상단 지표 없음"
    bottom = ", ".join(panel.get("bottom_indicators") or []) or "하단 지표 없음"
    compare = ", ".join(panel.get("compare") or []) or "비교 없음"
    return f"{_TF_LABEL.get(panel['timeframe'], panel['timeframe'])} · {_KIND_LABEL.get(panel['chart_kind'], panel['chart_kind'])} · {top} · {bottom} · {compare}"


def _load_panel_hist(panel: dict[str, Any]):
    ticker = panel["ticker"]
    tf = panel["timeframe"]
    if tf == "1d":
        return cached.ohlc(ticker, period="max")
    return cached.ohlc_tf(ticker, tf)


def _render_panel_chart(panel: dict[str, Any], *, height: int = 420) -> None:
    hist = _load_panel_hist(panel)
    if hist is None or getattr(hist, "empty", True):
        st.info(f"{panel['ticker']} 가격 데이터 없음")
        return
    view_days = _PERIOD_DAYS.get(panel.get("period") or "6mo")
    hist = charts.view_window(hist, view_days)
    compare = {}
    for ticker in panel.get("compare") or []:
        cmp_hist = cached.ohlc(ticker, "max") if panel["timeframe"] == "1d" else cached.ohlc_tf(ticker, panel["timeframe"])
        if cmp_hist is not None and not getattr(cmp_hist, "empty", True) and "Close" in cmp_hist.columns:
            compare[ticker] = charts.view_window(cmp_hist["Close"], view_days)
    kind = "line" if panel.get("chart_kind") == "line" else "candle"
    if panel.get("chart_kind") == "heikin_ashi":
        hist = charts.heikin_ashi(hist)
        kind = "candle"
    top = set(panel.get("top_indicators") or [])
    bottom = set(panel.get("bottom_indicators") or [])
    fig = charts.price_chart(
        hist,
        panel["ticker"],
        kind=kind,
        view_days=view_days,
        mas=(20, 60, 120, 200) if "이동평균선" in top else (),
        show_volume="거래량" in bottom,
        show_rsi="RSI" in bottom,
        show_macd="MACD" in bottom,
        show_stoch="스토캐스틱" in bottom,
        bollinger="볼린저 밴드" in top,
        ichimoku="일목균형표" in top,
        supertrend="슈퍼트렌드" in top,
        envelope="엔벨로프" in top,
        fractals="프랙탈" in top,
        vol_profile="매물대" in top,
        psar="파라볼릭 SAR" in top,
        donchian_on="프라이스 채널" in top,
        vwap=("VWAP(세션)" in top and panel["timeframe"] in {"5m", "1h"}),
        avwap="앵커드 VWAP" in top,
        keltner="켈트너 채널" in top,
        kama="KAMA" in top,
        chandelier="샹들리에 엑시트" in top,
        show_aroon="Aroon" in bottom,
        show_bbpct="%b" in bottom,
        show_pvt="PVT" in bottom,
        log_scale=bool(panel.get("log_scale")) and not compare,
        compare=compare,
    )
    fig.update_layout(height=height)
    from dashboard import plotly_embed

    bounds = plotly_embed.compare_bounds_json(hist, compare, view_days) if compare else None
    st.components.v1.html(
        plotly_embed.pannable_chart_html(
            fig,
            hist,
            height=height,
            view_days=view_days,
            bounds_json=bounds,
            pct_mode=bool(compare),
            store_key=f"{panel['ticker']}:{panel['timeframe']}:{'pct' if compare else 'lin'}:workspace",
            light=theme.is_light(),
        ),
        height=height + 150,
    )


def render_chart_workspace(
    workspace: dict | None = None,
    *,
    render_charts: bool = True,
) -> dict:
    """Render a saved chart workspace and return the normalized current state."""
    ws = chart_workspace.normalize_workspace(
        workspace or st.session_state.get("_cw_workspace"),
        ticker=st.session_state.get("ticker", "MSFT"),
    )
    st.session_state["_cw_workspace"] = ws

    st.markdown(f"#### {ws['name']}")
    c1, c2, c3, c4, c5, c6 = st.columns([1.1, 0.9, 0.9, 0.9, 1.2, 0.55], vertical_alignment="center")
    layout = c1.segmented_control(
        "레이아웃",
        ["1", "2v", "2h", "2x2", "3+1", "2x3"],
        default=ws["layout"],
        key="_cw_layout",
    ) or ws["layout"]
    ws["layout"] = layout
    ws = chart_workspace.normalize_workspace(ws)
    ws["sync"]["symbol"] = c2.toggle("심볼 동기화", value=bool(ws["sync"].get("symbol")), key="_cw_sync_symbol")
    ws["sync"]["interval"] = c3.toggle("봉 동기화", value=bool(ws["sync"].get("interval")), key="_cw_sync_interval")
    ws["sync"]["range"] = c4.toggle("기간 동기화", value=bool(ws["sync"].get("range")), key="_cw_sync_range")
    ws["sync"]["drawings"] = c5.selectbox(
        "드로잉",
        ["off", "layout_symbol", "global_symbol"],
        index=["off", "layout_symbol", "global_symbol"].index(ws["sync"].get("drawings", "layout_symbol")),
        key="_cw_sync_drawings",
    )
    with c6.popover("AI"):
        prompt = st.text_area(
            "요청",
            key="_cw_ai_prompt",
            placeholder="예: 5분봉으로 바꾸고 VWAP/매물대를 추가해줘",
        )
        if st.button("미리보기", key="_cw_ai_preview", width="stretch"):
            st.session_state["_cw_patch_preview"] = chart_workspace.propose_workspace_patch(prompt, ws)
        preview = st.session_state.get("_cw_patch_preview")
        if preview:
            st.caption(preview.get("summary") or "패치 미리보기")
            for row in preview.get("diff") or []:
                st.markdown(f"`{row['path']}`")
                st.caption(f"{row.get('before')} -> {row.get('after')}")
            for warning in preview.get("warnings") or []:
                st.warning(warning)
            if st.button("적용", key="_cw_ai_apply", width="stretch"):
                ws = preview["after"]
                st.session_state["_cw_workspace"] = ws
                st.session_state.pop("_cw_patch_preview", None)
                st.toast("차트 워크스페이스 패치를 적용했습니다.")
                st.rerun()
    st.caption("동기화 설정은 워크스페이스에 저장됩니다. 크로스헤어 동기화는 브라우저 런타임 검증 전까지 설정값만 보관합니다.")

    active = ws.get("active_panel") or "p1"
    panels = ws["panels"][: _panel_count(ws["layout"])]
    cols = _layout_columns(ws["layout"])
    for idx, panel in enumerate(panels):
        with cols[idx % len(cols)]:
            is_active = panel["id"] == active
            border = f"border:1px solid {'#2f81f7' if is_active else 'rgba(148,163,184,.18)'};border-radius:8px;padding:8px;margin-bottom:8px;"
            st.markdown(f"<div style='{border}'><b>{panel['ticker']}</b> · {panel['id']}</div>", unsafe_allow_html=True)
            st.caption(_caption_panel(panel))
            if st.button("활성", key=f"_cw_active_{panel['id']}", width="stretch"):
                ws["active_panel"] = panel["id"]
                st.session_state["_cw_workspace"] = ws
                st.rerun()
            if render_charts:
                _render_panel_chart(panel, height=390 if len(panels) > 1 else 760)

    st.session_state["_cw_workspace"] = chart_workspace.normalize_workspace(ws)
    return st.session_state["_cw_workspace"]
