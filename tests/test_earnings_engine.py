#!/usr/bin/env python3
"""test_earnings_engine.py — §G 어닝 인텔리전스 (무네트워크, FakeTicker 모킹).

검증: 밸류에이션 추출 + 결정적 배당 CAGR + 과거 서프라이즈 + 컨센서스/리비전 모멘텀 +
다음 실적일 + KR 열화모드 graceful + PEAD 집계.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── FakeTicker ──────────────────────────────────────────────────────────────
class FakeTicker:
    def __init__(self, info=None, divs=None, edates=None, ee=None, re=None, rev=None, apt=None, cal=None):
        self.info = info or {}
        self.dividends = divs if divs is not None else pd.Series(dtype=float)
        self._edates = edates
        self.earnings_estimate = ee
        self.revenue_estimate = re
        self.eps_revisions = rev
        self.analyst_price_targets = apt
        self.calendar = cal

    @property
    def earnings_dates(self):
        return self._edates

    def get_earnings_dates(self, limit=12):
        return self._edates


def _us_ticker():
    info = {"trailingPE": 25.3, "forwardPE": 22.0, "priceToBook": 12.0,
            "priceToSalesTrailing12Months": 11.0, "returnOnEquity": 0.35,
            "trailingEps": 11.8, "forwardEps": 13.2, "dividendYield": 0.008,
            "fiveYearAvgDividendYield": 0.9, "payoutRatio": 0.25}
    idx = pd.date_range("2022-03-01", periods=16, freq="QS")     # 4년 분기배당
    divs = pd.Series([0.5 * (1.1 ** (i // 4)) for i in range(16)], index=idx)  # 연 +10%
    edates = pd.DataFrame(
        {"EPS Estimate": [2.90, 2.50, 2.0], "Reported EPS": [2.88, 2.65, None],
         "Surprise(%)": [-0.69, 6.0, None]},
        index=pd.to_datetime(["2026-01-25", "2025-10-25", "2026-04-25"]))
    ee = pd.DataFrame({"numberOfAnalysts": [30, 28], "avg": [2.95, 12.5]}, index=["0q", "+1q"])
    rev = pd.DataFrame({"upLast30days": [5, 6], "downLast30days": [1, 2]}, index=["0q", "+1q"])
    apt = {"current": 400.0, "mean": 460.0}
    import datetime
    cal = {"Earnings Date": [datetime.date(2026, 4, 25)]}
    return FakeTicker(info, divs, edates, ee, None, rev, apt, cal)


def test_valuation_metrics_us():
    from providers import earnings_data as ed
    v = ed.valuation_metrics("MSFT", _t=_us_ticker())
    assert v["per"] == 25.3 and v["pbr"] == 12.0 and v["psr"] == 11.0
    assert v["roe"] == 0.35 and v["eps_ttm"] == 11.8 and v["eps_fwd"] == 13.2
    assert v["div_yield"] == 0.008 and v["payout"] == 0.25
    assert abs(v["div_growth_1y"] - 0.10) < 1e-6     # 결정적 10%
    assert abs(v["div_growth_3y"] - 0.10) < 1e-6
    assert v["market_type"] == "us"


def test_dividend_yield_percent_normalization():
    from providers import earnings_data as ed
    t = FakeTicker({"dividendYield": 2.4})    # %로 온 경우
    assert ed.valuation_metrics("X", _t=t)["div_yield"] == 0.024


def test_earnings_history_drops_future_and_orders():
    from providers import earnings_data as ed
    h = ed.earnings_history("MSFT", _t=_us_ticker())
    assert len(h) == 2                         # 미보고(미래) 분기 제외
    assert h[0]["date"] == "2026-01-25" and h[0]["surprise_pct"] == -0.69
    assert h[1]["eps_actual"] == 2.65


def test_consensus_and_revision_momentum():
    from providers import earnings_data as ed
    c = ed.consensus("MSFT", _t=_us_ticker())
    assert c["eps_fwd_avg"] == 12.5 and c["n_analysts"] == 30 or c["n_analysts"] == 28
    assert c["revision_momentum"] == 0.5       # (6-2)/(6+2)
    assert c["target_mean"] == 460.0 and c["target_upside_pct"] == 15.0


def test_next_earnings_with_injected_today():
    from providers import earnings_data as ed
    n = ed.next_earnings("MSFT", _t=_us_ticker(), today="2026-03-01")
    assert n["date"] == "2026-04-25" and n["days_until"] == 55


def test_kr_degraded_mode(monkeypatch):
    from providers import earnings_data as ed
    from providers import kr_fundamentals as kf
    kr = FakeTicker({"trailingPE": 9.0, "priceToBook": 1.1, "returnOnEquity": 0.12})  # 컨센서스 없음
    monkeypatch.setattr(kf, "recent_annual_metrics", lambda t: {
        "market_type": "kr", "confidence": "missing", "error": "DART_API_KEY 미설정"
    })
    monkeypatch.setattr(ed, "_ticker", lambda s: kr)
    monkeypatch.setattr(ed, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(ed, "_cache_put", lambda *a, **k: None)
    s = ed.summary("005930.KS", force=True, today="2026-03-01")
    assert s["market_type"] == "kr" and s["degraded"] is True
    assert s["valuation"]["per"] == 9.0        # 밸류에이션은 정상
    assert s["consensus"]["eps_fwd_avg"] is None  # 포워드 컨센서스 결측 → None


def test_summary_us_integration(monkeypatch):
    from providers import earnings_data as ed
    monkeypatch.setattr(ed, "_ticker", lambda s: _us_ticker())
    monkeypatch.setattr(ed, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(ed, "_cache_put", lambda *a, **k: None)
    s = ed.summary("MSFT", force=True, today="2026-03-01")
    assert s["degraded"] is False
    assert s["valuation"]["per"] == 25.3
    assert s["last_surprise"]["surprise_pct"] == -0.69
    assert s["next_earnings"]["days_until"] == 55


# ── G2: PEAD 반응 분석 ───────────────────────────────────────────────────────
def _pead_fixture():
    idx = pd.bdate_range("2025-10-01", periods=120)
    vals = [100.0] * 120
    # e1 = idx[20] (beat): 반응일=21 +5%, drift5(26) +2%, drift20(41) +10%
    vals[21], vals[26], vals[41] = 105.0, 105.0 * 1.02, 105.0 * 1.10
    # e2 = idx[60] (miss): 반응일=61 -3%, drift5(66) -2%, drift20(81) -5%
    vals[61], vals[66], vals[81] = 97.0, 97.0 * 0.98, 97.0 * 0.95
    prices = pd.Series(vals, index=idx)
    hist = [
        {"date": idx[20].strftime("%Y-%m-%d"), "surprise_pct": 8.0},
        {"date": idx[60].strftime("%Y-%m-%d"), "surprise_pct": -5.0},
    ]
    return prices, hist


def test_post_earnings_reactions_values():
    from reports import earnings_reaction as er
    prices, hist = _pead_fixture()
    rs = er.post_earnings_reactions("MSFT", prices=prices, hist=hist)
    assert len(rs) == 2
    e1, e2 = rs[0], rs[1]                       # 날짜 오름차순
    assert abs(e1["reaction_1d"] - 0.05) < 1e-9
    assert abs(e1["drift_5d"] - 0.02) < 1e-9
    assert abs(e1["drift_20d"] - 0.10) < 1e-9
    assert abs(e2["reaction_1d"] - (-0.03)) < 1e-9


def test_reaction_summary_aggregates():
    from reports import earnings_reaction as er
    prices, hist = _pead_fixture()
    s = er.reaction_summary(er.post_earnings_reactions("MSFT", prices=prices, hist=hist))
    assert s["n"] == 2
    assert abs(s["avg_abs_move_1d"] - 0.04) < 1e-9
    assert s["beat_up_rate"] == 1.0 and s["miss_down_rate"] == 1.0
    assert s["drift_persistence"] == 1.0       # 서프라이즈 부호 = 드리프트 부호
    assert abs(s["avg_drift_5d_on_beat"] - 0.02) < 1e-9


def test_reaction_summary_empty():
    from reports import earnings_reaction as er
    s = er.reaction_summary([])
    assert s["n"] == 0 and s["avg_abs_move_1d"] is None


# ── G1: 리포트 밸류에이션 렌더 ───────────────────────────────────────────────
def test_report_valuation_lines(monkeypatch):
    import providers.earnings_data as ed
    fake = {"valuation": {"per": 25.3, "forward_pe": 22.0, "pbr": 12.0, "psr": 11.0,
                          "roe": 0.35, "eps_ttm": 11.8, "div_yield": 0.008, "div_growth_1y": 0.10},
            "next_earnings": {"date": "2026-04-25", "days_until": 55},
            "last_surprise": {"surprise_pct": -0.69},
            "consensus": {"revision_momentum": 0.5, "target_upside_pct": 15.0}}
    monkeypatch.setattr(ed, "summary", lambda t: fake)
    from reports import investment_report as ir
    lines = ir._earnings_valuation_lines("MSFT")
    assert any("PER 25.3x" in l and "배당 0.8%" in l for l in lines)
    assert any("다음 실적 2026-04-25 (D-55)" in l for l in lines)
    assert any("리비전 모멘텀 +0.50" in l and "목표가 +15%" in l for l in lines)


def test_report_valuation_lines_graceful_on_error(monkeypatch):
    import providers.earnings_data as ed

    def _boom(t):
        raise RuntimeError("network down")
    monkeypatch.setattr(ed, "summary", _boom)
    from reports import investment_report as ir
    assert ir._earnings_valuation_lines("MSFT") == []   # 실패 → 섹션 생략(리포트 무손상)


# ── G6: /earnings 커맨드 ─────────────────────────────────────────────────────
def test_cmd_earnings_overview(monkeypatch):
    import providers.earnings_data as ed
    monkeypatch.setattr("portfolio_universe.load_portfolio_tickers", lambda: ["MSFT", "005930.KS"])

    def fake_summary(t):
        if t == "MSFT":
            return {"ticker": "MSFT", "valuation": {"per": 25.3, "div_yield": 0.008},
                    "next_earnings": {"date": "2026-04-25", "days_until": 55},
                    "last_surprise": {"surprise_pct": -0.69}}
        return {"ticker": "005930.KS", "valuation": {"per": 9.0}, "next_earnings": {},
                "last_surprise": None, "degraded": True}
    monkeypatch.setattr(ed, "summary", fake_summary)
    from bot import earnings_commands as ec
    out = []
    ec.cmd_earnings("123", [], send_fn=lambda cid, txt: out.append(txt))
    assert len(out) == 1
    assert "MSFT" in out[0] and "PER 25.3x" in out[0] and "D-55" in out[0]
    assert "005930.KS" in out[0]


def test_cmd_earnings_detail(monkeypatch):
    import providers.earnings_data as ed
    from reports import earnings_reaction as er
    monkeypatch.setattr(ed, "summary", lambda t: {
        "ticker": "MSFT", "degraded": False,
        "valuation": {"per": 25.3, "pbr": 12.0, "roe": 0.35, "eps_ttm": 11.8,
                      "div_yield": 0.008, "div_growth_1y": 0.10},
        "consensus": {"eps_fwd_avg": 13.2, "n_analysts": 28, "revision_momentum": 0.5,
                      "target_upside_pct": 15.0},
        "next_earnings": {"date": "2026-04-25", "days_until": 55}})
    monkeypatch.setattr(ed, "earnings_history", lambda t, limit=4: [
        {"date": "2026-01-25", "surprise_pct": -0.69, "eps_actual": 2.88, "eps_est": 2.90}])
    monkeypatch.setattr(er, "analyze", lambda t: {"summary": {
        "n": 8, "avg_abs_move_1d": 0.05, "beat_up_rate": 0.75, "drift_persistence": 0.6}})
    from bot import earnings_commands as ec
    out = []
    ec.cmd_earnings("123", ["msft"], send_fn=lambda cid, txt: out.append(txt))
    assert "PER 25.3x" in out[0] and "리비전 모멘텀 +0.50" in out[0]
    assert "PEAD" in out[0] and "beat→상승 75%" in out[0]


def test_earnings_command_owner_only_registered():
    import telegram_bot
    assert "/earnings" not in telegram_bot._GUEST_COMMANDS    # 게스트 차단
    assert "/earnings" in telegram_bot._COMMAND_HANDLERS       # owner 등록


# ── G5: 스냅샷 행 평탄화 ──────────────────────────────────────────────────────
def test_snapshot_row_flatten(monkeypatch):
    import providers.earnings_data as ed
    monkeypatch.setattr(ed, "summary", lambda t, force=False, today=None: {
        "market_type": "us", "degraded": False,
        "valuation": {"per": 25.3, "pbr": 12.0, "roe": 0.35, "eps_ttm": 11.8, "div_yield": 0.008,
                      "div_growth_1y": 0.1, "forward_pe": 22.0, "psr": 11.0},
        "consensus": {"eps_fwd_avg": 13.2, "n_analysts": 28, "revision_momentum": 0.5,
                      "eps_rev_up_30d": 6, "eps_rev_down_30d": 2, "target_upside_pct": 15.0, "rev_fwd_avg": 1.0e9},
        "next_earnings": {"date": "2026-04-25", "days_until": 55},
        "last_surprise": {"surprise_pct": -0.69}})
    from crons import earnings_snapshot as es
    row = es._row("MSFT", "2026-03-01")
    assert row["ticker"] == "MSFT" and row["date"] == "2026-03-01"
    assert row["revision_momentum"] == 0.5 and row["per"] == 25.3
    assert row["last_eps_surprise_pct"] == -0.69 and row["days_until"] == 55
    assert row["market_type"] == "us" and row["degraded"] is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
