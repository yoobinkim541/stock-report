"""Streamlit renderer for saved chart workspaces."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from dashboard import (
    cached,
    chart_backend,
    chart_document,
    chart_orderflow,
    chart_renderer,
    chart_replay_ui,
    chart_workbench,
    chart_workbench_ui,
    chart_surface,
    chart_workspace,
    charts,
    data,
    theme,
    views,
)

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
_ALERT_INDICATOR_LABELS = {
    "rsi_14": "RSI(14)",
    "macd": "MACD",
    "macd_signal": "MACD 시그널",
    "macd_hist": "MACD 히스토그램",
    "vwap": "VWAP",
    "volume_zscore_20": "거래량 z-score(20)",
}
_LAYOUT_LABELS = {
    "1": "1개",
    "2v": "2열",
    "2h": "2행",
    "2x2": "2x2",
    "3+1": "3+1",
    "2x3": "3x2",
    "3x3": "3x3",
    "4x3": "4x3",
    "4x4": "4x4",
}
_LINK_GROUP_LABELS = {
    "": "전체",
    "red": "빨강",
    "orange": "주황",
    "yellow": "노랑",
    "green": "초록",
    "blue": "파랑",
    "purple": "보라",
    "gray": "회색",
}


def _layout_columns(layout: str):
    if layout == "2v":
        return st.columns(2, gap="small")
    if layout == "2h":
        return [st.container()]
    if layout == "2x2":
        return st.columns(2, gap="small")
    if layout == "3+1":
        narrow, wide = st.columns([1, 2], gap="small")
        return [narrow, narrow, narrow, wide]
    if layout == "2x3":
        return st.columns(3, gap="small")
    if layout == "3x3":
        return st.columns(3, gap="small")
    if layout in {"4x3", "4x4"}:
        return st.columns(4, gap="small")
    return [st.container()]


def _panel_count(layout: str) -> int:
    return chart_workspace.LAYOUTS.get(layout, 1)


def _caption_panel(panel: dict[str, Any]) -> str:
    top = ", ".join(panel.get("top_indicators") or []) or "상단 지표 없음"
    bottom = ", ".join(panel.get("bottom_indicators") or []) or "하단 지표 없음"
    compare = ", ".join(panel.get("compare") or []) or "비교 없음"
    return f"{_TF_LABEL.get(panel['timeframe'], panel['timeframe'])} · {_KIND_LABEL.get(panel['chart_kind'], panel['chart_kind'])} · {top} · {bottom} · {compare}"


def _drawing_store_key(ws: dict[str, Any], panel: dict[str, Any], *, compare: bool) -> str | None:
    mode = str(((ws or {}).get("sync") or {}).get("drawings") or "layout_symbol")
    if mode == "off":
        return None
    ticker = str((panel or {}).get("ticker") or "").upper().strip() or "UNKNOWN"
    timeframe = str((panel or {}).get("timeframe") or "1d").lower().strip() or "1d"
    scale = "pct" if compare else "lin"
    if mode == "global_symbol":
        scope = "global"
    else:
        scope = str((ws or {}).get("id") or "default").strip() or "default"
    safe_scope = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in scope)
    safe_ticker = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in ticker)
    return f"cw:{safe_scope}:{safe_ticker}:{timeframe}:{scale}"


def _crosshair_store_key(ws: dict[str, Any]) -> str | None:
    if not bool(((ws or {}).get("sync") or {}).get("crosshair")):
        return None
    scope = str((ws or {}).get("id") or "default").strip() or "default"
    safe_scope = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in scope)
    return f"cw:{safe_scope}:xh"


def _range_sync_store_key(ws: dict[str, Any], panel: dict[str, Any]) -> str | None:
    if not bool(((ws or {}).get("sync") or {}).get("range")):
        return None
    scope = str((ws or {}).get("id") or "default").strip() or "default"
    group = str((panel or {}).get("link_group") or "all").strip() or "all"
    safe_scope = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in scope)
    safe_group = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in group)
    return f"cw:{safe_scope}:range:{safe_group}"


def _panel_render_profile(ws: dict[str, Any], panel: dict[str, Any]) -> dict[str, Any]:
    panel_id = str((panel or {}).get("id") or "")
    maximized = str((ws or {}).get("maximized_panel") or "")
    if maximized:
        return {
            "visible": panel_id == maximized,
            "compact": False,
            "height": 760,
        }
    count = _panel_count(str((ws or {}).get("layout") or "1"))
    if count == 1:
        return {"visible": True, "compact": False, "height": 760}
    if count > 6:
        return {"visible": True, "compact": True, "height": 260}
    return {"visible": True, "compact": False, "height": 390}


def _drawing_sync_url(ws: dict[str, Any], store_key: str | None) -> str | None:
    if not store_key:
        return None
    workspace_id = str((ws or {}).get("id") or "").strip()
    if not workspace_id:
        return None
    if str(store_key).startswith("cw:global:"):
        return None
    safe_workspace = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in workspace_id)
    public_base = str(os.getenv("AGENT_CONSOLE_PUBLIC_URL") or "").strip()
    base = public_base or str(os.getenv("AGENT_CONSOLE_URL") or "").strip()
    if not base:
        return None
    if not public_base and (urlparse(base).hostname or "").lower() in {
        "127.0.0.1", "localhost", "::1",
    }:
        return None
    return f"{base.rstrip('/')}/api/chart-workspaces/{safe_workspace}/drawings"


def _load_panel_hist(panel: dict[str, Any]):
    ticker = panel["ticker"]
    tf = panel["timeframe"]
    document = chart_document.normalize_chart_document(
        panel.get("document") or chart_document.document_from_panel(panel), ticker=ticker,
    )
    bundle = cached.chart_data_bundle(
        ticker, tf, session_policy=document["session"]["policy"],
    )
    return (bundle or {}).get("frame")


@st.fragment
def _render_panel_chart_fragment(
    ws: dict[str, Any],
    panel: dict[str, Any],
    *,
    height: int = 420,
    compact: bool = False,
    replay_until=None,
    replay_context: dict[str, Any] | None = None,
) -> None:
    """패널 차트 렌더를 프래그먼트로 격리 — 이 패널 안의 위젯(드로잉·리플레이 등)
    조작이 전체 스크립트 rerun 을 유발해 다른 모든 패널의 무거운 차트 재조회/재렌더
    까지 함께 일으키던 문제(감사 #19) 방지."""
    _render_panel_chart(
        ws, panel, height=height, compact=compact,
        replay_until=replay_until, replay_context=replay_context,
    )


def _render_panel_chart(
    ws: dict[str, Any],
    panel: dict[str, Any],
    *,
    height: int = 420,
    compact: bool = False,
    replay_until=None,
    replay_context: dict[str, Any] | None = None,
) -> None:
    hist = _load_panel_hist(panel)
    if hist is None or getattr(hist, "empty", True):
        st.info(f"{panel['ticker']} 가격 데이터 없음")
        return
    view_days = _PERIOD_DAYS.get(panel.get("period") or "6mo")
    hist = charts.view_window(hist, view_days)
    if replay_until is not None:
        hist = hist[hist.index <= replay_until]
    if hist is None or getattr(hist, "empty", True):
        st.info(f"{panel['ticker']} replay 구간에 데이터가 없습니다.")
        return
    compare = {}
    if not compact:
        for ticker in panel.get("compare") or []:
            cmp_hist = cached.ohlc(ticker, "max") if panel["timeframe"] == "1d" else cached.ohlc_tf(ticker, panel["timeframe"])
            if cmp_hist is not None and not getattr(cmp_hist, "empty", True) and "Close" in cmp_hist.columns:
                series = charts.view_window(cmp_hist["Close"], view_days)
                if replay_until is not None:
                    series = series[series.index <= replay_until]
                compare[ticker] = series
    top = set(panel.get("top_indicators") or [])
    bottom = set(panel.get("bottom_indicators") or [])
    if compact:
        top &= {"이동평균선"}
        bottom = set()
    try:
        alert_runs = views.chart_alert_runs(str(ws.get("id") or "").strip(), limit=5) if ws.get("id") and not compact else []
    except Exception:
        alert_runs = []
    alert_markers = _alert_event_markers_for_panel(alert_runs, panel, cutoff=replay_until)
    document = chart_document.normalize_chart_document(
        panel.get("document"), ticker=panel["ticker"],
    )
    document["source"].update({
        "name": "market-data",
        "as_of": str(hist.index[-1]) if len(hist.index) else None,
        "freshness": "provider-dependent",
        "quality": "indicative",
    })
    panel_replay = None
    if replay_context and replay_context.get("active"):
        session = replay_context.get("session") or {}
        if session.get("symbol") == panel["ticker"] and session.get("timeframe") == panel["timeframe"]:
            panel_replay = session
    rendered = chart_renderer.render_plotly_chart(
        document,
        hist,
        chart_kwargs=dict(
        trades=alert_markers,
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
        replay_session=panel_replay,
        ),
    )
    fig = rendered.figure
    for warning in rendered.warnings:
        st.caption(warning)
    fig.update_layout(
        height=height,
        showlegend=not compact,
        margin=dict(l=34, r=36, t=28 if compact else 46, b=28 if compact else 38),
    )
    from dashboard import plotly_embed

    bounds = plotly_embed.compare_bounds_json(hist, compare, view_days) if compare else None
    store_key = None if compact else _drawing_store_key(ws, panel, compare=bool(compare))
    range_sync_key = (
        _range_sync_store_key(ws, panel)
        if rendered.transform.x_mode == "time"
        else None
    )
    advanced_overlays = bool(
        top & {"매물대", "자동 추세선·채널", "프랙탈", "파라볼릭 SAR", "일목균형표"}
    )
    prepared = chart_surface.prepare_chart_surface(
        rendered,
        compare=bool(compare),
        compact=compact,
        lower_panes=any(name != "거래량" for name in bottom),
        editable_orders=chart_backend.requires_editable_orders(panel_replay),
        advanced_overlays=advanced_overlays,
        height=height,
        store_key=store_key,
        range_sync_key=range_sync_key,
        light=theme.is_light(),
    )
    st.caption(prepared.status if not compact else prepared.decision.backend.upper())
    if prepared.decision.backend == "canvas":
        st.iframe(prepared.html, height=prepared.component_height)
    else:
        st.iframe(
            plotly_embed.pannable_chart_html(
                fig,
                hist,
                height=height,
                view_days=view_days,
                bounds_json=bounds,
                pct_mode=bool(compare),
                store_key=store_key,
                drawing_sync_url=_drawing_sync_url(ws, store_key),
                order_patch_url=(replay_context.get("order_patch_url") if panel_replay else None),
                replay_revision=(replay_context.get("revision") if panel_replay else None),
                crosshair_key=_crosshair_store_key(ws),
                range_sync_key=range_sync_key,
                compact=compact,
                light=theme.is_light(),
            ),
            height=height + (20 if compact else 150),
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
    workspace_widget_scope = str(ws.get("id") or "default")

    st.markdown(f"#### {ws['name']}")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(
        [1.0, 0.72, 0.72, 0.72, 0.72, 1.05, 0.5], vertical_alignment="center",
    )
    layout_options = list(chart_workspace.LAYOUTS)
    layout = c1.selectbox(
        "레이아웃",
        layout_options,
        index=layout_options.index(ws["layout"]),
        format_func=lambda value: _LAYOUT_LABELS.get(value, value),
        key=f"_cw_layout_{workspace_widget_scope}",
    )
    ws["layout"] = layout
    ws = chart_workspace.normalize_workspace(ws)
    ws["sync"]["symbol"] = c2.toggle("심볼 동기화", value=bool(ws["sync"].get("symbol")), key=f"_cw_sync_symbol_{workspace_widget_scope}")
    ws["sync"]["interval"] = c3.toggle("봉 동기화", value=bool(ws["sync"].get("interval")), key=f"_cw_sync_interval_{workspace_widget_scope}")
    ws["sync"]["range"] = c4.toggle("기간 동기화", value=bool(ws["sync"].get("range")), key=f"_cw_sync_range_{workspace_widget_scope}")
    ws["sync"]["crosshair"] = c5.toggle(
        "십자선", value=bool(ws["sync"].get("crosshair")), key=f"_cw_sync_crosshair_{workspace_widget_scope}",
    )
    ws["sync"]["drawings"] = c6.selectbox(
        "드로잉",
        ["off", "layout_symbol", "global_symbol"],
        index=["off", "layout_symbol", "global_symbol"].index(ws["sync"].get("drawings", "layout_symbol")),
        key=f"_cw_sync_drawings_{workspace_widget_scope}",
    )
    with c7.popover("AI"):
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
    st.caption("심볼·봉·기간은 전체 또는 같은 링크 그룹에 전파됩니다. 십자선과 보이는 시간 범위는 브라우저에서 즉시 동기화됩니다.")
    ws = _render_workspace_library_bar(ws)
    ws = _render_template_library_bar(ws)

    active_panel = _active_panel(ws)
    active = active_panel["id"] if active_panel else (ws.get("active_panel") or "p1")
    if active_panel:
        edited_document = chart_workbench_ui.render_chart_toolbar(
            active_panel.get("document") or chart_document.document_from_panel(active_panel),
            key_prefix=f"cw_{ws.get('id', 'default')}_{active_panel['id']}",
        )
        edited_panel = chart_document.panel_from_document(edited_document, active_panel)
        legacy_fields = {
            "ticker", "timeframe", "period", "chart_kind", "top_indicators",
            "bottom_indicators", "compare", "log_scale",
        }
        changes = {
            field: edited_panel[field]
            for field in legacy_fields
            if edited_panel.get(field) != active_panel.get(field)
        }
        active_panel["document"] = edited_document
        active_panel.update(edited_panel)
        if changes:
            mutation = chart_workspace.mutate_panel(ws, active_panel["id"], changes)
            ws = mutation["workspace"]
            st.session_state["_cw_last_sync_trace"] = mutation["trace"]
            active_panel = _active_panel(ws)
            active = active_panel["id"] if active_panel else active
        st.session_state["_cw_workspace"] = ws
    replay_cutoff = None
    replay_context: dict[str, Any] = {"active": False}
    if render_charts and active_panel:
        try:
            active_hist = _load_panel_hist(active_panel)
        except Exception:
            active_hist = None
        active_hist = charts.view_window(
            active_hist, _PERIOD_DAYS.get(active_panel.get("period") or "6mo"),
        ) if active_hist is not None else None
        replay_context = chart_replay_ui.prepare_replay(
            active_hist,
            symbol=active_panel["ticker"],
            timeframe=active_panel["timeframe"],
            key_prefix=f"workspace_{ws.get('id', 'default')}_{active_panel['id']}",
            workspace_id=str(ws.get("id") or "workspace-default"),
        )
        replay_cutoff = replay_context.get("as_of") if replay_context.get("active") else None

    panels = ws["panels"][: _panel_count(ws["layout"])]
    visible_panels = [panel for panel in panels if _panel_render_profile(ws, panel)["visible"]]
    cols = [st.container()] if ws.get("maximized_panel") else _layout_columns(ws["layout"])
    for idx, panel in enumerate(visible_panels):
        with cols[idx % len(cols)]:
            is_active = panel["id"] == active
            profile = _panel_render_profile(ws, panel)
            h1, h2, h3 = st.columns([1.45, 0.95, 0.62], vertical_alignment="center")
            if h1.button(
                f"{panel['ticker']} · {_TF_LABEL.get(panel['timeframe'], panel['timeframe'])}",
                key=f"_cw_active_{panel['id']}",
                type="primary" if is_active else "secondary",
                width="stretch",
            ):
                ws["active_panel"] = panel["id"]
                st.session_state["_cw_workspace"] = ws
                st.rerun()
            group_options = list(_LINK_GROUP_LABELS)
            selected_group = h2.selectbox(
                "링크 그룹",
                group_options,
                index=group_options.index(panel.get("link_group") or ""),
                format_func=lambda value: _LINK_GROUP_LABELS[value],
                key=f"_cw_link_group_{workspace_widget_scope}_{panel['id']}",
                label_visibility="collapsed",
            )
            if selected_group != (panel.get("link_group") or ""):
                ws = chart_workspace.mutate_panel(
                    ws, panel["id"], {"link_group": selected_group},
                )["workspace"]
                st.session_state["_cw_workspace"] = ws
                st.rerun()
            maximize_label = "복원" if ws.get("maximized_panel") == panel["id"] else "확대"
            if h3.button(maximize_label, key=f"_cw_maximize_{panel['id']}", width="stretch"):
                ws["active_panel"] = panel["id"]
                ws["maximized_panel"] = None if ws.get("maximized_panel") == panel["id"] else panel["id"]
                st.session_state["_cw_workspace"] = chart_workspace.normalize_workspace(ws)
                st.rerun()
            st.caption(
                f"{panel['id']} · 링크 {_LINK_GROUP_LABELS.get(panel.get('link_group') or '', '전체')} · {_caption_panel(panel)}"
            )
            if render_charts:
                _render_panel_chart_fragment(
                    ws, panel, height=profile["height"], compact=profile["compact"],
                    replay_until=replay_cutoff, replay_context=replay_context,
                )

    chart_replay_ui.render_terminal(replay_context)

    _render_alert_manager(ws, panels)
    _render_analysis_rail(ws, render_charts=render_charts, replay_until=replay_cutoff)

    st.session_state["_cw_workspace"] = chart_workspace.normalize_workspace(ws)
    return st.session_state["_cw_workspace"]


def _render_template_library_bar(ws: dict[str, Any]) -> dict[str, Any]:
    active = _active_panel(ws)
    if not active:
        return ws

    st.markdown("##### 템플릿 라이브러리")
    st.caption(
        f"현재 패널 {active['id']} · {active['ticker']} · {_TF_LABEL.get(active['timeframe'], active['timeframe'])} · "
        f"{_KIND_LABEL.get(active['chart_kind'], active['chart_kind'])}"
    )

    save_kind, save_name, save_btn = st.columns([0.9, 1.4, 0.7], vertical_alignment="bottom")
    template_kind_options = ["style", "indicators", "series"]
    save_kind_choice = save_kind.selectbox(
        "저장 종류",
        template_kind_options,
        format_func=chart_workspace.template_kind_label,
        key=f"_cw_tpl_save_kind_{ws.get('id', 'default')}",
    )
    default_name = f"{active['ticker']} {chart_workspace.template_kind_label(save_kind_choice)}"
    template_name = save_name.text_input(
        "이름",
        value=default_name,
        key=f"_cw_tpl_name_{save_kind_choice}_{ws.get('id', 'default')}",
        placeholder="예: Trend Clean / Volume Focus / NVDA 5m",
    )
    if save_btn.button("현재 저장", key=f"_cw_tpl_save_{save_kind_choice}_{ws.get('id', 'default')}", width="stretch"):
        try:
            record = chart_workspace.chart_template_payload(
                ws,
                kind=save_kind_choice,
                name=template_name,
                panel_id=active.get("id"),
            )
            saved = views.chart_template_save(record)
            _refresh_template_caches()
            st.toast(f"{chart_workspace.template_kind_label(save_kind_choice)} 템플릿을 저장했습니다.")
            if saved:
                st.session_state["_cw_last_template"] = saved.get("id")
        except Exception as exc:
            st.error(f"템플릿 저장 실패: {exc}")

    apply_kind, apply_target, apply_btn = st.columns([0.9, 1.3, 0.8], vertical_alignment="bottom")
    apply_kind_choice = apply_kind.selectbox(
        "불러오기 종류",
        template_kind_options,
        format_func=chart_workspace.template_kind_label,
        key=f"_cw_tpl_apply_kind_{ws.get('id', 'default')}",
    )
    templates = cached.chart_templates(kind=apply_kind_choice)
    if templates:
        picked_idx = apply_target.selectbox(
            "템플릿",
            options=list(range(len(templates))),
            key=f"_cw_tpl_pick_{apply_kind_choice}_{ws.get('id', 'default')}",
            format_func=lambda i: _template_label(templates[int(i)]),
        )
        apply_target.caption(
            "불러오기 가능: "
            + " · ".join(str(row.get("name") or row.get("id") or "템플릿") for row in templates[:3])
            + (f" 외 {len(templates) - 3}개" if len(templates) > 3 else "")
        )
        apply_to_all = False
        if apply_kind_choice != "series":
            apply_to_all = st.checkbox(
                "전체 패널에 적용",
                value=True if apply_kind_choice == "indicators" else False,
                key=f"_cw_tpl_apply_all_{apply_kind_choice}_{ws.get('id', 'default')}",
                help="스타일/인디케이터 템플릿은 레이아웃 전체에 반영할 수 있습니다.",
            )
        else:
            st.caption("시리즈 템플릿은 활성 패널에만 적용됩니다.")
        if apply_btn.button("적용", key=f"_cw_tpl_apply_{apply_kind_choice}_{ws.get('id', 'default')}", width="stretch"):
            try:
                chosen = templates[int(picked_idx)]
                ws = chart_workspace.apply_chart_template(
                    ws,
                    chosen.get("template") or chosen,
                    panel_id=active.get("id"),
                    apply_to_all=bool(apply_to_all),
                )
                st.session_state["_cw_workspace"] = ws
                st.toast(f"{_template_label(chosen)} 적용 완료")
                st.rerun()
            except Exception as exc:
                st.error(f"템플릿 적용 실패: {exc}")
    else:
        apply_target.caption("저장된 템플릿이 없습니다.")

    if templates:
        with st.expander("템플릿 미리보기", expanded=False):
            rows = []
            for row in templates[:8]:
                tpl = row.get("template") or {}
                payload = tpl.get("payload") if isinstance(tpl, dict) else {}
                rows.append({
                    "이름": row.get("name"),
                    "ID": row.get("id"),
                    "요약": _template_summary(row.get("kind"), payload),
                    "수정": row.get("updated_at", "")[:10],
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(260, 44 + 32 * len(rows)))
    return ws


def _render_alert_manager(ws: dict[str, Any], panels: list[dict[str, Any]]) -> None:
    workspace_id = str(ws.get("id") or "").strip()
    active_id = str(ws.get("active_panel") or "p1")
    active_panel = next((panel for panel in panels if str(panel.get("id")) == active_id), panels[0] if panels else None)
    if not active_panel:
        return
    st.markdown("##### 알림 매니저")
    st.caption("활성 패널 기준으로 가격 crossing 알림을 저장합니다. 서버 평가 API는 가격·인디케이터·드로잉 라인·멀티컨디션 알림까지 처리할 수 있습니다.")
    st.caption("수동 실행으로 저장된 알림을 즉시 평가하고 최근 실행 결과를 갱신할 수 있습니다.")
    try:
        rules = views.chart_alert_rules(workspace_id, limit=20) if workspace_id else []
    except Exception:
        rules = []

    run_cols = st.columns([1.0, 1.0], vertical_alignment="bottom")
    notify_now = run_cols[0].checkbox("발송 포함", value=False, key=f"_cw_alert_run_notify_{workspace_id}_{active_id}")
    if run_cols[1].button("지금 평가", key=f"_cw_alert_run_now_{workspace_id}_{active_id}", width="stretch", disabled=not bool(workspace_id)):
        try:
            result = views.chart_alert_run_once(workspace_id, notify=notify_now)
            st.toast(f"알림 평가 완료: 트리거 {result.get('event_count', 0)}건")
            st.rerun()
        except Exception as exc:
            st.error(f"알림 평가 실패: {exc}")

    with st.expander("가격 알림 만들기", expanded=False):
        c1, c2, c3, c4 = st.columns([1.0, 1.1, 0.9, 1.0], vertical_alignment="bottom")
        symbol = str(active_panel.get("ticker") or "").upper().strip()
        timeframe = str(active_panel.get("timeframe") or "1d").lower().strip()
        operator = c1.selectbox(
            "조건",
            ["crossing", "crossing_up", "crossing_down", "greater_than", "less_than"],
            format_func=_alert_operator_label,
            key=f"_cw_alert_operator_{workspace_id}_{active_id}",
        )
        threshold = c2.number_input("가격", min_value=0.0, value=0.0, step=1.0, key=f"_cw_alert_value_{workspace_id}_{active_id}")
        frequency = c3.selectbox("빈도", ["once", "per_bar"], format_func=lambda v: "한 번" if v == "once" else "봉마다", key=f"_cw_alert_freq_{workspace_id}_{active_id}")
        name = c4.text_input("이름", value=f"{symbol} {_alert_operator_label(operator)}", key=f"_cw_alert_name_{workspace_id}_{active_id}")
        message = st.text_input("메시지", value=f"{symbol} {operator} {threshold:g}", key=f"_cw_alert_msg_{workspace_id}_{active_id}")
        r1, r2, r3, r4 = st.columns([0.75, 1.15, 1.0, 1.0], vertical_alignment="bottom")
        indicator_enabled = r1.checkbox("보조 조건", value=False, key=f"_cw_alert_indicator_enabled_{workspace_id}_{active_id}")
        indicator_field = r2.selectbox(
            "지표",
            list(_ALERT_INDICATOR_LABELS),
            format_func=lambda field: _ALERT_INDICATOR_LABELS.get(str(field), str(field)),
            key=f"_cw_alert_indicator_field_{workspace_id}_{active_id}",
            disabled=not indicator_enabled,
        )
        indicator_operator = r3.selectbox(
            "지표 조건",
            ["less_than", "greater_than"],
            format_func=_alert_operator_label,
            key=f"_cw_alert_indicator_operator_{workspace_id}_{active_id}",
            disabled=not indicator_enabled,
        )
        indicator_value = r4.number_input(
            "지표 값",
            value=70.0 if indicator_field == "rsi_14" else 0.0,
            step=1.0,
            key=f"_cw_alert_indicator_value_{workspace_id}_{active_id}",
            disabled=not indicator_enabled,
        )
        store_key = _drawing_store_key(ws, active_panel, compare=bool(active_panel.get("compare")))
        if st.button("알림 저장", key=f"_cw_alert_save_{workspace_id}_{active_id}", width="stretch", disabled=not bool(workspace_id and store_key and threshold > 0)):
            try:
                views.chart_alert_rule_save({
                    "workspace_id": workspace_id,
                    "store_key": store_key,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "name": name,
                    "condition": _alert_condition_payload(
                        price_operator=operator,
                        price_value=float(threshold),
                        indicator_enabled=bool(indicator_enabled),
                        indicator_field=str(indicator_field),
                        indicator_operator=str(indicator_operator),
                        indicator_value=float(indicator_value),
                    ),
                    "message": message,
                    "frequency": frequency,
                    "enabled": True,
                })
                st.toast("차트 알림을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"알림 저장 실패: {exc}")

    if rules:
        st.dataframe(
            pd.DataFrame([{
                "이름": row.get("name"),
                "종목": row.get("symbol"),
                "봉": row.get("timeframe"),
                "조건": _alert_rule_label(row),
                "빈도": "한 번" if row.get("frequency") == "once" else row.get("frequency"),
                "상태": "켜짐" if row.get("enabled") else "꺼짐",
                "최근": (row.get("last_state") or {}).get("last_checked_at") or "—",
            } for row in rules]),
            hide_index=True,
            width="stretch",
            height=min(280, 44 + 32 * len(rules)),
        )
    else:
        st.caption("저장된 차트 알림이 아직 없습니다.")

    try:
        runs = views.chart_alert_runs(workspace_id, limit=5) if workspace_id else []
    except Exception:
        runs = []
    if runs:
        latest = runs[0]
        notification = latest.get("notification") or {}
        attempted = int(notification.get("attempted") or 0)
        delivered = int(notification.get("delivered") or 0)
        missing = len(latest.get("missing_bars") or [])
        st.caption(
            f"최근 실행 · 룰 {latest.get('rule_count', 0)} · 트리거 {latest.get('event_count', 0)} · "
            f"발송 {delivered}/{attempted} · 누락 {missing} · {latest.get('created_at', '—')}"
        )
        if len(runs) > 1:
            st.dataframe(
                pd.DataFrame([{
                    "시각": row.get("created_at"),
                    "룰": row.get("rule_count"),
                    "트리거": row.get("event_count"),
                    "발송": f"{(row.get('notification') or {}).get('delivered', 0)}/{(row.get('notification') or {}).get('attempted', 0)}",
                    "누락": len(row.get("missing_bars") or []),
                    "상태": row.get("status"),
                } for row in runs[:5]]),
                hide_index=True,
                width="stretch",
                height=min(220, 44 + 32 * min(len(runs), 5)),
            )
        latest_events = _alert_run_events(latest)
        if latest_events:
            st.markdown("##### 최근 이벤트")
            first_event = latest_events[0]
            st.caption(
                f"{first_event.get('symbol', '—')} · {first_event.get('name', '—')} · "
                f"{', '.join(str(item) for item in first_event.get('matched_conditions') or [])}"
            )
            st.dataframe(
                pd.DataFrame([{
                    "시각": event.get("as_of"),
                    "종목": event.get("symbol"),
                    "이름": event.get("name"),
                    "현재가": event.get("current_price"),
                    "이전": event.get("previous_price"),
                    "조건": ", ".join(str(item) for item in event.get("matched_conditions") or []),
                    "메시지": event.get("message"),
                } for event in latest_events[:10]]),
                hide_index=True,
                width="stretch",
                height=min(280, 44 + 32 * min(len(latest_events), 10)),
            )


def _alert_operator_label(operator: str) -> str:
    return {
        "crossing": "돌파/이탈",
        "crossing_up": "상향 돌파",
        "crossing_down": "하향 이탈",
        "greater_than": "초과",
        "less_than": "미만",
    }.get(str(operator), str(operator))


def _alert_rule_label(rule: dict[str, Any]) -> str:
    condition = rule.get("condition") or {}
    leaves = condition.get("all") if isinstance(condition.get("all"), list) else [condition]
    labels = [_alert_condition_leaf_label(item) for item in leaves if isinstance(item, dict)]
    return " · ".join(label for label in labels if label) or "—"


def _alert_condition_leaf_label(condition: dict[str, Any]) -> str:
    value = condition.get("value")
    try:
        value_text = f"{float(value):g}"
    except (TypeError, ValueError):
        value_text = str(value or "—")
    ctype = str(condition.get("type") or "price").strip().lower()
    operator_text = _alert_operator_label(str(condition.get("operator") or ""))
    if ctype == "indicator":
        field = str(condition.get("field") or "").strip().lower()
        return f"{_ALERT_INDICATOR_LABELS.get(field, field or 'indicator')} {operator_text} {value_text}"
    return f"{operator_text} {value_text}"


def _alert_condition_payload(
    *,
    price_operator: str,
    price_value: float,
    rsi_enabled: bool = False,
    rsi_operator: str = "less_than",
    rsi_value: float = 70.0,
    indicator_enabled: bool | None = None,
    indicator_field: str = "rsi_14",
    indicator_operator: str = "less_than",
    indicator_value: float = 70.0,
) -> dict[str, Any]:
    price = {"type": "price", "operator": str(price_operator), "value": float(price_value)}
    enabled = bool(rsi_enabled) if indicator_enabled is None else bool(indicator_enabled)
    field = "rsi_14" if rsi_enabled and indicator_enabled is None else str(indicator_field or "rsi_14")
    op = str(rsi_operator if rsi_enabled and indicator_enabled is None else indicator_operator)
    value = float(rsi_value if rsi_enabled and indicator_enabled is None else indicator_value)
    if not enabled:
        return price
    return {
        "all": [
            price,
            {"type": "indicator", "field": field, "operator": op, "value": value},
        ]
    }


def _alert_run_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    result = run.get("result") if isinstance(run, dict) else {}
    events = result.get("events") if isinstance(result, dict) else []
    return [event for event in events or [] if isinstance(event, dict)]


def _alert_event_markers_for_panel(
    runs: list[dict[str, Any]],
    panel: dict[str, Any],
    *,
    cutoff=None,
) -> list[dict[str, Any]]:
    symbol = str((panel or {}).get("ticker") or "").upper().strip()
    if not symbol:
        return []
    markers: list[dict[str, Any]] = []
    cutoff_ts = None
    if cutoff is not None:
        try:
            cutoff_ts = pd.Timestamp(cutoff)
        except Exception:
            cutoff_ts = None
    for run in runs or []:
        for event in _alert_run_events(run):
            event_symbol = str(event.get("symbol") or "").upper().strip()
            if event_symbol != symbol:
                continue
            timestamp = str(event.get("as_of") or event.get("timestamp") or "").strip()
            if not timestamp:
                continue
            if cutoff_ts is not None:
                try:
                    event_ts = pd.Timestamp(timestamp)
                    if event_ts > cutoff_ts:
                        continue
                except Exception:
                    pass
            try:
                price = float(event.get("current_price"))
            except (TypeError, ValueError):
                continue
            conditions = ", ".join(str(item) for item in event.get("matched_conditions") or [] if str(item).strip())
            message = str(event.get("message") or "").strip()
            note = " · ".join(part for part in (conditions, message) if part)
            markers.append({
                "event_id": event.get("alert_id") or event.get("id"),
                "date": timestamp,
                "timestamp": timestamp,
                "ticker": event_symbol,
                "side": "alert",
                "qty": None,
                "price": price,
                "avg_price": None,
                "account": "chart-alerts",
                "source": event.get("name") or "차트 알림",
                "currency": "",
                "note": note,
            })
    return markers[:50]


def _render_workspace_library_bar(ws: dict[str, Any]) -> dict[str, Any]:
    catalog = _safe_workspace_catalog()
    workspaces = list(catalog.get("workspaces") or [])
    saved_count = int(catalog.get("count") or len(workspaces) or 0)

    st.markdown("##### 저장된 레이아웃")
    st.caption(f"저장 {saved_count}개 · 레이아웃, 패널, 비교종목, 지표, AI 패치 결과를 버전으로 남깁니다.")
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.3, 0.8, 0.8, 0.9], vertical_alignment="bottom")
    ws["name"] = c1.text_input("이름", value=str(ws.get("name") or "Workspace"), key=f"_cw_name_{ws.get('id', 'default')}")

    picked_idx = None
    if workspaces:
        default_idx = _workspace_index(workspaces, str(ws.get("id") or ""))
        picked_idx = c2.selectbox(
            "레이아웃",
            options=list(range(len(workspaces))),
            index=default_idx,
            key="_cw_catalog_index",
            format_func=lambda i: _workspace_label(workspaces[i]),
        )
        c2.caption(_workspace_label(workspaces[int(picked_idx)]))
    else:
        c2.caption("저장된 레이아웃이 아직 없습니다.")

    if c3.button("불러오기", key="_cw_load_workspace", width="stretch", disabled=picked_idx is None):
        picked = workspaces[int(picked_idx)]
        loaded = _load_workspace_record(str(picked.get("id") or ""))
        if loaded:
            st.session_state["_cw_workspace"] = loaded
            st.toast("차트 레이아웃을 불러왔습니다.")
            st.rerun()

    if c4.button("저장", key="_cw_save_workspace", type="primary", width="stretch"):
        saved = _save_workspace(ws)
        if saved:
            st.session_state["_cw_workspace"] = saved
            st.toast("차트 레이아웃을 저장했습니다.")
            st.rerun()

    versions = _safe_workspace_versions(str(ws.get("id") or "")) if str(ws.get("id") or "") not in {"", "default"} else []
    if versions:
        vidx = c5.selectbox(
            "버전",
            options=list(range(len(versions))),
            key="_cw_version_index",
            format_func=lambda i: f"v{versions[i].get('version')} · {str(versions[i].get('created_at') or '')[:10]}",
        )
        if c5.button("버전 적용", key="_cw_load_version", width="stretch"):
            loaded = _load_workspace_record(str(ws.get("id") or ""), version=int(versions[int(vidx)].get("version") or 0))
            if loaded:
                st.session_state["_cw_workspace"] = loaded
                st.toast("선택한 버전을 불러왔습니다.")
                st.rerun()
    else:
        c5.caption("버전 없음")

    return chart_workspace.normalize_workspace(ws)


def _analysis_orderflow_loader(replay_until):
    if replay_until is None:
        return chart_orderflow.load_snapshot
    return lambda _symbol: {"ok": False, "reason": "replay_isolated"}


def _render_analysis_rail(
    ws: dict[str, Any],
    *,
    render_charts: bool,
    replay_until=None,
) -> None:
    st.divider()
    panel = _active_panel(ws)
    if not panel:
        st.markdown("##### 분석 레일")
        st.caption("활성 패널을 선택하면 분석 레일이 표시됩니다.")
        return
    if not render_charts:
        st.markdown("##### 분석 레일")
        st.caption("차트 렌더링이 꺼진 모드에서는 데이터 조회 없이 분석 레일 구조만 표시합니다.")
        return

    try:
        hist = _load_panel_hist(panel)
    except Exception as exc:
        st.warning(f"{panel['ticker']} 분석 데이터 로딩 실패: {exc}")
        return
    if hist is None or getattr(hist, "empty", True):
        st.info(f"{panel['ticker']} 분석 데이터 없음")
        return
    if replay_until is not None:
        hist = hist[hist.index <= replay_until]
        if hist is None or getattr(hist, "empty", True):
            st.info(f"{panel['ticker']} replay 구간에 분석 데이터가 없습니다.")
            return

    def _replay_loader(ticker: str, tf: str):
        df = _analysis_ohlc_loader(ticker, tf)
        if replay_until is not None and df is not None and not getattr(df, "empty", True):
            df = df[df.index <= replay_until]
        return df

    document = chart_document.normalize_chart_document(
        panel.get("document") or chart_document.document_from_panel(panel),
        ticker=panel["ticker"],
    )
    document["source"].update({
        "name": "market-data",
        "as_of": str(hist.index[-1]) if len(hist.index) else None,
        "freshness": "provider-dependent",
        "quality": "indicative",
    })
    snapshot = chart_workbench.build_analysis_snapshot(
        document,
        hist,
        ohlc_loader=_replay_loader,
        fundamental_loader=lambda symbol: cached.valuation(symbol) or {},
        alert_loader=lambda _symbol: list(document.get("alerts") or []),
        orderflow_loader=_analysis_orderflow_loader(replay_until),
    )
    chart_workbench_ui.render_analysis_rail(snapshot)
    tools_key = f"cw_{ws.get('id', 'default')}_{panel['id']}"
    with st.expander("시리즈·인디케이터·조건", expanded=False):
        document = chart_workbench_ui.render_series_manager(document, key_prefix=tools_key)
        document = chart_workbench_ui.render_study_manager(document, key_prefix=tools_key)
        condition = chart_workbench_ui.render_condition_builder(document, key_prefix=tools_key)
        if condition is not None:
            st.session_state[f"{tools_key}_condition_draft"] = condition
        chart_workbench_ui.render_exports(document, hist, snapshot, key_prefix=tools_key)
    panel["document"] = document
    panel.update(chart_document.panel_from_document(document, panel))


def _active_panel(ws: dict[str, Any]) -> dict[str, Any] | None:
    panels = list((ws or {}).get("panels") or [])
    active = (ws or {}).get("active_panel")
    for panel in panels:
        if panel.get("id") == active:
            return panel
    return panels[0] if panels else None


def _benchmark_ticker(panel: dict[str, Any]) -> str:
    compare = [str(x).strip().upper() for x in panel.get("compare") or [] if str(x).strip()]
    if compare:
        return compare[0]
    ticker = str(panel.get("ticker") or "").upper()
    if ticker.endswith((".KS", ".KQ")) or (ticker.isdigit() and len(ticker) == 6):
        return "^KS11"
    return "QQQ"


def _analysis_ohlc_loader(ticker: str, tf: str):
    return (cached.chart_data_bundle(ticker, tf, session_policy="regular") or {}).get("frame")


def _render_mtfa_table(mtfa: dict[str, Any]) -> None:
    st.markdown("###### 멀티타임프레임")
    rows = []
    for row in mtfa.get("rows") or []:
        rows.append(
            {
                "봉": _TF_LABEL.get(row.get("timeframe"), row.get("timeframe")),
                "상태": "OK" if row.get("ok") else "부족",
                "추세": _trend_label(row.get("trend")),
                "RSI": _rsi_zone_label(row.get("rsi_zone")),
                "최근가": row.get("last") if row.get("ok") else row.get("reason"),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(220, 44 + 32 * len(rows)))
    else:
        st.caption("멀티타임프레임 분석 결과 없음")


def _render_pattern_table(patterns: list[dict[str, Any]]) -> None:
    st.markdown("###### 자동 패턴 후보")
    if not patterns:
        st.caption("현재 구간에서 우선 표시할 자동 패턴 후보가 없습니다.")
        return
    rows = []
    for row in patterns:
        rows.append(
            {
                "패턴": _pattern_label(row.get("kind")),
                "방향": _direction_label(row.get("direction")),
                "신뢰": _pct(row.get("confidence")),
                "무효화": row.get("invalidation_price"),
                "근거": ", ".join(row.get("evidence") or []),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(240, 44 + 32 * len(rows)))


def _render_seasonality_table(seasonality: dict[str, Any]) -> None:
    st.markdown("###### 월별 계절성")
    if not seasonality.get("ok"):
        st.caption("월별 계절성을 계산할 만큼 긴 이력이 없습니다.")
        return
    rows = []
    for row in seasonality.get("months") or []:
        rows.append(
            {
                "월": f"{row.get('month')}월",
                "평균": _pct(row.get("avg_return")),
                "승률": _pct(row.get("win_rate")),
                "표본": row.get("sample"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=430)


def _pct(value: Any) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _quadrant_label(value: Any) -> str:
    return {
        "leading": "주도",
        "weakening": "둔화",
        "lagging": "소외",
        "improving": "개선",
    }.get(str(value or ""), "—")


def _trend_label(value: Any) -> str:
    return {"up": "상승", "down": "하락", "mixed": "혼조"}.get(str(value or ""), "—")


def _rsi_zone_label(value: Any) -> str:
    return {"overbought": "과열", "oversold": "침체", "neutral": "중립"}.get(str(value or ""), "—")


def _pattern_label(value: Any) -> str:
    return {
        "channel_breakout": "채널 돌파",
        "bollinger_squeeze_expansion": "밴드 압축 해소",
    }.get(str(value or ""), str(value or "—"))


def _direction_label(value: Any) -> str:
    return {"up": "상방", "down": "하방"}.get(str(value or ""), "—")


def _safe_workspace_catalog() -> dict[str, Any]:
    try:
        return cached.chart_workspace_catalog()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "workspaces": []}


def _safe_workspace_versions(workspace_id: str) -> list[dict[str, Any]]:
    if not workspace_id:
        return []
    try:
        return cached.chart_workspace_versions(workspace_id)
    except Exception:
        try:
            return views.chart_workspace_versions(workspace_id)
        except Exception:
            return []


def _load_workspace_record(workspace_id: str, *, version: int | None = None) -> dict[str, Any] | None:
    try:
        row = cached.chart_workspace_detail(workspace_id, version=version)
    except Exception:
        try:
            row = views.chart_workspace_detail(workspace_id, version=version)
        except Exception as exc:
            st.error(f"레이아웃 불러오기 실패: {exc}")
            return None
    workspace = row.get("workspace") if isinstance(row, dict) else None
    if not isinstance(workspace, dict):
        st.error("레이아웃 데이터가 올바르지 않습니다.")
        return None
    workspace = dict(workspace)
    workspace["id"] = str(row.get("id") or row.get("workspace_id") or workspace.get("id") or workspace_id)
    workspace["version"] = int(row.get("version") or workspace.get("version") or 1)
    return chart_workspace.normalize_workspace(workspace)


def _save_workspace(ws: dict[str, Any]) -> dict[str, Any] | None:
    try:
        saved = views.chart_workspace_save(ws)
    except Exception as exc:
        st.error(f"레이아웃 저장 실패: {exc}")
        return None
    _refresh_workspace_caches()
    workspace = saved.get("workspace") if isinstance(saved, dict) else None
    if not isinstance(workspace, dict):
        st.error("저장 응답이 올바르지 않습니다.")
        return None
    workspace = dict(workspace)
    workspace["id"] = str(saved.get("id") or workspace.get("id") or "")
    workspace["version"] = int(saved.get("version") or workspace.get("version") or 1)
    return chart_workspace.normalize_workspace(workspace)


def _refresh_workspace_caches() -> None:
    for cache in (
        getattr(cached, "chart_workspace_catalog", None),
        getattr(cached, "chart_workspace_detail", None),
        getattr(cached, "chart_workspace_versions", None),
    ):
        try:
            clear = getattr(cache, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass


def _workspace_index(rows: list[dict[str, Any]], workspace_id: str) -> int:
    for idx, row in enumerate(rows):
        if str(row.get("id") or "") == workspace_id:
            return idx
    return 0


def _workspace_label(row: dict[str, Any]) -> str:
    return f"{row.get('name') or 'Workspace'} · {row.get('layout') or '1'} · v{row.get('version') or 0}"


def _template_label(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or "").strip().lower()
    tpl = row.get("template") if isinstance(row.get("template"), dict) else {}
    payload = tpl.get("payload") if isinstance(tpl, dict) else {}
    source = tpl.get("source") if isinstance(tpl, dict) else {}
    source_ticker = str((source or {}).get("ticker") or "").strip().upper()
    summary = _template_summary(kind, payload)
    ticker_part = f" · {source_ticker}" if source_ticker else ""
    return f"{chart_workspace.template_kind_label(kind)} · {row.get('name') or row.get('id')}{ticker_part} · {summary}"


def _template_summary(kind: str, payload: dict[str, Any] | None) -> str:
    payload = payload or {}
    kind = str(kind or "").strip().lower()
    if kind == "style":
        return (
            f"{_TF_LABEL.get(payload.get('timeframe'), payload.get('timeframe', ''))}"
            f" · {_KIND_LABEL.get(payload.get('chart_kind'), payload.get('chart_kind', ''))}"
            f" · {'로그' if payload.get('log_scale') else '선형'}"
        ).strip(" ·")
    if kind == "indicators":
        top = len(payload.get("top_indicators") or [])
        bottom = len(payload.get("bottom_indicators") or [])
        return f"상단 {top} · 하단 {bottom}"
    if kind == "series":
        ticker = str(payload.get("ticker") or "").upper()
        compare = len(payload.get("compare") or [])
        return f"{ticker or '—'} · 비교 {compare}"
    return "—"


def _refresh_template_caches() -> None:
    for cache in (
        getattr(cached, "chart_templates", None),
    ):
        try:
            clear = getattr(cache, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass
