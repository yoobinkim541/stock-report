"""시장·캘린더 — 경제 일정(달력) + 수집 뉴스(출처별·중요도순)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ticker_names
from dashboard import cached, theme, views

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
    title = item["title"].replace("[", "［").replace("]", "］")   # 마크다운 링크 충돌 방지
    head = f"{_score_emoji(item['score'])} **{item['score']}**"
    body = f"[{title}]({item['url']})" if item.get("url") else title
    tks = " ".join(f"`${t}`" for t in item.get("tickers") or [])
    return f"{head} · {body}{_llm_tag(item.get('llm'))} {tks} — {item.get('time_str', '')}"


def _calendar_section():
    st.subheader("📅 경제 일정")
    ec = cached.econ(21)
    if not ec:
        st.info("경제 일정 없음 (saveticker)")
        return
    theme.render(theme.econ_calendar_html(ec, weeks=3))
    st.caption("🔴 고중요 · 🟠/🟡 중간 · 🟢 낮음 · ⚪ 정보 — 셀에 마우스를 올리면 시각·전체 제목")
    with st.expander("일정 목록 (중요도순)"):
        rows = sorted(ec, key=lambda e: (_IMP_ORDER.get(e.get("importance"), 9),
                                         str(e.get("when") or "9999")))
        st.dataframe(
            pd.DataFrame([{"중요도": e["marker"], "일시": e["date_str"], "이벤트": e["title"]}
                          for e in rows[:60]]),
            hide_index=True, width="stretch")


def _health_banner():
    """수집 공백 출처 경고 (source_health.json — 없으면 침묵)."""
    sh = cached.source_health()
    stale = (sh or {}).get("stale") or []
    if not stale:
        return
    bits = []
    for s in stale[:6]:
        gap = "이력 없음" if s.get("hours") is None else f"{s['hours']:.0f}h 공백"
        bits.append(f"`{s['source']}` ({gap})")
    st.warning("⛔ 수집이 멈춘 출처: " + " · ".join(bits)
               + " — 서버 수집 크론 로그(/tmp/…collector) 확인", icon="⚠️")


def _wsb_card():
    """🗣️ 레딧/WSB 심리 — insidertracking 분석 포스트 구조화 (표시·컨텍스트 전용)."""
    ss = cached.social_sentiment()
    s = (ss or {}).get("summary")
    if not s:
        return
    with st.expander(f"🗣️ 레딧/WSB 심리 — {str(s.get('published_at'))[:10]} "
                     f"(주인공: {' · '.join(s.get('top_tickers', [])[:4]) or '—'})", expanded=True):
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
        st.caption(f"출처: 텔레그램 insidertracking{src} · 소셜 심리는 컨텍스트 — 매매신호 아님 "
                   "(판단 반영은 LLM 라벨→news 축 게이트 경유만)")


def _news_section():
    st.subheader("🗞️ 수집 뉴스 — 출처별 · 중요도순")
    _health_banner()
    _wsb_card()
    hours = st.radio("수집 범위", [24, 48, 72], index=1, horizontal=True,
                     format_func=lambda h: f"최근 {h}시간", key="news_hours",
                     label_visibility="collapsed")
    nd = cached.collected_news(hours)
    groups = (nd or {}).get("groups") or {}
    if not groups:
        st.info("수집 뉴스 없음 — source_collector 크론(30분)·source-cache 확인"
                + (f" ({nd['error']})" if nd.get("error") else ""))
        return

    keys = [k for k in views.NEWS_SOURCE_ORDER if k in groups] \
        + sorted(k for k in groups if k not in views.NEWS_SOURCE_ORDER)
    tabs = st.tabs([f"{views.NEWS_SOURCE_LABEL.get(k, k)} ({len(groups[k])})" for k in keys])
    for tab, k in zip(tabs, keys):
        with tab:
            items = groups[k]
            hi = sum(1 for it in items if it["score"] >= 7)
            if hi:
                st.caption(f"중요(7+) {hi}건 — 규칙 점수(포트폴리오 종목 8·핵심 키워드 7) "
                           "+ LLM 라벨(📈호재/📉악재·강도) 병기")
            st.markdown("\n\n".join(_news_line(it) for it in items[:50]))
            if len(items) > 50:
                st.caption(f"…외 {len(items) - 50}건 (상위 50 표시)")


def render():
    st.title("🗓️ 시장 · 캘린더")
    _calendar_section()
    st.divider()
    _news_section()
    st.divider()
    ticker = st.session_state.get("ticker", "MSFT")
    st.subheader(f"뉴스 · {ticker_names.label(ticker)}")
    st.markdown(cached.news(ticker) or "_뉴스 없음_")
