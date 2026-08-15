"""dashboard/pages/watchlist.py — 관심종목 (읽기 전용). 삭제는 봇 /watch remove 에서만."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import cached, data

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
        col = cols[idx % len(cols)]
        with col:
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
    st.markdown(f"### {heading}")
    if analysis.get("summary"):
        st.markdown(analysis["summary"])
    st.caption(f"신뢰도 {confidence:.2f}")
    if shared_moves:
        st.markdown("\n".join(["**공통 움직임**", *[f"- {item}" for item in shared_moves]]))
    if divergences:
        st.markdown("\n".join(["**차이점**", *[f"- {item}" for item in divergences]]))


def _fmt_price(q: dict | None) -> str:
    if not q or q.get("price") is None:
        return "—"
    return f"${q['price']:,.2f}"


def _fmt_chg(q: dict | None) -> str:
    if not q or q.get("chg_pct") is None:
        return "—"
    return f"{q['chg_pct']:+.2f}%"


def _watchlist_section(rows: list[dict]) -> None:
    st.markdown("### ⭐ 내 관심종목")
    if not rows:
        st.info("관심종목이 비어 있습니다 — 봇에서 `/watch add TICKER 메모` 로 추가하거나 "
                "버핏 13F 신규편입 크론(매주 월요일)을 기다리세요.")
        return

    query = st.text_input("🔍 검색 (티커·종목명·사유)", key="_watchlist_search",
                          placeholder="예: PLTR, 반도체").strip().lower()
    filtered = rows
    if query:
        filtered = [r for r in rows if query in r["ticker"].lower()
                   or query in (r["name"] or "").lower() or query in (r["reason"] or "").lower()]
    if not filtered:
        st.caption(f"'{query}' 검색 결과 없음 (전체 {len(rows)}개)")
        return

    tickers = tuple(sorted({r["ticker"] for r in filtered}))
    try:
        quotes = cached.watchlist_quotes(tickers)
    except Exception:
        quotes = {}

    df = pd.DataFrame([{
        "티커": r["ticker"], "종목": r["name"],
        "현재가": _fmt_price(quotes.get(r["ticker"])),
        "등락률": _fmt_chg(quotes.get(r["ticker"])),
        "추가 사유": r["reason"], "추가일": r["added_at"][:10] if r["added_at"] else "",
    } for r in filtered])

    st.caption(f"{len(filtered)}개 · 🔍 **행을 클릭**하면 해당 종목 상세 분석으로 이동 "
              "· 가격은 최근 종가 기준(최대 5분 지연, 국내 티커는 미지원)")
    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    )
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if sel and sel[0] < len(filtered):
        picked = filtered[sel[0]]["ticker"]
        if picked and picked != st.session_state.get("ticker"):
            st.session_state["ticker"] = picked
            st.toast(f"종목 분석 → {picked}")
            pg = st.session_state.get("_ticker_page")
            if pg:
                st.switch_page(pg)
            else:
                st.rerun()


_TX_TYPE_LABELS = {"Purchase": "매수", "Sale": "매도", "Exchange": "교환"}


def _congress_trading_section() -> None:
    st.markdown("### 🏛️ 정치인 최근 거래 (미 하원, STOCK Act 공시)")
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
    st.caption("금액은 정확한 액수가 아니라 신고 구간(bracket) · 공시 지연(최대 45일) 반영 "
              "· 정보·표시용 — 매매 신호 아님")


def _bucket_table(col, title: str, rows: list[dict]) -> None:
    with col:
        st.caption(title)
        if not rows:
            st.caption("해당 없음")
            return
        df = pd.DataFrame([{
            "티커": r.get("ticker") or "—", "종목": r.get("name"),
            "기관수": r.get("count"), "평균변화": f"{r.get('avg_delta_pct', 0) * 100:+.1f}%p",
        } for r in rows])
        st.dataframe(df, hide_index=True, width="stretch")


def _congress_table(rows: list[dict]) -> None:
    if not rows:
        st.caption("해당 없음")
        return
    df = pd.DataFrame([{
        "티커": r.get("ticker"), "거래 의원수": r.get("member_count"),
        "추정금액(합)": f"${r.get('total_amount_mid', 0):,.0f}",
    } for r in rows])
    st.dataframe(df, hide_index=True, width="stretch")


def _screening_section() -> None:
    screen = cached.institution_screener(_SCREEN_13F_KEYS)
    congress = cached.congress_top_traded(90)

    st.markdown("### 🔄 여러 기관 공통 움직임 (직전 분기 대비)")
    cols = st.columns(3)
    _bucket_table(cols[0], "🆕 신규편입", screen.get("new_buys") or [])
    _bucket_table(cols[1], "📈 비중 증가", screen.get("increased") or [])
    _bucket_table(cols[2], "📉 비중 감소", screen.get("decreased") or [])
    st.caption("직전 분기 13F 대비, 여러 기관이 같은 방향으로 움직인 종목만 표시 · 정보용")

    st.markdown("### 🏛️ 정치인 매수·매도 상위 (최근 90일, 하원)")
    ccols = st.columns(2)
    with ccols[0]:
        st.caption("많이 산 종목")
        _congress_table(congress.get("bought") or [])
    with ccols[1]:
        st.caption("많이 판 종목")
        _congress_table(congress.get("sold") or [])
    st.caption("금액은 신고 구간 중간값 추정 합계 · 매매 신호 아님")

    st.markdown("### 🧠 왜 이런 움직임일까? (LLM 분석)")
    if st.button("🧠 스크리닝 해설 생성", key="_watch_screen_explain_btn"):
        st.session_state["_watch_screen_explain_requested"] = True
    if st.session_state.get("_watch_screen_explain_requested"):
        with st.spinner("분석 중…"):
            explain = cached.institution_screen_explain(screen, congress)
        st.markdown(explain.get("summary") or "표시할 해설이 없습니다.")
        st.caption(f"신뢰도 {explain.get('confidence', 0.0):.2f} · "
                  f"{'LLM 추정' if explain.get('mode') == 'llm' else '사실 나열(LLM 미가용)'}"
                  " — 투자 조언 아님")
    else:
        st.caption("버튼을 누르면 위 스크리닝 결과를 LLM이 해설합니다 (자동 실행 안 함).")


def _institution_hub_section() -> None:
    hub_all = cached.institution_watch()
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
        st.warning(f"기관 허브 로드 실패: {hub_all['error']}")
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
    _screening_section()
    st.divider()
    all_selected = len(selected_keys) == len(all_keys)
    hub = hub_all if all_selected else cached.institution_watch(tuple(selected_keys))
    _render_institution_cards(list(hub.get("institutions") or []))
    st.markdown("### 비교 테이블")
    _render_comparison_table(hub.get("comparison") or {})

    st.markdown("### 공통 패턴 요약")
    if st.button("🧠 LLM 요약 생성 (최대 20초 소요)", key="_watch_llm_btn"):
        st.session_state["_watch_llm_requested"] = True
    if st.session_state.get("_watch_llm_requested"):
        with st.spinner("LLM 요약 생성 중…"):
            keys_arg = None if all_selected else tuple(selected_keys)
            hub_llm = cached.institution_watch(keys_arg, with_llm_summary=True)
        _render_common_moves(hub_llm.get("analysis") or {})
    else:
        st.caption("버튼을 누르면 기관별 공통 패턴·차이를 LLM이 요약합니다 (자동 실행 안 함).")


def render():
    st.title("⭐ 관심종목")
    st.caption("직접 추가한 종목을 보여주는 페이지 · 표시 전용 · "
              "추가/삭제는 텔레그램 봇 `/watch add|remove`")

    rows = data.load_watchlist()
    _watchlist_section(rows)

    st.divider()
    show_hub = st.toggle("🏦 유명 투자자 비교 보기 (13F 스냅샷 — 펼치면 로드)",
                         value=st.session_state.get("_watch_show_hub", False),
                         key="_watch_show_hub")
    if show_hub:
        _institution_hub_section()
        st.divider()
        _congress_trading_section()
