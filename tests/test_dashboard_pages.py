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
data.trade_events = lambda *a, **k: []
data.load_kr_holdings = lambda *a, **k: {}
cached.econ = lambda *a, **k: [{"marker":"\U0001f534","date_str":"06/29 21:30","title":"CPI"}]
cached.news = lambda t: "뉴스 본문"
cached.etf = lambda t: {"ticker": t, "is_etf": False}
cached.tr_pr = lambda t, years=5: None
cached.fx_now = lambda: 1400.0
cached.port_history = lambda: [
    {"date": "2026-07-06", "total_usd": 9300.0, "total_krw": 14000000, "exchange_rate": 1505.0,
     "qqq_price": 700.0},
    {"date": "2026-07-07", "total_usd": 9411.0, "total_krw": 14239554, "exchange_rate": 1513.0,
     "qqq_price": 704.9}]
cached.target_weights_map = lambda: {"MSFT": 0.5, "NVDA": 0.4, "SGOV": 0.1}
cached.income_summary = lambda *a, **k: {"records": [{"amount": 12.5}], "total": 12.5,
    "est_monthly": 20.0, "est_detail": {"note": "최근 3개월 평균 배당 기준"}}
cached.fx_timing = lambda: {"ok": True, "rate": 1509.8, "pct_display": 96.1,
    "emoji": "\U0001f534", "verdict": "원화 약세 구간", "multiplier": 0.3,
    "action": "환전 최소화 - 필요분만"}
cached.etf_peers = lambda t: {}
cached.screener_last = lambda: None
cached.trendlines_for = lambda *a, **k: []
cached.market_temp_history = lambda: [{"date": "2026-07-07", "score": 0.1},
                                      {"date": "2026-07-08", "score": 0.2}]
cached.next_earnings = lambda t: None
cached.portfolio_flows = lambda: {}
cached.social_sentiment = lambda: {"summary": {"title": "미국 레딧 게시물 분석",
    "published_at": "2026-07-05T10:00:00+09:00", "url": "https://t.me/insidertracking/1",
    "top_tickers": ["MU", "SNDK", "NVDA"],
    "mood_bullets": ["메모리가 압도적인 주인공", "YOLO 콜옵션 심리 강함"],
    "sections": [{"emoji": "\U0001f4be", "heading": "MU / SNDK - AI 메모리",
                  "tickers": ["MU", "SNDK"], "bullets": ["갭업 기대", "ATH 반복 언급"]},
                 {"emoji": "\U0001f525", "heading": "현재 WSB 전체 시장 심리",
                  "tickers": [], "bullets": ["Risk-On"]}]}}
cached.source_health = lambda: {"health": {"saveticker": {"last_count": 12}},
    "stale": [{"source": "telegram:insidertracking", "hours": None, "threshold": 12}]}
cached.collected_news = lambda hours=48: {"hours": hours, "groups": {
    "saveticker": [{"title": "엔비디아 [실적] 서프라이즈", "url": "https://example.com/1",
                    "score": 8, "reason": "포트폴리오 종목", "published_at": "2026-07-06T10:00:00+09:00",
                    "time_str": "07-06 10:00", "tickers": ["NVDA"],
                    "llm": {"direction": 1, "strength": 4, "event_type": "실적"}}],
    "telegram": [{"title": "일반 채널 뉴스", "url": None, "score": 5, "reason": "",
                  "published_at": "2026-07-06T09:00:00+09:00", "time_str": "07-06 09:00",
                  "tickers": [], "llm": None}]}}
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
cached.screener = lambda n: {"rows": [{"rank": 1, "ticker": "NVDA", "name": "NVIDIA",
    "score": 2.54, "price": 196.9, "tech_rating": "매수", "surv_flag": False,
    "reason": "52주 고점 근접 · 6M 모멘텀 +42%%", "rsi_14": 62.0,
    "close_vs_52w_high": 0.97, "mom_126d": 0.42, "excess_mom_60d": 0.08, "fund_score": 72.0}],
    "feats": {"NVDA": {"rsi_14": 62.0, "mom_126d": 0.42}},
    "meta": {"ic": 0.05, "icir": 0.8, "top_decile": 0.02, "train_end": "2026-06-01",
             "importance": {"mom_126d": 100, "rsi_14": 50}}}
cached.backtest = lambda: {"error":"skip"}
cached.sp500_heatmap = lambda: [
    {"ticker":"AAPL","name":"Apple","sector_kr":"기술","market_cap":4e12,"pct":1.96},
    {"ticker":"MSFT","name":"Microsoft","sector_kr":"기술","market_cap":2.8e12,"pct":3.17},
    {"ticker":"JPM","name":"JPMorgan","sector_kr":"금융","market_cap":9e11,"pct":-2.18}]
cached.sp500_valuation = lambda: {"per": 27.3, "fper": 21.9, "eps_growth_pct": 24.7,
    "peg": 1.11, "n": 100, "cov_trailing_pct": 68.0, "cov_forward_pct": 66.0,
    "per_reported": 32.28, "per_pctile_all": 97.8, "per_pctile_20y": 91.7,
    "hist_n": 1867, "asof": "2026-07-08"}
cached.market_indicators = lambda: {"fear_greed":{"score":32.0,"rating":"fear","prev_week":26.0,"prev_month":56.0},
    "indices":[{"ticker":"^GSPC","name":"S&P 500","price":6000.0,"chg":1.2,"rsi_d":63.0,"rsi_w":81.0},
               {"ticker":"^IXIC","name":"나스닥","price":20000.0,"chg":0.8,"rsi_d":58.0,"rsi_w":75.0}]}
cached.axes_gate = lambda: {
    "kr":{"available":True,"env_on":True,"asof":"2026-07-04 10:45","period":"2001~2026",
          "verdict":{"code":"OBSERVE","label":"\U0001f440 OBSERVE — OOS 순초과>0 이나 통계 관문 미달",
                     "net_excess_cagr":0.0549,"dsr":0.095,"pbo":0.175,
                     "oos":{"cagr":0.157,"mdd":0.483},"bench":{"cagr":0.102,"mdd":0.541}},
          "recommendation":{"chosen":"hi52","policy_weights":{"w_hi52":0.35,"w_lowvol":0.0,
                            "w_mom12":0.0,"w_mom":0.0},"window":["2021-07-02","2026-07-02"]},
          "chosen_history":{"hi52":6,"lowvol":3},
          "shadow":{"asof":"2026-07-04 10:45","chosen":"hi52","fresh":True,"applied":True},
          "regime_overlay":{"code":"OBSERVE","label":"\U0001f440 OBSERVE(방어)",
              "overlay":{"cagr":0.139,"mdd":0.422},"offense_alone":{"cagr":0.131,"mdd":0.60},
              "bench":{"cagr":0.104,"mdd":0.541},"mdd_vs_offense_pp":-17.8,
              "dsr":0.03,"ir":0.168,"bear_defend_years":"6/7","mdd_win_years":"10/25"},
          "cost_sensitivity":{"axis":["hi52"],"drag_saved_pp":2.0,
              "current":{"scheme":"월간·버퍼2","drag_pp":2.44,"net_cagr":0.125},
              "best":{"scheme":"반기·버퍼2","net_cagr":0.147},
              "oos":{"verdict":"ROBUST","year_win_rate":0.64,"n_years":22,"gross_preserved":True,
                     "gross_mo":0.150,"gross_semi":0.152,"cross_axis_confirmed":True,
                     "live_reco":{"min_hold_days":60,"expected_drag_save_pp":2.0,"caveat":"꼬리위험·모의 검증"}},
              "rows":[{"scheme":"월간·버퍼2","net_cagr":0.125,"drag_pp":2.44,"turnover":0.79,
                       "net_excess_pp":2.09,"mdd":0.60},
                      {"scheme":"반기·버퍼2","net_cagr":0.147,"drag_pp":0.47,"turnover":0.91,
                       "net_excess_pp":4.29,"mdd":0.63}]}},
    "us":{"available":False,"env_on":False}}
cached.tier3_gate = lambda: {"available":True,"reco_lev":1.3,"verdict":"GO",
                             "at":"2026-07-04","fresh":True,"sleeve_env":True}
cached.paper = lambda s: {"surface":s,"currency":"₩" if s=="kr_mock" else "$",
    "bench_name":"KOSPI" if s=="kr_mock" else "QQQ","balance_ok":True,
    "nav":10500000.0,"cash":1200000.0,
    "positions":[{"symbol":"005930","name":"삼성전자","shares":10,"avg":70000.0,"cur":75000.0,
                  "value":750000.0,"ret":7.1}],
    "nav_series":[{"date":"2026-06-01","nav":10000000.0},{"date":"2026-06-02","nav":10500000.0}],
    "inception_date":"2026-06-01","cum_ret":5.0,"day_ret":0.5,"strat_mdd":3.2,
    "bench_ret":2.0,"bench_mdd":5.0,
    "cost":{"total":15000.0,"turnover":120.0,"drag":0.15},
    "scorecard":{"buy_hit":55.0,"n_buy":20,"sell_hit":50.0,"n_sell":8},
    "sleeve":({"enabled":True,"symbol":"QLD","reco":1.3,"shares":300,"frac":30.0}
              if s=="us_mock" else None),
    "decisions":[{"date":"2026-06-02","side":"편입","ticker":"005930.KS","name":"삼성전자 (005930.KS)",
                  "qty":10,"price":70000.0,"policy_score":0.812,
                  "reason":"score 85·A등급·수급 양호","ok":True,
                  "features":{"mom12":0.71,"hi52":0.95,"lowvol":0.6,"pead":0.58},
                  "fwd_excess":0.021,"correct":True,"matured_at":"2026-06-20"},
                 {"date":"2026-06-02","side":"퇴출","ticker":"000660.KS","name":"SK하이닉스 (000660.KS)",
                  "qty":5,"price":180000.0,"policy_score":0.31,
                  "reason":"타깃이탈","ok":True,"features":{},
                  "fwd_excess":None,"correct":None,"matured_at":None}]}
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
    ("from dashboard.pages import paper", "paper.render()"),
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
    assert any("적립 금액 (₩)" in str(getattr(n, "label", "")) for n in at.number_input)
    assert any("적용 환율" in str(getattr(n, "label", "")) for n in at.number_input)
    seg = " ".join(str(s) for s in at.segmented_control)
    assert "매일" in seg and "매주" in seg and "매월" in seg
    assert any("적립 1회 기록" in str(b.label) for b in at.button)
    # 안전 라벨(실주문 아님) 노출
    assert any("실주문 아님" in str(c.value) or "기록 전용" in str(c.value) for c in at.caption)


def test_paper_kpis_and_decisions():
    """모의투자: 계좌 KPI(NAV·누적·vs지수·MDD) + 로직평가 + 판단근거 원장표 + 안전 라벨 (P1)."""
    at = AppTest.from_string(_script("from dashboard.pages import paper", "paper.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert len(at.metric) >= 8                    # 계좌 4 + 예수금 + 로직평가 4
    assert len(at.dataframe) >= 2                 # 보유표 + 결정 원장표
    assert any("판단 근거" in str(m.value) for m in at.markdown)
    assert any("실거래 아님" in str(c.value) for c in at.caption)   # 안전 라벨


def test_paper_empty_graceful():
    """모의투자: 데이터 전무(크론 미실행) 시 안내만 — 무예외 (P1 graceful)."""
    script = _STUBS + (
        'cached.paper = lambda s: {"surface":s,"currency":"₩","bench_name":"KOSPI","balance_ok":False,'
        '"nav":None,"cash":None,"positions":[],"nav_series":[],"inception_date":None,'
        '"cum_ret":None,"day_ret":None,"strat_mdd":None,"bench_ret":None,"bench_mdd":None,'
        '"cost":None,"scorecard":{},"decisions":[]}\n'
        "from dashboard.pages import paper\npaper.render()\n")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("계좌 데이터 없음" in str(i.value) for i in at.info)


def test_research_axes_gate_section():
    """리서치 '축 게이트' — KR verdict 카드·권고·shadow 반영 상태 + US 미생성 안내 (P2)."""
    at = AppTest.from_string(_script("from dashboard.pages import research", "research.render()"),
                             default_timeout=30)
    at.session_state["research_section"] = "축 게이트"
    at.run()
    assert not at.exception, str(at.exception)
    assert any("가격축 ★게이트" in str(s.value) for s in at.subheader)
    assert any("OBSERVE" in str(m.value) for m in at.markdown)          # KR verdict
    assert any("hi52" in str(m.value) for m in at.markdown)             # 권고 축
    assert len(at.metric) >= 4                                          # 순초과·MDD·DSR·PBO
    assert any("반영 중" in str(c.value) for c in at.caption)           # shadow applied
    assert any("검증 결과 없음" in str(i.value) for i in at.info)        # US 미생성 안내
    # 🛡️ 레짐 방어 오버레이 + 💸 비용 최적화 expander (P4)
    exp = " ".join(str(e.label) for e in at.expander)
    assert "레짐 방어 오버레이" in exp and "비용·회전율 최적화" in exp
    caps = " ".join(str(c.value) for c in at.caption)
    assert "OOS 검증" in caps and "최소 보유 60일" in caps          # 비용 OOS verdict + 라이브 권고


def test_paper_us_sleeve_badge_and_axes_columns():
    """모의투자 US — 🏗️ 슬리브 배지 + '축 피처 보기' 토글 시 원장에 축 열 (P2)."""
    at = AppTest.from_string(_script("from dashboard.pages import paper", "paper.render()"),
                             default_timeout=30)
    at.session_state["paper_market"] = "us_mock"
    at.session_state["paper_axes_us_mock"] = True          # 토글 on 시뮬
    at.run()
    assert not at.exception, str(at.exception)
    assert any("구조레버 슬리브" in str(m.value) and "GO ×1.30" in str(m.value)
               for m in at.markdown)
    df = at.dataframe[-1].value                            # 마지막 표 = 결정 원장
    assert "mom12" in df.columns and "pead" in df.columns


def test_home_shows_gate_signal_line():
    """홈 — 🚦 ML 게이트 신호등 한 줄 (구조레버·KR축·US축) (P2)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    caps = " ".join(str(c.value) for c in at.caption)
    assert "ML 게이트" in caps and "GO ×1.30" in caps and "OBSERVE·hi52" in caps


def test_portfolio_shows_tier3_gate():
    """포트폴리오 — Tier3 구조레버 게이트 상태 캡션 (P2)."""
    at = AppTest.from_string(_script("from dashboard.pages import portfolio", "portfolio.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    caps = " ".join(str(c.value) for c in at.caption)
    assert "Tier3 구조적 레버리지 게이트" in caps and "슬리브 ✅ ON" in caps


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
    assert any("시장 맵" in str(m.value) for m in at.markdown)   # 시장맵 섹션 (3맵 탭 통합)
    assert any("시장 지표" in str(m.value) for m in at.markdown)          # F&G·RSI 패널 (O2)


def test_sidebar_paper_rail_and_nav(monkeypatch):
    """사이드바 🧪 모의투자 레일 + 상세 버튼 → 모의투자 페이지 이동 (P3).

    entry app 은 실 store(빈 DB)라 레일이 숨음 — cached.paper_glance 를 세션 전에
    스텁할 수 없어 스크립트 방식으로 app 사이드바 로직 대신 views→theme 경로만 검증하고,
    버튼 플래그(_nav_to_paper)는 entry app 에서 세션 주입으로 switch 무예외 확인.
    """
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.session_state["_nav_to_paper"] = True          # 레일 버튼 클릭 시뮬
    at.run()
    assert not at.exception, str(at.exception)


def test_ticker_page_etf_view():
    """ETF 티커는 개별주 섹션 대신 ETF 전용 뷰(프로필·Top10·보수·괴리율·배당) — 무예외."""
    etf_stub = '''
st.session_state["ticker"] = "QQQI"
cached.etf = lambda t: {"ticker": "QQQI", "is_etf": True,
    "name": "NEOS Nasdaq 100 High Income ETF",
    "description": "나스닥 100에 커버드콜 전략으로 투자하는 ETF",
    "family": "NEOS Investment Management LLC", "category": "Derivative Income",
    "total_assets": 1.291e10, "nav": 56.1, "price": 55.82, "premium_pct": -0.5,
    "expense_ratio": 0.0068, "shares_outstanding": 230230000, "inception": "2024-01-30",
    "top_holdings": [
        {"symbol": "NVDA", "name": "NVIDIA", "pct": 7.65},
        {"symbol": "AAPL", "name": "Apple", "pct": 6.63},
        {"symbol": "MSFT", "name": "Microsoft", "pct": 4.38}],
    "sector_weights": {"technology": 51.2, "communication_services": 15.3},
    "dividends": {"count_12m": 12, "per_share_12m": 7.62, "yield_pct": 13.69, "freq_label": "매월"}}
'''
    at = AppTest.from_string(_STUBS + etf_stub + "\nfrom dashboard.pages import ticker\nticker.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(str(x) for x in at.markdown) + " ".join(m.label for m in at.metric)
    assert "운용보수" in body and "괴리율" in body            # ETF 지표 렌더
    assert not any("PER" == m.label for m in at.metric)      # 주식 밸류 섹션 미렌더


def test_ticker_page_kr_etf_view():
    """국내 ETF는 원화·추종지수·구성종목 중심의 ETF 분석 화면을 렌더한다."""
    etf_stub = '''
st.session_state["ticker"] = "069500.KS"
cached.etf = lambda t: {"ticker": "069500.KS", "stock_code": "069500", "is_etf": True,
    "market_type": "kr", "currency": "KRW", "name": "KODEX 200",
    "description": "KOSPI 200 지수를 추종하는 국내 대표 시장 ETF",
    "family": "삼성자산운용", "category": "국내 주식형", "benchmark": "KOSPI 200",
    "total_assets": 7800000000000, "nav": 38950.0, "price": 38900.0, "premium_pct": -0.13,
    "tracking_error_pct": 0.08, "expense_ratio": 0.0015, "inception": "2002-10-14",
    "top_holdings": [
        {"symbol": "005930", "name": "삼성전자", "pct": 28.5, "shares": 1000, "amount": 70000000},
        {"symbol": "000660", "name": "SK하이닉스", "pct": 9.2, "shares": 200, "amount": 36000000}],
    "dividends": {"count_12m": 4, "per_share_12m": 820.0, "yield_pct": 2.1, "freq_label": "분기"}}
'''
    at = AppTest.from_string(_STUBS + etf_stub + "\nfrom dashboard.pages import ticker\nticker.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    # HTML markdown 요소는 str() 이 repr("Markdown(allow_html=True)")라 본문이 안 잡힘 — .value 로
    body = (" ".join(str(getattr(x, "value", x)) for x in at.markdown)
            + " ".join(m.label for m in at.metric))
    assert "추종지수" in body and "KOSPI 200" in body
    assert "종목코드" in body and "069500" in body
    assert "추적오차" in body
    assert any("구성종목" in str(s.value) for s in at.subheader)
    assert len(at.dataframe) >= 1
    assert not any("PER" == m.label for m in at.metric)


def test_ticker_page_etf_tr_pr_and_peer_score():
    """ETF 뷰 신규 섹션 — TR vs PR 지표·차트 + 동종그룹 비교표·점수 게이지 (합성 주입)."""
    etf_stub = '''
st.session_state["ticker"] = "QQQI"
cached.etf = lambda t: {"ticker": "QQQI", "is_etf": True, "name": "NEOS NDX High Income",
    "expense_ratio": 0.0068, "price": 55.8, "nav": 56.1, "premium_pct": -0.5,
    "dividends": {"count_12m": 12, "per_share_12m": 7.6, "yield_pct": 13.7, "freq_label": "매월"}}
_TIDX = pd.date_range("2023-01-01", periods=900, freq="D")
_TR = pd.Series([100.0 * 1.0006 ** i for i in range(900)], index=_TIDX)
_PR = pd.Series([100.0 * 1.0001 ** i for i in range(900)], index=_TIDX)
cached.tr_pr = lambda t, years=5: {"tr": _TR, "pr": _PR, "asof": "2026-07-08"}
_ROW = {"ticker": "QQQI", "expense_ratio": 0.0068, "aum": 1.3e10, "div_yield_pct": 13.7,
        "div_count_12m": 12, "tr_1y": 22.2, "tr_3y_ann": 18.0, "pr_1y": 6.1, "pr_3y_ann": 2.0,
        "mdd": 20.0, "mdd_window_y": 3.0, "history_years": 2.4, "avg_dollar_vol": 5e7,
        "tracking_diff": -9.3, "score": 60,
        "score_detail": {"score": 60, "components": {"비용": 38, "성과": 62, "인컴": 88,
                         "리스크": 50, "유동성": 75}, "n_peers": 4, "low_confidence": False,
                         "basis": "1y", "strategy": "covered_call"}}
_ROW2 = dict(_ROW, ticker="QYLD", score=47, tr_1y=21.7)
cached.etf_peers = lambda t: {"group": {"key": "ndx_covered_call",
    "name": "나스닥100 커버드콜", "strategy": "covered_call", "bench": "QQQ"},
    "rows": [_ROW, _ROW2], "asof": "2026-07-08 07:00 UTC"}
'''
    at = AppTest.from_string(_STUBS + etf_stub + "\nfrom dashboard.pages import ticker\nticker.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    body = (" ".join(str(x) for x in at.markdown)
            + " ".join(m.label for m in at.metric)
            + " ".join(str(x.value) for x in at.subheader))
    assert "TR vs PR" in body and "분배 기여" in body            # TR/PR 섹션
    assert "동종 ETF 비교" in body and "나스닥100 커버드콜" in body   # 피어 섹션
    html_body = " ".join(str(getattr(x, "value", "")) for x in at.markdown)
    assert "ETF 점수" in html_body                               # 점수 게이지 (HTML 마크다운)
    assert "매매신호 아님" in " ".join(str(c.value) for c in at.caption)
    assert len(at.dataframe) >= 1                                # 피어 지표표


def test_research_screener_enriched():
    """스크리너 — 기업명·판단근거 컬럼 + 무엣지 캡션 (합성 주입)."""
    script = _STUBS + '''
st.session_state["scr_done"] = True
from dashboard.pages import research
research._screener_section()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1
    df0 = at.dataframe[0].value
    assert "판단근거" in df0.columns and "종목" in df0.columns
    assert "NVIDIA (NVDA)" in str(df0["종목"].iloc[0])            # 기업명 병기
    caps = " ".join(str(c.value) for c in at.caption)
    assert "매매신호 아님" in caps


def test_reconnect_watchdog_html_contract():
    """서버 재기동 워치독 — health 폴링·down→up 전이 시 parent reload 계약."""
    from dashboard import auth
    h = auth.reconnect_watchdog_html(2500)
    assert "/_stcore/health" in h and "2500" in h
    assert "window.parent.location.reload" in h
    assert "down = true" in h                       # 실패 → 회복 전이만 리로드


def test_chart_full_page():
    """차트 풀뷰 — 동일 컨트롤(_price_chart 공용)·840 높이·복귀 버튼 (무예외)."""
    script = _STUBS + '''
st.session_state["ticker"] = "MSFT"
cached.realtime_quote = lambda t: None
from dashboard.pages import chart_full
chart_full.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    labels = " ".join(str(b.label) for b in at.button)
    assert "↙" in labels                              # 복귀 버튼
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "Microsoft" in body or "MSFT" in body      # 히어로 라벨
