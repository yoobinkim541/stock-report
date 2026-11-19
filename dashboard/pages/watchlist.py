"""dashboard/pages/watchlist.py — 관심종목. 추가·삭제는 이 페이지와 봇 /watch 가 같은 lib.watchlist 저장소. 폴더 이동도 이 페이지.

토스증권식 리스트(아바타·종목명 / 현재가·등락)와 항상 펼쳐진 관리 카드·투자자 동향 섹션.
종목 행은 `?tk=` 앵커라 누르면 종목 분석으로 이동한다(app.py 가 소비).
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import cached, data, theme, ui_actions

_SCREEN_13F_KEYS = ("berkshire", "bridgewater", "scion", "citadel", "duquesne",
                    "pershing_square", "point72", "third_point", "tudor", "nps")

_FRESHNESS_LABELS = {
    "fresh": "최신",
    "stale": "지연",
    "proxy": "프록시",
}

_SOURCE_LABELS = {
    "13f": "13F",
    "seed": "시드",
}

_CATEGORY_LABELS = {
    "holding_company": "지주회사",
    "asset_manager": "운용사",
    "hedge_fund": "헤지펀드",
    "family_office": "패밀리오피스",
    "venture_fund": "벤처펀드",
    "pension": "연기금",
    "seed": "시드",
}

_FLAG_LABELS = {
    "available": "공개",
    "proxy": "프록시",
    "unavailable": "비공개",
}


def _flag_text(flags: dict, key: str) -> str:
    return _FLAG_LABELS.get(str((flags or {}).get(key) or "unavailable"), "비공개")


def _fmt_pct(value, flag: str) -> str:
    if value is None:
        return f"— ({_FLAG_LABELS.get(flag, '비공개')})"
    try:
        return f"{float(value) * 100:.1f}% ({_FLAG_LABELS.get(flag, flag)})"
    except Exception:
        return f"{value} ({_FLAG_LABELS.get(flag, flag)})"


def _fmt_sources(value) -> str:
    items = [str(v) for v in (value or []) if str(v).strip()]
    return ", ".join(items[:3]) if items else "—"


def _render_institution_cards(rows: list[dict]):
    if not rows:
        st.info("기관 허브에 표시할 스냅샷이 아직 없습니다.")
        return
    cols = st.columns(min(3, len(rows)))
    for idx, row in enumerate(rows):
        flags = row.get("availability_flags") or {}
        with cols[idx % len(cols)].container(border=True, key=f"tn-card-inst-{idx}"):
            st.markdown(
                "\n".join([
                    f"#### {row.get('display_name', row.get('key', '기관'))}",
                    (
                        f"`{_CATEGORY_LABELS.get(row.get('category'), row.get('category', ''))}`"
                        f" · `{_SOURCE_LABELS.get(row.get('source_kind'), row.get('source_kind', ''))}`"
                        f" · `{_FRESHNESS_LABELS.get(row.get('freshness'), row.get('freshness', ''))}`"
                    ),
                    f"- 보유 종목 수: {int(row.get('holdings_count') or 0)}",
                    f"- 현금 비중: {_flag_text(flags, 'cash_ratio')}",
                    f"- 옵션 노출: {_flag_text(flags, 'options_exposure')}",
                    f"- 주요 출처: {_fmt_sources(row.get('primary_sources'))}",
                ])
            )
            top = list(row.get("top_holdings") or [])
            total = row.get("total_value_usd")
            if top:
                donut_rows = [{"ticker": h.get("ticker") or h.get("issuer") or "?",
                              "value": h.get("value_usd") or 0,
                              "name": h.get("issuer") or h.get("ticker")} for h in top]
                if total:
                    top_sum = sum(d["value"] for d in donut_rows)
                    other = total - top_sum
                    if other > 0:
                        donut_rows.append({"ticker": "기타", "value": other, "name": "기타(상위10 외)"})
                from dashboard import charts
                st.plotly_chart(charts.allocation_donut(donut_rows), width="stretch",
                                config={"displayModeBar": False})


def _render_comparison_table(comparison: dict):
    rows = list(comparison.get("rows") or [])
    if not rows:
        st.info("비교 가능한 기관 스냅샷이 아직 충분하지 않습니다.")
        return
    table = pd.DataFrame([{
        "기관": row.get("display_name"),
        "카테고리": _CATEGORY_LABELS.get(row.get("category"), row.get("category")),
        "소스": _SOURCE_LABELS.get(row.get("source_kind"), row.get("source_kind")),
        "신선도": _FRESHNESS_LABELS.get(row.get("freshness"), row.get("freshness")),
        "보유 종목 수": row.get("holdings_count"),
        "집중도": _fmt_pct(row.get("portfolio_concentration"), row.get("portfolio_concentration_flag")),
        "현금 비중": _fmt_pct(row.get("cash_ratio"), row.get("cash_ratio_flag")),
        "옵션 노출": _fmt_pct(row.get("options_exposure"), row.get("options_exposure_flag")),
        "수익률": _fmt_pct(row.get("reported_return"), row.get("reported_return_flag")),
        "대체 수익률": _fmt_pct(row.get("return_proxy"), row.get("return_proxy_flag")),
        "주요 출처": _fmt_sources(row.get("primary_sources")),
    } for row in rows])
    st.dataframe(table, hide_index=True, width="stretch")


def _render_common_moves(analysis: dict):
    shared_moves = list(analysis.get("shared_moves") or [])
    divergences = list(analysis.get("divergences") or [])
    confidence = float(analysis.get("confidence") or 0.0)
    heading = "LLM 공통 패턴 요약" if analysis.get("mode") == "llm" else "공통 패턴 요약"
    st.markdown(f"**{heading}**")
    if analysis.get("summary"):
        st.markdown(analysis["summary"])
    st.caption(f"신뢰도 {confidence:.2f}")
    if shared_moves:
        st.markdown("\n".join(["**공통 움직임**", *[f"- {item}" for item in shared_moves]]))
    if divergences:
        st.markdown("\n".join(["**차이점**", *[f"- {item}" for item in divergences]]))


def _fmt_price(ticker: str, q: dict | None) -> str:
    if not q or q.get("price") is None:
        return "—"
    price = float(q["price"])
    if q.get("currency") == "KRW":
        return f"₩{price:,.0f}"
    return f"${price:,.2f}"


def _fmt_chg(q: dict | None) -> str:
    if not q or q.get("chg_pct") is None:
        return "—"
    return f"{q['chg_pct']:+.2f}%"


def _fmt_basis(q: dict | None) -> str:
    """시세 기준 — 일봉 날짜·출처, 실패면 사유."""
    if not q:
        return "⚠️ 시세 없음"
    if q.get("error"):
        return f"⚠️ {q['error']}"
    parts = [f"{q['as_of']} 일봉" if q.get("as_of") else "", str(q.get("source") or "")]
    return " · ".join(p for p in parts if p) or "—"


def _sign(value) -> int:
    if value is None:
        return 0
    return 1 if value > 0 else -1 if value < 0 else 0


_SORTS = ("추가순", "상승률", "하락률")


def _sort_rows(rows: list[dict], quotes: dict, how: str | None) -> list[dict]:
    """추가순은 저장소 순서(최근 추가 먼저). 등락률 정렬에서 시세 없는 종목은 맨 뒤."""
    if how not in ("상승률", "하락률"):
        return rows

    def _chg(r):
        return (quotes.get(r["ticker"]) or {}).get("chg_pct")
    known = [r for r in rows if _chg(r) is not None]
    unknown = [r for r in rows if _chg(r) is None]
    return sorted(known, key=_chg, reverse=how == "상승률") + unknown


def _watch_list_rows(rows: list[dict], quotes: dict) -> tuple[list[dict], dict]:
    """리스트 행과 요약(상승·하락·시세 미확인 수, 대표 일봉 날짜)."""
    dates = Counter(q.get("as_of") for q in quotes.values() if q and q.get("as_of"))
    common = dates.most_common(1)[0][0] if dates else None
    out, up, down, failed = [], 0, 0, 0
    for r in rows:
        q = quotes.get(r["ticker"])
        name = r.get("name") or r["ticker"]
        tags = [r.get("folder") or "미분류"]
        if q and q.get("price") is not None:
            direction = _sign(q.get("chg_pct"))
            up += direction > 0
            down += direction < 0
            if q.get("as_of") and q["as_of"] != common:
                tags.append(f"{q['as_of']} 기준")
            value, delta = _fmt_price(r["ticker"], q), _fmt_chg(q)
        else:
            failed += 1
            direction, value, delta = 0, "—", "시세 없음"
        out.append({"ticker": r["ticker"], "name": name, "tags": tags, "memo": r.get("reason") or "",
                    "value": value, "delta": delta, "direction": direction,
                    "title": f"{name} · {_fmt_basis(q)} · 누르면 종목 분석"})
    return out, {"up": up, "down": down, "failed": failed, "as_of": common}


def _summary_meta(summary: dict) -> str:
    parts = [f"상승 {summary['up']} · 하락 {summary['down']}"]
    if summary["failed"]:
        parts.append(f"⚠️ 시세 미확인 {summary['failed']}개")
    if summary["as_of"]:
        parts.append(f"{summary['as_of']} 일봉")
    return " · ".join(parts)


def _ticker_label(rows: list[dict]):
    names = {r["ticker"]: r.get("name") or r["ticker"] for r in rows}
    return lambda t: f"{names.get(t, t)} ({t})" if names.get(t, t) != t else t


def _add_card() -> None:
    with st.container(border=True, key="tn-card-wl-add"):
        st.markdown("**종목 추가**")
        ticker = st.text_input("티커", key="_watchlist_add_ticker", placeholder="PLTR · 005930")
        note = st.text_input("메모 (선택)", key="_watchlist_add_note", placeholder="실적 기대")
        if st.button("추가", key="_watchlist_add_btn", type="primary", width="stretch"):
            if not ticker.strip():
                st.warning("티커를 입력하세요.")
            else:
                ok, msg = data.add_watchlist_ticker(ticker, note=note)
                if ok:
                    st.toast(f"⭐ 관심종목 추가: {msg}")
                    st.rerun()
                else:
                    st.error(msg)


def _move_card(rows: list[dict], folders: list[str]) -> None:
    with st.container(border=True, key="tn-card-wl-move"):
        st.markdown("**폴더 이동**")
        pick_ticker = st.selectbox("종목", options=[r["ticker"] for r in rows],
                                   format_func=_ticker_label(rows), key="_watchlist_move_ticker")
        target = st.selectbox("이동할 폴더", options=folders + ["+ 새 폴더"],
                              key="_watchlist_move_folder_pick")
        if target == "+ 새 폴더":
            target = st.text_input("새 폴더 이름", key="_watchlist_move_folder_new").strip()
        if st.button("이동", key="_watchlist_move_btn", width="stretch") and target:
            if data.move_watchlist_folder(pick_ticker, target):
                st.toast(f"{pick_ticker} → {target} 이동 완료")
                st.rerun()
            else:
                st.error(f"{pick_ticker} → {target} 이동 실패. 관심종목 목록에 없는 종목입니다.")


def _remove_card(rows: list[dict]) -> None:
    with st.container(border=True, key="tn-card-wl-remove"):
        st.markdown("**삭제**")
        target = st.selectbox("삭제할 종목", options=[r["ticker"] for r in rows],
                              format_func=_ticker_label(rows), key="_watchlist_remove_ticker")
        if target and ui_actions.confirm_delete(
                target,
                button_key="_watchlist_remove_btn",
                label="삭제"):
            if data.remove_watchlist_ticker(target):
                st.toast(f"🗑️ 관심종목 삭제: {target}")
                st.rerun()
            else:
                st.error(f"관심종목에 없습니다: {target}")


def _manage_section(rows: list[dict], folders: list[str]) -> None:
    """추가·폴더 이동·삭제 카드 — 접지 않고 항상 보인다 (봇 /watch add|remove 와 같은 저장소)."""
    theme.render(theme.section_head_html("관심종목 관리", "봇 /watch add·remove 와 같은 목록 · 삭제는 확인 후"))
    if not rows:
        _add_card()
        return
    c_add, c_move, c_del = st.columns(3)
    with c_add:
        _add_card()
    with c_move:
        _move_card(rows, folders)
    with c_del:
        _remove_card(rows)


def _watchlist_section(rows: list[dict]) -> None:
    if not rows:
        st.info("관심종목이 비어 있습니다 — 아래에서 추가하거나, 종목 분석의 ☆ · "
                "봇 `/watch add 종목코드` 로 추가하세요.")
        _manage_section(rows, [])
        return

    folders = data.watchlist_folders()
    counts = Counter(r.get("folder") or "미분류" for r in rows)
    folder_pick = st.pills(
        "폴더", options=["전체"] + folders, default="전체", required=True,
        format_func=lambda f: f"{f} {len(rows) if f == '전체' else counts.get(f, 0)}",
        key="_watchlist_folder_filter", label_visibility="collapsed") or "전체"
    folder_rows = rows if folder_pick == "전체" else [r for r in rows if r.get("folder") == folder_pick]

    c_search, c_sort = st.columns([3, 2], vertical_alignment="center")
    query = c_search.text_input("검색", key="_watchlist_search", label_visibility="collapsed",
                                placeholder="🔍 종목명 · 티커 · 메모 검색").strip().lower()
    sort = c_sort.segmented_control("정렬", _SORTS, default="추가순", key="_watchlist_sort",
                                    label_visibility="collapsed")
    filtered = folder_rows
    if query:
        filtered = [r for r in folder_rows if query in r["ticker"].lower()
                    or query in (r["name"] or "").lower() or query in (r["reason"] or "").lower()]

    quotes: dict = {}
    if filtered:
        try:
            quotes = cached.watchlist_quotes(tuple(sorted({r["ticker"] for r in filtered})))
        except Exception:
            quotes = {}
    list_rows, summary = _watch_list_rows(_sort_rows(filtered, quotes, sort), quotes)
    empty = (f"'{query}' 검색 결과 없음 ({len(folder_rows)}개 중)" if query
             else f"'{folder_pick}' 폴더에 종목이 없습니다.")
    theme.render(theme.stock_list_html(
        list_rows, title=f"{folder_pick} {len(filtered)}",
        meta=_summary_meta(summary) if filtered else "", empty=empty))
    st.caption("종목을 누르면 상세 분석으로 이동 · 가격은 yfinance 일봉 마지막 값"
               "(장중엔 지연 체결가, 5분 캐시 · 국내 ₩·미국 $)")

    _manage_section(rows, folders)


_TX_TYPE_LABELS = {"Purchase": "매수", "Sale": "매도", "Exchange": "교환"}


def _usd_compact(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


def _load_investor_data() -> tuple[dict, dict, dict]:
    """기관 허브·13F 스크리닝·정치인 상위를 동시에 불러온다.

    셋 다 따로 캐시되며 콜드일 때 직렬 합이 ≈6.5초(2.7+2.8+1.0)라, 접지 않고 늘 그리는 대신
    병렬로 가장 느린 것 하나만큼만 기다린다. 워커가 캐시를 쓰도록 ScriptRunContext 를 붙인다.
    """
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    jobs = (cached.institution_watch,
            lambda: cached.institution_screener(_SCREEN_13F_KEYS),
            lambda: cached.congress_top_traded(90))
    with ThreadPoolExecutor(max_workers=len(jobs), initializer=add_script_run_ctx,
                            initargs=(None, get_script_run_ctx())) as ex:
        hub, screen, congress = (f.result() for f in [ex.submit(job) for job in jobs])
    return hub or {}, screen or {}, congress or {}


def _congress_trading_section() -> None:
    theme.render(theme.section_head_html("정치인 거래 검색", "미 하원 공시 · 상원은 아직 미지원"))
    name = st.text_input("의원 이름 검색 (영문)", key="_congress_member_search",
                         placeholder="예: Pelosi")
    if not name.strip():
        st.caption("의원 이름을 입력하면 최근 공시 거래를 보여줍니다 · 상원은 아직 미지원")
        return
    rows = cached.congress_trading(name.strip())
    if not rows:
        st.caption(f"'{name}' 에 해당하는 하원의원 공시 거래를 찾지 못했습니다.")
        return
    table = pd.DataFrame([{
        "일자": r.get("date"), "티커": r.get("ticker") or "—", "종목": r.get("asset"),
        "구분": _TX_TYPE_LABELS.get(r.get("type"), r.get("type")),
        "금액(구간)": r.get("amount"), "명의": r.get("owner"),
    } for r in rows])
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("금액은 정확한 액수가 아니라 신고 구간 · 공시 지연(최대 45일) 반영 "
              "· 정보·표시용 — 매매 신호 아님")


def _bucket_list(title: str, rows: list[dict]) -> str:
    items = [{
        "ticker": r.get("ticker") or "—", "name": r.get("name") or r.get("ticker") or "—",
        "value": f"기관 {r.get('count') or 0}곳",
        "delta": f"{(r.get('avg_delta_pct') or 0) * 100:+.1f}%p",
        "direction": _sign(r.get("avg_delta_pct")),
    } for r in rows]
    return theme.stock_list_html(items, title=title, meta=f"{len(items)}종목", empty="해당 없음")


def _congress_list(title: str, rows: list[dict]) -> str:
    items = [{
        "ticker": r.get("ticker") or "—", "name": r.get("ticker") or "—",
        "value": _usd_compact(r.get("total_amount_mid")),
        "delta": f"의원 {r.get('member_count') or 0}명",
    } for r in rows]
    return theme.stock_list_html(items, title=title, meta="추정금액 합 · 거래 의원수", empty="해당 없음")


def _screening_section(screen: dict, congress: dict) -> None:
    theme.render(theme.section_head_html(
        "여러 기관 공통 움직임", "직전 분기 13F 대비 여러 기관이 같은 방향으로 움직인 종목 · 누르면 종목 분석"))
    cols = st.columns(3)
    for col, title, key in ((cols[0], "신규 편입", "new_buys"),
                            (cols[1], "비중 확대", "increased"),
                            (cols[2], "비중 축소", "decreased")):
        with col:
            theme.render(_bucket_list(title, screen.get(key) or []))

    theme.render(theme.section_head_html(
        "정치인 매수·매도 상위", "최근 90일 미 하원 공시 · 금액은 신고 구간 중간값 추정 · 매매 신호 아님"))
    ccols = st.columns(2)
    with ccols[0]:
        theme.render(_congress_list("많이 산 종목", congress.get("bought") or []))
    with ccols[1]:
        theme.render(_congress_list("많이 판 종목", congress.get("sold") or []))

    theme.render(theme.section_label_html("왜 이런 움직임일까?"))
    if st.button("🧠 스크리닝 해설 생성", key="_watch_screen_explain_btn"):
        st.session_state["_watch_screen_explain_requested"] = True
    if st.session_state.get("_watch_screen_explain_requested"):
        with st.spinner("분석 중…"):
            explain = cached.institution_screen_explain(screen, congress)
        st.markdown(explain.get("summary") or "표시할 해설이 없습니다.")
        st.caption(f"신뢰도 {explain.get('confidence', 0.0):.2f} · "
                  f"{'자동 추정' if explain.get('mode') == 'llm' else '사실 나열(해설 모델을 쓸 수 없음)'}"
                  " — 투자 조언 아님")
    else:
        st.caption("버튼을 누르면 위 스크리닝 결과를 해설합니다. 자동으로 실행하지 않습니다.")


def _institution_compare_section(hub_all: dict) -> None:
    theme.render(theme.section_head_html("기관 포트폴리오 비교", "13F 스냅샷 · 보유 상위 비중"))
    all_rows = list(hub_all.get("institutions") or [])
    all_keys = [row.get("key") for row in all_rows if row.get("key")]
    label_by_key = {
        row["key"]: (
            f"{row.get('display_name', row['key'])}"
            f" · {_CATEGORY_LABELS.get(row.get('category'), row.get('category', ''))}"
            f" · {_SOURCE_LABELS.get(row.get('source_kind'), row.get('source_kind', ''))}"
            f" · {_FRESHNESS_LABELS.get(row.get('freshness'), row.get('freshness', ''))}"
        )
        for row in all_rows
    }
    if hub_all.get("error"):
        st.warning(ui_actions.user_message(hub_all.get("error"), "기관 목록을 불러오지 못했습니다."))
    selected_keys = []
    if all_keys:
        if "_institution_watch_keys" not in st.session_state:
            st.session_state["_institution_watch_keys"] = list(all_keys)
        default_keys = list(st.session_state.get("_institution_watch_keys", all_keys))
        selected_keys = st.multiselect(
            "비교할 기관",
            options=all_keys,
            default=[key for key in default_keys if key in all_keys],
            format_func=lambda key: label_by_key.get(key, key),
            key="_institution_watch_keys",
        )
        st.caption(f"선택 {len(selected_keys)} / 전체 {len(all_keys)}")
    all_selected = len(selected_keys) == len(all_keys)
    hub = hub_all if all_selected else cached.institution_watch(tuple(selected_keys))
    _render_institution_cards(list(hub.get("institutions") or []))
    theme.render(theme.section_label_html("비교 테이블"))
    _render_comparison_table(hub.get("comparison") or {})

    theme.render(theme.section_label_html("공통 패턴 요약"))
    if st.button("🧠 해설 생성 (최대 20초)", key="_watch_llm_btn"):
        st.session_state["_watch_llm_requested"] = True
    if st.session_state.get("_watch_llm_requested"):
        with st.spinner("요약 생성 중…"):
            keys_arg = None if all_selected else tuple(selected_keys)
            hub_llm = cached.institution_watch(keys_arg, with_llm_summary=True)
        _render_common_moves(hub_llm.get("analysis") or {})
    else:
        st.caption("버튼을 누르면 기관별 공통 패턴과 차이를 요약합니다. 자동으로 실행하지 않습니다.")


def render():
    st.title("관심종목")
    st.caption("직접 고른 종목 · 종목을 누르면 상세 분석 · 봇 `/watch add|remove` 와 같은 목록")

    rows = data.load_watchlist()
    _watchlist_section(rows)

    with st.spinner("기관·정치인 데이터 불러오는 중…"):
        hub_all, screen, congress = _load_investor_data()
    _screening_section(screen, congress)
    _institution_compare_section(hub_all)
    _congress_trading_section()
