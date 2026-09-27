import math

from ml.adaptive import Ledger

from ml.recommendation_contracts import (
    canonical_horizon,
    canonical_outcome_key,
    dedupe_canonical_outcomes,
    finite_json_value,
    sanitize_record,
)


def test_canonical_horizon_maps_supported_numeric_aliases():
    assert canonical_horizon(20) == "20d"
    assert canonical_horizon("20d") == "20d"
    assert canonical_horizon(60) == "60d"
    assert canonical_horizon("60d") == "60d"
    assert canonical_horizon("30m") == "30m"
    assert canonical_horizon(7) == "7"


def test_finite_json_value_replaces_nested_non_finite_values():
    value = {"nan": float("nan"), "nested": [1, float("inf"), {"ok": 2.0}]}

    assert finite_json_value(value) == {"nan": None, "nested": [1, None, {"ok": 2.0}]}
    assert sanitize_record(value) == {"nan": None, "nested": [1, None, {"ok": 2.0}]}


def test_dedupe_prefers_canonical_valid_latest_matured_outcome():
    rows = [
        {"decision_id": "d:h20", "horizon": 20, "status": "matured", "matured_at": "2026-09-03"},
        {"decision_id": "d:h20d", "horizon": "20d", "status": "pending", "matured_at": "2026-09-09"},
        {"decision_id": "d:h20d", "horizon": "20d", "status": "matured", "matured_at": "2026-09-08"},
        {"decision_id": "d:h20d", "horizon": "20d", "status": "matured", "matured_at": "2026-09-07"},
    ]

    assert canonical_outcome_key("d", 20) == ("d", "20d")
    assert dedupe_canonical_outcomes(rows) == [rows[2]]


def test_dedupe_prefers_canonical_decision_id_when_horizon_is_omitted():
    legacy = {"decision_id": "d:h20", "status": "matured"}
    canonical = {"decision_id": "d:h20d", "status": "matured"}

    assert dedupe_canonical_outcomes([legacy, canonical]) == [canonical]
    assert dedupe_canonical_outcomes([canonical, legacy]) == [canonical]


def test_finite_json_value_keeps_finite_scalars():
    assert finite_json_value(3) == 3
    assert finite_json_value(2.5) == 2.5
    assert finite_json_value(True) is True
    assert math.isfinite(finite_json_value(2.5))


def test_ledger_sanitizes_only_the_serialized_copy(tmp_path):
    ledger = Ledger("recommendations", base_dir=tmp_path)
    record = {"id": "d1", "features": {"score": float("nan")}}

    ledger.log_decision(record)

    assert math.isnan(record["features"]["score"])
    assert ledger.read_decisions() == [{"id": "d1", "features": {"score": None}}]
