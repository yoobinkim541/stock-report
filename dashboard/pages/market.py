"""시장·캘린더 — 경제 일정(달력) + 수집 뉴스(출처별·중요도순)."""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, theme, ui_actions, views

_IMP_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _score_emoji(score: int) -> str:
    return "🔴" if score >= 8 else "🟠" if score == 7 else "🟡" if score >= 5 else "⚪"


def _llm_tag(llm: dict | None) -> str:
    if not llm:
        return ""
    arrow = "📈" if llm.get("direction", 0) > 0 else "📉" if llm.get("direction", 0) < 0 else "➖"
    et = llm.get("event_type") or ""
    return f" {arrow}`{et}·강도{llm.get('strength')}`"


def _news_line(item: dict) -> str:
    title = str(item.get("title") or "")
    title = title.replace("[", "［").replace("]", "］").replace("*", "∗").replace("_", "＿")
    # `$` 두 개가 한 줄에 있으면 st.markdown 이 그 사이를 LaTeX 수식으로 렌더한다 ($500 billion … `$NVDA`).
    title = title.replace("$", r"\$")
    head = f"{_score_emoji(int(item.get('score') or 0))} **{item.get('score', 0)}**"
    url = str(item.get("url") or "").replace(")", "%29").replace("(", "%28")
    body = f"[{title}]({url})" if url else title
    tks = " ".join(f"`${t}`" for t in item.get("tickers") or [])
    return f"{head} · {body}{_llm_tag(item.get('llm'))} {tks} — {item.get('time_str', '')}"


def _calendar_section():
    st.subheader("📅 경제 일정")
    from providers.econ_calendar import KST
    today = datetime.now(KST).date()
    monday = today - timedelta(days=today.weekday())
    ec = cached.econ(21, monday.isoformat())
    if not ec:
        st.info("경제 일정이 비어 있습니다. 일정 수집이 아직 없거나 멈춰 있습니다.")
        return
    theme.render(theme.econ_calendar_html(ec, start=today, weeks=3))
    st.caption("🔴 고중요 · 🟠/🟡 중간 · 🟢 낮음 · ⚪ 정보 — 셀에 마우스를 올리면 시각·전체 제목 · 한국 시간")
    with st.expander("일정 목록 (시간순)"):
        rows = sorted(ec, key=lambda e: (str(e.get("when") or "9999"),
                                         _IMP_ORDER.get(e.get("importance"), 9)))
        st.dataframe(
            pd.DataFrame([{"중요도": e["marker"], "일시": e["date_str"], "이벤트": e["title"]}
                          for e in rows[:60]]),
            hide_index=True, width="stretch")
        if len(rows) > 60:
            st.caption(f"…외 {len(rows) - 60}건")


_SOURCE_TAIL = {"fed": "연준", "sec": "미국 공시", "dart": "전자공시", "edgar": "미국 공시"}


def _source_label(source: str) -> str:
    raw = str(source or "기타")
    key, _, rest = raw.partition(":")
    label = views.NEWS_SOURCE_LABEL.get(key)
    if label is None:
        label = "기타" if key.isascii() else key
    tail = _SOURCE_TAIL.get(rest.strip().lower())
    if rest and key in views.NEWS_SOURCE_LABEL:
        return f"{label} ({tail or rest})"
    return label


def _health_banner():
    """수집 공백 출처 경고 (source_health.json — 없으면 침묵)."""
    sh = cached.source_health()
    stale = (sh or {}).get("stale") or []
    if not stale:
        return
    bits = []
    for s in stale[:6]:
        gap = "이력 없음" if s.get("hours") is None else f"{s['hours']:.0f}시간 공백"
        bits.append(f"{_source_label(s.get('source'))} ({gap})")
    st.warning("⛔ 수집이 멈춘 출처: " + " · ".join(bits), icon="⚠️")


def _wsb_card(hours: int):
    """🗣️ 레딧/WSB 심리 — insidertracking 분석 포스트 구조화 (표시·컨텍스트 전용)."""
    ss = cached.social_sentiment(hours)
    s = (ss or {}).get("summary")
    if not s:
        return
    with st.expander(f"🗣️ 레딧/WSB 심리 — {str(s.get('published_at'))[:10]} "
                     f"(주인공: {' · '.join(s.get('top_tickers', [])[:4]) or '—'})", expanded=False):
        if s.get("mood_bullets"):
            st.markdown("**🔥 전체 시장 심리**")
            for b in s["mood_bullets"][:4]:
                st.markdown(f"- {b}")
        cols = st.columns(2)
        secs = [x for x in s.get("sections", []) if "심리" not in x["heading"]][:8]
        for i, sec in enumerate(secs):
            with cols[i % 2]:
                tks = " ".join(f"`{t}`" for t in sec.get("tickers", [])[:4])
                st.markdown(f"**{sec['emoji']} {sec['heading']}** {tks}")
                for b in sec.get("bullets", [])[:3]:
                    st.markdown(f"<span style='color:{theme.MUTED};font-size:.85rem'>· {b}</span>",
                                unsafe_allow_html=True)
        src = f" · [원문]({s['url']})" if s.get("url") else ""
        st.caption("소셜 심리는 맥락일 뿐입니다. 매매 신호가 아니며, 판단에는 뉴스 라벨을 거친 뒤에만 반영됩니다.")


def _news_section():
    st.subheader("🗞️ 수집 뉴스 — 출처별 · 중요도순")
    _health_banner()
    ticker = st.session_state.get("ticker", "MSFT")
    c_hours, c_only = st.columns([3, 2], vertical_alignment="center")
    hours = c_hours.radio("수집 범위", [24, 48, 72], index=1, horizontal=True,
                          format_func=lambda h: f"최근 {h}시간", key="news_hours",
                          label_visibility="collapsed")
    ticker_only = c_only.toggle(f"{ticker_names.label(ticker)} 뉴스만", key="news_ticker_only",
                                help="사이드바에서 고른 종목이 언급된 기사만 모아 봅니다.")
    if not ticker_only:
        _wsb_card(hours)
    nd = cached.collected_news(hours)
    groups = (nd or {}).get("groups") or {}
    if not groups:
        st.info("수집 뉴스가 없습니다. 뉴스 수집이 비어 있거나 멈춰 있습니다.")
        return
    if ticker_only:
        _ticker_news(groups, ticker)
        return

    keys = [k for k in views.NEWS_SOURCE_ORDER if k in groups] \
        + sorted(k for k in groups if k not in views.NEWS_SOURCE_ORDER)
    tabs = st.tabs([f"{_source_label(k)} ({len(groups[k])})" for k in keys])
    for tab, k in zip(tabs, keys):
        with tab:
            items = groups[k]
            hi = sum(1 for it in items if it["score"] >= 7)
            if hi:
                st.caption(f"중요(7+) {hi}건 — 규칙 점수(포트폴리오 종목 8·핵심 키워드 7) "
                           "+ LLM 라벨(📈호재/📉악재·강도) 병기")
            st.markdown("\n\n".join(_news_line(it) for it in items[:50]))
            jumps = ui_actions.mentioned_tickers(items[:50])
            if jumps:
                st.caption("언급된 종목 — 누르면 차트")
                ui_actions.render_ticker_jumps(jumps, key_prefix=f"mkt_news_{k}")
            if len(items) > 50:
                st.caption(f"…외 {len(items) - 50}건 (상위 50 표시)")


def ticker_news_items(groups: dict, ticker: str) -> list[dict]:
    """모든 출처에서 ticker 가 언급된 기사 — 점수·최신순. 주식 클래스(BRK.A/BRK.B)는 구분한다."""
    from providers.news_labels import _ticker_base
    base = _ticker_base(ticker)
    items = [
        it for rows in groups.values() for it in rows
        if base and base in {_ticker_base(t) for t in (it.get("tickers") or [])}
    ]
    items.sort(key=lambda x: (int(x.get("score") or 0), str(x.get("published_at") or "")), reverse=True)
    return items


def _ticker_news(groups: dict, ticker: str) -> None:
    items = ticker_news_items(groups, ticker)
    if not items:
        st.caption("이 종목 관련 수집 뉴스 없음")
        return
    st.markdown("\n\n".join(_news_line(it) for it in items[:20]))
    others = [s for s in ui_actions.mentioned_tickers(items[:20]) if s != ticker]
    if others:
        st.caption("같은 기사에 함께 나온 종목")
        ui_actions.render_ticker_jumps(others, key_prefix="mkt_news_ticker")


def render():
    st.title("시장 · 캘린더")
    _calendar_section()
    st.divider()
    _news_section()
