"""tests/test_dashboard.py — 퀀트 터미널 데이터·인증 순수로직 (무네트워크·무 streamlit)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import auth, data


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
    got = {r["ticker"]: r for r in views.sp500_heatmap()}
    assert "AAPL" in got and "MSFT" in got                     # Close 있는 종목만
    assert abs(got["AAPL"]["pct"] - 2.0) < 0.01
    assert got["AAPL"]["sector_kr"] == "기술" and got["AAPL"]["market_cap"] > 0   # 실제 메타


def test_views_sp500_heatmap_graceful(monkeypatch):
    import yfinance as yf
    from dashboard import views

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(yf, "download", boom)
    assert views.sp500_heatmap() == []
