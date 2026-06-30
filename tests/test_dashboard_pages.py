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


def test_portfolio_renders_risk_kpis():
    """포트폴리오: 리스크 KPI 4 + 보유표 (위험기여·팩터 막대는 plotly로 무예외)."""
    at = AppTest.from_string(_script("from dashboard.pages import portfolio", "portfolio.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 4
    assert len(at.dataframe) >= 1


def test_home_has_donut_and_holdings():
    """홈: 도넛(plotly) + 보유표 + KPI 가 렌더되는지(요소 존재)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 3               # Phase·낙폭·DCA (총액은 히어로 HTML)
    assert len(at.dataframe) >= 1            # 보유표
    assert any("국면" in str(i.value) for i in at.info)  # Phase 행동 박스
