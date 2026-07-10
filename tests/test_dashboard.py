"""tests/test_dashboard.py — 퀀트 터미널 데이터·인증 순수로직 (무네트워크·무 streamlit)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import auth, data


@pytest.fixture(autouse=True)
def _no_realtime_overlay(monkeypatch):
    """데이터 테스트 결정성 보장 — 실시간 오버레이 차단.

    load_holdings 는 providers.market_data._realtime_current 로 실시간가를 덧씌운다. 다른 테스트가
    load_dotenv 로 REALTIME_ENABLED 를 os.environ 에 흘리면(테스트 순서 의존) 라이브 캐시가 tmp
    스냅샷 기대값을 덮어 스윕에서만 실패한다. 이 seam 을 None 으로 고정해 결정적으로 만든다.
    """
    try:
        import providers.market_data as _md
        monkeypatch.setattr(_md, "_realtime_current", lambda *a, **k: None, raising=False)
    except Exception:
        pass


def test_portfolio_summary(tmp_path):
    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({"overseas_general": {"holdings_usd": [
        {"ticker": "MSFT", "value_usd": 240, "cost_usd": 200, "shares": 2, "return_pct": 20},
        {"ticker": "SGOV", "value_usd": 100, "cost_usd": 100, "shares": 1, "return_pct": 0},
    ]}}), encoding="utf-8")
    s = data.portfolio_summary(str(snap))
    assert s["n_holdings"] == 2
    assert abs(s["total_usd"] - 340) < 1e-9
    assert abs(s["return_pct"] - (340 / 300 - 1) * 100) < 1e-6


def test_holding_position(tmp_path):
    """보유 포지션 조회 — 평단·주수·손익 (J2 · 해외 general)."""
    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({"overseas_general": {"holdings_usd": [
        {"ticker": "NVDA", "shares": 2.7875, "avg_price_usd": 190.29, "value_usd": 536.7,
         "cost_usd": 530.4, "return_pct": 1.18},
    ]}}), encoding="utf-8")
    p = data.holding_position("NVDA", str(snap))
    assert p and abs(p["avg_price_usd"] - 190.29) < 1e-6 and abs(p["shares"] - 2.7875) < 1e-6
    assert data.holding_position("ZZZZ", str(snap)) is None   # 비보유 → None


def test_trade_events_reads_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "stock_report.db"))
    import store
    store._initialized.clear()
    from lib import trade_events

    trade_events.record_trade(
        ticker="MSFT", side="buy", qty=1, price=420,
        account="manual", source="manual_holding", timestamp="2026-07-07T10:00:00",
        event_id="dash-1")
    rows = data.trade_events("MSFT")
    assert len(rows) == 1
    assert rows[0]["event_id"] == "dash-1"


def test_portfolio_merges_general_and_fractional(tmp_path):
    """Q1: general(holdings_usd) + fractional(holdings) 티커별 합산 — 과소계상·중복행 방지."""
    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({
        "overseas_general": {"holdings_usd": [
            {"ticker": "NVDA", "shares": 2.0, "value_usd": 400, "cost_usd": 380, "avg_price_usd": 190},
            {"ticker": "MSFT", "shares": 1.0, "value_usd": 400, "cost_usd": 400}]},
        # fractional 은 실제 키가 'holdings' (general 은 'holdings_usd') — 같은 티커 별도 lot
        "overseas_fractional": {"holdings": [
            {"ticker": "NVDA", "shares": 0.5, "value_usd": 100, "cost_usd": 95}]},
    }), encoding="utf-8")
    s = data.portfolio_summary(str(snap))
    assert s["n_holdings"] == 2                          # NVDA 중복 아님(합산)
    assert abs(s["total_usd"] - 900) < 1e-6              # 400 + 400 + 100 (fractional 포함)
    rows = {r["ticker"]: r for r in data.load_holdings(str(snap))}
    assert abs(rows["NVDA"]["shares"] - 2.5) < 1e-9      # 2.0 + 0.5 합산
    assert abs(rows["NVDA"]["value"] - 500) < 1e-6       # 400 + 100


def test_portfolio_weights_sum_to_one(tmp_path):
    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({"overseas_general": {"holdings_usd": [
        {"ticker": "MSFT", "value_usd": 300}, {"ticker": "SGOV", "value_usd": 100},
    ]}}), encoding="utf-8")
    w = data.portfolio_weights(str(snap))
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert abs(w["MSFT"] - 0.75) < 1e-9


def test_phase_badge(tmp_path):
    sp = tmp_path / "barbell_state.json"
    sp.write_text(json.dumps({"market_type": "bear", "phase_key": "1", "drawdown_pct": -5.2}),
                  encoding="utf-8")
    b = data.phase_badge(str(sp))
    assert b["dca"] == 1.5
    assert "조정" in b["label"]
    assert abs(b["drawdown"] + 5.2) < 1e-9


def test_phase_badge_missing_file_graceful():
    b = data.phase_badge("/nonexistent/barbell_state.json")
    assert b["dca"] == 1.0 and b["label"] == "—"


def test_portfolio_summary_pnl(tmp_path):
    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({"overseas_general": {"holdings_usd": [
        {"ticker": "MSFT", "value_usd": 240, "cost_usd": 200},
    ]}}), encoding="utf-8")
    s = data.portfolio_summary(str(snap))
    assert abs(s["pnl_usd"] - 40) < 1e-9 and abs(s["cost_usd"] - 200) < 1e-9


# ── 기술 신호 (게이지용) ─────────────────────────────────────────────────────
def test_technical_score_uptrend():
    import pandas as pd
    r = data.technical_score(pd.Series(range(1, 80)))
    assert r and r["score"] > 0.5 and r["rsi"] > 60


def test_technical_score_downtrend():
    import pandas as pd
    r = data.technical_score(pd.Series(range(80, 1, -1)))
    assert r and r["score"] < -0.3


def test_technical_score_short_none():
    import pandas as pd
    assert data.technical_score(pd.Series([1, 2, 3])) is None


def test_company_analysis_summary_positive_case():
    s = data.company_analysis_summary(
        {"roe": 0.22, "per": 18.0, "pbr": 2.5, "eps_ttm": 3200, "market_type": "kr"},
        {"rev_yoy": 0.12, "net_margin": 0.18, "debt_to_assets": 0.28},
        {"upside_pct": 18.0},
    )
    assert s["verdict"] == "양호"
    assert any("ROE" in x for x in s["positives"])
    assert any("매출 성장" in x for x in s["positives"])
    assert "특이 위험 제한적" in s["risks"]
    assert any("DART" in x for x in s["checks"])


def test_company_analysis_summary_risk_case():
    s = data.company_analysis_summary(
        {"roe": 0.04, "per": 55.0, "pbr": 6.2, "eps_ttm": -120, "per_status": "loss"},
        {"rev_yoy": -0.08, "net_margin": 0.02, "net_margin_chg": -0.05, "debt_to_assets": 0.82},
        {"upside_pct": -24.0},
    )
    assert s["verdict"] == "주의 우선"
    assert any("적자" in x for x in s["risks"])
    assert any("PER" in x for x in s["risks"])
    assert any("매출 역성장" in x for x in s["risks"])
    assert any("부채/자산" in x for x in s["risks"])


def test_rsi_none_on_short():
    import pandas as pd
    assert data.rsi(pd.Series([1, 2, 3])) is None


def test_verify_password():
    assert auth.verify_password("abc", "abc") is True
    assert auth.verify_password("abc", "xyz") is False
    assert auth.verify_password("abc", None) is False     # fail-closed (미설정)
    assert auth.verify_password("", "") is False


# ── 포맷터 (스케일 명시 — 부호/스케일 버그 차단) ─────────────────────────────
def test_formatters_scale():
    assert data.f_ratio(22.227) == "22.2"
    assert data.f_frac_pct(0.3401) == "34.0%"        # 분수 → %
    assert data.f_frac_pct_s(0.102) == "+10.2%"
    assert data.f_pct(0.98, 2) == "0.98%"            # 이미 % (div_yield)
    assert data.f_pct_s(50.4) == "+50.4%"            # 이미 % (target_upside)
    assert data.f_usd(16.78) == "$16.78"


def test_formatters_none_nan_safe():
    for f in (data.f_ratio, data.f_frac_pct, data.f_frac_pct_s, data.f_pct, data.f_pct_s, data.f_usd):
        assert f(None) == "—"
        assert f(float("nan")) == "—"
        assert f("n/a") == "—"


def test_fair_value_multiple_uses_current_multiple_on_forward_eps():
    fv = data.fair_value_multiple(100, 25, 20, eps_fwd=5)
    assert fv["fair"] == pytest.approx(125.0)
    assert fv["upside_pct"] == pytest.approx(25.0)
    assert fv["eps_fwd"] == pytest.approx(5.0)
    assert fv["per"] == 25
    assert fv["fper"] == 20
    assert fv["source"] == "eps_fwd"


def test_fair_value_multiple_falls_back_to_implied_forward_eps():
    fv = data.fair_value_multiple(100, 25, 20)
    assert fv["fair"] == pytest.approx(125.0)
    assert fv["eps_fwd"] == pytest.approx(5.0)
    assert fv["source"] == "implied_fper"


def test_fair_value_multiple_can_use_forward_eps_without_fper():
    fv = data.fair_value_multiple(100, 25, None, eps_fwd=5)
    assert fv["fair"] == pytest.approx(125.0)
    assert fv["fper"] is None


def test_fair_value_multiple_rejects_invalid_inputs():
    assert data.fair_value_multiple(None, 25, 20) is None
    assert data.fair_value_multiple(100, 0, 20) is None
    assert data.fair_value_multiple(100, 25, -1) is None
    assert data.fair_value_multiple(100, 250, 20) is None   # extreme ratio: likely bad data
    assert data.fair_value_multiple(100, 1, 20) is None


# ── views 배선 (provider monkeypatch — 무네트워크) ───────────────────────────
def test_views_strip_html():
    from dashboard import views
    assert views._strip_html("<b>x</b>\n<pre>y</pre>") == "x\ny"


def test_views_valuation_assembles(monkeypatch):
    from dashboard import views
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics", lambda t: {"per": 20})
    monkeypatch.setattr(earnings_data, "consensus", lambda t: {"n_analysts": 5})
    monkeypatch.setattr(earnings_data, "earnings_history", lambda t, limit=8: [{"date": "x"}])
    v = views.valuation("MSFT")
    assert v["metrics"]["per"] == 20
    assert v["consensus"]["n_analysts"] == 5
    assert v["history"]


def test_views_valuation_error_isolated(monkeypatch):
    """한 provider 실패가 다른 섹션을 깨지 않음(graceful)."""
    from dashboard import views
    from providers import earnings_data

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(earnings_data, "valuation_metrics", boom)
    monkeypatch.setattr(earnings_data, "consensus", lambda t: {"n_analysts": 5})
    monkeypatch.setattr(earnings_data, "earnings_history", lambda t, limit=8: [])
    v = views.valuation("MSFT")
    assert "metrics_error" in v
    assert v["consensus"]["n_analysts"] == 5


def test_views_financials_routes_kr_to_dart(monkeypatch):
    from dashboard import views
    from providers import edgar, kr_fundamentals

    monkeypatch.setattr(kr_fundamentals, "financial_trends",
                        lambda t: {"market_type": "kr", "source": "DART", "trends": {"n_years": 3}})
    monkeypatch.setattr(edgar, "fundamental_trends",
                        lambda t: (_ for _ in ()).throw(AssertionError("EDGAR should not be called")))

    f = views.financials("005930.KS")

    assert f["market_type"] == "kr"
    assert f["source"] == "DART"
    assert f["trends"]["n_years"] == 3


def test_views_financials_routes_us_to_edgar(monkeypatch):
    from dashboard import views
    from providers import edgar

    monkeypatch.setattr(edgar, "fundamental_trends", lambda t: {"rev_yoy": 0.2, "n_years": 4})

    f = views.financials("MSFT")

    assert f["trends"]["rev_yoy"] == 0.2


def test_views_risk_no_weights():
    from dashboard import views
    assert "보유 데이터 없음" in views.risk_report_text({})


def test_views_screener_assembles(monkeypatch):
    import pandas as pd
    from dashboard import views
    from ml import ranker

    class _R:
        oos_ic, oos_icir, oos_top_decile_ret, train_end_date = 0.01, 0.1, 0.02, "2026-04-14"

    monkeypatch.setattr(ranker, "rank_today",
                        lambda mode="nasdaq100", top_n=20: pd.DataFrame([{"rank": 1, "ticker": "MDLZ", "score": 2.1}]))
    monkeypatch.setattr(ranker, "load_ranker", lambda: _R())
    out = views.screener(20)
    assert out["rows"][0]["ticker"] == "MDLZ"
    assert out["meta"]["train_end"] == "2026-04-14"


def test_views_screener_graceful(monkeypatch):
    from dashboard import views
    from ml import ranker

    def boom(**k):
        raise RuntimeError("net")

    monkeypatch.setattr(ranker, "rank_today", boom)
    out = views.screener(20)
    assert out["rows"] == [] and "error" in out


def test_views_backtest_graceful(monkeypatch):
    from dashboard import views
    from ml import data_pipeline

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(data_pipeline, "build_real_sweetspot_data", boom)
    out = views.backtest_summary()
    assert "error" in out


# ── M2 S&P500 시장 맵 데이터 조립 (yf 배치 monkeypatch·무네트워크) ─────────
def test_views_sp500_heatmap_assembles(monkeypatch):
    import pandas as pd
    import yfinance as yf
    from dashboard import views
    idx = pd.date_range("2025-01-01", periods=2)
    cols = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]])
    df = pd.DataFrame(1.0, index=idx, columns=cols)
    df[("AAPL", "Close")] = [100.0, 102.0]   # +2%
    df[("MSFT", "Close")] = [200.0, 194.0]   # -3%
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)
    got = {r["ticker"]: r for r in views._sp500_heatmap_live()}   # 라이브 조립(스냅샷 우회)
    assert "AAPL" in got and "MSFT" in got                     # Close 있는 종목만
    assert abs(got["AAPL"]["pct"] - 2.0) < 0.01
    assert got["AAPL"]["sector_kr"] == "기술" and got["AAPL"]["market_cap"] > 0   # 실제 메타


def test_views_sp500_heatmap_graceful(monkeypatch):
    import yfinance as yf
    from dashboard import views

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(yf, "download", boom)
    assert views._sp500_heatmap_live() == []


def test_views_sp500_heatmap_snapshot_first(monkeypatch, tmp_path):
    """O3: 크론 JSON 스냅샷(<90분) 우선 → 라이브(_sp500_heatmap_live) 미호출·즉시."""
    import json
    from dashboard import views
    snap = tmp_path / "sp500_heatmap.json"
    rows = [{"ticker": "AAPL", "name": "Apple", "sector_kr": "기술", "market_cap": 4e12, "pct": 1.5}]
    snap.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(views, "_HEATMAP_SNAP", str(snap))

    def boom():
        raise AssertionError("라이브가 호출되면 안 됨(스냅샷 우선)")

    monkeypatch.setattr(views, "_sp500_heatmap_live", boom)
    assert views.sp500_heatmap() == rows


# ── O1 시장 지표 (F&G + 지수 RSI·monkeypatch·무네트워크) ─────────────────
def test_views_market_indicators(monkeypatch):
    import pandas as pd
    import yfinance as yf
    from dashboard import views
    from providers import market_data
    monkeypatch.setattr(market_data, "fetch_fear_greed",
                        lambda: {"score": 31.9, "rating": "fear", "prev_week": 26.0, "prev_month": 56.5})
    idx = pd.date_range("2025-01-01", periods=40)
    cols = pd.MultiIndex.from_product([["^GSPC", "^IXIC"], ["Open", "High", "Low", "Close", "Volume"]])
    df = pd.DataFrame(1.0, index=idx, columns=cols)
    df[("^GSPC", "Close")] = range(100, 140)
    df[("^IXIC", "Close")] = range(200, 240)
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)
    mi = views.market_indicators()
    assert mi["fear_greed"]["score"] == 31.9 and mi["fear_greed"]["rating"] == "fear"
    names = {i["name"]: i for i in mi["indices"]}
    assert "S&P 500" in names and "나스닥" in names
    assert names["S&P 500"]["rsi_d"] is not None and names["S&P 500"]["price"] is not None


def test_views_market_indicators_graceful(monkeypatch):
    import yfinance as yf
    from dashboard import views
    from providers import market_data

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(market_data, "fetch_fear_greed", boom)
    monkeypatch.setattr(yf, "download", boom)
    mi = views.market_indicators()
    assert mi["fear_greed"] is None and mi["indices"] == []


def test_views_macro_assets(monkeypatch):
    """매크로 자산 — yf 배치 → 최신가·전일대비·스파크. 부분 실패 스킵·전체 실패 []."""
    import pandas as pd
    import yfinance as yf
    from dashboard import views
    syms = [s[0] for s in views._MACRO_SPECS]
    idx = pd.date_range("2025-06-01", periods=30)
    cols = pd.MultiIndex.from_product([syms, ["Open", "High", "Low", "Close", "Volume"]])
    df = pd.DataFrame(1.0, index=idx, columns=cols)
    df[("KRW=X", "Close")] = [1400.0 + i for i in range(30)]     # 상승
    df[("GC=F", "Close")] = [4100.0 - i for i in range(30)]      # 하락
    df[("BTC-USD", "Close")] = float("nan")                       # 결측 → 스킵
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)
    out = views.macro_assets()
    by = {x["symbol"]: x for x in out}
    assert "KRW=X" in by and by["KRW=X"]["pct"] > 0 and by["KRW=X"]["ticker"] == "KRW=X"
    assert "GC=F" in by and by["GC=F"]["pct"] < 0
    assert "BTC-USD" not in by                                    # 전량 NaN → 스킵
    assert by["KRW=X"]["spark"] and len(by["KRW=X"]["spark"]) <= 30
    assert by["KRW=X"]["unit"] == "₩" and by["GC=F"]["emoji"]

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(yf, "download", boom)
    assert views.macro_assets() == []                            # 전체 실패 graceful


def test_ticker_alerts_crud(tmp_path, monkeypatch):
    """차트→가격 알림 래퍼 — 등록/종목 필터/삭제.

    store DB 는 conftest 가 tmp 격리하지만 **레거시 미러(ALERTS_FILE)는 모듈 상수** —
    리다이렉트 없이는 라이브 price_alerts.json 을 덮어쓴다(CLAUDE.md 테스트 규칙).
    """
    import bot.price_alerts as pa
    from dashboard import data as ddata
    monkeypatch.setattr(pa, "ALERTS_FILE", str(tmp_path / "price_alerts.json"))
    aid = ddata.add_ticker_alert("TSLA", 250.5, "buy", note="지지선")
    assert aid
    ddata.add_ticker_alert("NVDA", 999.0, "sell")
    mine = ddata.ticker_alerts("TSLA")
    assert any(a["id"] == aid and a["price"] == 250.5 and a["type"] == "buy" for a in mine)
    assert all(a["ticker"] == "TSLA" for a in mine)         # 타 종목 미포함
    assert ddata.remove_ticker_alert(aid) is True
    assert not any(a["id"] == aid for a in ddata.ticker_alerts("TSLA"))
    # 불량 입력 graceful
    assert ddata.add_ticker_alert("TSLA", 0, "buy") is None
    assert ddata.remove_ticker_alert("nonexist") is False


def test_price_alerts_cross_process_serialized(tmp_path):
    """봇↔대시보드 **교차 프로세스** 동시 등록 — flock 직렬화로 lost update 0.

    save_collection 은 컬렉션 통째 교체 — 락 없이는 두 프로세스의 load→append→save 가
    겹쳐 알림이 소실된다(대시보드가 신규 writer 가 되며 현실화된 위험). 각 15건×2프로세스
    동시 등록 후 30건 전부 존재해야 통과.
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, STOCK_REPORT_DB=str(tmp_path / "t.db"))
    prog = (
        "import sys, time; sys.path.insert(0, {root!r})\n"
        "import bot.price_alerts as pa\n"
        "pa.ALERTS_FILE = {mirror!r}\n"
        "time.sleep(0.05)\n"                     # 두 프로세스 기동 후 동시 시작
        "for i in range(15):\n"
        "    pa.add_alert('P{tag}N%d' % i, 100.0 + i, 'buy')\n"
    )
    procs = [subprocess.Popen(
        [sys.executable, "-c",
         prog.format(root=root, mirror=str(tmp_path / "mirror.json"), tag=t)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for t in ("A", "B")]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()[-800:]
    # 검증도 동일 env 서브프로세스로 — 부모 store 는 conftest DB 에 바인딩돼 있음
    chk = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {root!r})\n"
         "import bot.price_alerts as pa\n"
         f"pa.ALERTS_FILE = {str(tmp_path / 'mirror.json')!r}\n"
         "print(len(pa.load_alerts()))"],
        env=env, capture_output=True, text=True, timeout=60)
    assert chk.returncode == 0, chk.stderr[-800:]
    n = int(chk.stdout.strip().splitlines()[-1])
    assert n == 30, f"lost update! {n}/30"


def test_is_macro_classification_and_units():
    """매크로 판별 + 단위 단일 소스 (환율에 'USD', 금리에 'USD' 붙는 오표기 방지)."""
    import ticker_names as tn
    for t in ("KRW=X", "GC=F", "BTC-USD", "^TNX", "DX-Y.NYB", "^VIX", "krw=x"):
        assert tn.is_macro(t), t
    for t in ("NVDA", "QQQ", "005930.KS", "", None):
        assert not tn.is_macro(t), t
    assert tn.macro_unit("KRW=X") == "₩" and tn.macro_unit("gc=f") == "$/oz"
    assert tn.macro_unit("^TNX") == "%" and tn.macro_unit("BTC-USD") == "$"
    assert tn.macro_unit("NVDA") == ""                     # 주식 → 빈 문자열
    assert set(tn.MACRO) == set(tn.MACRO_UNITS)            # 멤버십·단위 동기


def test_query_param_ticker_is_whitelisted():
    """`?tk=` 는 앵커 카드가 쓰는 **비신뢰 입력** — 마크업·경로·SQL 류는 전부 None.

    app.py 가 normalize_input 결과만 session_state["ticker"] 에 넣고, 그 값은
    unsafe_allow_html 히어로/사이드바 라벨로 흘러가므로 여기서 차단되어야 한다.
    """
    import ticker_names as tn
    for bad in ('<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "A<B",
                "javascript:alert(1)", "../../etc/passwd", "GC=F<script>", "<b>x</b>"):
        assert tn.normalize_input(bad) is None, bad
    # 정상 매크로 심볼은 통과 (시드 화이트리스트 경유)
    assert tn.normalize_input("GC=F") == "GC=F"
    assert tn.normalize_input("krw=x") == "KRW=X"
    assert tn.normalize_input("^TNX") == "^TNX"


def test_series_profile_and_percentile():
    """성과 프로필(수익률·52주 위치·변동성·200일 이격) + 역사 백분위 — 순수·graceful."""
    import numpy as np
    import pandas as pd
    from dashboard import data as ddata
    idx = pd.date_range("2024-01-01", periods=400, freq="D")
    up = pd.DataFrame({"Close": np.linspace(100.0, 150.0, 400)}, index=idx)
    p = ddata.series_profile(up)
    assert p["r1y"] > 0 and p["ytd"] > 0 and p["last"] == 150.0
    assert p["hi52"] == 150.0 and p["pos52"] == pytest.approx(1.0)
    assert p["vol_ann"] is not None and p["ma200_gap"] > 0     # 상승 → 200일선 위
    assert ddata.history_percentile(up) > 95                   # 최근값이 최고 → 상위 백분위
    dn = pd.DataFrame({"Close": np.linspace(150.0, 100.0, 400)}, index=idx)
    assert ddata.series_profile(dn)["ma200_gap"] < 0
    assert ddata.history_percentile(dn) < 5
    # 짧은 이력·결측 graceful
    assert ddata.series_profile(pd.DataFrame({"Close": [1.0, 2.0]})) is None
    assert ddata.series_profile(None) is None
    assert ddata.history_percentile(pd.DataFrame({"Close": [1.0, 2.0]})) is None
    short = pd.DataFrame({"Close": np.linspace(10.0, 11.0, 20)},
                         index=pd.date_range("2026-06-20", periods=20, freq="D"))
    sp = ddata.series_profile(short)
    # 창을 못 덮는 구간은 None — 20일치로 "1년/3개월/YTD" 라벨을 만들면 허위
    assert sp is not None and sp["r1y"] is None and sp["r3m"] is None
    assert sp["ytd"] is None and sp["ma200_gap"] is None
    assert sp["r1w"] is not None                                # 1주는 덮음
    # tz-aware 인덱스에서도 YTD 계산 (yfinance 미국장 인덱스)
    tzs = pd.DataFrame({"Close": np.linspace(100.0, 110.0, 400)},
                       index=pd.date_range("2025-06-01", periods=400, freq="D",
                                           tz="America/New_York"))
    assert ddata.series_profile(tzs)["ytd"] is not None


def test_macro_correlations(monkeypatch):
    """연관 자산 상관 — 90일 피어슨·30일 등락·미지원 티커 []·yf 실패 graceful []."""
    import numpy as np
    import pandas as pd
    import yfinance as yf
    from dashboard import views
    idx = pd.date_range("2026-01-01", periods=180, freq="D")
    base = pd.Series(np.linspace(100.0, 120.0, 180), index=idx)
    frames = {"GC=F": base, "^TNX": pd.Series(np.linspace(5.0, 4.0, 180), index=idx),  # 역방향
              "DX-Y.NYB": base * 1.01, "SI=F": base * 0.5}
    df = pd.concat({k: pd.DataFrame({"Close": v}) for k, v in frames.items()}, axis=1)
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)
    out = views.macro_correlations("GC=F")
    assert len(out) == 3 and {r["symbol"] for r in out} == {"^TNX", "DX-Y.NYB", "SI=F"}
    tnx = next(r for r in out if r["symbol"] == "^TNX")
    assert tnx["corr90"] is not None and tnx["chg90" if False else "chg30"] is not None
    assert all(r.get("note") for r in out)                       # 관계 설명 필수
    assert views.macro_correlations("NVDA") == []                # 미지원 → 빈 목록

    def _boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(yf, "download", _boom)
    assert views.macro_correlations("GC=F") == []                # graceful


def test_views_chart_news_events(tmp_path, monkeypatch):
    """차트 뉴스 마커 로더 — base 심볼 매칭(.KS 무접미)·필드 추출·파일없음 graceful []."""
    import json as _json
    from dashboard import views
    from providers import news_labels
    rows = [
        {"id": "1", "published_at": "2026-07-01T09:00:00+09:00", "tickers": ["NVDA"],
         "event_type": "실적", "direction": 1, "strength": 4, "title_head": "beat"},
        {"id": "2", "published_at": "2026-07-02T09:00:00+09:00", "tickers": ["005930"],
         "event_type": "규제", "direction": -1, "strength": 3, "title_head": "krx"},
        {"id": "3", "published_at": "", "tickers": ["NVDA"],
         "event_type": "기타", "direction": 0, "strength": 1, "title_head": "no-date"},
    ]
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(news_labels, "LABELS_PATH", p)
    out = views.chart_news_events("NVDA")
    assert len(out) == 1 and out[0]["date"] == "2026-07-01" and out[0]["direction"] == 1
    kr = views.chart_news_events("005930.KS")            # .KS → base 매칭
    assert len(kr) == 1 and kr[0]["event_type"] == "규제"
    monkeypatch.setattr(news_labels, "LABELS_PATH", tmp_path / "none.jsonl")
    assert views.chart_news_events("NVDA") == []          # 파일 없음 graceful


def test_views_ohlc_tf_2h_4h_resample(monkeypatch):
    """2h/4h 커스텀 봉 — yfinance 1h 조회 후 로컬 리샘플 (interval 인자 검증)."""
    import pandas as pd
    import yfinance as yf
    from dashboard import views
    idx = pd.date_range("2026-07-06 08:00", periods=8, freq="h")   # 2h 버킷 경계 정렬
    hourly = pd.DataFrame({"Open": range(100, 108), "High": range(101, 109),
                           "Low": range(99, 107), "Close": range(100, 108),
                           "Volume": [10.0] * 8}, index=idx)
    seen = {}

    class _T:
        def __init__(self, t):
            pass

        def history(self, period=None, interval=None):
            seen["interval"] = interval
            return hourly

    monkeypatch.setattr(yf, "Ticker", _T)
    d2 = views.ohlc_tf("NVDA", "2h")
    assert seen["interval"] == "1h"                       # 2h 는 yf 미지원 → 1h 조회
    assert len(d2) == 4 and float(d2["High"].iloc[0]) == 102.0   # (101,102) max
    assert float(d2["Volume"].iloc[0]) == 20.0            # 합산
    d4 = views.ohlc_tf("NVDA", "4h")
    assert len(d4) == 2 and float(d4["Close"].iloc[0]) == 103.0  # last of 09~12
    d1 = views.ohlc_tf("NVDA", "1h")
    assert seen["interval"] == "1h" and len(d1) == 8      # 1h 는 그대로


def test_macro_symbols_resolve_and_search():
    """매크로 심볼 — 한/영/티커 resolve + 검색 유니버스 포함 + 표시명 (검색 발견성)."""
    import ticker_names as tn
    assert tn.resolve("금") == "GC=F"
    assert tn.resolve("비트코인") == "BTC-USD"
    assert tn.resolve("환율") == "KRW=X"
    assert tn.normalize_input("이더리움") == "ETH-USD"
    for t in ("GC=F", "BTC-USD", "KRW=X", "^TNX", "ETH-USD", "SI=F", "CL=F", "DX-Y.NYB"):
        assert t in tn.universe(), f"{t} not in search universe"
        assert tn.display_name(t, allow_net=False)               # 깔끔한 표시명


# ── P1 자동 모의투자 (원장 조인·스코어카드·요약 조립 — 순수·무네트워크) ─────────
def test_views_join_decisions_matches_outcomes():
    from dashboard import views
    decs = [{"id": "2026-06-02:005930.KS", "date": "2026-06-02", "side": "편입",
             "ticker": "005930.KS", "code": "005930", "qty": 10, "price": 70000.0,
             "policy_score": 0.81, "rationale": {"one_line_reason": "A등급·수급 양호"}, "ok": True},
            {"id": "2026-06-03:AAPL", "date": "2026-06-03", "side": "퇴출",
             "ticker": "AAPL", "qty": 5, "price": 200.0, "policy_score": 0.2,
             "rationale": {"one_line_reason": "타깃이탈"}, "ok": True}]
    outs = [{"decision_id": "2026-06-02:005930.KS", "fwd_excess": 0.021,
             "success": True, "matured_at": "2026-06-20"}]   # KR 은 success 만 (correct 폴백 검증)
    rows = views.join_decisions(decs, outs)
    assert [r["date"] for r in rows] == ["2026-06-03", "2026-06-02"]   # 최신 우선
    kr = rows[1]
    assert kr["reason"] == "A등급·수급 양호" and kr["fwd_excess"] == 0.021
    assert kr["correct"] is True                       # success → correct 폴백
    assert rows[0]["fwd_excess"] is None and rows[0]["correct"] is None   # 미성숙


def test_views_paper_scorecard_hits():
    from dashboard import views
    rows = [{"side": "편입", "correct": True}, {"side": "편입", "correct": False},
            {"side": "증액", "correct": True}, {"side": "퇴출", "correct": True},
            {"side": "편입", "correct": None}]          # 미성숙은 판정 제외
    sc = views.paper_scorecard(rows)
    assert sc["n_buy"] == 3 and abs(sc["buy_hit"] - 66.7) < 0.1
    assert sc["n_sell"] == 1 and sc["sell_hit"] == 100.0


def test_views_paper_summary_assembles(monkeypatch, tmp_path):
    """store 스냅샷 + 원장 + 벤치를 monkeypatch — NAV 폴백·누적·MDD·비용·결정 조인 검증."""
    import store
    from dashboard import views
    from ml import adaptive
    from providers import market_data

    hist = [{"kind": "snapshot", "date": "2026-06-01 15:40", "nav": 10_000_000.0, "cash": 1_000_000.0},
            {"kind": "snapshot", "date": "2026-06-02 15:40", "nav": 10_500_000.0, "cash": 900_000.0},
            {"kind": "cost", "cost": 15000.0, "notional": 12_000_000.0}]
    monkeypatch.setattr(store, "all", lambda name, **k: list(hist))
    import kiwoom_mock
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {"ok": False})   # 잔고 API 불가 → 폴백
    monkeypatch.setattr(market_data, "fetch_kospi_stats",
                        lambda since, symbol="^KS11": {"return_pct": 2.0, "mdd": 0.05})

    led = adaptive.Ledger("kr_mock", base_dir=tmp_path)
    led.log_decision({"date": "2026-06-02", "ticker": "005930.KS", "side": "편입", "qty": 10,
                      "price": 70000.0, "policy_score": 0.81,
                      "rationale": {"one_line_reason": "A등급"}, "ok": True})
    monkeypatch.setattr(adaptive, "Ledger", lambda s: led)

    d = views.paper_summary("kr_mock")
    assert d["balance_ok"] is False
    assert d["nav"] == 10_500_000.0                     # 마지막 스냅샷 폴백
    assert abs(d["cum_ret"] - 5.0) < 1e-6
    assert d["inception_date"] == "2026-06-01"
    assert d["bench_ret"] == 2.0 and abs(d["bench_mdd"] - 5.0) < 1e-9
    assert d["cost"] and d["cost"]["total"] == 15000.0
    assert d["decisions"] and d["decisions"][0]["reason"] == "A등급"
    assert len(d["nav_series"]) == 2


def test_views_paper_summary_graceful_empty(monkeypatch):
    """store·잔고·원장 전부 실패해도 무예외 — 빈 뼈대 반환 (크론 미실행 신규 환경)."""
    import store
    from dashboard import views

    def boom(*a, **k):
        raise RuntimeError("db")

    monkeypatch.setattr(store, "all", boom)
    import kiwoom_mock
    monkeypatch.setattr(kiwoom_mock, "get_balance", boom)
    d = views.paper_summary("kr_mock")
    assert d["nav"] is None and d["positions"] == [] and d["nav_series"] == []


# ── P2 ML 게이트 요약 (파일 read-only·graceful) ───────────────────────────────
def _write_json(p, obj):
    import json
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_views_axes_gate_summary(monkeypatch, tmp_path):
    from datetime import datetime
    from dashboard import views
    bt, sh = tmp_path / "kr_bt.json", tmp_path / "kr_sh.json"
    _write_json(bt, {"asof": "2026-07-04 10:45", "period": "2001~2026",
                     "verdict": {"code": "OBSERVE", "net_excess_cagr": 0.0549},
                     "recommendation": {"chosen": "hi52"},
                     "chosen_history": {"hi52": 6}})
    _write_json(sh, {"asof": datetime.now().strftime("%Y-%m-%d %H:%M"), "chosen": "hi52",
                     "policy_weights": {"w_hi52": 0.35}})
    monkeypatch.setattr(views, "_GATE_FILES",
                        {"kr": (str(bt), str(sh), "ADAPTIVE_KR_AXES_ENABLED"),
                         "us": (str(tmp_path / "none.json"), str(tmp_path / "none2.json"),
                                "ADAPTIVE_US_AXES_ENABLED")})
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    g = views.axes_gate_summary()
    kr = g["kr"]
    assert kr["available"] and kr["verdict"]["code"] == "OBSERVE"
    assert kr["shadow"]["fresh"] and kr["shadow"]["applied"]      # env on + 신선 → 반영 중
    assert g["us"] == {"available": False, "env_on": False}       # 파일 없음 graceful


def test_views_axes_gate_stale_shadow_not_applied(monkeypatch, tmp_path):
    from dashboard import views
    bt, sh = tmp_path / "bt.json", tmp_path / "sh.json"
    _write_json(bt, {"verdict": {"code": "OBSERVE"}})
    _write_json(sh, {"asof": "2026-01-01 00:00", "chosen": "hi52"})   # stale
    monkeypatch.setattr(views, "_GATE_FILES",
                        {"kr": (str(bt), str(sh), "ADAPTIVE_KR_AXES_ENABLED")})
    monkeypatch.setenv("ADAPTIVE_KR_AXES_ENABLED", "true")
    kr = views.axes_gate_summary()["kr"]
    assert kr["shadow"]["fresh"] is False and kr["shadow"]["applied"] is False


def test_views_tier3_gate_status(monkeypatch, tmp_path):
    from datetime import datetime
    from dashboard import views
    p = tmp_path / "t3.json"
    _write_json(p, {"reco_lev": 1.3, "verdict": "GO",
                    "_meta": {"at": datetime.now().strftime("%Y-%m-%d %H:%M")}})
    monkeypatch.setattr(views, "_TIER3_SHADOW", str(p))
    monkeypatch.setenv("US_MOCK_LEV_SLEEVE", "true")
    t3 = views.tier3_gate_status()
    assert t3["available"] and t3["reco_lev"] == 1.3 and t3["fresh"] and t3["sleeve_env"]
    monkeypatch.setattr(views, "_TIER3_SHADOW", str(tmp_path / "none.json"))
    assert views.tier3_gate_status()["available"] is False


def test_views_join_decisions_carries_features():
    from dashboard import views
    decs = [{"id": "d:T", "date": "2026-06-02", "side": "편입", "ticker": "T",
             "features": {"mom12": 0.7, "pead": 0.6}}]
    rows = views.join_decisions(decs, [])
    assert rows[0]["features"] == {"mom12": 0.7, "pead": 0.6}


# ── P3 사이드바 모의 레일 (초경량 글랜스 — store 스냅샷만) ────────────────────
def test_views_paper_glance_from_snapshots(monkeypatch):
    import store
    from dashboard import views
    hists = {"kr_mock_history": [
                 {"kind": "snapshot", "nav": 10_000_000.0},
                 {"kind": "cost", "cost": 1.0},                    # 스냅샷 아님 — 무시
                 {"kind": "snapshot", "nav": 10_500_000.0}],
             "us_mock_history": [{"kind": "snapshot", "nav": 100_000.0}]}
    monkeypatch.setattr(store, "all", lambda name, **k: list(hists.get(name, [])))
    g = views.paper_glance()
    assert [r["surface"] for r in g] == ["kr_mock", "us_mock"]
    kr = g[0]
    assert kr["nav"] == 10_500_000.0 and abs(kr["cum_ret"] - 5.0) < 1e-9
    assert abs(kr["day_ret"] - 5.0) < 1e-9 and kr["n_days"] == 2
    us = g[1]
    assert us["cum_ret"] == 0.0 and us["day_ret"] == 0.0           # 단일 스냅샷


def test_views_paper_glance_empty_and_error(monkeypatch):
    import store
    from dashboard import views
    monkeypatch.setattr(store, "all", lambda name, **k: [])
    assert views.paper_glance() == []                              # 크론 미실행 → 레일 숨김

    def boom(*a, **k):
        raise RuntimeError("db")
    monkeypatch.setattr(store, "all", boom)
    assert views.paper_glance() == []


# ── 수집 뉴스 그룹핑 (시장·캘린더) ────────────────────────────────────────────

def test_views_group_news_sorts_by_importance_then_recency():
    from dashboard import views
    events = [
        {"id": "a", "source": "saveticker", "title": "일반 뉴스 옛것",
         "published_at": "2026-07-05T10:00:00+09:00", "tags": []},
        {"id": "b", "source": "saveticker", "title": "포트 종목 뉴스",
         "published_at": "2026-07-05T09:00:00+09:00", "tags": ["$NVDA"]},
        {"id": "c", "source": "saveticker", "title": "일반 뉴스 최신",
         "published_at": "2026-07-06T10:00:00+09:00", "tags": []},
        {"id": "d", "source": "telegram:chan1", "title": "채널 뉴스",
         "published_at": "2026-07-06T11:00:00+09:00", "tags": []},
    ]
    score = lambda e: (8, "포트") if "$NVDA" in (e.get("tags") or []) else (5, "")
    g = views.group_news(events, score_fn=score)
    assert set(g) == {"saveticker", "telegram"}                  # 채널 접미사 제거 그룹
    sv = g["saveticker"]
    assert [x["title"] for x in sv] == ["포트 종목 뉴스", "일반 뉴스 최신", "일반 뉴스 옛것"]
    assert sv[0]["score"] == 8 and sv[0]["tickers"] == ["NVDA"]


def test_views_group_news_llm_label_boost_and_dedupe():
    from dashboard import views
    events = [
        {"id": "x", "source": "saveticker", "title": "실적 뉴스",
         "published_at": "2026-07-06T10:00:00+09:00", "tags": ["$MSFT"]},
        {"id": "x", "source": "saveticker", "title": "실적 뉴스",       # 중복 id → 1건
         "published_at": "2026-07-06T10:00:00+09:00", "tags": ["$MSFT"]},
    ]
    labels = {"x": {"direction": 1, "strength": 5, "event_type": "실적"}}
    g = views.group_news(events, label_by_id=labels, score_fn=lambda e: (5, ""))
    assert len(g["saveticker"]) == 1
    it = g["saveticker"][0]
    assert it["llm"] == {"direction": 1, "strength": 5, "event_type": "실적"}
    assert it["score"] == 8                                       # max(5, 3+5)


def test_views_group_news_empty_and_graceful():
    from dashboard import views
    assert views.group_news([]) == {}
    g = views.group_news([{"source": None, "title": "  ", "tags": []},
                          {"source": "arca", "title": "글", "published_at": ""}])
    assert list(g) == ["arca"]                                    # 빈 제목 스킵


def test_theme_econ_calendar_html():
    from datetime import date, datetime
    from dashboard import theme
    today = date(2026, 7, 7)                                      # 화요일
    events = [
        {"when": datetime(2026, 7, 8, 21, 30), "title": "CPI 발표", "marker": "🔴", "importance": "high"},
        {"when": datetime(2026, 7, 8, 10, 0), "title": "저중요 <이벤트>", "marker": "🟢", "importance": "low"},
        {"when": None, "title": "날짜 미정", "marker": "⚪", "importance": "info"},
    ]
    html = theme.econ_calendar_html(events, start=today, weeks=2)
    assert html.count('class="ec-head"') == 7 and "월" in html   # 요일 헤더(CSS 셀렉터 제외)
    assert html.count('class="ec-cell') == 14                     # 2주 그리드
    assert "ec-today" in html and "CPI 발표" in html
    assert "&lt;이벤트&gt;" in html                                # HTML escape
    assert "날짜 미정" not in html                                 # when 없는 건 제외
    # 같은 날 중요도순: 🔴 CPI 가 🟢 보다 먼저
    assert html.index("CPI 발표") < html.index("저중요")


def test_theme_econ_calendar_overflow_chip():
    from datetime import date, datetime
    from dashboard import theme
    d = date(2026, 7, 7)
    events = [{"when": datetime(2026, 7, 7, 9 + i), "title": f"이벤트{i}", "marker": "🟡",
               "importance": "medium"} for i in range(6)]
    html = theme.econ_calendar_html(events, start=d, weeks=1)
    assert "+2건 더" in html                                       # 셀당 4개 + 초과 표시


def test_ohlc_tf_resamples_weekly_monthly(monkeypatch):
    """주·월봉 = 일봉 max 리샘플 (추가 네트워크 0) — OHLC 집계 정합."""
    import pandas as pd
    from dashboard import views
    idx = pd.date_range("2026-01-05", periods=10, freq="B")   # 2주 (월~금 ×2)
    daily = pd.DataFrame({"Open": range(10, 20), "High": range(20, 30),
                          "Low": range(1, 11), "Close": range(15, 25),
                          "Volume": [100.0] * 10}, index=idx)
    import providers.market_data as md
    monkeypatch.setattr(md, "_history_cached", lambda t, period="max": daily)
    wk = views.ohlc_tf("TST", "1wk")
    assert len(wk) == 2
    assert wk["Open"].iloc[0] == 10 and wk["Close"].iloc[0] == 19   # 첫 주 first/last
    assert wk["High"].iloc[0] == 24 and wk["Low"].iloc[0] == 1      # 주 내 max/min
    assert wk["Volume"].iloc[0] == 500.0
    mo = views.ohlc_tf("TST", "1mo")
    assert len(mo) == 1 and mo["Volume"].iloc[0] == 1000.0
    assert views.ohlc_tf("TST", "1d") is daily                      # 일봉 = 원본 passthrough


# ── 가치평가 종합 점수 (게이지용 · 순수) ──────────────────────────────────────
def test_valuation_score_undervalued():
    m = {"peg": 0.8, "per": 20.0, "forward_pe": 16.0, "eps_ttm": 5.0, "eps_fwd": 6.5}
    c = {"target_median": 130.0}
    iv = {"rim": {"mid": 125.0}, "upside_pct": 25.0}
    vs = data.valuation_score(100.0, m, c, iv)
    assert vs and vs["score"] > 0.3                    # 저평가 방향
    assert "PEG 0.7" in vs["sub"] and vs["n"] == 5     # 교과서식 20÷30% (야후 0.8 아님)


def test_valuation_score_overvalued_and_insufficient():
    # 자기일관 입력: price=120=eps_ttm×per — 기준가 upside ≈ +2.5%(중립), PEG·목표가가 압도
    m = {"peg": 3.5, "per": 60.0, "forward_pe": 55.0, "eps_ttm": 2.0, "eps_fwd": 2.05}
    vs = data.valuation_score(120.0, m, {"target_median": 96.0}, None)
    assert vs and vs["score"] < -0.3                   # 고평가 방향
    assert data.valuation_score(100.0, {"peg": 1.0}) is None      # 재료 1개 → 생략
    assert data.valuation_score(None, m) is None
    assert data.valuation_score(100.0, {}) is None


def test_screener_drivers():
    """판단근거 화이트리스트 — 중요도 정렬·상위 3·결측 '—' (순수)."""
    feats = {"close_vs_52w_high": 0.97, "mom_126d": 0.42, "rsi_14": 75.0,
             "excess_mom_60d": 0.081, "cmf_21": 0.01}
    s = data.screener_drivers(feats, {"mom_126d": 100, "rsi_14": 90,
                                      "close_vs_52w_high": 10}, top=3)
    parts = s.split(" · ")
    assert len(parts) == 3
    assert parts[0].startswith("6M 모멘텀 +42%")        # 중요도 1위 규칙 먼저
    assert "RSI 75 과열" in s
    assert data.screener_drivers({}, {}) == "—"
    assert data.screener_drivers({"cmf_21": 0.0}, None) == "—"   # 규칙 미발동


def test_peg_textbook_and_eps_growth():
    """교과서식 PEG = PER ÷ EPS 증가율(fwd/ttm) — 야후 PEG 와 구분·성장 ≤0 정직 None."""
    m = {"per": 30.0, "eps_ttm": 5.0, "eps_fwd": 9.7, "peg": 0.6}
    assert data.eps_growth_fwd(m) == pytest.approx(94.0)
    pt = data.peg_textbook(m)
    assert pt["peg"] == pytest.approx(30.0 / 94.0, abs=0.001)   # 0.319 (야후 0.6 과 다름)
    assert pt["yahoo"] == 0.6
    assert data.peg_textbook({"per": 30.0, "eps_ttm": 5.0, "eps_fwd": 4.0}) is None  # 역성장
    assert data.peg_textbook({}) is None
    assert data.eps_growth_fwd({"eps_ttm": 0.0, "eps_fwd": 1.0}) is None


def test_format_screener_features():
    """피처 표시 — 한글 라벨·카테고리·스마트 포맷·중요도 정렬·미등록 폴백 (순수)."""
    feats = {"obv": -79_488_400, "mom_126d": 0.42, "golden_cross": 1.0,
             "sma_200": 57.7986, "vol_63d": 0.2401, "beta_60d": -0.4929,
             "unknown_feat": 1.2345}
    rows = data.format_screener_features(feats, {"mom_126d": 100, "obv": 90})
    by = {r["지표"]: r for r in rows}
    assert rows[0]["지표"] == "6개월 모멘텀" and rows[0]["값"] == "+42.0%"   # 중요도 1위
    assert by["OBV 누적 흐름"]["값"] == "-79.5M" and by["OBV 누적 흐름"]["구분"] == "거래량"
    assert by["골든크로스"]["값"] == "✓"
    assert by["200일 이평"]["값"] == "$57.80"
    assert by["변동성 3개월"]["값"] == "+24.0%"
    assert by["베타 60일"]["값"] == "-0.49"
    assert by["unknown_feat"]["구분"] == "기타"                              # 폴백
    assert data.format_screener_features({}, {}) == []


# ── 포트폴리오 페이지 보강 (P1 · 순수) ────────────────────────────────────────
def _hist_rec():
    return [{"date": "2026-06-30", "total_usd": 9000.0, "total_krw": 13_500_000,
             "exchange_rate": 1500.0, "qqq_price": 690.0},
            {"date": "2026-07-07", "total_usd": 9411.0, "total_krw": 14_239_554,
             "exchange_rate": 1513.0, "qqq_price": 704.9}]


def test_growth_series_normalized():
    g = data.growth_series(_hist_rec())
    assert g["port"][0] == 0.0 and g["qqq"][0] == 0.0            # 첫 기록 = 0%
    assert g["port"][-1] == pytest.approx((9411 / 9000 - 1) * 100)
    assert g["qqq"][-1] == pytest.approx((704.9 / 690 - 1) * 100)
    assert data.growth_series([]) == {} and data.growth_series(_hist_rec()[:1]) == {}


def test_fx_attribution():
    fx = data.fx_attribution(_hist_rec(), days=30)
    assert fx["usd_ret"] == pytest.approx(4.567, abs=0.01)
    assert fx["krw_ret"] == pytest.approx(5.478, abs=0.01)
    # 환율 기여 = (1+₩)/(1+$)−1 ≈ +0.87%p (원화 약세 → 원화 평가 이득)
    assert fx["fx_ret"] == pytest.approx(0.871, abs=0.02)
    assert data.fx_attribution([]) == {}


def test_rebalance_gaps():
    holdings = [{"ticker": "MSFT", "name": "Microsoft", "value": 6000.0},
                {"ticker": "QQQI", "name": "NEOS", "value": 4000.0}]
    rb = data.rebalance_gaps(holdings, {"MSFT": 0.5, "SGOV": 0.1})
    by = {g["ticker"]: g for g in rb["gaps"]}
    assert by["MSFT"]["gap_pp"] == pytest.approx(10.0)           # 60 − 50 → 축소 방향
    assert by["MSFT"]["usd_delta"] == pytest.approx(-1000.0)
    assert by["SGOV"]["gap_pp"] == pytest.approx(-10.0)          # 미보유 목표 → 증액 방향
    assert by["SGOV"]["usd_delta"] == pytest.approx(1000.0)
    assert "QQQI" not in by                                      # 목표 미설정 → 갭 제외
    assert rb["untargeted"] == ["QQQI"]                          # 별도 반환 (안전/인컴 축)
    assert rb["target_sum_pct"] == pytest.approx(60.0)
    assert data.rebalance_gaps(holdings, {}) == {}


def test_exposures_and_asset_class():
    assert data.asset_class_of("SGOV") == "현금성 (초단기 국채)"
    assert data.asset_class_of("QQQI") == "인컴 (커버드콜)"
    assert data.asset_class_of("QQQ") == "지수·팩터 ETF"
    assert data.asset_class_of("SPMO") == "지수·팩터 ETF"       # S&P500 모멘텀 지수
    assert data.asset_class_of("QLD") == "레버리지 ETF (Tier3)"  # 2x — 별도 분류
    assert data.asset_class_of("MSFT") == "개별주"
    ex = data.exposures([{"ticker": "MSFT", "value": 500.0},
                         {"ticker": "SGOV", "value": 500.0}])
    assert ex["class"]["개별주"] == pytest.approx(50.0)
    assert ex["class"]["현금성 (초단기 국채)"] == pytest.approx(50.0)
    assert any("기술" in k or k == "기타·해외" for k in ex["sector"])   # MSFT 섹터 시드
    assert data.exposures([]) == {}


def test_aggregate_index_valuation():
    """지수 밸류 상향 집계 — 시총가중 조화평균·성장·PEG·결측 제외 (순수)."""
    from providers.market_valuation import aggregate_index_valuation
    rows = [{"cap": 3000, "per": 30.0, "fper": 24.0},
            {"cap": 1000, "per": 20.0, "fper": 16.0},
            {"cap": 500, "per": None, "fper": None}]      # 결측 — 커버리지에서 제외
    v = aggregate_index_valuation(rows)
    # 조화평균: Σcap/Σ(cap/PE) = 4000/(100+50) ≈ 26.67
    assert v["per"] == pytest.approx(26.7, abs=0.05)
    assert v["fper"] == pytest.approx(21.3, abs=0.05)     # 4000/(125+62.5)
    assert v["eps_growth_pct"] == pytest.approx(25.0, abs=0.1)   # 이익합 187.5/150
    assert v["peg"] == pytest.approx(26.67 / 25.0, abs=0.01)
    assert v["cov_trailing_pct"] == pytest.approx(4000 / 4500 * 100, abs=0.1)
    assert aggregate_index_valuation([]) == {}


def test_multpl_parsers():
    """multpl 파서 — 현재값·월별 테이블(abbr/&#x2002; 스킵)·역사 백분위 (순수)."""
    from providers.market_valuation import (hist_percentile, parse_multpl_current,
                                            parse_multpl_table)
    cur_html = '<div id="current"><b>Current<span>S&P 500 PE</span>:</b>\n32.28\n</div>'
    assert parse_multpl_current(cur_html) == 32.28
    assert parse_multpl_current("<html></html>") is None
    tbl = ('<tr><td>Jun 1, 2026</td>\n<td>\n<abbr title="Estimate">†</abbr>\n31.93\n</td>'
           '<tr><td>Dec 1, 2024</td>\n<td>\n&#x2002;\n28.60\n</td>'
           '<tr><td>Mar 1, 1871</td>\n<td>\n&#x2002;\n11.52\n</td>')
    rows = parse_multpl_table(tbl)
    assert [v for _, v in rows] == [31.93, 28.60, 11.52]
    assert hist_percentile([10, 20, 30, 40], 32.28) == 75.0
    assert hist_percentile([], 30) is None


def test_market_temperature():
    """시장 온도계 — 역발상 부호·가중 평균·재료 부족 None (순수)."""
    hot = data.market_temperature(fear_greed=85, rsi_w=80, per_pctile_20y=95,
                                  peg=2.5, drawdown_pct=0.0)
    assert hot["score"] < -0.5                        # 과열 → 신중
    cold = data.market_temperature(fear_greed=15, rsi_w=30, per_pctile_20y=30,
                                   peg=0.7, drawdown_pct=-12.0)
    assert cold["score"] > 0.5                        # 공포·저평가 → 기회
    assert "공포탐욕 15" in cold["sub"]
    assert data.market_temperature(fear_greed=50) is None       # 재료 1개


def test_top_feature_bars():
    """핵심 피처 바 — 중요도 순 라벨(한글 · 포맷값)·top 제한 (순수)."""
    feats = {"mom_126d": 0.42, "rsi_14": 62.0, "obv": -38_800_000}
    tb = data.top_feature_bars(feats, {"obv": 100, "mom_126d": 90, "rsi_14": 10}, top=2)
    assert tb["labels"] == ["OBV 누적 흐름 · -38.8M", "6개월 모멘텀 · +42.0%"]
    assert tb["values"] == [100.0, 90.0]
    assert data.top_feature_bars(feats, {}) == {}
    assert data.top_feature_bars({}, {"x": 1}) == {}


def test_rank_badge_and_move():
    """순위 배지(메달)·직전 대비 변동(▲▼〓NEW) — 순수."""
    assert data.rank_badge(1) == "🥇 1" and data.rank_badge(3) == "🥉 3"
    assert data.rank_badge(7) == "7" and data.rank_badge(None) == "—"
    assert data.rank_move(2, 5) == "▲3"                # 5위 → 2위 상승
    assert data.rank_move(6, 4) == "▼2"
    assert data.rank_move(3, 3) == "〓"
    assert data.rank_move(1, None) == "NEW"


def test_entry_levels():
    """진입 레벨 조립 — 아래 지지 근접순 3·위 저항 2·밸류 갭 (순수)."""
    lv = data.entry_levels(
        100.0,
        supports=[("MA60", 96.0), ("MA200", 88.0), ("볼린저 하단", 93.0),
                  ("52주 저점", 70.0), ("MA120", 101.0)],       # 101 은 위 → 제외
        resistances=[("52주 고점", 120.0), ("추세 저항선", 108.0)],
        fairs=[("멀티플 기준가", 117.0), ("목표가 중앙값", 123.0)])
    labs = [x[0] for x in lv["entries"]]
    assert labs[0] == "MA60" and "MA200" in labs           # 근접순 유지 (클러스터 라벨)
    assert lv["entries"][0][2] == pytest.approx(-4.0)
    assert lv["zones"][0]["n"] == 1                        # 1.5% 밖 — 미병합
    assert [x[0] for x in lv["resists"]] == ["추세 저항선", "52주 고점"]
    assert lv["fair_gap_pct"] == pytest.approx(20.0)            # (117+123)/2 = 120
    assert data.entry_levels(0, [], [], []) == {}
    assert data.entry_levels(100.0, [], [], []) == {}           # 재료 전무 — 생략
    # 합류 존 — 1.5% 이내 재료 병합·강도 ×n
    lv2 = data.entry_levels(100.0,
                            supports=[("MA200", 95.0), ("매물대(HVN)", 95.8),
                                      ("추세 지지선", 95.4), ("52주 저점", 80.0)],
                            resistances=[], fairs=[("기준가", 110.0), ("목표가", 112.0)])
    z0 = lv2["zones"][0]
    assert z0["n"] == 3 and z0["lo"] == 95.0 and z0["hi"] == 95.8
    assert "MA200" in z0["labels"] and "매물대(HVN)" in z0["labels"]
    assert lv2["zones"][1]["n"] == 1                       # 52주 저점은 별도 존


def test_twr_series_deposit_not_gain():
    """TWR — 적립(현금 유입)이 수익으로 잡히지 않음 (단순 총액과 대비)."""
    rec = [{"date": "2026-07-01", "total_usd": 1000.0},
           {"date": "2026-07-02", "total_usd": 1500.0},    # +500 적립 (가격 불변)
           {"date": "2026-07-03", "total_usd": 1650.0}]    # +10% 상승
    flows = {"2026-07-02": 500.0}
    tw = data.twr_series(rec, flows)
    assert tw["twr"][1] == pytest.approx(0.0)              # 적립일 수익 0
    assert tw["twr"][2] == pytest.approx(10.0)             # 진짜 수익만
    assert tw["simple"][2] == pytest.approx(65.0)          # 단순 총액은 65% (왜곡)
    assert tw["flows_total"] == 500.0
    assert data.twr_series(rec[:1], flows) == {}


def test_load_kr_holdings(tmp_path):
    """국내북 로더 — domestic 섹션 정규화 (원화 평가·동기화 시각)."""
    import json
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"domestic": {"holdings": [
        {"name": "SOL AI반도체TOP2플러스", "ticker": "SOL", "avg_price": 23683,
         "current_price": 25200, "shares": 20, "pnl_krw": 30210, "return_pct": 6.38}]},
        "last_domestic_sync": "2026-07-08 08:35"}))
    kr = data.load_kr_holdings(str(p))
    assert kr["total"] == 25200 * 20
    assert kr["rows"][0]["ret"] == 6.38
    assert kr["last_sync"].startswith("2026-07-08")
    empty = tmp_path / "e.json"
    empty.write_text("{}")
    assert data.load_kr_holdings(str(empty)) == {}


def test_backtest_persist_roundtrip(tmp_path, monkeypatch):
    """백테스트 결과 디스크 영속 — equity DataFrame 직렬화 왕복."""
    import pandas as pd

    from dashboard import views
    monkeypatch.setattr(views, "_backtest_last_path", lambda: tmp_path / "bt.json")
    assert views.backtest_last() is None              # 파일 없음 → None
    eq = pd.DataFrame({"ml": [1.0, 1.1], "qqq": [1.0, 0.9]})
    views._backtest_persist({"ml": {"cagr": 0.2, "sharpe": 1.1}, "qqq": {"cagr": 0.1},
                             "overlay": {}, "verdict": "채택", "reasons": ["r1"],
                             "equity": eq, "wf": {"ignored": object()}})
    d = views.backtest_last()
    assert d["verdict"] == "채택" and d["asof"] and d["ml"]["cagr"] == 0.2
    assert list(d["equity"].columns) == ["ml", "qqq"] and len(d["equity"]) == 2


def test_valuation_score_kr_no_dart_suppressed():
    """KR 열화모드(DART 미가용·per None) — yfinance garbage 로 게이지 오도 방지 → None."""
    from dashboard import data
    kr_yf = {"market_type": "kr", "per": None, "forward_pe": None, "peg": None,
             "psr": None, "roe": 0.19, "kr_yf_fallback": True}
    # 목표가만 있어도 KR 열화모드면 억제 (야후 KR 목표가도 불신)
    assert data.valuation_score(286250, kr_yf, {"target_mean": 480000}, {}) is None
    # DART 가용(per 존재)이면 정상 채점
    kr_dart = {"market_type": "kr", "per": 12.0, "pbr": 1.1, "roe": 0.12,
               "eps_ttm": 5000, "eps_fwd": 5800}
    got = data.valuation_score(60000, kr_dart, {"target_mean": 72000}, {})
    assert got is not None and -1.0 <= got["score"] <= 1.0


def test_valuation_metrics_kr_suppresses_yf_multiples(monkeypatch):
    """KR yfinance 폴백 — 신뢰불가 멀티플(forward_pe·peg·psr) 폐기 + 폴백 마커.

    밀폐: 다른 테스트가 .env(DART 키)를 로드하면 DART 경로가 성공해 순서 의존 실패
    → DART 를 명시적으로 미가용 처리해 yf 폴백 분기를 고정 검증.
    """
    from providers import earnings_data, kr_fundamentals
    monkeypatch.setattr(kr_fundamentals, "recent_annual_metrics",
                        lambda t, **k: {"confidence": "missing"})
    m = earnings_data.valuation_metrics("005930.KS")
    assert m.get("market_type") == "kr"
    assert m.get("forward_pe") is None and m.get("peg") is None and m.get("psr") is None
    assert m.get("kr_yf_fallback") is True


def test_ohlc_disk_fallback_prevents_blank(tmp_path, monkeypatch):
    """yfinance 실패(빈값) 시 디스크 폴백 → 차트 blank 방지 (마감 후 재방문 케이스)."""
    import pandas as pd

    from dashboard import cached
    monkeypatch.setattr(cached, "_ohlc_disk_path",
                        lambda t, period: tmp_path / f"{t}__{period}.parquet")
    good = pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
                         "Close": [1.0, 2.0], "Volume": [10, 20]},
                        index=pd.to_datetime(["2026-07-07", "2026-07-08"]))
    import providers.market_data as md
    monkeypatch.setattr(md, "_history_cached", lambda t, period="1y": good)
    assert len(cached.ohlc.__wrapped__("005930.KS", "max")) == 2   # 정상 → 디스크 백업
    # 이제 yfinance 실패(빈 DataFrame) → 디스크 폴백이 마지막 정상 데이터 반환
    monkeypatch.setattr(md, "_history_cached", lambda t, period="1y": pd.DataFrame())
    out = cached.ohlc.__wrapped__("005930.KS", "max")
    assert out is not None and not out.empty and len(out) == 2       # blank 아님


def test_valuation_score_kr_trailing_gauge():
    """KR DART 트레일링(PER·PBR·ROE) 게이지 — forward/PEG 없어도 채점(기존엔 상시 공백)."""
    from dashboard import data
    kr = {"market_type": "kr", "per": 12.0, "pbr": 1.0, "roe": 0.15,
          "forward_pe": None, "peg": None, "eps_ttm": 5000, "eps_fwd": None}
    got = data.valuation_score(60000, kr, {}, {})
    assert got is not None and got["n"] >= 2            # PER + P/B·ROE → 게이지 표시
    assert "PER" in got["sub"] and "P/B" in got["sub"]
    # 저평가 방향: PER 12(이익수익률 8.3%)·PBR 1.0 vs 정당 P/B(ROE15%→2.0) → 저평가(+)
    assert got["score"] > 0
    # KR yfinance 목표가(신뢰불가)는 게이지에 반영 안 됨 — target 만으론 채점 불가
    kr_only_tgt = {"market_type": "kr", "per": None, "pbr": None, "roe": None}
    assert data.valuation_score(60000, kr_only_tgt, {"target_mean": 99999}, {}) is None
