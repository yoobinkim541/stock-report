from __future__ import annotations

from datetime import datetime, timezone

from dashboard import chart_orderflow


def _events():
    return [
        {
            "version": 1,
            "event_type": "trade",
            "symbol": "005930",
            "market": "KR",
            "received_at": 100.0,
            "price": 71000.0,
            "size": 20.0,
            "volume_method": "cumulative_delta",
        },
        {
            "version": 1,
            "event_type": "trade",
            "symbol": "005930",
            "market": "KR",
            "received_at": 101.0,
            "price": 71000.0,
            "size": 5.0,
            "volume_method": "cumulative_delta",
        },
        {
            "version": 1,
            "event_type": "trade",
            "symbol": "005930",
            "market": "KR",
            "received_at": 102.0,
            "price": 71100.0,
            "size": 7.0,
            "volume_method": "cumulative_delta",
        },
        {
            "version": 1,
            "event_type": "book",
            "symbol": "005930",
            "market": "KR",
            "received_at": 103.0,
            "bids": [[71000.0, 80.0], [70900.0, 20.0]],
            "asks": [[71100.0, 25.0], [71200.0, 25.0]],
            "best_bid": 71000.0,
            "best_ask": 71100.0,
            "depth": 2,
        },
    ]


def test_snapshot_builds_depth_and_volume_at_price_without_fake_delta():
    snapshot = chart_orderflow.build_snapshot("005930.KS", _events(), now=104.0)

    assert snapshot["ok"] is True
    assert snapshot["symbol"] == "005930"
    assert snapshot["book"]["spread"] == 100.0
    assert snapshot["book"]["imbalance"] == 1 / 3
    assert snapshot["volume_profile"] == [
        {"price": 71000.0, "volume": 25.0},
        {"price": 71100.0, "volume": 7.0},
    ]
    assert snapshot["coverage"]["capabilities"]["footprint"] is False
    assert snapshot["blocked"]["footprint"] == "authoritative_aggressor_side_unavailable"


def test_snapshot_reports_capture_gap_instead_of_fabricating_data():
    snapshot = chart_orderflow.build_snapshot("MSFT", [], now=104.0)

    assert snapshot["ok"] is False
    assert snapshot["reason"] == "capture_empty"
    assert snapshot["volume_profile"] == []
    assert snapshot["book"] is None


def test_provider_trade_size_survives_cumulative_reset_marker():
    events = [{
        "version": 1,
        "event_type": "trade",
        "symbol": "AAPL",
        "market": "US",
        "received_at": 100.0,
        "price": 200.0,
        "size": 12.0,
        "volume_method": "provider_trade_size",
        "volume_anomaly": True,
    }]

    snapshot = chart_orderflow.build_snapshot("AAPL", events, now=101.0)

    assert snapshot["volume_profile"] == [{"price": 200.0, "volume": 12.0}]


def test_orderflow_figures_encode_real_bid_ask_and_trade_volume():
    snapshot = chart_orderflow.build_snapshot("005930", _events(), now=104.0)

    depth = chart_orderflow.depth_figure(snapshot)
    profile = chart_orderflow.volume_profile_figure(snapshot)

    assert [trace.name for trace in depth.data] == ["매수 잔량", "매도 잔량"]
    assert list(depth.data[0].x) == [20.0, 80.0]
    assert list(depth.data[1].x) == [-25.0, -25.0]
    assert [trace.name for trace in profile.data] == ["체결량"]
    assert list(profile.data[0].x) == [25.0, 7.0]


def test_load_snapshot_reads_the_bounded_local_store(tmp_path):
    from providers import orderflow_store

    orderflow_store.append_events(_events(), base_dir=tmp_path, date_utc="2026-08-09")

    snapshot = chart_orderflow.load_snapshot(
        "005930.KS", date_utc="2026-08-09", base_dir=tmp_path, now=104.0,
    )

    assert snapshot["ok"] is True
    assert snapshot["coverage"]["events"] == 4
    assert snapshot["coverage"]["storage_window"]["truncated"] is False


def test_load_snapshot_uses_exchange_local_session_date(tmp_path):
    from providers import orderflow_store

    events = [{**_events()[0], "symbol": "AAPL", "market": "US",
               "session_date": "2026-08-09"}]
    orderflow_store.append_events(events, base_dir=tmp_path, date_utc="2026-08-09")
    now = datetime(2026, 8, 10, 0, 30, tzinfo=timezone.utc).timestamp()

    snapshot = chart_orderflow.load_snapshot("AAPL", base_dir=tmp_path, now=now)

    assert snapshot["ok"] is True
    assert snapshot["coverage"]["events"] == 1
