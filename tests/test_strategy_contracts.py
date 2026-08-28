from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ml.strategy_studio import (
    DataStamp,
    FillEvent,
    OrderIntent,
    PositionState,
    SignalOutput,
    StrategySpec,
    deserialize_event,
    serialize_event,
    strategy_spec_hash,
)


def test_strategy_spec_accepts_profile_signal_and_promotion_fields():
    spec = StrategySpec.from_dict({
        "name": "5m momentum",
        "market": "kr",
        "timeframe": "5m",
        "base_symbol": "005930.KS",
        "data_profile": "kr_intraday",
        "execution_profile": "kr_intraday",
        "universe": {"type": "list", "symbols": ["005930.KS"]},
        "signal": {"type": "ensemble", "members": [{"type": "rule", "ref": "rsi"}]},
        "portfolio": {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.15},
        "execution": {"latency_ms": 500, "partial_fill": True},
        "promotion": {"environment": "sandbox"},
    })

    assert spec.data_profile == "kr_intraday"
    assert spec.execution_profile == "kr_intraday"
    assert spec.signal["type"] == "ensemble"
    assert spec.portfolio["max_position_pct"] == 0.15
    assert spec.promotion["environment"] == "sandbox"


def test_strategy_spec_rejects_unknown_profile_and_python_plugin():
    with pytest.raises(ValueError, match="unsupported data profile"):
        StrategySpec.from_dict({"name": "bad", "data_profile": "unknown"})

    with pytest.raises(ValueError, match="python"):
        StrategySpec.from_dict({
            "name": "bad",
            "signal": {"type": "model", "plugin": "python"},
        })


def test_strategy_spec_rejects_unsafe_plugins_in_nested_blocks():
    for block in (
        {"signal": {"type": "rule", "config": {"plugin": "shell"}}},
        {"features": [{"plugin": "exec"}]},
        {"execution": {"plugin": "unregistered"}},
    ):
        with pytest.raises(ValueError, match="plugin"):
            StrategySpec.from_dict({"name": "bad", **block})


def test_strategy_spec_defaults_keep_legacy_shape_and_new_blocks_empty():
    spec = StrategySpec.from_dict({"name": "legacy"})

    assert spec.market == "us"
    assert spec.timeframe == "1d"
    assert spec.data_profile == "generic"
    assert spec.execution_profile == "bar"
    assert spec.features == []
    assert spec.signal == {}
    assert spec.portfolio == {}
    assert spec.execution == {}
    assert spec.promotion == {}
    assert "data_profile" not in spec.to_dict()
    assert "signal" not in spec.to_dict()


def test_strategy_spec_keeps_legacy_positional_constructor_order():
    spec = StrategySpec(
        "legacy", "us", "1d", "QQQ", {}, [], {}, {}, {}, {}, {}, {}, 3, "spec-3"
    )

    assert spec.version == 3
    assert spec.id == "spec-3"
    assert spec.data_profile == "generic"


def test_strategy_spec_retains_explicit_extended_fields_in_canonical_dict():
    spec = StrategySpec.from_dict({
        "name": "extended",
        "data_profile": "generic",
        "signal": {},
    })

    canonical = spec.to_dict()

    assert canonical["data_profile"] == "generic"
    assert canonical["signal"] == {}
    assert "execution_profile" not in canonical
    assert "portfolio" not in canonical


def test_legacy_strategy_hash_ignores_implicit_contract_defaults():
    legacy = {"name": "legacy", "base_symbol": "QQQ"}

    assert StrategySpec.from_dict(legacy).to_dict()["version"] == 1
    assert strategy_spec_hash(legacy) == "2994ec31ddd1ec1ea164"


def test_data_stamp_normalizes_naive_timestamp_and_requires_provenance():
    stamp = DataStamp(
        symbol="AAPL",
        timestamp="2026-08-28T10:00:00",
        source="yahoo",
        timeframe="1d",
        quality="complete",
    )

    assert stamp.timestamp == "2026-08-28T10:00:00+00:00"

    with pytest.raises(ValueError, match="source"):
        DataStamp(symbol="AAPL", timestamp=datetime.now(timezone.utc), source="", timeframe="1d", quality="complete")


def test_order_and_position_dtos_reject_negative_quantities_and_prices():
    with pytest.raises(ValueError, match="quantity"):
        OrderIntent(symbol="AAPL", side="buy", quantity=-1, decision_at="2026-08-28T10:00:00Z")

    with pytest.raises(ValueError, match="price"):
        OrderIntent(symbol="AAPL", side="buy", quantity=1, decision_at="2026-08-28T10:00:00Z", decision_price=-1)

    with pytest.raises(ValueError, match="quantity"):
        PositionState(symbol="AAPL", quantity=-1, average_price=100)


def test_fill_event_round_trip_preserves_partial_fill_fields():
    event = FillEvent(
        run_id="run-1", symbol="AAPL", side="buy", requested_qty=100,
        filled_qty=60, decision_price=100.0, fill_price=100.2,
        status="partial", decision_at="2026-08-28T10:00:00Z",
        filled_at="2026-08-28T10:00:01Z",
    )

    payload = serialize_event(event)
    restored = deserialize_event(payload, "fill")

    assert event.decision_at == "2026-08-28T10:00:00+00:00"
    assert event.filled_at == "2026-08-28T10:00:01+00:00"
    assert payload["decision_at"] == "2026-08-28T10:00:00+00:00"
    assert restored == event
    assert restored.filled_qty == 60
    assert restored.status == "partial"


def test_event_serialization_round_trip_preserves_datetime_and_optional_fields():
    events = [
        DataStamp("AAPL", "2026-08-28T10:00:00+09:00", "kis", "5m", "complete"),
        SignalOutput("AAPL", 0.7, 0.8, "2026-08-28T10:00:00Z", feature_version="f1", model_version="m1"),
        OrderIntent("AAPL", "sell", 5, "2026-08-28T10:00:00Z", decision_price=101.0, order_type="limit", limit_price=100.5),
        PositionState("AAPL", 5, 100.5, as_of="2026-08-28T10:00:00Z"),
    ]
    event_types = ["data", "signal", "order", "position"]

    for event, event_type in zip(events, event_types):
        assert all(
            not isinstance(value, datetime)
            for value in (event.__getattribute__(name) for name in ("timestamp", "as_of", "decision_at", "filled_at") if hasattr(event, name))
        )
        assert deserialize_event(serialize_event(event), event_type) == event


def test_strategy_spec_rejects_unknown_nested_types_and_profiles():
    with pytest.raises(ValueError, match="unsupported signal type"):
        StrategySpec.from_dict({"name": "bad", "signal": {"type": "random"}})

    with pytest.raises(ValueError, match="unsupported feature type"):
        StrategySpec.from_dict({"name": "bad", "features": [{"type": "unknown_plugin"}]})

    with pytest.raises(ValueError, match="unsupported execution type"):
        StrategySpec.from_dict({"name": "bad", "execution": {"type": "unknown_plugin"}})

    with pytest.raises(ValueError, match="unsupported execution profile"):
        StrategySpec.from_dict({"name": "bad", "execution_profile": "unknown"})

    with pytest.raises(ValueError, match="unsupported promotion environment"):
        StrategySpec.from_dict({"name": "bad", "promotion": {"environment": "dev"}})
