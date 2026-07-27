from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


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
