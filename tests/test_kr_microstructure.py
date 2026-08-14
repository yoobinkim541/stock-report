from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo


def test_fetch_naver_indices_runs_requests_in_parallel(monkeypatch):
    """3개 지수 호출이 순차면 3×슬립, 병렬이면 1×슬립 근처여야 한다."""
    from providers import kr_microstructure as km

    monkeypatch.setattr(km, "naver_enabled", lambda: True)

    def _slow_http(url):
        time.sleep(0.05)
        return {}

    monkeypatch.setattr(km, "_http_json", _slow_http)
    t0 = time.monotonic()
    km.fetch_naver_indices()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.12                              # 순차였다면 >=0.15s


def test_fetch_naver_breadth_runs_requests_in_parallel(monkeypatch):
    """6개(2시장×3정렬) 호출이 순차면 6×슬립, 병렬이면 1×슬립 근처여야 한다."""
    from providers import kr_microstructure as km

    monkeypatch.setattr(km, "naver_enabled", lambda: True)

    def _slow_http(url):
        time.sleep(0.05)
        return {}

    monkeypatch.setattr(km, "_http_json", _slow_http)
    t0 = time.monotonic()
    km.fetch_naver_breadth()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.15                               # 순차였다면 >=0.3s


def test_build_snapshot_merges_indices_flow_futures_breadth_and_status():
    from providers import kr_microstructure as km

    now = datetime(2026, 7, 27, 10, 15, tzinfo=ZoneInfo("Asia/Seoul"))
    sources = {
        "indices": {"kospi": {"price": 3310.2, "change_pct": 0.42}, "kosdaq": {"price": 912.1, "change_pct": -0.2}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -40000000000}},
        "k200_futures": {"price": 452.2, "change_pct": 0.31, "foreign_net": 1800},
        "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74},
        "fx": {"usdkrw": {"rate": 1387.2, "change": -2.1}},
    }

    got = km.build_snapshot(now=now, sources=sources)

    assert got["as_of"] == "2026-07-27T10:15:00+09:00"
    assert got["indices"]["kospi"]["price"] == 3310.2
    assert got["investor_flow"]["kospi"]["foreign_net"] == 120000000000
    assert got["k200_futures"]["foreign_net"] == 1800
    assert got["breadth"]["advancers"] == 510
    assert got["fx"]["rate"] == 1387.2
    assert got["fx"]["usdkrw"]["rate"] == 1387.2
    assert got["field_status"]["breadth"]["ok"] is True


def test_build_snapshot_marks_missing_fields_without_fabricating_data():
    from providers import kr_microstructure as km

    got = km.build_snapshot(sources={"indices": {"kospi": {"price": "3310.2"}}})

    assert got["indices"]["kospi"]["price"] == 3310.2
    assert got["investor_flow"] == {}
    assert got["k200_futures"] is None
    assert got["field_status"]["investor_flow"]["ok"] is False
    assert {row["field"] for row in got["unavailable"]} >= {"investor_flow", "k200_futures", "breadth", "fx"}


def test_collect_sources_keeps_fetcher_failures_bounded():
    from providers import kr_microstructure as km

    sources, errors = km.collect_sources({
        "indices": lambda: {"kospi": {"price": 3310.2}},
        "breadth": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    })

    assert sources["indices"]["kospi"]["price"] == 3310.2
    assert errors == ["breadth: boom"]



def test_parse_naver_realtime_index_payload_maps_core_indices():
    from providers import kr_microstructure as km

    payload = {
        "datas": [
            {
                "symbolCode": "KOSPI",
                "closePriceRaw": "6755.75",
                "fluctuationsRatioRaw": "0.97",
                "compareToPreviousClosePriceRaw": "65.13",
                "localTradedAt": "2026-07-27T16:03:00+09:00",
            },
            {
                "symbolCode": "KPI200",
                "closePriceRaw": "452.22",
                "fluctuationsRatioRaw": "0.31",
                "localTradedAt": "2026-07-27T16:03:00+09:00",
            },
        ]
    }

    got = km.parse_naver_index_payload(payload)

    assert got["kospi"]["price"] == 6755.75
    assert got["kospi"]["change_pct"] == 0.97
    assert got["kospi"]["change"] == 65.13
    assert got["kospi"]["source"] == "naver_realtime_index"
    assert got["kospi200"]["price"] == 452.22


def test_parse_naver_breadth_payloads_sums_markets_and_preserves_breakdown():
    from providers import kr_microstructure as km

    payloads = {
        ("KOSPI", "marketValue"): {"totalCount": 2471, "marketStatus": "CLOSE"},
        ("KOSPI", "up"): {"totalCount": 1549, "marketStatus": "CLOSE"},
        ("KOSPI", "down"): {"totalCount": 763, "marketStatus": "CLOSE"},
        ("KOSDAQ", "marketValue"): {"totalCount": 1822, "marketStatus": "CLOSE"},
        ("KOSDAQ", "up"): {"totalCount": 1007, "marketStatus": "CLOSE"},
        ("KOSDAQ", "down"): {"totalCount": 628, "marketStatus": "CLOSE"},
    }

    got = km.parse_naver_breadth_payloads(payloads)

    assert got["advancers"] == 2556
    assert got["decliners"] == 1391
    assert got["unchanged"] == 346
    assert got["markets"]["kospi"]["unchanged"] == 159
    assert got["markets"]["kosdaq"]["unchanged"] == 187
    assert got["source"] == "naver_stock_counts"


def test_parse_kiwoom_investor_flow_sums_sector_rows_to_market_krw():
    from providers import kr_microstructure as km

    payload = {
        "inds_netprps": [
            {"inds_nm": "대형주", "frgnr_netprps": "+1,200", "orgn_netprps": "-300", "ind_netprps": "-900"},
            {"inds_nm": "중형주", "frgnr_netprps": "+200", "orgn_netprps": "-50", "ind_netprps": "-150"},
        ]
    }

    got = km.parse_kiwoom_investor_flow_payload(payload, market="kospi")

    assert got["kospi"]["foreign_net"] == 1400000000
    assert got["kospi"]["institution_net"] == -350000000
    assert got["kospi"]["individual_net"] == -1050000000
    assert got["kospi"]["unit"] == "KRW"
    assert got["kospi"]["source"] == "kiwoom_ka10051"


def test_parse_kiwoom_investor_flow_prefers_composite_total_row():
    from providers import kr_microstructure as km

    payload = {
        "inds_netprps": [
            {"inds_nm": "종합(KOSPI)", "frgnr_netprps": "-30,516", "orgn_netprps": "+8,609", "ind_netprps": "+21,694"},
            {"inds_nm": "대형주", "frgnr_netprps": "-28,967", "orgn_netprps": "+8,596", "ind_netprps": "+20,259"},
        ]
    }

    got = km.parse_kiwoom_investor_flow_payload(payload, market="kospi")

    assert got["kospi"]["foreign_net"] == -30516000000
    assert got["kospi"]["institution_net"] == 8609000000
    assert got["kospi"]["individual_net"] == 21694000000


def test_fetch_kiwoom_investor_flow_prefers_market_intraday_then_tags_scope(monkeypatch):
    from providers import kr_microstructure as km

    class MarketApi:
        def __init__(self):
            self.calls = []

        def intraday_investor_trading_request_ka10063(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "inds_netprps": [
                    {"inds_nm": "종합(KOSPI)", "frgnr_netprps": "+12,000", "orgn_netprps": "-5,000", "ind_netprps": "-7,000"},
                ]
            }

    class SectorApi:
        def __init__(self):
            self.called = False

        def industrywise_investor_net_buy_request_ka10051(self, **kwargs):
            self.called = True
            raise AssertionError("sector fallback should not be used when market API works")

    market_api = MarketApi()
    sector_api = SectorApi()
    monkeypatch.setenv("KIWOOM_API_KEY", "k")
    monkeypatch.setenv("KIWOOM_API_SECRET", "s")
    monkeypatch.setenv("KIWOOM_INVESTOR_FLOW_BASE_DT", "20260731")
    monkeypatch.setattr(km, "_build_kiwoom_clients", lambda: (market_api, sector_api))
    monkeypatch.setattr(km, "_kiwoom_market_phase", lambda now=None: "intraday")

    got = km.fetch_kiwoom_investor_flow()

    assert market_api.calls
    assert market_api.calls[0]["mrkt_tp"] == "0"
    assert market_api.calls[0]["amt_qty_tp"] == "0"
    assert got["kospi"]["foreign_net"] == 12000000000
    assert got["kospi"]["institution_net"] == -5000000000
    assert got["kospi"]["source"] == "kiwoom_ka10063"
    assert got["kospi"]["scope"] == "market"
    assert got["kospi"]["basis"] == "intraday"
    assert got["kospi"]["base_dt"] == "20260731"
    assert sector_api.called is False


def test_fetch_kiwoom_investor_flow_falls_back_to_sector_when_market_missing(monkeypatch):
    from providers import kr_microstructure as km

    class SectorApi:
        def __init__(self):
            self.calls = []

        def industrywise_investor_net_buy_request_ka10051(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "inds_netprps": [
                    {"inds_nm": "종합(KOSDAQ)", "frgnr_netprps": "-1,500", "orgn_netprps": "+900", "ind_netprps": "+600"},
                ]
            }

    sector_api = SectorApi()
    monkeypatch.setenv("KIWOOM_API_KEY", "k")
    monkeypatch.setenv("KIWOOM_API_SECRET", "s")
    monkeypatch.setenv("KIWOOM_INVESTOR_FLOW_BASE_DT", "20260731")
    monkeypatch.setattr(km, "_build_kiwoom_clients", lambda: (None, sector_api))
    monkeypatch.setattr(km, "_kiwoom_market_phase", lambda now=None: "intraday")

    got = km.fetch_kiwoom_investor_flow()

    assert sector_api.calls
    assert got["kosdaq"]["foreign_net"] == -1500000000
    assert got["kosdaq"]["institution_net"] == 900000000
    assert got["kosdaq"]["source"] == "kiwoom_ka10051"
    assert got["kosdaq"]["scope"] == "industry"
    assert got["kosdaq"]["basis"] == "intraday"


def test_fetch_investor_flow_prefers_file_then_kiwoom(monkeypatch):
    from providers import kr_microstructure as km

    file_flow = {"kospi": {"foreign_net": 1, "source": "fixture"}}
    monkeypatch.setattr(km, "fetch_snapshot_file", lambda: {"investor_flow": file_flow})
    monkeypatch.setattr(km, "fetch_kiwoom_investor_flow", lambda: {"kospi": {"foreign_net": 2}})

    assert km.fetch_investor_flow() == file_flow

    monkeypatch.setattr(km, "fetch_snapshot_file", lambda: {})
    assert km.fetch_investor_flow()["kospi"]["foreign_net"] == 2


def test_fetch_k200_futures_prefers_file_then_kis_without_aliasing_index(monkeypatch):
    from providers import kr_microstructure as km

    file_futures = {"price": 425.5, "source": "fixture"}
    monkeypatch.setattr(km, "fetch_snapshot_file", lambda: {"k200_futures": file_futures, "indices": {"kospi200": {"price": 452.2}}})
    monkeypatch.setattr(km, "fetch_kis_k200_futures", lambda: {"price": 426.0, "source": "kis_futureoption"})
    assert km.fetch_k200_futures() == file_futures

    monkeypatch.setattr(km, "fetch_snapshot_file", lambda: {"indices": {"kospi200": {"price": 452.2}}})
    assert km.fetch_k200_futures() == {"price": 426.0, "source": "kis_futureoption"}

    monkeypatch.setattr(km, "fetch_kis_k200_futures", lambda: None)
    assert km.fetch_k200_futures() is None


def test_normalize_futures_preserves_symbol_and_basis():
    from providers import kr_microstructure as km

    got = km.normalize_futures({"symbol": "A05608", "price": "1070.34", "basis": "1.09", "source": "kis_futureoption"})

    assert got["symbol"] == "A05608"
    assert got["basis"] == 1.09
