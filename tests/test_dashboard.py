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
