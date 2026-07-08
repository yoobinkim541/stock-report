"""dashboard/cached.py — st.cache_data 래퍼 (멀티페이지 공용).

views 의 네트워크 호출을 캐시. 모든 페이지가 동일 캐시를 공유한다.
(app.py 가 sys.path 에 프로젝트 루트를 넣은 뒤 import 되므로 dashboard import OK.)
"""
from __future__ import annotations

import streamlit as st

from dashboard import data, views

_TTL = 900       # 15분
_TTL_SLOW = 1800  # 30분
_TTL_HEAVY = 3600  # 1시간 (ML)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def valuation(t):
    return views.valuation(t)


@st.cache_data(ttl=_TTL_HEAVY, show_spinner="ETF 정보 불러오는 중…")
def etf(t):
    return views.etf_overview(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def financials(t):
    return views.financials(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def institutional(t):
    return views.institutional(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def news(t):
    return views.news_digest(t)


@st.cache_data(ttl=600, show_spinner="수집 뉴스 불러오는 중…")
def collected_news(hours=48):
    return views.collected_news(hours)


@st.cache_data(ttl=600, show_spinner=False)
def source_health():
    return views.source_health_summary()


@st.cache_data(ttl=900, show_spinner=False)
def social_sentiment():
    return views.social_sentiment()


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def earnings(t):
    return views.earnings_calendar(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def intrinsic(t):
    return views.intrinsic_value(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def insider(t):
    return views.insider_trades(t)


@st.cache_data(ttl=_TTL_SLOW, show_spinner="불러오는 중…")
def disclosures(t):
    return views.disclosures(t)


@st.cache_data(ttl=_TTL, show_spinner="불러오는 중…")
def risk():
    return views.risk_report_text(data.portfolio_weights())


@st.cache_data(ttl=_TTL, show_spinner="리스크 계산 중…")
def risk_struct():
    """구조화 리스크 요약 (위험기여·팩터β·레버리지 — 차트용·U3)."""
    return views.risk_summary(data.portfolio_weights())


@st.cache_data(ttl=_TTL_SLOW, show_spinner="불러오는 중…")
def econ(days=14):
    return views.econ_events(days)


@st.cache_data(ttl=_TTL_HEAVY, show_spinner="랭킹 계산 중… (최대 1분)")
def screener(n):
    return views.screener(n)


@st.cache_data(ttl=_TTL_HEAVY, show_spinner="백테스트 실행 중… (최대 1분)")
def backtest():
    return views.backtest_summary()


@st.cache_data(ttl=_TTL_SLOW, show_spinner=False)
def axes_gate():
    """KR·US 가격축 ★게이트 검증 + shadow 반영 상태 (로컬 파일 — 30분 캐시)."""
    return views.axes_gate_summary()


@st.cache_data(ttl=_TTL_SLOW, show_spinner=False)
def tier3_gate():
    """Tier3 구조레버 게이트 shadow 상태 (로컬 파일 — 30분 캐시)."""
    return views.tier3_gate_status()


@st.cache_data(ttl=_TTL, show_spinner=False)
def paper_glance():
    """사이드바 모의 레일 초경량 요약 (store 스냅샷만 — 무네트워크·15분)."""
    return views.paper_glance()


@st.cache_data(ttl=_TTL, show_spinner="모의 계좌 불러오는 중…")
def paper(surface):
    """자동 모의투자 계좌 요약 (NAV·벤치·보유·결정 원장 — 잔고 API graceful 폴백)."""
    return views.paper_summary(surface)


@st.cache_data(ttl=_TTL_SLOW, show_spinner="학습 이력 불러오는 중…")
def learning_evolution(surface):
    """모의 자기개선 진화 (주간 학습 이력 + 라이브 verdict)."""
    return views.learning_evolution(surface)


@st.cache_data(ttl=_TTL, show_spinner="가격 불러오는 중…")
def ohlc(t, period="6mo"):
    """OHLC 가격 히스토리 (가격차트용·U3). _history_cached 재사용."""
    try:
        from providers.market_data import _history_cached
        return _history_cached(t, period=period)
    except Exception:
        return None


@st.cache_data(ttl=8, show_spinner=False)
def realtime_quote(ticker):
    """실시간 시세+호가 (KIS·8s 캐시). off/미보유 시 None → yfinance 폴백."""
    return views.realtime_quote(ticker)


@st.cache_data(ttl=300, show_spinner="시장 맵 불러오는 중…")
def sp500_heatmap():
    """S&P500 시장 맵 rows (섹터·시총 정적 + 당일 등락 라이브·30분 캐시·크론 스냅샷 우선)."""
    return views.sp500_heatmap()


@st.cache_data(ttl=_TTL, show_spinner="시장 지표 불러오는 중…")
def market_indicators():
    """공포·탐욕지수 + S&P500·나스닥 일/주봉 RSI (15분 캐시)."""
    return views.market_indicators()


@st.cache_data(ttl=60, show_spinner=False)
def intraday_overview(market):
    return views.intraday_overview(market)


@st.cache_data(ttl=60, show_spinner=False)
def intraday_day(market, date):
    return views.intraday_day(market, date)


@st.cache_data(ttl=60, show_spinner="분봉 불러오는 중…")
def intraday_chart(symbol, market, date, interval):
    return views.intraday_chart(symbol, market, date, interval)


@st.cache_data(ttl=_TTL, show_spinner="봉 데이터 불러오는 중…")
def ohlc_tf(t, tf):
    return views.ohlc_tf(t, tf)


@st.cache_data(ttl=300, show_spinner="코스피200 맵 불러오는 중…")
def kr200_heatmap():
    return views.kr200_heatmap()


@st.cache_data(ttl=300, show_spinner="러셀2000 맵 불러오는 중…")
def russell2000_heatmap():
    return views.russell2000_heatmap()


@st.cache_data(ttl=_TTL, show_spinner="추세선 감지 중…")
def trendlines_for(t, tf, lines, ch_key):
    return views.trendlines_for(t, tf, lines=lines, channels=tuple(ch_key))
