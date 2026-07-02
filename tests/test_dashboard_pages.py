"""dashboard 페이지 렌더 스모크 — streamlit AppTest.

비루트 cwd 에서 실행해도 통과해야 함(streamlit `sys.path[0]=스크립트dir` 함정 재발 방지·U1 교훈).
스크립트가 루트를 직접 insert + 모든 네트워크/무거운 호출을 monkeypatch → 무예외만 검증.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

_STUBS = '''
import os, sys
sys.path.insert(0, %r)
import pandas as pd
import streamlit as st
from dashboard import cached, data
_IDX = pd.date_range("2025-01-01", periods=70, freq="D")
data.load_holdings = lambda *a, **k: [
    {"ticker":"MSFT","name":"Microsoft","shares":10,"value":4000.0,"ret":12.0,"weight":40.0},
    {"ticker":"NVDA","name":"Nvidia","shares":5,"value":6000.0,"ret":30.0,"weight":60.0}]
data.portfolio_summary = lambda *a, **k: {"total_usd":10000.0,"return_pct":15.0,"n_holdings":2}
data.portfolio_weights = lambda *a, **k: {"MSFT":0.4,"NVDA":0.6}
cached.econ = lambda *a, **k: [{"marker":"\U0001f534","date_str":"06/29 21:30","title":"CPI"}]
cached.news = lambda t: "뉴스 본문"
cached.valuation = lambda t: {"metrics":{"per":30.0,"roe":0.4},"consensus":{"n_analysts":5},
    "history":[{"date":"2026-04-30","eps_est":2.1,"eps_actual":2.3,"surprise_pct":9.5},
               {"date":"2026-01-30","eps_est":2.0,"eps_actual":1.9,"surprise_pct":-5.0}]}
cached.financials = lambda t: {"trends":{"rev_yoy":0.1,"net_margin":0.3,"n_years":5}}
cached.institutional = lambda t: {"accum":{"accum_score":7.2,
    "signals":{"obv_norm":0.3,"cmf":0.1,"updown_ratio":1.4},"institutional":None},"inst13f":None}
cached.insider = lambda t: {"transactions":[],"error":""}
cached.disclosures = lambda t: {"list":[],"error":"","market":"US"}
cached.earnings = lambda t: {"history":[{"date":"2026-04-30","eps_est":2.1,"eps_actual":2.3,"surprise_pct":9.5}]}
cached.intrinsic = lambda t: {"rim":{"low":250,"mid":320,"high":400},"ddm":None,"upside_pct":12.0,"ddm_reliable":False}
cached.risk = lambda: "리스크 텍스트"
cached.risk_struct = lambda: {"port_vol":0.2,"n_eff":3.5,"n_assets":5,"mdd_est":0.3,
    "contributions":[("MSFT",0.4,0.45),("NVDA",0.6,0.55)],
    "factor_net":{"QQQ":0.95,"TLT":-0.1},"factor_caveat":"베타 참고",
    "leverage":{"recommend":1.3,"dd_cap":1.3,"current":1.0,
                "kelly_half":{"conservative":0.5,"moderate":0.9,"trailing":1.1}}}
cached.ohlc = lambda t, period="6mo": pd.DataFrame(
    {"Open":range(100,170),"High":range(101,171),"Low":range(99,169),"Close":range(100,170)}, index=_IDX)
cached.screener = lambda n: {"rows":[],"error":"skip"}
cached.backtest = lambda: {"error":"skip"}
cached.sp500_heatmap = lambda: [
    {"ticker":"AAPL","name":"Apple","sector_kr":"기술","market_cap":4e12,"pct":1.96},
    {"ticker":"MSFT","name":"Microsoft","sector_kr":"기술","market_cap":2.8e12,"pct":3.17},
    {"ticker":"JPM","name":"JPMorgan","sector_kr":"금융","market_cap":9e11,"pct":-2.18}]
cached.market_indicators = lambda: {"fear_greed":{"score":32.0,"rating":"fear","prev_week":26.0,"prev_month":56.0},
    "indices":[{"ticker":"^GSPC","name":"S&P 500","price":6000.0,"chg":1.2,"rsi_d":63.0,"rsi_w":81.0},
               {"ticker":"^IXIC","name":"나스닥","price":20000.0,"chg":0.8,"rsi_d":58.0,"rsi_w":75.0}]}
cached.learning_evolution = lambda s: {"surface":s,
    "snapshot":{"n":52,"realized_ic":0.06,"buy_hit":55.0,"cum_net_excess":0.03},
    "verdict":{"code":"edge","emoji":"\U0001f9ec","label":"약한 엣지 형성","note":"순비용 IC +0.060"},
    "series":[{"date":"2026-06-01","excess":0.01,"ic":0.02,"adopted":False},
              {"date":"2026-06-08","excess":0.03,"ic":0.06,"adopted":True}],
    "adoptions":[{"date":"2026-06-08","excess_challenger":0.03}],"n_runs":2}
st.session_state["ticker"] = "MSFT"
''' % ROOT


def _script(mod, call):
    return _STUBS + f"\n{mod}\n{call}\n"


@pytest.mark.parametrize("mod,call", [
    ("from dashboard.pages import home", "home.render()"),
    ("from dashboard.pages import portfolio", "portfolio.render()"),
    ("from dashboard.pages import ticker", "ticker.render()"),
    ("from dashboard.pages import market", "market.render()"),
    ("from dashboard.pages import research", "research.render()"),
])
def test_page_renders_without_exception(mod, call):
    at = AppTest.from_string(_script(mod, call), default_timeout=30)
    at.run()
    assert not at.exception, f"{mod}: {at.exception}"


def test_entry_app_runs_through_nav():
    """app.py 엔트리: 인증 통과 후 sys.path·사이드바·st.navigation·기본 홈 렌더 무예외.

    비루트 cwd 에서 통과해야 함(streamlit `sys.path[0]=스크립트dir` 함정 가드·U1 교훈).
    views 가 전부 graceful try/except 라 오프라인에서도 예외 없이 빈 데이터로 렌더.
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.run()
    assert not at.exception, str(at.exception)


def test_sidebar_unified_search_selectbox():
    """사이드바 단일 검색 셀렉트박스: 유니버스에 MU 포함 + 선택 시 세션 반영 (H1).

    타입어헤드 필터는 클라이언트측(마이크론·micron·MU 모두 라벨 매칭)이라 AppTest 로는
    옵션 존재 + 선택 반영만 검증. 라벨/resolve 는 test_ticker_names 가 커버.
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.run()
    assert not at.exception, str(at.exception)
    sb = [s for s in at.selectbox if "검색" in (s.label or "")]
    assert sb, "검색 셀렉트박스 미발견"
    # options 는 format_func 적용 라벨 — 마이크론(MU) 라벨이 존재해야 타입어헤드로 도달 가능
    assert any("(MU)" in o and "마이크론" in o for o in sb[0].options), "MU 라벨 없음"
    # 선택 반영: 위젯 key(_tsel)에 raw 티커 세팅 = 셀렉트박스 선택 시뮬
    at.session_state["_tsel"] = "MU"
    at.run()
    assert not at.exception, str(at.exception)
    assert at.session_state["ticker"] == "MU"


def test_ticker_survives_page_context_no_reset():
    """비보유 종목을 외부(행클릭 시뮬)로 설정해도 사이드바가 holdings[0]로 되돌리지 않음 (H1 리셋버그 회귀차단).

    기존 버그: 검색/행클릭한 비보유 종목이 rerun 시 셀렉트박스에 의해 보유[0]으로 리셋.
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.run()
    # 홈 행클릭이 하는 것과 동일: 논리 ticker 를 외부에서 세팅 후 rerun
    at.session_state["ticker"] = "MU"      # 비보유(마이크론)
    at.run()
    assert not at.exception, str(at.exception)
    assert at.session_state["ticker"] == "MU", f"리셋됨 → {at.session_state['ticker']}"


def test_sidebar_select_navigates_and_ticker_sticks():
    """사이드바 셀렉트박스로 종목 선택 → ticker 반영 + 종목분석 이동(J1).

    switch_page 후 위젯상태 유실 시 셀렉트박스가 첫 옵션으로 리셋되던 취약점 회귀차단
    (_tsel not in _opts 재동기화). 선택한 종목이 유지되어야 함.
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.run()
    at.session_state["_tsel"] = "MU"       # 셀렉트박스 선택 시뮬(위젯 key)
    at.run()
    assert not at.exception, str(at.exception)
    assert at.session_state["ticker"] == "MU", f"선택 유실 → {at.session_state['ticker']}"


def test_sidebar_freeform_ticker_guard_no_reset():
    """자유입력 신규 티커가 reconciliation 에 리셋되지 않고 ticker 로 반영 (K2 _pending 가드).

    첫 run 에 (ticker=구 MSFT · _tsel=신규 DDOG · sync=MSFT) 를 심어 '방금 입력' 상태 재현.
    _pending 가드가 정규화 가능한 DDOG 를 _opts 에 편입 → reconciliation 이 첫 옵션으로
    되돌리지 않음 → normalize_input 정규화·이동. 가드 없으면 DDOG 가 _opts 밖이라 MSFT 로 리셋.
    (실브라우저 accept_new_options 타이핑 자체는 세션주입으로 시뮬 불가 — 정규화는 unit 커버.)
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.session_state["ticker"] = "MSFT"        # 현재(구) 종목
    at.session_state["_tsel"] = "DDOG"         # 방금 입력한 신규 티커(위젯 key)
    at.session_state["_tsel_sync"] = "MSFT"    # 아직 구 종목과 동기
    at.run()
    assert not at.exception, str(at.exception)
    assert at.session_state["ticker"] == "DDOG", f"자유입력 유실(가드 실패) → {at.session_state.get('ticker')}"


def test_portfolio_renders_risk_kpis():
    """포트폴리오: 리스크 KPI 4 + 보유표 (위험기여·팩터 막대는 plotly로 무예외)."""
    at = AppTest.from_string(_script("from dashboard.pages import portfolio", "portfolio.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 4
    assert len(at.dataframe) >= 1


def test_research_screener_gated_no_autocompute():
    """리서치 진입(기본 '종목 랭킹') 시 스크리너 자동실행 안 함 — ▶버튼 + 안내만 (H2 지연제거)."""
    at = AppTest.from_string(_script("from dashboard.pages import research", "research.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert any("실행" in str(b.label) for b in at.button), "실행 버튼 없음"
    assert any("자동 실행하지 않" in str(i.value) for i in at.info), "게이트 안내 없음"


def test_research_shows_learning_curve():
    """리서치 '정책 학습' 섹션 선택 시 곡선·verdict·이력표 (H2 섹션 셀렉터)."""
    at = AppTest.from_string(_script("from dashboard.pages import research", "research.render()"),
                             default_timeout=30)
    at.session_state["research_section"] = "정책 학습"   # 섹션 셀렉터 프리셋
    at.run()
    assert not at.exception
    assert any("정책 학습 곡선" in str(s.value) for s in at.subheader)
    assert any("엣지" in str(m.value) for m in at.markdown)   # verdict 라벨
    assert len(at.dataframe) >= 1                             # 채택 이력표


def test_ticker_position_management_renders():
    """종목분석 하단 포지션 관리 — 입력·버튼 렌더(J3). 실제 write 없음(클릭 안 함)."""
    script = _STUBS + (
        'cached.realtime_quote = lambda t: {"price": 200.0, "bids": [], "asks": [], "market": "US"}\n'
        'data.holding_position = lambda t, *a, **k: {"shares": 5.0, "avg_price_usd": 180.0,'
        ' "value": 1000.0, "ret": 11.1, "cost": 900.0}\n'
        'st.session_state["ticker"] = "NVDA"\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert len(at.number_input) >= 1                 # 주수/단가 입력
    assert any("기록" in str(b.label) for b in at.button)   # 추가/적립/축소 기록 버튼
    # 안전 라벨(실주문 아님) 노출
    assert any("실주문 아님" in str(c.value) or "기록 전용" in str(c.value) for c in at.caption)


def test_home_has_donut_and_holdings():
    """홈: 도넛(plotly) + 보유표 + KPI 가 렌더되는지(요소 존재)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 3               # Phase·낙폭·DCA (총액은 히어로 HTML)
    assert len(at.dataframe) >= 1            # 보유표
    assert any("국면" in str(i.value) for i in at.info)  # Phase 행동 박스


def test_home_shows_market_map():
    """홈 S&P500 시장 맵 섹션 렌더 (M3·트리맵·무예외·클릭 안 함)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("S&P 500 시장 맵" in str(m.value) for m in at.markdown)   # 시장맵 섹션
    assert any("시장 지표" in str(m.value) for m in at.markdown)          # F&G·RSI 패널 (O2)
