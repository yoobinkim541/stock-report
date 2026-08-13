from __future__ import annotations

import json
from datetime import datetime, timezone

from providers import orderflow_store


def test_recorder_converts_cumulative_volume_to_explicit_deltas():
    recorder = orderflow_store.OrderFlowRecorder(book_sample_seconds=1.0)

    first = recorder.capture(
        {"symbol": "005930", "kind": "trade", "price": 71000, "volume": 1000},
        received_at=100.0,
        market="KR",
    )
    second = recorder.capture(
        {"symbol": "005930", "kind": "trade", "price": 71100, "volume": 1025},
        received_at=100.2,
        market="KR",
    )
    reset = recorder.capture(
        {"symbol": "005930", "kind": "trade", "price": 70900, "volume": 5},
        received_at=200.0,
        market="KR",
    )

    assert first["size"] == 0.0
    assert first["volume_partial"] is True
    assert second["size"] == 25.0
    assert second["volume_method"] == "cumulative_delta"
    assert reset["size"] == 0.0
    assert reset["volume_anomaly"] is True
    assert "aggressor_side" not in second


def test_recorder_prefers_provider_trade_size_and_normalizes_exchange_time():
    recorder = orderflow_store.OrderFlowRecorder()

    kr = recorder.capture(
        {"symbol": "005930", "kind": "trade", "price": 71000, "trade_size": 17,
         "volume": 1000, "exchange_time": "093015"},
        received_at=1786233600.0,
        market="KR",
    )
    us = recorder.capture(
        {"symbol": "AAPL", "kind": "trade", "price": 200, "trade_size": 23,
         "volume": 1000, "exchange_date": "20260809", "exchange_time": "153015"},
        received_at=1786233600.0,
        market="US",
    )

    assert kr["size"] == 17.0
    assert kr["volume_method"] == "provider_trade_size"
    assert kr["exchange_at"].endswith("+09:00")
    assert us["size"] == 23.0
    assert us["exchange_at"] == "2026-08-09T15:30:15-04:00"


def test_recorder_assigns_exchange_local_session_date_across_utc_midnight():
    recorder = orderflow_store.OrderFlowRecorder()
    received_at = datetime(2026, 8, 10, 0, 30, tzinfo=timezone.utc).timestamp()

    us = recorder.capture(
        {"symbol": "AAPL", "kind": "trade", "price": 200, "trade_size": 1,
         "exchange_time": "193000"},
        received_at=received_at,
        market="US",
    )
    kr = recorder.capture(
        {"symbol": "005930", "kind": "trade", "price": 71000, "trade_size": 1,
         "exchange_time": "093000"},
        received_at=received_at,
        market="KR",
    )

    assert us["session_date"] == "2026-08-09"
    assert kr["session_date"] == "2026-08-10"


def test_zero_provider_size_falls_back_to_cumulative_delta():
    recorder = orderflow_store.OrderFlowRecorder()
    recorder.capture(
        {"symbol": "AAPL", "kind": "trade", "price": 200, "trade_size": 0, "volume": 100},
        received_at=100,
        market="US",
    )

    event = recorder.capture(
        {"symbol": "AAPL", "kind": "trade", "price": 201, "trade_size": 0, "volume": 125},
        received_at=101,
        market="US",
    )

    assert event["size"] == 25.0
    assert event["volume_method"] == "cumulative_delta"


def test_recorder_samples_books_and_preserves_real_depth():
    recorder = orderflow_store.OrderFlowRecorder(book_sample_seconds=1.0)
    book = {
        "symbol": "005930",
        "kind": "ask",
        "bids": [(70900, 5), (70800, 7)],
        "asks": [(71100, 10), (71200, 20)],
        "best_bid": 70900,
        "best_ask": 71100,
    }

    first = recorder.capture(book, received_at=100.0, market="KR")
    skipped = recorder.capture(book, received_at=100.5, market="KR")
    next_sample = recorder.capture(book, received_at=101.0, market="KR")

    assert first["event_type"] == "book"
    assert first["depth"] == 2
    assert first["bids"] == [[70900.0, 5.0], [70800.0, 7.0]]
    assert skipped is None
    assert next_sample["received_at"] == 101.0


def test_append_and_load_are_symbol_filtered_and_byte_bounded(tmp_path):
    recorder = orderflow_store.OrderFlowRecorder(book_sample_seconds=0)
    events = [
        recorder.capture(
            {"symbol": symbol, "kind": "trade", "price": 100, "volume": 10},
            received_at=100 + index,
            market="US",
        )
        for index, symbol in enumerate(("AAPL", "MSFT"))
    ]

    assert orderflow_store.append_events(events, base_dir=tmp_path, date_utc="2026-08-09") == 2
    loaded = orderflow_store.load_events("AAPL", "2026-08-09", base_dir=tmp_path)
    assert [row["symbol"] for row in loaded] == ["AAPL"]

    aapl_path = orderflow_store.event_path("2026-08-09", "AAPL", tmp_path)
    msft_path = orderflow_store.event_path("2026-08-09", "MSFT", tmp_path)
    assert aapl_path.exists() and msft_path.exists() and aapl_path != msft_path
    before_aapl = aapl_path.read_text(encoding="utf-8")
    before_msft = msft_path.read_text(encoding="utf-8")
    current_total = len(before_aapl.encode("utf-8")) + len(before_msft.encode("utf-8"))
    assert orderflow_store.append_events(
        events,
        base_dir=tmp_path,
        date_utc="2026-08-09",
        max_bytes=current_total,
    ) == 0
    assert aapl_path.read_text(encoding="utf-8") == before_aapl
    assert msft_path.read_text(encoding="utf-8") == before_msft
    assert all(json.loads(line)["version"] == 1 for line in before_aapl.splitlines())


def test_append_partitions_events_by_exchange_session_date(tmp_path):
    events = [
        {"version": 1, "event_type": "trade", "symbol": "AAPL",
         "market": "US", "session_date": "2026-08-09", "received_at": 1.0,
         "price": 100.0, "size": 1.0},
        {"version": 1, "event_type": "trade", "symbol": "005930",
         "market": "KR", "session_date": "2026-08-10", "received_at": 2.0,
         "price": 71000.0, "size": 1.0},
    ]

    assert orderflow_store.append_events(events, base_dir=tmp_path) == 2
    assert orderflow_store.event_path("2026-08-09", "AAPL", tmp_path).exists()
    assert orderflow_store.event_path("2026-08-10", "005930", tmp_path).exists()


def test_prune_partitions_bounds_total_retention_without_touching_other_paths(tmp_path):
    for value in ("2026-08-07", "2026-08-08", "2026-08-09", "2026-08-10"):
        path = tmp_path / value
        path.mkdir()
        (path / "AAPL.jsonl").write_text("{}\n", encoding="utf-8")
    other = tmp_path / "manual-notes"
    other.mkdir()

    result = orderflow_store.prune_partitions(
        base_dir=tmp_path, retention_days=2, as_of_date="2026-08-10",
    )

    assert result["deleted_partitions"] == 2
    assert not (tmp_path / "2026-08-07").exists()
    assert not (tmp_path / "2026-08-08").exists()
    assert (tmp_path / "2026-08-09").exists()
    assert (tmp_path / "2026-08-10").exists()
    assert other.exists()


def test_capture_status_is_atomic_and_disclosed_with_reader_window(tmp_path):
    orderflow_store.write_capture_status(
        {"capture_enabled": True, "dropped_events": 3, "write_failures": 1},
        base_dir=tmp_path,
    )
    event = {"version": 1, "event_type": "trade", "symbol": "AAPL",
             "market": "US", "received_at": 1.0, "price": 100.0, "size": 1.0}
    orderflow_store.append_events([event], base_dir=tmp_path, date_utc="2026-08-09")

    _rows, window = orderflow_store.load_event_window(
        "AAPL", "2026-08-09", base_dir=tmp_path,
    )

    assert window["capture_status"]["dropped_events"] == 3
    assert window["capture_status"]["write_failures"] == 1
    assert window["capture_complete"] is False


def test_reader_window_keeps_capture_completeness_unknown_without_status(tmp_path):
    _rows, window = orderflow_store.load_event_window(
        "AAPL", "2026-08-09", base_dir=tmp_path,
    )

    assert window["capture_status"] == {}
    assert window["capture_complete"] is None


def test_reader_window_scopes_capture_loss_to_requested_session(tmp_path):
    orderflow_store.write_capture_status(
        {"capture_enabled": True, "dropped_events": 3,
         "dropped_by_session": {"2026-08-08": 3}},
        base_dir=tmp_path,
    )

    _rows, window = orderflow_store.load_event_window(
        "AAPL", "2026-08-09", base_dir=tmp_path,
    )

    assert window["capture_status"]["session_dropped_events"] == 0
    assert window["capture_complete"] is True


def test_event_path_rejects_traversal_symbols(tmp_path):
    try:
        orderflow_store.event_path("2026-08-09", "../../secret", tmp_path)
    except ValueError as exc:
        assert "symbol" in str(exc)
    else:
        raise AssertionError("unsafe symbols must not become paths")


def test_load_window_reads_recent_events_and_discloses_truncation(tmp_path):
    events = [
        {
            "version": 1,
            "event_type": "trade",
            "symbol": "AAPL",
            "market": "US",
            "received_at": float(index),
            "price": 100.0 + index,
            "size": 1.0,
        }
        for index in range(20)
    ]
    orderflow_store.append_events(events, base_dir=tmp_path, date_utc="2026-08-09")

    rows, window = orderflow_store.load_event_window(
        "AAPL", "2026-08-09", base_dir=tmp_path, limit=3, max_scan_bytes=512,
    )

    assert [row["received_at"] for row in rows] == [17.0, 18.0, 19.0]
    assert window["truncated"] is True
    assert window["returned_events"] == 3
    assert 0 < window["scanned_bytes"] <= 512
    assert window["file_bytes"] >= window["scanned_bytes"]


def test_load_window_keeps_first_complete_line_when_scan_starts_on_boundary(tmp_path):
    events = [
        {
            "version": 1,
            "event_type": "trade",
            "symbol": "AAPL",
            "market": "US",
            "received_at": float(index),
            "price": 100.0 + index,
            "size": 1.0,
        }
        for index in range(2)
    ]
    orderflow_store.append_events(events, base_dir=tmp_path, date_utc="2026-08-09")
    path = orderflow_store.event_path("2026-08-09", "AAPL", tmp_path)
    last_line_bytes = len(path.read_bytes().splitlines(keepends=True)[-1])

    rows, window = orderflow_store.load_event_window(
        "AAPL", "2026-08-09", base_dir=tmp_path, limit=10,
        max_scan_bytes=last_line_bytes,
    )

    assert [row["received_at"] for row in rows] == [1.0]
    assert window["truncated"] is True


def test_coverage_requires_side_on_every_sized_trade():
    events = [
        {"event_type": "trade", "size": 10, "aggressor_side": "buy"},
        {"event_type": "trade", "size": 20},
        {"event_type": "trade", "size": 0, "aggressor_side": "sell"},
    ]

    coverage = orderflow_store.coverage(events)

    assert coverage["capabilities"]["footprint"] is False
    assert coverage["capabilities"]["bid_ask_delta"] is False


def test_coverage_never_claims_aggressor_side_without_source_field():
    recorder = orderflow_store.OrderFlowRecorder(book_sample_seconds=0)
    events = [
        recorder.capture(
            {"symbol": "AAPL", "kind": "trade", "price": 200, "volume": 100},
            received_at=100,
            market="US",
        ),
        recorder.capture(
            {"symbol": "AAPL", "kind": "trade", "price": 201, "volume": 120},
            received_at=101,
            market="US",
        ),
        recorder.capture(
            {"symbol": "AAPL", "kind": "ask", "bids": [(200, 10)], "asks": [(201, 5)]},
            received_at=102,
            market="US",
        ),
    ]

    coverage = orderflow_store.coverage(events)

    assert coverage["trade_events"] == 2
    assert coverage["book_events"] == 1
    assert coverage["max_depth"] == 1
    assert coverage["capabilities"]["volume_at_price"] is True
    assert coverage["capabilities"]["historical_depth"] is True
    assert coverage["capabilities"]["footprint"] is False
    assert coverage["capabilities"]["bid_ask_delta"] is False
