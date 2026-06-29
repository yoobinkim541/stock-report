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
import streamlit as st
from dashboard import cached, data
data.load_holdings = lambda *a, **k: [
    {"ticker":"MSFT","name":"Microsoft","shares":10,"value":4000.0,"ret":12.0,"weight":40.0},
    {"ticker":"NVDA","name":"Nvidia","shares":5,"value":6000.0,"ret":30.0,"weight":60.0}]
data.portfolio_summary = lambda *a, **k: {"total_usd":10000.0,"return_pct":15.0,"n_holdings":2}
data.portfolio_weights = lambda *a, **k: {"MSFT":0.4,"NVDA":0.6}
cached.econ = lambda *a, **k: [{"marker":"\U0001f534","date_str":"06/29 21:30","title":"CPI"}]
cached.news = lambda t: "뉴스 본문"
cached.valuation = lambda t: {"metrics":{"per":30.0,"roe":0.4},"consensus":{"n_analysts":5},"history":[]}
cached.financials = lambda t: {"trends":{"rev_yoy":0.1,"net_margin":0.3,"n_years":5}}
cached.institutional = lambda t: {"accum":None,"inst13f":None}
cached.insider = lambda t: {"transactions":[],"error":""}
cached.disclosures = lambda t: {"list":[],"error":"","market":"US"}
cached.earnings = lambda t: {"history":[]}
cached.intrinsic = lambda t: {"rim":None,"ddm":None}
cached.risk = lambda: "리스크 텍스트"
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


def test_home_has_donut_and_holdings():
    """홈: 도넛(plotly) + 보유표 + KPI 가 렌더되는지(요소 존재)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 4               # 히어로 KPI 4
    assert len(at.dataframe) >= 1            # 보유표
    assert any("국면" in str(i.value) for i in at.info)  # Phase 행동 박스
