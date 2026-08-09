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

from agent_console import agent as _agent_agent
from agent_console import context as _agent_context
from agent_console import storage as _agent_storage
_AGENT_MODULE_SNAPSHOTS = [
    (_agent_agent, dict(vars(_agent_agent))),
    (_agent_context, dict(vars(_agent_context))),
    (_agent_storage, dict(vars(_agent_storage))),
]


@pytest.fixture(scope="module", autouse=True)
def _restore_dashboard_modules():
    """_STUBS 가 AppTest 인프로세스로 dashboard.data/cached 모듈 속성을 직접 덮어씀 —
    같은 pytest 세션의 후속 테스트(test_dashboard.py 등)가 가짜 보유종목을 보지 않도록
    원본 속성을 스냅샷하고 모듈 종료 시 복원."""
    from dashboard import cached, data
    saved = [(mod, dict(vars(mod))) for mod in (data, cached)]
    saved.extend(_AGENT_MODULE_SNAPSHOTS)
    yield
    for mod, snap in saved:
        for key in list(vars(mod)):
            if key not in snap:
                delattr(mod, key)
        for key, val in snap.items():
            setattr(mod, key, val)


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
data.ticker_alerts = lambda t: [{"id": "ab12cd34", "ticker": "MSFT", "price": 380.0,
                                 "type": "buy", "note": "지지선", "triggered": False,
                                 "created_at": "2026-07-09T10:00:00"}]
data.add_ticker_alert = lambda *a, **k: "ab12cd34"
data.remove_ticker_alert = lambda i: True
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
cached.chart_news = lambda t: [{"date": "2026-07-01", "direction": 1, "strength": 4,
                                "event_type": "실적", "title": "beat"}]
cached.macro_corr = lambda t: [{"symbol": "^TNX", "label": "미 10년물", "note": "역상관 경향",
                                "corr90": -0.62, "chg30": 1.4}]
cached.llm_related = lambda t: ([{"ticker": "AMD", "relation": "경쟁사", "reason": "GPU"}], "cached")
cached.ai_briefing = lambda: None
cached.llm_related.clear = lambda: None
cached.macro_assets = lambda: [
    {"symbol": "KRW=X", "label": "달러/원 환율", "emoji": "\U0001f4b1", "unit": "₩",
     "ticker": "KRW=X", "price": 1505.05, "chg": -3.2, "pct": -0.21, "spark": [1500, 1503, 1505]},
    {"symbol": "GC=F", "label": "금", "emoji": "\U0001f947", "unit": "$/oz", "ticker": "GC=F",
     "price": 4105.3, "chg": 12.1, "pct": 0.30, "spark": [4090, 4100, 4105]},
    {"symbol": "BTC-USD", "label": "비트코인", "emoji": "₿", "unit": "$", "ticker": "BTC-USD",
     "price": 64162, "chg": 980, "pct": 1.55, "spark": [63000, 63800, 64162]}]
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
cached.wiki_pipeline_health = lambda: {"dry_run": True, "generated_at": "2026-07-31T00:00:00+00:00",
    "source_health": {"overall": {"tracked_sources": 2, "expected_sources": 2,
    "healthy_sources": 1, "stale_sources": 1, "missing_success_sources": 0},
    "sources": [{"source": "saveticker", "observed": True, "has_success": True},
                {"source": "telegram:insidertracking", "observed": True, "has_success": False}],
    "stale_sources": [{"source": "telegram:insidertracking", "hours": 18.0, "threshold": 12}],
    "recent": {"event_total": 2, "sources": {"saveticker": 2}}},
    "wiki_health": {"source_backed_count": 1, "unverified_count": 1, "stale_count": 1,
        "unused_count": 2, "open_question_count": 1, "stats": {"total": 2,
        "status_counts": {"reviewed": 1, "stable": 1, "archived": 0}}},
    "curation_health": {"source_digest_count": 1, "source_digest_linked_count": 0,
        "source_digest_unlinked_count": 1}, "recommendations": [
            {"category": "collection", "title": "소스 수집 공백 점검", "detail": "telegram:insidertracking 18.0h/12h"},
            {"category": "curation", "title": "큐레이션 승격 경로 보강", "detail": "source_digest 1개가 judgment page로 연결되지 않음"},
        ]}
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
cached.earnings_history_deep = lambda t, limit=12: []
data.load_watchlist = lambda *a, **k: [
    {"ticker": "AAPL", "name": "Apple Inc",
     "reason": "Berkshire Hathaway (Warren Buffett) 신규 편입 (2026-05-15)",
     "source": "notable_investor:berkshire", "note": None, "added_at": "2026-05-16T00:00:00+00:00"},
]
cached.intrinsic = lambda t: {"rim":{"low":250,"mid":320,"high":400},"ddm":None,"upside_pct":12.0,"ddm_reliable":False}
cached.risk = lambda: "리스크 텍스트"
cached.risk_struct = lambda: {"port_vol":0.2,"n_eff":3.5,"n_assets":5,"mdd_est":0.3,
    "contributions":[("MSFT",0.4,0.45),("NVDA",0.6,0.55)],
    "factor_net":{"QQQ":0.95,"TLT":-0.1},"factor_caveat":"베타 참고",
    "leverage":{"recommend":1.3,"dd_cap":1.3,"current":1.0,
                "kelly_half":{"conservative":0.5,"moderate":0.9,"trailing":1.1}}}
cached.institution_watch = lambda keys=None: {
    "institutions": [
        {"key": "berkshire", "display_name": "Berkshire Hathaway", "source_kind": "13f",
         "freshness": "fresh", "holdings_count": 27,
         "availability_flags": {"cash_ratio": "unavailable", "options_exposure": "unavailable"}},
    ],
    "comparison": {"rows": [
        {"display_name": "Berkshire Hathaway", "source_kind": "13f", "freshness": "fresh",
         "holdings_count": 27, "portfolio_concentration": 0.51,
         "portfolio_concentration_flag": "available", "cash_ratio": None, "cash_ratio_flag": "unavailable",
         "options_exposure": None, "options_exposure_flag": "unavailable",
         "reported_return": None, "reported_return_flag": "unavailable",
         "return_proxy": None, "return_proxy_flag": "unavailable"}
    ], "selected_keys": ["berkshire"]},
    "analysis": {"summary": "1개 기관 비교 기준으로 공통 패턴과 차이를 함께 요약했습니다.",
                 "shared_moves": ["상위 보유 구성이 유지되고 있습니다."],
                 "divergences": ["공시 범위가 제한적입니다."], "confidence": 0.45},
}
cached.ohlc = lambda t, period="6mo": pd.DataFrame(
    {"Open":range(100,170),"High":range(101,171),"Low":range(99,169),"Close":range(100,170)}, index=_IDX)
cached.ohlc_tf = lambda t, tf: cached.ohlc(t, period="max")
cached.chart_data_bundle = lambda t, tf, session_policy="regular": {
    "frame": cached.ohlc_tf(t, tf), "requested_timeframe": tf, "actual_timeframe": tf,
    "session": {"policy": session_policy, "decision": "provider_bars_retained"},
    "source": {"name": "test-bars", "source": "test-bars", "as_of": str(_IDX[-1]),
               "freshness": "delayed", "market": "us", "timezone": "America/New_York"},
}
cached.screener = lambda n: {"rows": [{"rank": 1, "ticker": "NVDA", "name": "NVIDIA",
    "score": 2.54, "price": 196.9, "tech_rating": "매수", "surv_flag": False,
    "reason": "52주 고점 근접 · 6M 모멘텀 +42%%", "rsi_14": 62.0,
    "close_vs_52w_high": 0.97, "mom_126d": 0.42, "excess_mom_60d": 0.08, "fund_score": 72.0}],
    "feats": {"NVDA": {"rsi_14": 62.0, "mom_126d": 0.42}},
    "meta": {"ic": 0.05, "icir": 0.8, "top_decile": 0.02, "train_end": "2026-06-01",
             "importance": {"mom_126d": 100, "rsi_14": 50}}}
cached.backtest = lambda: {"error":"skip"}
cached.backtest_last = lambda: {"ml": {"cagr": 0.21, "sharpe": 1.1, "mdd": -0.18},
    "qqq": {"cagr": 0.18, "sharpe": 0.9, "mdd": -0.22}, "overlay": {},
    "verdict": "비채택", "reasons": ["OOS 개선 미달"], "equity": None,
    "asof": "2026-07-08 12:00"}
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
                  "base_score":0.812,"selection_score":0.914,"momentum_score":0.936,
                  "momentum_tilt":0.102,"momentum_multiplier":1.12,"momentum_state":"strong",
                  "overlay_active":True,"regime":"risk_on","market":"kr",
                  "reason_codes":["regime:risk_on","state:strong"],
                  "reason":"score 85·A등급·수급 양호","ok":True,
                  "features":{"mom12":0.71,"hi52":0.95,"lowvol":0.6,"pead":0.58},
                  "fwd_excess":0.021,"correct":True,"matured_at":"2026-06-20"},
                 {"date":"2026-06-02","side":"퇴출","ticker":"000660.KS","name":"SK하이닉스 (000660.KS)",
                  "qty":5,"price":180000.0,"policy_score":0.31,
                  "base_score":0.31,"selection_score":0.31,"momentum_score":0.5,
                  "momentum_tilt":0.0,"momentum_multiplier":1.0,"momentum_state":"inactive",
                  "overlay_active":False,"regime":"—","market":"kr",
                  "reason_codes":["flag:off"],
                  "reason":"타깃이탈","ok":True,"features":{},
                  "fwd_excess":None,"correct":None,"matured_at":None}]}
cached.learning_evolution = lambda s: {"surface":s,
    "snapshot":{"n":52,"realized_ic":0.06,"buy_hit":55.0,"cum_net_excess":0.03},
    "verdict":{"code":"edge","emoji":"\U0001f9ec","label":"약한 엣지 형성","note":"순비용 IC +0.060"},
    "series":[{"date":"2026-06-01","excess":0.01,"ic":0.02,"adopted":False},
              {"date":"2026-06-08","excess":0.03,"ic":0.06,"adopted":True}],
    "adoptions":[{"date":"2026-06-08","excess_challenger":0.03}],"n_runs":2}
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
    ("from dashboard.pages import kr_etf", "kr_etf.render()"),
    ("from dashboard.pages import ai_console", "ai_console.render()"),
])
def test_page_renders_without_exception(mod, call):
    at = AppTest.from_string(_script(mod, call), default_timeout=30)
    at.run()
    assert not at.exception, f"{mod}: {at.exception}"


def test_ai_console_strategy_canvas_allocation_normalize():
    from dashboard.pages import ai_console

    rows = ai_console._normalize_allocations([
        {"symbol": "QQQ", "weight_pct": 45, "note": "core"},
        {"symbol": "CASH", "weight_pct": 15, "note": "buffer"},
    ])

    assert [r["symbol"] for r in rows] == ["QQQ", "CASH"]


def test_ai_console_canvas_buy_rsi_persists_across_rerun():
    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    rsi_input.set_value(45).run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    assert int(rsi_input.value) == 45


def test_ai_console_quick_prompt_list_stays_small():
    from dashboard.pages import ai_console

    prompts = ai_console._quick_prompt_texts()

    assert len(prompts) == 3
    assert any("위키" in prompt for prompt in prompts)


def test_wiki_pipeline_health_summary_wraps_report(monkeypatch):
    from dashboard import views
    from reports import wiki_pipeline_health

    monkeypatch.setattr(
        wiki_pipeline_health,
        "build_pipeline_health_report",
        lambda dry_run=False: {
            "dry_run": dry_run,
            "generated_at": "2026-07-31T00:00:00+00:00",
            "source_health": {"overall": {"tracked_sources": 2, "expected_sources": 2}},
            "wiki_health": {"source_backed_count": 1, "unverified_count": 1},
            "curation_health": {"source_digest_unlinked_count": 1},
            "recommendations": [{"category": "collection", "title": "소스 수집 공백 점검", "detail": "telegram"}],
        },
    )

    report = views.wiki_pipeline_health_summary()

    assert report["source_health"]["overall"]["tracked_sources"] == 2
    assert report["curation_health"]["source_digest_unlinked_count"] == 1



def test_ai_console_memory_detail_shows_source_link(monkeypatch):
    """World Memory 상세카드 — url 있으면 '원문 보기' 링크(2026-07-25), 없으면 링크 생략."""
    from dashboard.pages import ai_console
    calls = []
    monkeypatch.setattr(ai_console.st, "markdown", lambda html_str, **k: calls.append(html_str))

    class _Sel:
        rows = [0]

    class _Event:
        selection = _Sel()

    rows = [{"title": "MSFT 클라우드 성장", "observed_at": "2026-07-25", "source": "news_llm_label",
            "symbols": ["MSFT"], "impact": "high", "body": "본문 텍스트",
            "url": "https://example.com/msft"}]
    ai_console._memory_detail(_Event(), rows)
    assert calls and "원문 보기" in calls[0] and 'href="https://example.com/msft"' in calls[0]
    assert 'target="_blank"' in calls[0] and 'rel="noopener noreferrer"' in calls[0]

    calls.clear()
    rows_no_url = [{**rows[0], "url": None}]
    ai_console._memory_detail(_Event(), rows_no_url)
    assert calls and "원문 보기" not in calls[0]

    # 안전: javascript: 스킴 등은 href 로 절대 안 들어가야 함
    calls.clear()
    rows_bad_url = [{**rows[0], "url": "javascript:alert(1)"}]
    ai_console._memory_detail(_Event(), rows_bad_url)
    assert calls and "javascript:" not in calls[0]


def test_ai_console_context_glance_items_are_compact():
    from dashboard.pages import ai_console

    items = ai_console._context_glance_items({
        "sources": {"events": [{"title": "a"}, {"title": "b"}]},
        "memory": [{"title": "m"}],
        "models": {"items": [{"name": "x"}]},
        "reports": [{"name": "investment-report-2026-07-23.md"}],
    })

    assert [item["label"] for item in items] == ["최근 이벤트", "누적 기억", "모델 파일", "최신 리포트"]


def test_ticker_analysis_context_collects_kr_deep_signals(monkeypatch):
    from dashboard.pages import ticker
    import pandas as pd

    hist = pd.DataFrame(
        {"Open": [100.0, 102.0, 104.0, 106.0, 108.0],
         "High": [105.0, 106.0, 107.0, 108.0, 109.0],
         "Low": [99.0, 100.0, 103.0, 104.0, 106.0],
         "Close": [104.0, 105.0, 106.0, 107.0, 108.0]},
        index=pd.to_datetime(["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01"]),
    )
    monkeypatch.setattr(ticker.cached, "valuation", lambda t: {
        "metrics": {"market_type": "kr", "source": "DART+marcap", "fiscal_year": 2025,
                     "confidence": "high", "per": 12.0, "forward_pe": 9.5, "pbr": 1.1,
                     "roe": 0.18, "eps_ttm": 5000, "eps_fwd": 5600, "market_cap": 1_000_000,
                     "net_income": 80_000, "equity": 450_000, "bps": 20000,
                     "kr_consensus_source": "naver", "kr_consensus_year": 2026},
        "consensus": {"target_mean": 68000.0, "target_upside_pct": 12.5, "revision_momentum": 0.07,
                       "n_analysts": 8},
        "history": [{"date": "2026-07-01", "surprise_pct": 5.4, "eps_est": 1100, "eps_actual": 1160}],
    })
    monkeypatch.setattr(ticker.cached, "financials", lambda t: {"trends": {
        "rev_yoy": 0.11, "net_margin": 0.15, "debt_to_assets": 0.3, "n_years": 5}})
    monkeypatch.setattr(ticker.cached, "intrinsic", lambda t: {"upside_pct": 9.0})
    monkeypatch.setattr(ticker.cached, "institutional", lambda t: {
        "accum": {"accum_score": 72.0},
        "kr_flow": {"foreign_net_5d": 12000, "inst_net_5d": 8000, "smart_net_20d": 20000,
                    "foreign_ratio": 0.47, "foreign_buy_streak": 4, "n": 20},
    })
    monkeypatch.setattr(ticker.cached, "disclosures", lambda t: {"list": [{"date": "2026-07-30"}]})
    monkeypatch.setattr(ticker.cached, "ohlc", lambda t, period="2y": hist)
    monkeypatch.setattr(ticker.cached, "earnings_history_deep", lambda t, limit=12: [
        {"date": "2026-07-01", "eps_actual": 1.0},
        {"date": "2026-04-01", "eps_actual": 1.0},
        {"date": "2026-01-01", "eps_actual": 1.0},
        {"date": "2025-10-01", "eps_actual": 1.0},
        {"date": "2025-07-01", "eps_actual": 1.0},
    ])

    ctx = ticker._analysis_context("005930.KS", hist, 60000)
    assert ctx["is_kr"] is True
    assert ctx["band"]["median"] is not None
    assert ctx["band"]["n"] >= 2
    assert any("DART" in c for c in ctx["summary"]["checks"])
    assert any("Naver" in c for c in ctx["summary"]["checks"])
    assert any("목표가" in p for p in ctx["summary"]["positives"])
    assert any("매집" in p for p in ctx["summary"]["positives"])

    facts = ticker._llm_analysis_facts("005930.KS", hist, 60000)
    assert "KR심화" in facts
    assert "수급" in facts["KR심화"]
    assert facts["KR심화"]["기준"]["source"] == "DART+marcap"


def test_ai_console_rail_status_items_show_realtime_quotes(monkeypatch):
    from dashboard.pages import ai_console

    monkeypatch.setattr(ai_console.st, "session_state", {"agent_last_engine": "codex"})

    items = ai_console._rail_status_items(
        "market",
        {
            "sources": {"events": []},
            "memory": [],
            "models": {"items": []},
            "market_snapshot": {
                "status": "partial",
                "quotes": [{"symbol": "QQQ"}, {"symbol": "005930"}],
            },
        },
    )

    assert {"label": "quotes", "value": "2개"} in items
    assert {"label": "실시간", "value": "partial"} in items


def test_ai_console_rail_status_items_show_engine_detail_and_capped_counts(monkeypatch):
    from dashboard.pages import ai_console

    fake_state = {
        "agent_auto_detail": "한국증시",
        "agent_last_engine": "codex",
    }
    monkeypatch.setattr(ai_console.st, "session_state", fake_state)

    items = ai_console._rail_status_items(
        "market",
        {
            "sources": {"events": [{"title": str(i)} for i in range(40)]},
            "memory": [{"title": str(i)} for i in range(50)],
            "models": {"items": [{"name": str(i)} for i in range(4)]},
        },
    )

    assert items[0] == {"label": "맥락", "value": "시장"}
    assert {"label": "세부", "value": "한국증시"} in items
    assert {"label": "엔진", "value": "codex"} in items
    assert {"label": "events", "value": "40개+"} in items
    assert {"label": "memory", "value": "50개+"} in items
    assert {"label": "models", "value": "4개"} in items


def test_ai_console_chat_state_is_surface_scoped(monkeypatch):
    from dashboard.pages import ai_console

    fake_state = {}
    monkeypatch.setattr(ai_console.st, "session_state", fake_state)

    ai_console._ensure_chat_state("market")
    ai_console._ensure_chat_state("portfolio")
    fake_state[ai_console._chat_key("market")].append({"role": "user", "content": "시장 질문"})
    fake_state[ai_console._chat_key("portfolio")].append({"role": "user", "content": "포트 질문"})

    assert ai_console._chat_key("market") != ai_console._chat_key("portfolio")
    assert fake_state[ai_console._chat_key("market")][-1]["content"] == "시장 질문"
    assert fake_state[ai_console._chat_key("portfolio")][-1]["content"] == "포트 질문"
    assert ai_console._prompt_key("market") != ai_console._prompt_key("portfolio")


def test_ai_console_run_agent_question_marks_context_fallback(monkeypatch):
    from dashboard.pages import ai_console

    fake_state = {}
    monkeypatch.setattr(ai_console.st, "session_state", fake_state)
    monkeypatch.setattr(ai_console.agent, "answer", lambda question, surface: {
        "ok": True,
        "answer": "fallback answer",
        "context": {"event_count": 0, "memory_count": 0, "context_error": "boom"},
    })

    ai_console._run_agent_question("테스트", "portfolio")

    msgs = fake_state[ai_console._chat_key("portfolio")]
    assert msgs[-1]["content"] == "fallback answer"
    assert "context fallback" in msgs[-1]["meta"]


def test_ai_console_run_agent_question_uses_fast_progress_path(monkeypatch):
    from dashboard.pages import ai_console

    fake_state = {}
    updates = []
    seen = {}

    class FakeStatus:
        def __init__(self, label, **kwargs):
            updates.append(label)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            if kwargs.get("label"):
                updates.append(kwargs["label"])

    def fake_answer(question, surface, *, async_postprocess=False):
        seen["async_postprocess"] = async_postprocess
        return {
            "ok": True,
            "answer": "fast answer",
            "context": {
                "event_count": 1,
                "memory_count": 2,
                "postprocess": {"wiki_autocurate": "queued"},
            },
        }

    monkeypatch.setattr(ai_console.st, "session_state", fake_state)
    monkeypatch.setattr(ai_console.st, "status", FakeStatus)
    monkeypatch.setattr(ai_console.agent, "answer", fake_answer)

    ai_console._run_agent_question("빠르게 답해줘", "market")

    msgs = fake_state[ai_console._chat_key("market")]
    assert msgs[-1]["content"] == "fast answer"
    assert seen["async_postprocess"] is True
    assert len(set(updates)) >= 4
    assert any("LLM" in label for label in updates)
    assert "후처리 queued" in msgs[-1]["meta"]


def test_ai_console_strategy_canvas_uses_matrix_dsl(monkeypatch):
    from dashboard.pages import ai_console
    import pandas as pd

    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    close = pd.DataFrame(
        {
            "QQQ": [
                *range(100, 130),
                *range(130, 100, -1),
            ][:60],
        },
        index=idx,
    )
    monkeypatch.setattr(ai_console, "_canvas_prices", lambda symbols, period: close)

    result = ai_console._strategy_canvas_backtest(
        [{"symbol": "QQQ", "weight_pct": 100}],
        period="3mo",
        signal_symbol="QQQ",
        buy_rsi=30,
        sell_rsi=70,
    )

    assert result["ok"] is True
    assert result["functionSpec"]["language"] == "portfolio-matrix-dsl"
    assert "Sortino" in result["metrics"].columns
    assert result["matrix"]


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


def test_portfolio_renders_when_risk_struct_is_none():
    """리스크 모델이 데이터 부족으로 None 을 반환해도 포트폴리오 페이지는 깨지지 않는다."""
    script = _script(
        "from dashboard import cached\nfrom dashboard.pages import portfolio",
        "cached.risk_struct = lambda: None\nportfolio.render()",
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception
    assert any("리스크 분석 불가" in str(w.value) for w in at.warning)


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


def test_research_strategy_studio_section_renders():
    """리서치 '전략 스튜디오' 섹션 — 범용 백테스트/패치 UI가 렌더되어야 한다."""
    script = _STUBS + '''
from agent_console import context as agent_context
from dashboard import cached
from dashboard.pages import research

agent_context.context_pack = lambda surface, hours=72: {
    "strategy_studio": {"ok": True, "spec_count": 1, "version_count": 2, "latest": {"name": "EMA trend"}},
    "surface": surface,
    "generated_at": "2026-08-01T00:00:00+00:00",
}
cached.strategy_studio_catalog = lambda: {
    "ok": True,
    "count": 1,
    "latest": {"id": "spec-1", "name": "EMA trend", "version": 2, "spec": {"name": "EMA trend"}},
    "specs": [{"id": "spec-1", "name": "EMA trend", "version": 2, "spec": {"name": "EMA trend"}}],
    "version_total": 2,
}
cached.strategy_studio_preview = lambda *args, **kwargs: {
    "ok": True,
    "report": {
        "summary": {"name": "EMA trend", "trade_count": 4, "cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35},
        "metrics": {"cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35, "trade_count": 4},
        "warnings": [],
        "trades": [{"date": "2026-01-02", "action": "enter_long"}],
        "equity": {"columns": ["nav"], "index": ["2026-01-01"], "rows": [{"nav": 100.0}]},
        "weights": {"columns": ["QQQ"], "index": ["2026-01-01"], "rows": [{"QQQ": 1.0}]},
    },
    "metrics": {"cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35, "trade_count": 4},
    "benchmark": {"symbol": "QQQ", "available": True},
    "warnings": ["stale quotes"],
    "errors": [],
    "trade_count": 4,
}
cached.strategy_studio_versions = lambda *args, **kwargs: [
    {"id": "spec-1", "version": 2, "name": "EMA trend", "source": "ui", "created_at": "2026-08-01T00:00:00+00:00"},
    {"id": "spec-1", "version": 1, "name": "EMA trend", "source": "create", "created_at": "2026-07-31T00:00:00+00:00"},
]
research.render()
'''
    at = AppTest.from_string(script,
                             default_timeout=30)
    at.session_state["research_section"] = "전략 스튜디오"
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "전략 스튜디오" in body
    assert "EMA trend" in body
    assert "미리보기" in body


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


def test_ticker_page_kr_institutional_shows_flow_not_13f():
    """국내 종목의 '기관·내부자' 탭 — 13F/Form4 대신 외인·기관 수급이 뜬다(2026-07-29 보강)."""
    script = _STUBS + (
        'st.session_state["ticker"] = "005930.KS"\n'
        'st.session_state["ticker_section"] = "기관·내부자"\n'
        'cached.institutional = lambda t: {"is_kr": True, "accum": None,\n'
        '    "kr_flow": {"foreign_net_5d": 15000, "inst_net_5d": -3000, "smart_net_20d": 42000,\n'
        '                "foreign_ratio": 0.512, "foreign_buy_streak": 4, "n": 20}}\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown) + " ".join(m.label for m in at.metric)
    assert "외인" in body and "수급" in body
    assert "13F" not in body
    assert "SEC Form 4" not in body


def test_ticker_page_kr_bare_code_shows_company_name():
    """국내 6자리 코드만 들어와도 종목 분석 화면이 회사명으로 정상 표기된다."""
    script = _STUBS + (
        'st.session_state["ticker"] = "005930"\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "삼성전자" in body


def test_ticker_page_per_self_band_renders_on_valuation_tab():
    """가치평가 탭 — 자체 역사 PER 밴드가 접힘 없이 본문에 바로 보인다."""
    script = _STUBS + (
        'st.session_state["ticker"] = "MSFT"\n'
        'cached.earnings_history_deep = lambda t, limit=12: [\n'
        '    {"date": "2026-04-01", "eps_actual": 1.0}, {"date": "2026-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-10-01", "eps_actual": 1.0}, {"date": "2025-07-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-04-01", "eps_actual": 1.0}, {"date": "2025-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2024-10-01", "eps_actual": 1.0}]\n'
        'cached.ohlc = lambda t, period="6mo": pd.DataFrame(\n'
        '    {"Close": [50.0, 55.0, 60.0, 64.0]},\n'
        '    index=pd.DatetimeIndex(["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01"]))\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("자체 역사 PER 밴드" in str(e.label) for e in at.expander)
    labels = [m.label for m in at.metric]
    assert "최저" in labels and "중앙값" in labels and "최고" in labels and "현재 PER" in labels
    caps = " ".join(str(c.value) for c in at.caption)
    assert "최근 4개 분기 TTM-EPS 기준 역산" in caps


def test_ticker_page_per_self_band_hidden_when_insufficient_history():
    """실적 이력 부족이면 밴드 섹션 자리에서 정직한 안내를 보여준다."""
    script = _STUBS + 'st.session_state["ticker"] = "MSFT"\nfrom dashboard.pages import ticker\nticker.render()\n'
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("자체 역사 PER 밴드" in str(e.label) for e in at.expander)
    info_body = " ".join(str(getattr(item, "value", "")) for item in at.info)
    assert "자체 역사 PER 밴드를 표시할 실적 이력이 부족합니다." in info_body


def test_ticker_page_per_self_band_fetch_failure_shows_error():
    """PER 밴드 원본 fetch 실패는 info 가 아니라 error 블록으로 드러난다."""
    script = _STUBS + (
        'st.session_state["ticker"] = "MSFT"\n'
        'def _band_ohlc(t, period="6mo"):\n'
        '    if period == "2y":\n'
        '        raise RuntimeError("band fetch failed")\n'
        '    return pd.DataFrame(\n'
        '        {"Close": [50.0, 55.0, 60.0, 64.0]},\n'
        '        index=pd.DatetimeIndex(["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01"]))\n'
        'cached.ohlc = _band_ohlc\n'
        'cached.earnings_history_deep = lambda t, limit=12: [\n'
        '    {"date": "2026-04-01", "eps_actual": 1.0}, {"date": "2026-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-10-01", "eps_actual": 1.0}, {"date": "2025-07-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-04-01", "eps_actual": 1.0}, {"date": "2025-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2024-10-01", "eps_actual": 1.0}]\n'
        'from dashboard.pages import ticker\n'
        'ticker.render()\n'
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("자체 역사 PER 밴드" in str(e.value) for e in at.error)
    assert "band fetch failed" in " ".join(str(e.value) for e in at.error)
    assert not any("자체 역사 PER 밴드" in str(e.label) for e in at.expander)


def test_ticker_page_peer_comparables_renders_for_us():
    """미국 종목은 피어 비교가 접힘 없이 본문에 바로 보인다."""
    script = _STUBS + (
        'st.session_state["ticker"] = "MSFT"\n'
        'cached.valuation = lambda t: {"metrics":{"per":30.0,"pbr":10.0,"roe":0.4,"market_type":"us"},\n'
        '    "consensus":{"n_analysts":5}, "history":[]}\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("동종업계 비교" in str(e.label) for e in at.expander)
    caps = " ".join(str(c.value) for c in at.caption)
    assert "같은 섹터 시총 상위 종목" in caps
    comparison = next(item.value for item in at.dataframe if "ROE(%)" in item.value.columns)
    assert "종목" in comparison.columns
    assert comparison.shape[0] >= 2
    assert comparison.iloc[0]["종목"]


def test_ticker_page_peer_comparables_hidden_for_kr_without_dart_key():
    """국내 DART fallback 이면 피어 비교 자리에 이유가 보이는 안내를 남긴다."""
    script = _STUBS + (
        'st.session_state["ticker"] = "005930.KS"\n'
        'cached.valuation = lambda t: {"metrics":{"per":None,"market_type":"kr","kr_yf_fallback":True},\n'
        '    "consensus":{}, "history":[]}\n'
        'from dashboard.pages import ticker\nticker.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("동종업계 비교" in str(e.label) for e in at.expander)
    info_body = " ".join(str(getattr(item, "value", "")) for item in at.info)
    assert "동종업계 비교는 DART 기반 국내 밸류에이션이 준비되면 표시됩니다." in info_body


def test_ticker_page_per_band_and_peer_sections_are_visible():
    script = _STUBS + (
        'st.session_state["ticker"] = "MSFT"\n'
        'st.session_state["_bot_1d"] = ["거래량", "펀더멘털"]\n'
        'cached.valuation = lambda t: {"metrics":{"per":30.0,"forward_pe":25.0,"eps_fwd":12.0,\n'
        '    "pbr":10.0,"roe":0.4,"market_type":"us"}, "consensus":{"n_analysts":5}, "history":[]}\n'
        'cached.chart_fundamentals = lambda t: {"quarterly": [\n'
        '    {"date": "2025-06-30", "revenue": 5.0e10, "net_income": 1.2e10, "margin": 0.24},\n'
        '    {"date": "2025-09-30", "revenue": 5.5e10, "net_income": 1.4e10, "margin": 0.25},\n'
        '    {"date": "2025-12-31", "revenue": 6.0e10, "net_income": 1.6e10, "margin": 0.27},\n'
        '    {"date": "2026-03-31", "revenue": 6.4e10, "net_income": 1.8e10, "margin": 0.28}],\n'
        '    "annual": []}\n'
        'cached.earnings_history_deep = lambda t, limit=12: [\n'
        '    {"date": "2026-04-01", "eps_actual": 1.0}, {"date": "2026-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-10-01", "eps_actual": 1.0}, {"date": "2025-07-01", "eps_actual": 1.0},\n'
        '    {"date": "2025-04-01", "eps_actual": 1.0}, {"date": "2025-01-01", "eps_actual": 1.0},\n'
        '    {"date": "2024-10-01", "eps_actual": 1.0}]\n'
        'cached.ohlc = lambda t, period="6mo": pd.DataFrame(\n'
        '    {"Close": [50.0, 55.0, 60.0, 64.0]},\n'
        '    index=pd.DatetimeIndex(["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01"]))\n'
        'from dashboard.pages import ticker\n'
        'ticker.render()\n'
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("기업 판단 요약" in str(s.value) for s in at.subheader)
    assert not any("자체 역사 PER 밴드" in str(e.label) for e in at.expander)
    assert not any("동종업계 비교" in str(e.label) for e in at.expander)
    labels = [m.label for m in at.metric]
    assert "현재 PER" in labels
    assert "멀티플 기준가" in labels
    caps = " ".join(str(c.value) for c in at.caption)
    assert "같은 섹터 시총 상위 종목" in caps
    comparison = next(item.value for item in at.dataframe if "ROE(%)" in item.value.columns)
    assert comparison.shape[0] >= 2
    frames = [item.proto.srcdoc for item in at.get("iframe")]
    assert frames and any('"매출"' in src for src in frames)


def test_paper_kpis_and_decisions():
    """모의투자: 계좌 KPI(NAV·누적·vs지수·MDD) + 로직평가 + 판단근거 원장표 + 안전 라벨 (P1)."""
    at = AppTest.from_string(_script("from dashboard.pages import paper", "paper.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert len(at.metric) >= 8                    # 계좌 4 + 예수금 + 로직평가 4
    assert len(at.dataframe) >= 2                 # 보유표 + 결정 원장표
    decision_df = next(item.value for item in at.dataframe if "정책점수" in item.value.columns)
    for col in ("선택점수", "모멘텀점수", "틸트", "배수", "상태", "오버레이", "레짐"):
        assert col in decision_df.columns
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


def test_portfolio_shows_kr_section_with_cash(monkeypatch):
    """포트폴리오 — 국내(KR) 섹션에 예수금·소스명 표시 (2026-07-25 추가)."""
    body_script = _STUBS + '''
data.load_kr_holdings = lambda *a, **k: {
    "rows": [{"ticker": "0167A0", "name": "SOL AI반도체TOP2플러스",
              "display": "SOL AI반도체TOP2플러스", "shares": 23,
              "avg": 20681.3, "cur": 17185.0, "value": 395255.0, "ret": -16.91, "pnl": -80415.0}],
    "total": 395255.0, "cash": 41011.0, "total_with_cash": 436266.0,
    "last_sync": "2026-07-25T04:35:00", "source": "toss"}
from dashboard.pages import portfolio
portfolio.render()
'''
    at = AppTest.from_string(body_script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "토스 동기화" in body
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("예수금") == "₩41,011"


def test_home_has_donut_and_holdings():
    """홈: 도넛(plotly) + 보유표 + KPI 가 렌더되는지(요소 존재)."""
    at = AppTest.from_string(_script("from dashboard.pages import home", "home.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 3               # Phase·낙폭·DCA (총액은 히어로 HTML)
    assert len(at.dataframe) >= 1            # 보유표
    assert any("국면" in str(i.value) for i in at.info)  # Phase 행동 박스


def test_home_shows_kr_donut_and_holdings_with_cash():
    """홈 — 국내(KR) 배분 도넛+보유표+예수금 (2026-07-25 추가)."""
    body_script = _STUBS + '''
data.load_kr_holdings = lambda *a, **k: {
    "rows": [{"ticker": "0167A0", "name": "SOL AI반도체TOP2플러스",
              "display": "SOL AI반도체TOP2플러스", "shares": 23,
              "avg": 20681.3, "cur": 17185.0, "value": 395255.0, "ret": -16.91, "pnl": -80415.0}],
    "total": 395255.0, "cash": 41011.0, "total_with_cash": 436266.0,
    "last_sync": "2026-07-25T04:35:00"}
from dashboard.pages import home
home.render()
'''
    at = AppTest.from_string(body_script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("국내 배분" in str(c.value) for c in at.caption)
    caps = " ".join(str(c.value) for c in at.caption)
    assert "예수금 ₩41,011" in caps
    assert len(at.dataframe) >= 2                          # USD 보유표 + KR 보유표
    kr_df = at.dataframe[-1].value
    # 종목 열엔 코스피200 아닌 종목은 이름만(코드 안 보임) — display 필드 사용(2026-07-29)
    assert "SOL AI반도체TOP2플러스" in kr_df["종목"].values
    assert "0167A0" not in kr_df["종목"].values


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


def test_ticker_page_macro_view():
    """매크로 자산(금)은 주식 섹션(호가·진입레벨·밸류·재무·포지션관리) 대신 전용 뷰.

    성과 프로필·연관 자산 상관이 뜨고, 주식 전용 섹션은 렌더되지 않아야 한다.
    """
    macro_stub = '''
st.session_state["ticker"] = "GC=F"
'''
    at = AppTest.from_string(_STUBS + macro_stub
                             + "\nfrom dashboard.pages import ticker\nticker.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "성과 프로필" in body and "연관 자산" in body
    # 주식 전용 섹션 부재 (매크로엔 무의미)
    for stock_only in ("진입 레벨 가이드", "포지션 관리", "실시간 호가"):
        assert stock_only not in body, f"매크로 뷰에 주식 섹션 노출: {stock_only}"


def test_ticker_page_macro_krw_fx_timing():
    """환율(KRW=X)은 환전 타이밍 + 포트 민감도 특화 섹션."""
    at = AppTest.from_string(_STUBS + '\nst.session_state["ticker"] = "KRW=X"\n'
                             + "\nfrom dashboard.pages import ticker\nticker.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + \
        " ".join(str(c.value) for c in at.caption) + " ".join(str(i.value) for i in at.info)
    assert "환전 타이밍" in body and "민감도" in body


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


def test_kr_etf_page_renders_domestic_only():
    """국내 ETF 전용 페이지는 원화 기준 표시와 ETF 섹션을 유지한다."""
    etf_stub = '''
st.session_state["ticker"] = "0167A0.KS"
cached.etf = lambda t: {"ticker": "0167A0.KS", "stock_code": "0167A0", "is_etf": True,
    "market_type": "kr", "currency": "KRW", "name": "SOL AI반도체TOP2플러스",
    "description": "AI 반도체 대표 종목에 집중하는 국내 테마 ETF",
    "family": "신한자산운용", "category": "국내 테마형", "benchmark": "FnGuide AI반도체 TOP2+",
    "total_assets": 1200000000000, "nav": 10250.0, "price": 10210.0, "premium_pct": -0.39,
    "tracking_error_pct": 0.12, "expense_ratio": 0.0045, "inception": "2023-06-21",
    "top_holdings": [
        {"symbol": "000660", "name": "SK하이닉스", "pct": 18.5, "shares": 200, "amount": 36000000},
        {"symbol": "005930", "name": "삼성전자", "pct": 16.2, "shares": 400, "amount": 70000000}],
    "dividends": {"count_12m": 4, "per_share_12m": 120.0, "yield_pct": 1.2, "freq_label": "분기"}}
'''
    at = AppTest.from_string(_STUBS + etf_stub + "\nfrom dashboard.pages import kr_etf\nkr_etf.render()\n",
                             default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    body = (" ".join(str(x) for x in at.markdown)
            + " ".join(m.label for m in at.metric)
            + " ".join(str(x.value) for x in at.subheader))
    assert "국내 ETF 전용" in body
    assert "추적오차" in body and "NAV" in body


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


def test_ticker_chart_live_stable_html_and_feeder():
    """⚡ live — 메인 차트 html 바이트 안정(8초 재실행 재마운트 방지) + 피더가 가격을 나름.

    live 모드에서 실시간가를 서버 bake 하면 srcdoc 이 8초마다 바뀌어 iframe 재마운트
    (그리던 드로잉 리셋 + 수 MB 재전송) — 가격이 달라도 메인 html 은 불변이어야 하고
    가격은 초소형 피더(tnrt localStorage push)만 나른다. 비 live 는 종전 bake 유지.
    """
    def _script(px, live):
        # ⚠️ _STUBS 는 이미 % 포맷 소비돼 리터럴 % 를 품음 — 재-% 포맷 금지(f-string 결합)
        return _STUBS + f'''
cached.realtime_quote = lambda t: {{"price": {px}}}
st.session_state["_chart_live"] = {live}
from dashboard.pages import ticker
ticker.render()
'''
    docs = {}
    for px in ("172.5", "199.25"):
        at = AppTest.from_string(_script(px, True), default_timeout=30)
        at.run()
        assert not at.exception, str(at.exception)
        frames = [i.proto.srcdoc for i in at.get("iframe")]
        main = [s for s in frames if "patchLast" in s]
        feed = [s for s in frames if "tnrt:MSFT" in s and len(s) < 1024]
        assert len(main) == 1 and len(feed) == 1, "live: 메인 차트 1 + 피더 1 이어야"
        assert "const live = true" in main[0]
        assert px in feed[0]                        # 가격은 피더가 나름
        assert px not in main[0]                    # 메인 html 엔 실시간가 미포함 (bake 0)
        docs[px] = main[0]
    assert docs["172.5"] == docs["199.25"], "실시간가가 달라도 메인 html 은 바이트 불변"
    # 비 live — 종전 서버 bake 유지 (bounds JSON 에 실시간가 반영·피더 없음)
    at = AppTest.from_string(_script("172.5", False), default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    frames = [i.proto.srcdoc for i in at.get("iframe")]
    main = [s for s in frames if "patchLast" in s]
    assert len(main) == 1 and "const live = false" in main[0]
    assert "172.5" in main[0]                       # bake 로 bounds 에 실시간가
    assert not any("tnrt:MSFT" in s and len(s) < 1024 for s in frames)   # 피더 없음


def test_reconnect_watchdog_html_contract():
    """서버 재기동 워치독 — health 폴링·down→up 전이 시 parent reload 계약."""
    from dashboard import auth
    h = auth.reconnect_watchdog_html(2500)
    assert "/_stcore/health" in h and "2500" in h
    assert "window.parent.location.reload" in h
    assert "down = true" in h                       # 실패 → 회복 전이만 리로드
    assert "AbortController" in h                   # fetch hang 방어 타임아웃
    assert ">= 3" in h                              # 연속 3회 실패부터 다운 판정


_WATCHDOG_HARNESS = r"""
let tick = null;
global.setInterval = (fn, ms) => { tick = fn; return 0; };
let reloads = 0;
global.window = { parent: { location: { reload() { reloads++; } } } };
const outcomes = [];                       // 틱마다 소비 — 'ok' | 'fail'
global.fetch = () => (outcomes.shift() === "ok"
  ? Promise.resolve({ ok: true }) : Promise.reject(new Error("ECONNRESET")));
__SCRIPT__
(async () => {
  function fail(m) { console.error("FAIL " + m); process.exit(1); }
  if (!tick) fail("no_interval");
  // 1) 일시적 reset 1회 → 즉시 회복: reload 금지 (hair-trigger 튕김 방지)
  outcomes.push("fail", "ok"); await tick(); await tick();
  if (reloads !== 0) fail("single_blip_reloaded");
  // 2) 2연속 실패 → 회복: 아직 임계(3) 미달 — reload 금지
  outcomes.push("fail", "fail", "ok"); await tick(); await tick(); await tick();
  if (reloads !== 0) fail("two_blips_reloaded");
  // 3) 연속 3회 실패(진짜 다운) → 회복: reload 정확히 1회
  outcomes.push("fail", "fail", "fail", "ok");
  await tick(); await tick(); await tick(); await tick();
  if (reloads !== 1) fail("no_reload_after_downtime reloads=" + reloads);
  console.log("OK watchdog");
})();
"""


@pytest.mark.skipif(__import__("shutil").which("node") is None,
                    reason="node 미설치 — 런타임 JS 검증 스킵")
def test_reconnect_watchdog_runtime(tmp_path):
    """워치독 런타임 — 단일/2연속 실패는 무시, 3연속 실패 후 회복 시만 reload 1회."""
    import re
    import subprocess

    from dashboard import auth
    h = auth.reconnect_watchdog_html(1000)
    js = re.findall(r"<script>(.*?)</script>", h, re.S)[0]
    runner = tmp_path / "watchdog.js"
    runner.write_text(_WATCHDOG_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run(["node", str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"watchdog runtime fail: {r.stdout}\n{r.stderr}"
    assert "OK watchdog" in r.stdout


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


def test_ticker_chart_new_indicators_render():
    """V-series 지표(켈트너·KAMA·샹들리에·Aroon·%b·PVT) 전체 선택 렌더 — 무예외."""
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
st.session_state["_top_1d"] = ["이동평균선", "켈트너 채널", "KAMA", "샹들리에 엑시트"]
st.session_state["_bot_1d"] = ["거래량", "RSI", "MACD", "스토캐스틱", "Aroon", "%b", "PVT"]
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    frames = [i.proto.srcdoc for i in at.get("iframe")]
    main = [s for s in frames if "bt-reg" in s]
    assert main, "차트 iframe 에 신규 도구 버튼 미포함"
    assert "bt-avwap" in main[0] and "bt-vprof" in main[0]


def test_ticker_compare_tray_renders_active_state():
    """종목 비교 — 풀폭 트레이에서 선택 상태·제거 버튼·% 비교 차트가 함께 렌더."""
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
st.session_state["_cmp_panel_open"] = True
st.session_state["_cmp_active"] = ["QQQ", "SPY"]
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    caps = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "종목 비교" in body
    assert "공통 시작점=0% 상대수익" in caps
    labels = " ".join(str(b.label) for b in at.button)
    assert "× Invesco QQQ" in labels and "× SPDR S&P 500" in labels
    frames = [i.proto.srcdoc for i in at.get("iframe")]
    main = [s for s in frames if "const pctMode = true" in s]
    assert main, "비교 선택 시 % 상대수익 차트로 렌더되어야 함"


def test_ticker_chart_renko_uses_shared_renderer_without_iframe_assumptions():
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
st.session_state["_chart_kind_value"] = "renko"
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert at.get("plotly_chart"), "가격 변환 차트는 공용 Plotly 렌더러로 표시되어야 함"


def test_ticker_chart_rejects_intraday_daily_substitution():
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
st.session_state["_chart_tf"] = "5분"
cached.chart_data_bundle = lambda t, tf, session_policy="regular": {
    "frame": None, "requested_timeframe": tf, "actual_timeframe": "1d",
    "session": {"policy": session_policy, "decision": "timeframe_mismatch"},
    "source": {"name": "test-bars", "freshness": "unknown"},
}
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    warnings = " ".join(str(item.value) for item in at.warning)
    assert "다른 봉으로 대체하지 않았습니다" in warnings


def test_compare_window_aligns_to_shared_start():
    """비교 창은 공통 시작점부터 잘라서 불필요한 선행 공백을 줄여야 한다."""
    import pandas as pd
    from dashboard.pages import ticker

    main_idx = pd.date_range("2004-08-01", periods=40, freq="D")
    spy_idx = pd.date_range("1993-01-01", periods=5000, freq="D")
    goog_idx = pd.date_range("2004-08-01", periods=40, freq="D")
    df = pd.DataFrame({"Close": [100.0 + i for i in range(40)]}, index=main_idx)
    compare = {
        "SPY": pd.Series([50.0 + i for i in range(len(spy_idx))], index=spy_idx),
        "GOOGL": pd.Series([80.0 + i for i in range(40)], index=goog_idx),
    }

    trimmed_df, trimmed_compare = ticker._align_compare_window(df, compare, None)

    assert trimmed_df.index[0] == main_idx[0]
    assert trimmed_df.index[-1] == main_idx[-1]
    assert trimmed_compare["SPY"].index[0] == main_idx[0]
    assert trimmed_compare["SPY"].index[-1] == main_idx[-1]
    assert trimmed_compare["GOOGL"].index[0] == main_idx[0]
    assert trimmed_compare["GOOGL"].index[-1] == main_idx[-1]


def test_ticker_chart_fundamentals_panel_renders():
    """펀더멘털 하단 지표 — 스텁 rows 로 렌더 무예외 + 캡션 표기 (W-series)."""
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
cached.chart_fundamentals = lambda t: {"quarterly": [
    {"date": "2025-06-30", "revenue": 5.0e10, "net_income": 1.2e10, "margin": 0.24},
    {"date": "2025-09-30", "revenue": 5.5e10, "net_income": 1.4e10, "margin": 0.25},
    {"date": "2025-12-31", "revenue": 6.0e10, "net_income": 1.6e10, "margin": 0.27},
    {"date": "2026-03-31", "revenue": 6.4e10, "net_income": 1.8e10, "margin": 0.28}],
    "annual": []}
st.session_state["_bot_1d"] = ["거래량", "펀더멘털"]
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    caps = " ".join(str(c.value) for c in at.caption)
    assert "펀더멘털 패널" in caps and "분기" in caps
    frames = [i.proto.srcdoc for i in at.get("iframe")]
    assert any('"매출"' in s for s in frames), "차트 iframe 에 매출 트레이스 없음"
    # ETF 등 데이터 없음 → 정직 안내
    script2 = script.replace('"quarterly": [', '"quarterly_x": [')
    at2 = AppTest.from_string(script2, default_timeout=30)
    at2.run()
    assert not at2.exception, str(at2.exception)
    assert "펀더멘털 데이터 없음" in " ".join(str(c.value) for c in at2.caption)


def test_ticker_page_kr_core_context_is_visible_without_expander():
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
cached.chart_fundamentals = lambda t: {"quarterly": [
    {"date": "2025-06-30", "revenue": 5.0e10, "net_income": 1.2e10, "margin": 0.24},
    {"date": "2025-09-30", "revenue": 5.5e10, "net_income": 1.4e10, "margin": 0.25},
    {"date": "2025-12-31", "revenue": 6.0e10, "net_income": 1.6e10, "margin": 0.27},
    {"date": "2026-03-31", "revenue": 6.4e10, "net_income": 1.8e10, "margin": 0.28}],
    "annual": []}
cached.valuation = lambda t: {
    "metrics": {"market_type": "kr", "source": "DART+marcap", "fiscal_year": 2025,
                 "confidence": "high", "per": 12.0, "forward_pe": 9.5, "pbr": 1.1,
                 "roe": 0.18, "eps_ttm": 5000, "eps_fwd": 5600, "market_cap": 1_000_000,
                 "net_income": 80_000, "equity": 450_000, "bps": 20000,
                 "kr_consensus_source": "naver", "kr_consensus_year": 2026},
    "consensus": {"target_mean": 68000.0, "target_high": 76000.0, "target_low": 62000.0,
                   "target_upside_pct": 12.5, "revision_momentum": 0.07, "n_analysts": 8,
                   "rec_buy": 4, "rec_hold": 3, "rec_strong_buy": 1, "eps_rev_up_30d": 2,
                   "eps_rev_down_30d": 1},
    "history": [{"date": "2026-07-01", "surprise_pct": 5.4, "eps_est": 1100, "eps_actual": 1160}],
}
cached.financials = lambda t: {"trends": {
    "rev_yoy": 0.11, "net_margin": 0.15, "debt_to_assets": 0.3, "n_years": 5}}
cached.institutional = lambda t: {
    "accum": {"accum_score": 72.0},
    "kr_flow": {"foreign_net_5d": 12000, "inst_net_5d": 8000, "smart_net_20d": 20000,
                "foreign_ratio": 0.47, "foreign_buy_streak": 4, "n": 20},
}
cached.disclosures = lambda t: {"list": [{"date": "2026-07-30"}], "error": "", "market": "KR"}
cached.earnings = lambda t: {"history": [{"date": "2026-07-01", "surprise_pct": 5.4}]}
cached.earnings_history_deep = lambda t, limit=12: [
    {"date": "2026-07-01", "eps_actual": 1.0},
    {"date": "2026-04-01", "eps_actual": 1.0},
    {"date": "2026-01-01", "eps_actual": 1.0},
    {"date": "2025-10-01", "eps_actual": 1.0},
    {"date": "2025-07-01", "eps_actual": 1.0},
]
st.session_state["ticker"] = "005930.KS"
st.session_state["_bot_1d"] = ["거래량", "펀더멘털"]
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("한국 종목 심화 컨텍스트" in str(e.label) for e in at.expander)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "목표가 여력" in body
    assert "리비전 모멘텀" in body
    labels = [m.label for m in at.metric]
    assert "PER" in labels and "Fwd PE" in labels
    frames = [item.proto.srcdoc for item in at.get("iframe")]
    assert frames and any("bt-reg" in src for src in frames)


def test_ticker_page_kr_core_context_shows_fallback_when_consensus_inputs_missing():
    script = _STUBS + '''
cached.realtime_quote = lambda t: None
cached.ohlc = lambda t, period="max": pd.DataFrame()
cached.valuation = lambda t: {
    "metrics": {"market_type": "kr", "source": "DART", "fiscal_year": 2025,
                 "confidence": "high", "per": 12.0, "forward_pe": 9.5, "pbr": 1.1,
                 "roe": 0.18, "eps_ttm": 5000, "kr_yf_fallback": True},
    "consensus": {},
    "history": [],
}
cached.financials = lambda t: {"trends": {
    "rev_yoy": 0.11, "net_margin": 0.15, "debt_to_assets": 0.3, "n_years": 5}}
cached.institutional = lambda t: {
    "accum": {"accum_score": 72.0},
    "kr_flow": {"foreign_net_5d": 12000, "inst_net_5d": 8000, "smart_net_20d": 20000,
                "foreign_ratio": 0.47, "foreign_buy_streak": 4, "n": 20},
}
cached.disclosures = lambda t: {"list": [], "error": "", "market": "KR"}
cached.earnings = lambda t: {"history": []}
cached.earnings_history_deep = lambda t, limit=12: []
st.session_state["ticker"] = "005930.KS"
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("한국 종목 심화 컨텍스트" in str(e.label) for e in at.expander)
    assert any("한국 종목 심화 컨텍스트" in str(m.value) for m in at.markdown)
    assert any("DART 키 설정 시 활성" in str(m.value) for m in at.markdown)
    info_body = " ".join(str(getattr(item, "value", "")) for item in at.info)
    assert "애널리스트 의견 데이터 없음" in info_body
    assert "목표가 팬 차트 표시 불가" in info_body


def test_ticker_llm_analysis_section():
    """🤖 AI 종목 분석 — 버튼 게이트·클릭 시 구조화 해설 렌더 (views 스텁·무LLM)."""
    script = _STUBS + '''
import dashboard.views as _views
cached.realtime_quote = lambda t: None
cached.llm_analysis = lambda t, fj: ({
    "summary": "고성장 대비 밸류 부담 공존", "bulls": ["클라우드 성장"],
    "bears": ["PER 부담"], "valuation": "프리미엄 구간.",
    "technicals": "200일선 아래.", "checkpoints": ["다음 분기 마진"],
    "generated_at": "2026-07-11 06:00", "model": "gpt-5.5"}, "ok")
from dashboard.pages import ticker
ticker.render()
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    btns = [b for b in at.button if "분석 생성" in str(b.label)]
    assert btns, "분석 생성 버튼 미발견 (버튼 게이트)"
    btns[0].click()
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "고성장 대비 밸류 부담 공존" in body
    assert "클라우드 성장" in body and "PER 부담" in body
    caps = " ".join(str(c.value) for c in at.caption)
    assert "매매신호 아님" in caps and "판단(신호·배분)에 미반영" in caps.replace("시스템 ", "시스템")


def test_home_ai_briefing_card():
    """🌅 홈 AI 브리핑 카드 — 크론 JSON 있으면 표시·없으면 조용히 생략 (표시 전용)."""
    body_script = _STUBS + '''
cached.ai_briefing = lambda: {"summary": "기술주 중심 — 실적 시즌 진입",
    "highlights": ["MSFT — 분기 매출 증가"], "risks": ["기술주 집중"],
    "checkpoints": ["CPI"], "generated_at": "2026-07-11 07:45", "model": "gpt-5.5"}
from dashboard.pages import home
home.render()
'''
    at = AppTest.from_string(body_script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "기술주 중심 — 실적 시즌 진입" in body and "MSFT — 분기 매출 증가" in body
    caps = " ".join(str(c.value) for c in at.caption)
    assert "매매신호 아님" in caps
    # 브리핑 없음 → 카드 미표시·무예외
    at2 = AppTest.from_string(_STUBS + '''
from dashboard.pages import home
home.render()
''', default_timeout=30)
    at2.run()
    assert not at2.exception, str(at2.exception)
    body2 = " ".join(str(getattr(m, "value", "")) for m in at2.markdown)
    assert "오늘 주목" not in body2


def test_app_theme_toggle_light_dark():
    """라이트/다크 토글 — tn_light 세션에 따라 라이트 오버라이드 주입 여부 전환."""
    at = AppTest.from_file(os.path.join(ROOT, "dashboard", "app.py"), default_timeout=60)
    at.session_state["_authed"] = True
    at.run()
    assert not at.exception, str(at.exception)
    dark_md = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "#f7f8fa" not in dark_md                    # 다크 기본 — 라이트 오버라이드 미주입
    # 라이트로 전환
    at.session_state["tn_light"] = True
    at.run()
    assert not at.exception, str(at.exception)
    light_md = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "#f7f8fa" in light_md and "--tn-panel:#ffffff" in light_md


def test_ai_console_auto_question_routes_and_appends_to_single_thread(monkeypatch):
    """자동 맥락 UX — 버튼 선택 없이 질문하면 infer_surface 로 라우팅되고
    메시지는 단일(auto) 스레드에 쌓인다. meta 에 추론 맥락 표기."""
    from dashboard.pages import ai_console

    fake_state = {}
    monkeypatch.setattr(ai_console.st, "session_state", fake_state)
    seen = {}

    def fake_answer(question, surface):
        seen["surface"] = surface
        return {"ok": True, "answer": "auto answer",
                "context": {"event_count": 1, "memory_count": 2}}

    monkeypatch.setattr(ai_console.agent, "answer", fake_answer)

    ai_console._run_agent_question_auto("내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘")

    assert seen["surface"] == "portfolio"
    assert fake_state["agent_auto_surface"] == "portfolio"
    msgs = fake_state[ai_console._chat_key(ai_console._AUTO_CHAT)]
    assert msgs[-1]["content"] == "auto answer"
    assert "포트폴리오" in msgs[-1]["meta"]


def test_ai_console_surface_pin_overrides_inference(monkeypatch):
    """맥락 고정(pin) 시 추론을 건너뛰고 고정 surface 로 실행."""
    from dashboard.pages import ai_console

    fake_state = {"agent_surface_pin": "paper"}
    monkeypatch.setattr(ai_console.st, "session_state", fake_state)
    seen = {}
    monkeypatch.setattr(ai_console.agent, "answer", lambda q, s: (seen.setdefault("surface", s),
                                                                  {"ok": True, "answer": "x", "context": {}})[1])

    ai_console._run_agent_question_auto("오늘 시장 분위기 요약해줘")

    assert seen["surface"] == "paper"


def test_watchlist_page_renders_rows_and_navigates_on_click():
    """관심종목 페이지 — 테이블 렌더 + 행 클릭 시 종목분석 이동(2026-07-29)."""
    script = _STUBS + 'from dashboard.pages import watchlist\nwatchlist.render()\n'
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert len(at.dataframe) >= 1
    df = next(item.value for item in at.dataframe if "티커" in item.value.columns)
    assert "AAPL" in df["티커"].values
    assert "Apple Inc" in df["종목"].values


def test_watchlist_page_renders_institution_hub_and_rows():
    script = _STUBS + '''
cached.institution_watch = lambda keys=None: {
    "institutions": [
        {"key": "berkshire", "display_name": "Berkshire Hathaway", "source_kind": "13f",
         "category": "holding_company", "freshness": "fresh", "holdings_count": 27,
         "primary_sources": ["13f"], "availability_flags": {"cash_ratio": "unavailable",
         "options_exposure": "unavailable"}},
        {"key": "bridgewater", "display_name": "Bridgewater", "source_kind": "13f",
         "category": "hedge_fund", "freshness": "fresh", "holdings_count": 120,
         "primary_sources": ["13f"], "availability_flags": {"cash_ratio": "unavailable",
         "options_exposure": "available"}},
    ],
    "comparison": {
        "rows": [
            {"display_name": "Berkshire Hathaway", "category": "holding_company", "source_kind": "13f", "freshness": "fresh",
             "holdings_count": 27, "portfolio_concentration": None,
             "portfolio_concentration_flag": "unavailable", "cash_ratio": None,
             "cash_ratio_flag": "unavailable", "options_exposure": None,
             "options_exposure_flag": "unavailable", "reported_return": None,
             "reported_return_flag": "unavailable", "return_proxy": 0.12,
             "return_proxy_flag": "available", "primary_sources": ["13f"]},
            {"display_name": "Bridgewater", "category": "hedge_fund", "source_kind": "13f", "freshness": "proxy",
             "holdings_count": 120, "portfolio_concentration": 0.33,
             "portfolio_concentration_flag": "available", "cash_ratio": 0.05,
             "cash_ratio_flag": "proxy", "options_exposure": 0.14,
             "options_exposure_flag": "available", "reported_return": 0.08,
             "reported_return_flag": "proxy", "return_proxy": 0.1,
             "return_proxy_flag": "available", "primary_sources": ["13f"]},
        ],
        "selected_keys": ["berkshire", "bridgewater"],
    },
    "analysis": {"shared_moves": ["현금 비중 확대"], "divergences": ["옵션 사용 여부 차이"], "confidence": 0.8,
                 "summary": "LLM 요약", "mode": "llm"},
}
from dashboard.pages import watchlist
watchlist.render()
'''
    at = AppTest.from_string(script, default_timeout=30).run()

    assert any("Berkshire Hathaway" in item.value for item in at.markdown)
    assert any("LLM 공통 패턴 요약" in item.value for item in at.markdown)
    assert any("LLM 요약" in item.value for item in at.markdown)
    assert any("관심종목" in item.value for item in at.title)
    comparison = next(item.value for item in at.dataframe if "현금 비중" in item.value.columns)
    assert comparison.loc[0, "현금 비중"] == "— (비공개)"
    assert comparison.loc[1, "현금 비중"] == "5.0% (프록시)"


def test_watchlist_page_filters_selected_institutions():
    script = _STUBS + '''
import streamlit as st
st.session_state["_institution_watch_keys"] = ["berkshire"]
st.session_state["_inst_calls"] = []

def _hub(keys=None):
    st.session_state["_inst_calls"].append(keys)
    all_rows = [
        {"key": "berkshire", "display_name": "Berkshire Hathaway", "source_kind": "13f",
         "category": "holding_company", "freshness": "fresh", "holdings_count": 27,
         "primary_sources": ["13f"], "availability_flags": {"cash_ratio": "unavailable",
         "options_exposure": "unavailable"}},
        {"key": "bridgewater", "display_name": "Bridgewater", "source_kind": "13f",
         "category": "hedge_fund", "freshness": "fresh", "holdings_count": 120,
         "primary_sources": ["13f"], "availability_flags": {"cash_ratio": "unavailable",
         "options_exposure": "available"}},
    ]
    if keys:
        wanted = set(keys)
        all_rows = [row for row in all_rows if row["key"] in wanted]
    return {
        "institutions": all_rows,
        "comparison": {
            "rows": [
                {"display_name": row["display_name"], "category": row["category"], "source_kind": row["source_kind"],
                 "freshness": row["freshness"], "holdings_count": row["holdings_count"],
                 "portfolio_concentration": None, "portfolio_concentration_flag": "unavailable",
                 "cash_ratio": None, "cash_ratio_flag": "unavailable",
                 "options_exposure": None, "options_exposure_flag": "unavailable",
                 "reported_return": None, "reported_return_flag": "unavailable",
                 "return_proxy": None, "return_proxy_flag": "unavailable",
                 "primary_sources": row["primary_sources"]}
                for row in all_rows
            ],
            "selected_keys": [row["key"] for row in all_rows],
        },
        "analysis": {"shared_moves": [], "divergences": [], "confidence": 0.0, "summary": "LLM 요약", "mode": "heuristic"},
    }

cached.institution_watch = _hub
data.load_watchlist = lambda *a, **k: []
from dashboard.pages import watchlist
watchlist.render()
'''
    at = AppTest.from_string(script, default_timeout=30).run()

    assert at.session_state["_inst_calls"][-1] == ("berkshire",)
    body = " ".join(str(item.value) for item in at.markdown)
    assert "Berkshire Hathaway" in body
    assert "Bridgewater" not in body


def test_watchlist_page_clicks_through_to_ticker_detail(monkeypatch):
    import streamlit as _st
    monkeypatch.setattr(_st, "dataframe", _st.dataframe)  # 스크립트 내부 st.dataframe 오버라이드를 테스트 종료 시 복원
    script = _STUBS + '''
import streamlit as st
from types import SimpleNamespace

cached.institution_watch = lambda keys=None: {
    "institutions": [],
    "comparison": {"rows": [], "selected_keys": []},
    "analysis": {"shared_moves": [], "divergences": [], "confidence": 0.0, "summary": "LLM 요약"},
}
data.load_watchlist = lambda *a, **k: [
    {"ticker": "AAPL", "name": "Apple Inc", "reason": "기관 편입", "added_at": "2026-07-01T00:00:00"},
]
st.session_state["_ticker_page"] = "ticker-page"
st.session_state["_switch_calls"] = []

class _Event:
    def __init__(self, rows):
        self.selection = SimpleNamespace(rows=rows)

def fake_dataframe(df, *args, **kwargs):
    if "티커" in getattr(df, "columns", []):
        return _Event([0])
    return _Event([])

st.dataframe = fake_dataframe
st.switch_page = lambda page: st.session_state["_switch_calls"].append(page)
from dashboard.pages import watchlist
watchlist.render()
'''
    at = AppTest.from_string(script, default_timeout=30).run()

    assert at.session_state["ticker"] == "AAPL"
    assert at.session_state["_switch_calls"] == ["ticker-page"]


def test_watchlist_page_uses_generic_label_for_fallback_analysis():
    script = _STUBS + '''
cached.institution_watch = lambda keys=None: {
    "institutions": [],
    "comparison": {"rows": [], "selected_keys": []},
    "analysis": {"shared_moves": [], "divergences": [], "confidence": 0.0,
                 "summary": "휴리스틱 요약", "mode": "heuristic"},
}
data.load_watchlist = lambda *a, **k: []
from dashboard.pages import watchlist
watchlist.render()
'''
    at = AppTest.from_string(script, default_timeout=30).run()

    assert any("공통 패턴 요약" in item.value for item in at.markdown)
    assert not any("LLM 공통 패턴 요약" in item.value for item in at.markdown)


def test_institution_watch_summary_uses_llm_prompt(monkeypatch):
    from agent_console import agent
    from dashboard import views
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "list_institutions", lambda: [
        {"key": "berkshire", "display_name": "Berkshire Hathaway"},
        {"key": "bridgewater", "display_name": "Bridgewater"},
    ])
    monkeypatch.setattr(iw, "latest_snapshot", lambda key: {
        "institution_key": key,
        "display_name": key.title(),
        "source_kind": "13f",
        "freshness": "fresh",
        "holdings_count": 2,
        "top_holdings": [{"ticker": "AAPL", "issuer": "APPLE", "weight_pct": 12.0, "value_usd": 100.0}],
        "portfolio_concentration": 0.4,
        "cash_ratio": None,
        "options_exposure": None,
        "reported_return": None,
        "return_proxy": 12.0,
        "availability_flags": {"cash_ratio": "unavailable", "options_exposure": "unavailable"},
        "notes": [],
    })
    monkeypatch.setattr(iw, "compare_institutions", lambda keys, snapshots=None: {
        "selected_keys": keys,
        "rows": [
            {"display_name": "Berkshire Hathaway", "source_kind": "13f", "freshness": "fresh",
             "holdings_count": 2, "portfolio_concentration": 0.4, "portfolio_concentration_flag": "available",
             "cash_ratio": None, "cash_ratio_flag": "unavailable", "options_exposure": None,
             "options_exposure_flag": "unavailable", "reported_return": None,
             "reported_return_flag": "unavailable", "return_proxy": 12.0, "return_proxy_flag": "available"},
            {"display_name": "Bridgewater", "source_kind": "13f", "freshness": "fresh",
             "holdings_count": 2, "portfolio_concentration": 0.4, "portfolio_concentration_flag": "available",
             "cash_ratio": None, "cash_ratio_flag": "unavailable", "options_exposure": None,
             "options_exposure_flag": "unavailable", "reported_return": None,
             "reported_return_flag": "unavailable", "return_proxy": 12.0, "return_proxy_flag": "available"},
        ],
    })
    monkeypatch.setattr(agent, "_try_llm_prompt", lambda prompt, runner=None, max_timeout=None: (
        '{"summary":"LLM 공통 패턴 요약","shared_moves":["현금 비중 확대"],'
        '"divergences":["옵션 사용 여부 차이"],"confidence":0.73}'
    ))

    result = views.institution_watch_summary()

    assert result["selected_keys"] == ["berkshire", "bridgewater"]
    assert result["analysis"]["summary"] == "LLM 공통 패턴 요약"
    assert result["analysis"]["shared_moves"] == ["현금 비중 확대"]
    assert result["analysis"]["divergences"] == ["옵션 사용 여부 차이"]
    assert result["analysis"]["confidence"] == 0.73


def test_watchlist_page_handles_empty_institution_hub_gracefully():
    script = _STUBS + '''
cached.institution_watch = lambda keys=None: {
    "institutions": [],
    "comparison": {"rows": [], "selected_keys": []},
    "analysis": {"shared_moves": [], "divergences": [], "confidence": 0.0},
}
from dashboard.pages import watchlist
watchlist.render()
'''
    at = AppTest.from_string(script, default_timeout=30).run()

    assert not at.exception, str(at.exception)
    info_body = " ".join(str(getattr(item, "value", "")) for item in at.info)
    assert "기관 허브에 표시할 스냅샷이 아직 없습니다." in info_body


def test_watchlist_page_empty_shows_message():
    script = (_STUBS + 'data.load_watchlist = lambda *a, **k: []\n'
              'from dashboard.pages import watchlist\nwatchlist.render()\n')
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(getattr(el, "value", "")) for el in at.info) + \
        " ".join(str(getattr(el, "value", "")) for el in at.markdown)
    assert "관심종목" in body


def _canvas_state(**overrides):
    base = {"buy_rsi": 30, "sell_rsi": 70, "max_loss": 8.0, "hypothesis": "", "allocations": []}
    base.update(overrides)
    return base


def test_heuristic_canvas_patch_rsi_pair():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("RSI를 25/75로 바꿔줘", _canvas_state(), "")

    assert patch == {"buy_rsi": 25, "sell_rsi": 75}


def test_heuristic_canvas_patch_max_loss():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("손실한도를 5%로 낮춰줘", _canvas_state(), "")

    assert patch == {"max_loss": 5.0}


def test_heuristic_canvas_patch_hypothesis():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch(
        "가설을 지금 답변대로 바꿔줘",
        _canvas_state(),
        "변동성 급등 구간에서 유리하고 금리 급락 시 꺼야 한다.",
    )

    assert patch == {"hypothesis": "변동성 급등 구간에서 유리하고 금리 급락 시 꺼야 한다."}


def test_heuristic_canvas_patch_no_match_returns_empty():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("오늘 시장 어때?", _canvas_state(), "")

    assert patch == {}


def test_heuristic_canvas_patch_allocation_add():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}])
    patch = ai_console._heuristic_canvas_patch("TLT 10%로 추가해줘", current, "")

    assert patch["allocations"] == [
        {"symbol": "QQQ", "weight_pct": 90.0, "note": "core"},
        {"symbol": "TLT", "weight_pct": 10.0, "note": ""},
    ]


def test_heuristic_canvas_patch_allocation_remove():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[
        {"symbol": "QQQ", "weight_pct": 80.0, "note": "core"},
        {"symbol": "TLT", "weight_pct": 20.0, "note": "hedge"},
    ])
    patch = ai_console._heuristic_canvas_patch("TLT 빼줘", current, "")

    assert patch["allocations"] == [{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}]


def test_heuristic_canvas_patch_allocation_unresolvable_name_is_skipped():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}])
    patch = ai_console._heuristic_canvas_patch("아무개코인 10%로 추가해줘", current, "")

    assert "allocations" not in patch


def test_heuristic_canvas_patch_allocation_update_preserves_note():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[
        {"symbol": "QQQ", "weight_pct": 60.0, "note": "핵심 성장"},
        {"symbol": "TLT", "weight_pct": 40.0, "note": "hedge"},
    ])
    patch = ai_console._heuristic_canvas_patch("QQQ 30%로 줄여줘", current, "")

    assert patch["allocations"] == [
        {"symbol": "TLT", "weight_pct": 70.0, "note": "hedge"},
        {"symbol": "QQQ", "weight_pct": 30.0, "note": "핵심 성장"},
    ]


def test_heuristic_canvas_patch_allocation_generic_noun_is_skipped():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}])
    patch = ai_console._heuristic_canvas_patch("현금 비중 20%로 늘려줘", current, "")

    assert "allocations" not in patch


def test_allocations_to_text_round_trips_through_parse_allocations():
    from dashboard.pages import ai_console

    rows = [{"symbol": "QQQ", "weight_pct": 90.0, "note": "core"}, {"symbol": "TLT", "weight_pct": 10.0, "note": ""}]
    text = ai_console._allocations_to_text(rows)
    parsed = ai_console._normalize_allocations(ai_console._parse_allocations(text))

    assert [r["symbol"] for r in parsed] == ["QQQ", "TLT"]
    assert [r["weight_pct"] for r in parsed] == [90.0, 10.0]


def test_ai_console_canvas_chat_propose_and_apply(monkeypatch):
    from agent_console import agent
    from dashboard.pages import ai_console

    monkeypatch.setattr(
        agent,
        "answer",
        lambda *a, **k: {"ok": True, "answer": "RSI를 조정하면 진입이 더 보수적으로 바뀝니다.", "context": {"engine": "test"}},
    )

    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    chat = at.chat_input(key="strategy_canvas_chat_input")
    chat.set_value("RSI를 25/75로 바꿔줘").run()
    assert not at.exception, str(at.exception)

    diff_frames = [df.value for df in at.dataframe if "필드" in df.value.columns]
    assert diff_frames, "제안된 변경 표가 렌더되지 않음"
    assert "매수 RSI" in diff_frames[0]["필드"].tolist()

    apply_button = next(b for b in at.button if b.key == "strategy_canvas_apply_patch")
    apply_button.click().run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    assert int(rsi_input.value) == 25


def test_ai_console_canvas_chat_no_match_shows_no_diff(monkeypatch):
    from agent_console import agent
    from dashboard.pages import ai_console

    monkeypatch.setattr(
        agent,
        "answer",
        lambda *a, **k: {"ok": True, "answer": "오늘은 특별한 이벤트가 없습니다.", "context": {"engine": "test"}},
    )

    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()

    chat = at.chat_input(key="strategy_canvas_chat_input")
    chat.set_value("오늘 시장 어때?").run()
    assert not at.exception, str(at.exception)

    apply_buttons = [b for b in at.button if b.key == "strategy_canvas_apply_patch"]
    assert not apply_buttons, "패치가 없는데 적용 버튼이 렌더됨"
