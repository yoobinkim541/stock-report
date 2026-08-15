#!/usr/bin/env python3
"""test_etf_data.py — ETF 데이터층 순수 로직 (무네트워크)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import etf_data as E


def test_premium_pct():
    assert E.premium_pct(55.82, 56.1) == -0.5
    assert E.premium_pct(56.1, 56.1) == 0.0
    assert E.premium_pct(None, 56.1) is None
    assert E.premium_pct(55.0, 0) is None
    assert E.premium_pct("x", "y") is None


def test_dividend_stats_monthly():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    pairs = [((now - timedelta(days=30 * i)).isoformat(), 0.635) for i in range(12)]
    s = E.dividend_stats(pairs, price=55.66, now=now)
    assert s["count_12m"] == 12 and s["freq_label"] == "매월"
    assert abs(s["per_share_12m"] - 7.62) < 0.01
    assert abs(s["yield_pct"] - 13.69) < 0.05


def test_dividend_stats_excludes_old_and_handles_empty():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    old = [((now - timedelta(days=400)).isoformat(), 1.0)]
    s = E.dividend_stats(old, price=100.0, now=now)
    assert s["count_12m"] == 0 and s["yield_pct"] is None and s["freq_label"] == "—"
    s2 = E.dividend_stats([], price=None, now=now)
    assert s2["per_share_12m"] == 0.0


def test_dividend_stats_quarterly_label():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    pairs = [((now - timedelta(days=91 * i)).isoformat(), 0.5) for i in range(4)]
    assert E.dividend_stats(pairs, 100.0, now=now)["freq_label"] == "분기"


def test_parse_top_holdings():
    df = pd.DataFrame({"Name": ["NVIDIA Corp", "Apple Inc"],
                       "Holding Percent": [0.0765, 0.0663]},
                      index=["NVDA", "AAPL"])
    out = E.parse_top_holdings(df)
    assert out == [{"symbol": "NVDA", "name": "NVIDIA Corp", "pct": 7.65},
                   {"symbol": "AAPL", "name": "Apple Inc", "pct": 6.63}]
    assert E.parse_top_holdings(None) == []
    assert E.parse_top_holdings(pd.DataFrame()) == []


def test_parse_kr_top_holdings():
    df = pd.DataFrame({
        "종목명": ["삼성전자", "SK하이닉스"],
        "비중": ["30.5", "12.25"],
        "수량": [1000, 200],
        "평가금액": [70_000_000, 36_000_000],
    }, index=["005930", "000660"])

    out = E.parse_kr_top_holdings(df)

    assert out[0]["symbol"] == "005930"
    assert out[0]["name"] == "삼성전자"
    assert out[0]["pct"] == 30.5
    assert out[0]["shares"] == 1000
    assert out[0]["amount"] == 70_000_000


def test_pykrx_reachable_probes_once_and_caches(monkeypatch):
    """이 서버는 KRX 도달 불가(문서화된 환경 사실) — 캐싱 없이는 ETF 캐시 미스마다
    최대 10회의 순차 doomed pykrx 호출이 반복된다. 프로세스당 1회만 프로브해야 한다."""
    import pykrx.stock as pykrx_stock
    monkeypatch.setattr(E, "_PYKRX_REACHABLE", None)
    calls = []

    def _fail(*a, **k):
        calls.append(1)
        raise RuntimeError("KRX unreachable")

    monkeypatch.setattr(pykrx_stock, "get_etf_ticker_name", _fail)

    assert E._pykrx_reachable() is False
    assert E._pykrx_reachable() is False
    assert len(calls) == 1                            # 두 번째 호출은 캐시만 읽음


def test_kr_pykrx_overlay_skips_entirely_when_unreachable(monkeypatch):
    """도달 불가로 확인되면 top_holdings/가격괴리율 등 어떤 pykrx 함수도 호출하지 않아야 한다."""
    import pykrx.stock as pykrx_stock
    monkeypatch.setattr(E, "_pykrx_reachable", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("pykrx.stock 함수가 호출되면 안 됨 — 도달 불가 상태")

    monkeypatch.setattr(pykrx_stock, "get_etf_ticker_name", _boom)
    monkeypatch.setattr(pykrx_stock, "get_etf_portfolio_deposit_file", _boom)

    out = {"stock_code": "069500"}
    E._kr_pykrx_overlay(out)                           # 예외 없이 조용히 스킵돼야 함
    assert "top_holdings" not in out


def test_kr_seed_etf_codes_are_recognized(monkeypatch):
    assert E.is_etf("0167A0.KS") is True

    monkeypatch.setattr(E, "_latest_kr_market_row", lambda code: {})
    monkeypatch.setattr(E, "_kr_yfinance_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_pykrx_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_kis_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_naver_overlay", lambda out: None)

    out = E.kr_etf_summary("0167A0.KS")
    assert out["is_etf"] is True
    assert out["name"] == "SOL AI반도체TOP2플러스"


def test_is_etf_known_list_and_quote_type():
    assert E.is_etf("QQQI") is True                  # 보유 ETF — 오프라인 폴백
    assert E.is_etf("SGOV") is True
    assert E.is_etf("NVDL") is True
    assert E.is_etf("TSLL") is True
    assert E.is_etf("A069500") is True
    assert E.is_etf("069500.KS") is True
    assert E.is_etf("005930.KS") is False
    assert E.is_etf("MSFT") is False
    assert E.is_etf("MSFT", quote_type="ETF") is True
    assert E.is_etf("QQQI", quote_type="EQUITY") is False   # 실판정 우선


def test_kr_etf_summary_fallback_shape(monkeypatch):
    monkeypatch.setattr(E, "_latest_kr_market_row", lambda code: {
        "Code": "069500", "Close": 38900.0, "Marcap": 7_800_000_000_000.0,
        "Stocks": 200_000_000.0, "Date": "2026-07-07",
    })
    monkeypatch.setattr(E, "_kr_yfinance_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_pykrx_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_kis_overlay", lambda out: None)
    monkeypatch.setattr(E, "_kr_naver_overlay", lambda out: None)

    out = E.kr_etf_summary("A069500")

    assert out["ticker"] == "069500.KS"
    assert out["market_type"] == "kr"
    assert out["currency"] == "KRW"
    assert out["name"] == "KODEX 200"
    assert out["benchmark"] == "KOSPI 200"
    assert out["price"] == 38900.0


def test_apply_kr_etf_metric_table_merges_nav_deviation_and_tracking_error():
    out = {"ticker": "069500.KS", "stock_code": "069500"}
    df = pd.DataFrame({
        "NAV": [38950.0],
        "종가": [38900.0],
        "괴리율": [-0.13],
        "추적오차율": [0.08],
        "순자산총액": [7_800_000_000_000],
    }, index=["069500"])

    E.apply_kr_etf_metric_table(out, df)

    assert out["nav"] == 38950.0
    assert out["price"] == 38900.0
    assert out["premium_pct"] == -0.13
    assert out["tracking_error_pct"] == 0.08
    assert out["total_assets"] == 7_800_000_000_000


def test_kr_kis_overlay_fills_nav_premium_holdings_from_live_snapshot(monkeypatch):
    """감사 후속 — 이 서버에서 pykrx(_kr_pykrx_overlay)가 KRX 403으로 죽어있어,
    KIS 실계좌 시세(kis_quote.get_etf_snapshot)로 NAV·괴리율·구성종목을 대신 채운다."""
    from providers import kis_quote

    def fake_snapshot(symbol):
        assert symbol == "069500"
        return {
            # kis_quote.get_etf_snapshot()는 이미 원 단위로 정규화해 반환(라이브 확인) —
            # 25조 4,325억 (네이버 totalNav 대조 확인값).
            "price": 110060.0, "nav": 110362.62, "premium_pct": -0.2742,
            "total_assets": 25_432_500_000_000.0,
            "holdings": [
                {"code": "005930", "name": "삼성전자", "weight_pct": 34.47, "price": 274500.0, "chg_pct": 2.43},
                {"code": "000660", "name": "SK하이닉스", "weight_pct": 25.15, "price": 1645000.0, "chg_pct": 3.26},
            ],
        }
    monkeypatch.setattr(kis_quote, "get_etf_snapshot", fake_snapshot)

    out = {"stock_code": "069500"}
    E._kr_kis_overlay(out)

    assert out["nav"] == 110362.62
    assert out["price"] == 110060.0
    assert out["premium_pct"] == -0.2742
    assert out["total_assets"] == 25_432_500_000_000.0
    assert out["top_holdings_source"] == "KIS"
    assert out["top_holdings"][0] == {"symbol": "005930", "name": "삼성전자", "pct": 34.47,
                                       "shares": None, "amount": None}


def test_kr_kis_overlay_does_not_override_existing_top_holdings(monkeypatch):
    """pykrx 가 먼저 top_holdings 를 채웠다면(다른 배포 환경) KIS 로 덮어쓰지 않는다."""
    from providers import kis_quote
    monkeypatch.setattr(kis_quote, "get_etf_snapshot", lambda symbol: {
        "price": 1.0, "nav": 1.0, "premium_pct": 0.0, "total_assets": 1.0,
        "holdings": [{"code": "999999", "name": "무시돼야함", "weight_pct": 1.0}],
    })

    out = {"stock_code": "069500", "top_holdings": [{"symbol": "005930", "name": "기존값"}]}
    E._kr_kis_overlay(out)

    assert out["top_holdings"] == [{"symbol": "005930", "name": "기존값"}]


def test_kr_kis_overlay_noop_when_snapshot_unavailable(monkeypatch):
    from providers import kis_quote
    monkeypatch.setattr(kis_quote, "get_etf_snapshot", lambda symbol: None)

    out = {"stock_code": "069500"}
    E._kr_kis_overlay(out)

    assert out == {"stock_code": "069500"}


def test_kr_kis_overlay_noop_without_stock_code():
    out = {}
    E._kr_kis_overlay(out)
    assert out == {}


def test_etf_summary_self_heals_stale_empty_kr_etf_cache(monkeypatch):
    """감사 후속 — KIS 오버레이 도입 전(pykrx 죽어서 top_holdings/premium_pct 둘 다
    빈) 12h 캐시가 남아있으면, 배포 후에도 새 데이터가 안 보이는 것을 방지해야 한다."""
    stale = {"ticker": "069500.KS", "is_etf": True, "stock_code": "069500",
             "top_holdings": [], "premium_pct": None}
    monkeypatch.setattr(E, "_load_cache", lambda tk: stale)
    monkeypatch.setattr(E, "_kr_etf_key", lambda tk: "069500.KS")
    monkeypatch.setattr(E, "is_etf", lambda tk: True)
    fresh = {"ticker": "069500.KS", "is_etf": True, "stock_code": "069500",
             "top_holdings": [{"symbol": "005930", "name": "삼성전자"}], "premium_pct": -0.27}
    monkeypatch.setattr(E, "kr_etf_summary", lambda tk: fresh)
    monkeypatch.setattr(E, "_save_cache", lambda tk, data: None)

    assert E.etf_summary("069500.KS") == fresh


def test_etf_summary_keeps_cache_with_real_holdings(monkeypatch):
    """이미 채워진(top_holdings 있음) 정상 캐시는 self-heal 대상이 아니어야 한다."""
    good = {"ticker": "069500.KS", "is_etf": True, "stock_code": "069500",
            "top_holdings": [{"symbol": "005930"}], "premium_pct": -0.27}
    monkeypatch.setattr(E, "_load_cache", lambda tk: good)

    assert E.etf_summary("069500.KS") == good


def test_kr_naver_overlay_fills_expense_ratio(monkeypatch):
    """감사 후속 — KIS 실계좌 시세 API 엔 총보수 필드가 없어 네이버로 보강."""
    from providers import naver_consensus

    def fake_summary(ticker):
        assert ticker == "069500.KS"
        return {"etf": {"expense_ratio": 0.0015, "issuer": "삼성자산운용",
                        "nav": 110362.62, "premium_pct": -0.2742,
                        "total_assets": 25_432_500_000_000.0}}
    monkeypatch.setattr(naver_consensus, "summary", fake_summary)

    out = {"stock_code": "069500"}
    E._kr_naver_overlay(out)

    assert out["expense_ratio"] == 0.0015
    assert out["family"] == "삼성자산운용"
    assert out["nav"] == 110362.62
    assert out["premium_pct"] == -0.2742
    assert out["total_assets"] == 25_432_500_000_000.0


def test_kr_naver_overlay_does_not_override_kis_values(monkeypatch):
    """KIS 가 이미 nav/premium_pct/total_assets/family 를 채웠으면 네이버로 덮지 않는다
    — 총보수만 KIS 공백이라 보강한다."""
    from providers import naver_consensus
    monkeypatch.setattr(naver_consensus, "summary", lambda ticker: {"etf": {
        "expense_ratio": 0.0015, "issuer": "무시돼야함",
        "nav": 1.0, "premium_pct": 99.0, "total_assets": 1.0,
    }})

    out = {"stock_code": "069500", "nav": 110362.62, "premium_pct": -0.2742,
           "total_assets": 25_432_500_000_000.0, "family": "삼성자산운용"}
    E._kr_naver_overlay(out)

    assert out["expense_ratio"] == 0.0015     # 이건 KIS 에 없던 값이라 채워짐
    assert out["nav"] == 110362.62            # KIS 값 유지
    assert out["premium_pct"] == -0.2742
    assert out["total_assets"] == 25_432_500_000_000.0
    assert out["family"] == "삼성자산운용"


def test_kr_naver_overlay_noop_when_no_etf_indicator(monkeypatch):
    from providers import naver_consensus
    monkeypatch.setattr(naver_consensus, "summary", lambda ticker: {"target_mean": 100.0})

    out = {"stock_code": "069500"}
    E._kr_naver_overlay(out)

    assert out == {"stock_code": "069500"}


def test_kr_naver_overlay_noop_without_stock_code():
    out = {}
    E._kr_naver_overlay(out)
    assert out == {}
