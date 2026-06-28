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
