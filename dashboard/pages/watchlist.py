"""dashboard/pages/watchlist.py — 관심종목 (읽기 전용). 삭제는 봇 /watch remove 에서만."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import cached, data


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


def render():
    st.title("⭐ 관심종목")
    st.caption("유명 기관투자자 스냅샷과 직접 추가한 종목을 함께 보는 허브 "
              "· 표시 전용 · 삭제는 텔레그램 봇 /watch remove")

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
    st.markdown("### 기관투자자 허브")
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
    hub = hub_all if len(selected_keys) == len(all_keys) else cached.institution_watch(tuple(selected_keys))
    _render_institution_cards(list(hub.get("institutions") or []))
    st.markdown("### 비교 테이블")
    _render_comparison_table(hub.get("comparison") or {})
    _render_common_moves(hub.get("analysis") or {})

    rows = data.load_watchlist()
    if not rows:
        st.info("관심종목이 비어 있습니다 — 봇에서 `/watch add TICKER 메모` 로 추가하거나 "
                "기관 스냅샷 업데이트 또는 버핏 13F 신규편입 크론(매주 월요일)을 기다리세요.")
        return

    df = pd.DataFrame([{
        "티커": r["ticker"], "종목": r["name"],
        "추가 사유": r["reason"], "추가일": r["added_at"][:10] if r["added_at"] else "",
    } for r in rows])

    st.caption("🔍 **행을 클릭**하면 해당 종목 상세 분석으로 이동")
    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    )
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if sel and sel[0] < len(rows):
        picked = rows[sel[0]]["ticker"]
        if picked and picked != st.session_state.get("ticker"):
            st.session_state["ticker"] = picked
            st.toast(f"종목 분석 → {picked}")
            pg = st.session_state.get("_ticker_page")
            if pg:
                st.switch_page(pg)
            else:
                st.rerun()
