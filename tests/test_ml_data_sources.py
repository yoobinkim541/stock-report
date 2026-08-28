import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml import data_sources
from ml.data_pipeline import (
    normalize_data_snapshot,
    source_coverage,
    source_freshness,
)
from ml.strategy_studio.contracts import DataStamp, ModelProvenance, serialize_event
from ml.strategy_studio.contracts import deserialize_event


def test_fetch_price_history_uses_stooq_then_clips_as_of(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
    calls = []

    def fake_stooq(ticker, start, end):
        calls.append(("stooq", ticker, start, end))
        return frame

    def fake_yahoo(ticker, start, end):
        calls.append(("yahoo", ticker, start, end))
        return pd.DataFrame()

    monkeypatch.setattr(data_sources, "_stooq_fetcher", fake_stooq)
    monkeypatch.setattr(data_sources, "_yahoo_fetcher", fake_yahoo)
    out = data_sources.fetch_price_history("QQQ", start="2024-01-01", end="2024-01-04", as_of="2024-01-02")
    assert list(out["close"]) == [1.0, 2.0]
    assert calls == [("stooq", "QQQ", "2024-01-01", "2024-01-04")]


def test_fetch_price_history_falls_back_to_yahoo(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=1, freq="D")
    yahoo_frame = pd.DataFrame({"close": [10.0]}, index=idx)

    monkeypatch.setattr(data_sources, "_stooq_fetcher", lambda ticker, start, end: pd.DataFrame())
    monkeypatch.setattr(data_sources, "_yahoo_fetcher", lambda ticker, start, end: yahoo_frame)
    out = data_sources.fetch_close("SPY", start="2024-01-01", end="2024-01-02")
    assert out.name == "SPY"
    assert out.iloc[0] == 10.0


def test_placeholders_raise_clear_errors():
    with pytest.raises(NotImplementedError):
        data_sources.fetch_fred_series("DFF")
    with pytest.raises(NotImplementedError):
        data_sources.fetch_cboe_putcall()


def test_data_snapshot_separates_event_received_and_available_times():
    frame = pd.DataFrame(
        {"close": [101.0, 100.0], "volume": [2, 1]},
        index=pd.to_datetime([
            "2026-08-28T10:05:00+09:00",
            "2026-08-28T10:00:00+09:00",
        ]),
    )
    frame.attrs.update({
        "received_at": "2026-08-28T10:05:02+09:00",
        "available_at": "2026-08-28T10:05:03+09:00",
        "raw_ref": "raw/kis/2026-08-28/A.json",
    })

    snapshot = normalize_data_snapshot(
        frame,
        symbol="A",
        source="kis",
        timeframe="5m",
        session="regular",
        adjustment="raw",
    )

    assert [stamp.timestamp for stamp in snapshot.data_stamps] == [
        "2026-08-28T10:00:00+09:00",
        "2026-08-28T10:05:00+09:00",
    ]
    assert snapshot.data_stamps[0].timestamp != snapshot.data_stamps[0].received_at
    assert snapshot.data_stamps[0].received_at == "2026-08-28T10:05:02+09:00"
    assert snapshot.data_stamps[0].available_at == "2026-08-28T10:05:03+09:00"
    assert snapshot.raw_ref == "raw/kis/2026-08-28/A.json"


def test_snapshot_rejects_missing_metadata_and_invalid_timestamp_order():
    frame = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T10:00:00+09:00"]))

    with pytest.raises(ValueError, match="source"):
        normalize_data_snapshot(frame, symbol="A", source="", timeframe="5m", session="regular", adjustment="raw")

    with pytest.raises(ValueError, match="available_at"):
        DataStamp(
            symbol="A",
            timestamp="2026-08-28T10:00:00Z",
            source="kis",
            timeframe="5m",
            quality="complete",
            received_at="2026-08-28T10:00:02Z",
            available_at="2026-08-28T10:00:01Z",
        )


def test_snapshot_marks_missing_event_and_availability_metadata_without_fabrication():
    frame = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-08-28T10:00:00Z", "NaT"]),
    )

    snapshot = normalize_data_snapshot(
        frame,
        symbol="A",
        source="kis",
        timeframe="5m",
        session="regular",
        adjustment="raw",
        received_at="2026-08-28T10:00:02Z",
    )

    assert len(snapshot.data_stamps) == 1
    assert "invalid_event_timestamp" in snapshot.warnings
    assert "available_at_missing" in snapshot.warnings
    assert snapshot.data_stamps[0].available_at is None


def test_stale_snapshot_is_visible_in_freshness_and_coverage_diagnostics():
    frame = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T09:00:00Z"]))
    frame.attrs["received_at"] = "2026-08-28T09:00:02Z"
    snapshot = normalize_data_snapshot(frame, symbol="A", source="kis", timeframe="5m", session="regular", adjustment="raw")

    freshness = source_freshness(snapshot, evaluation_at="2026-08-28T10:00:00Z", max_age_seconds=60)
    coverage = source_coverage(
        [snapshot],
        expected_symbols=["A", "B"],
        evaluation_at="2026-08-28T10:00:00Z",
        max_age_seconds=60,
    )

    assert freshness["status"] == "stale"
    assert "data_stale" in freshness["warnings"]
    assert coverage["coverage_ratio"] == pytest.approx(0.5)
    assert "B" in coverage["missing_symbols"]
    assert any("data_stale" in warning for warning in coverage["warnings"])


def test_explicit_stale_quality_remains_stale_even_with_recent_transport_time():
    snapshot = normalize_data_snapshot(
        pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T09:00:00Z"])),
        symbol="A", source="kis", timeframe="5m", session="regular", adjustment="raw",
        received_at="2026-08-28T09:00:02Z", quality="stale",
    )

    freshness = source_freshness(snapshot, evaluation_at="2026-08-28T09:00:10Z", max_age_seconds=60)

    assert freshness["status"] == "stale"
    assert "data_stale" in freshness["warnings"]


def test_source_coverage_merges_symbols_from_same_source():
    snapshots = [
        normalize_data_snapshot(
            pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T09:00:00Z"])),
            symbol="A", source="kis", timeframe="5m", session="regular", adjustment="raw",
            received_at="2026-08-28T09:00:02Z", available_at="2026-08-28T09:00:03Z",
        ),
        normalize_data_snapshot(
            pd.DataFrame({"close": [200.0]}, index=pd.to_datetime(["2026-08-28T09:00:00Z"])),
            symbol="B", source="kis", timeframe="5m", session="regular", adjustment="raw",
            received_at="2026-08-28T09:00:02Z", available_at="2026-08-28T09:00:03Z",
        ),
    ]

    coverage = source_coverage(snapshots, expected_symbols=["A", "B"], evaluation_at="2026-08-28T09:01:00Z", max_age_seconds=60)

    assert coverage["coverage_ratio"] == pytest.approx(1.0)
    assert coverage["sources"]["kis"]["symbols"] == ["A", "B"]
    assert coverage["sources"]["kis"]["observations"] == 2
    assert coverage["sources"]["kis"]["freshness"]["status"] == "fresh"


def test_snapshot_and_model_provenance_are_json_safe():
    stamp = DataStamp("A", "2026-08-28T10:00:00+09:00", "kis", "5m", "complete")
    snapshot = normalize_data_snapshot(
        pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T10:00:00+09:00"])),
        symbol="A", source="kis", timeframe="5m", session="regular", adjustment="raw",
        received_at="2026-08-28T10:00:02+09:00",
    )
    provenance = ModelProvenance(
        model_id="model-v1",
        feature_version="features-v1",
        train_start="2025-01-01T00:00:00Z",
        train_end="2026-08-27T00:00:00Z",
        code_commit="abc123",
        seed=42,
        metrics={"sharpe": None, "rmse": 0.1},
    )

    payload = {"stamp": serialize_event(stamp), "snapshot": snapshot.to_dict(), "model": provenance.to_dict()}
    json.dumps(payload, allow_nan=False)
    assert deserialize_event(snapshot.to_dict(), "snapshot") == snapshot
    assert deserialize_event(provenance.to_dict(), "model") == provenance


def test_legacy_price_frame_keeps_shape_when_snapshot_is_attached():
    from ml import data_pipeline

    frame = pd.DataFrame(
        {"Open": [99.0], "High": [101.0], "Low": [98.0], "Close": [100.0], "Volume": [10]},
        index=pd.to_datetime(["2026-08-28T10:00:00Z"]),
    )
    columns = frame.columns.tolist()

    out = data_pipeline._attach_snapshot_metadata(
        frame, symbol="A", source="yfinance", timeframe="1d", session="regular", adjustment="adjusted",
    )

    assert out is frame
    assert out.columns.tolist() == columns
    assert out.attrs["data_snapshot"].data_stamps[0].source == "yfinance"
    assert out.attrs["provenance"]["version"] == out.attrs["data_snapshot"].snapshot_id
