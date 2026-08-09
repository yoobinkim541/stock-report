from __future__ import annotations

import pytest

from dashboard import chart_conditions


@pytest.fixture
def contexts():
    return {
        ("AAPL", "1d"): {
            "previous": {"close": 198.0, "rsi_14": 54.0, "forward_pe": 24.0, "relative_return": 2.0},
            "current": {"close": 202.0, "rsi_14": 58.0, "forward_pe": 24.5, "relative_return": 3.5},
            "events": [{"date": "2026-08-08T11:30:00Z", "kind": "earnings", "label": "beat"}],
            "as_of": "2026-08-08T12:00:00Z",
        },
        ("QQQ", "1h"): {
            "previous": {"close": 499.0},
            "current": {"close": 501.0},
            "events": [],
            "as_of": "2026-08-08T12:00:00Z",
        },
    }


def test_legacy_all_migrates_to_canonical_tree_and_requirements():
    condition = chart_conditions.normalize_condition({
        "all": [
            {"type": "price", "symbol": "aapl", "timeframe": "1d", "operator": "greater_than", "value": 200},
            {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 70},
        ],
    })

    assert condition["op"] == "all"
    assert condition["children"][0]["symbol"] == "AAPL"
    assert chart_conditions.validate_condition(condition) == []
    assert chart_conditions.condition_requirements(
        condition, default_symbol="AAPL", default_timeframe="1d",
    ) == {("AAPL", "1d")}


def test_nested_condition_preserves_boolean_semantics(contexts):
    condition = {
        "op": "all",
        "children": [
            {"type": "indicator", "symbol": "AAPL", "timeframe": "1d", "field": "rsi_14", "operator": "greater_than", "value": 55},
            {"op": "any", "children": [
                {"type": "price", "symbol": "QQQ", "timeframe": "1h", "field": "close", "operator": "crossing_up", "value": 500},
                {"type": "fundamental", "symbol": "AAPL", "timeframe": "1d", "field": "forward_pe", "operator": "less_than", "value": 25},
            ]},
        ],
    }

    result = chart_conditions.evaluate_condition(condition, contexts)

    assert result["matched"] is True
    assert result["status"] == "true"
    assert len(result["trace"]) >= 5
    assert chart_conditions.condition_requirements(
        condition, default_symbol="MSFT", default_timeframe="5m",
    ) == {("AAPL", "1d"), ("QQQ", "1h")}


def test_unknown_data_does_not_turn_true_under_none(contexts):
    condition = {
        "op": "none",
        "children": [
            {"type": "indicator", "symbol": "MISSING", "timeframe": "1d", "field": "rsi_14", "operator": "greater_than", "value": 70},
        ],
    }

    result = chart_conditions.evaluate_condition(condition, contexts)

    assert result["matched"] is False
    assert result["status"] == "unknown"
    assert "missing context" in result["reason"]


def test_crossing_range_change_and_relative_performance(contexts):
    condition = {
        "op": "all",
        "children": [
            {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "crossing_up", "value": 200},
            {"type": "indicator", "symbol": "AAPL", "timeframe": "1d", "field": "rsi_14", "operator": "between", "value": [50, 60]},
            {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "change_greater_than", "value": 1.5, "unit": "percent"},
            {"type": "relative_performance", "symbol": "AAPL", "timeframe": "1d", "field": "relative_return", "operator": "greater_than", "value": 3},
        ],
    }

    result = chart_conditions.evaluate_condition(condition, contexts)

    assert result["matched"] is True
    leaves = [row for row in result["trace"] if row["node"] == "leaf"]
    assert all(row["status"] == "true" for row in leaves)


def test_drawing_line_and_happened_within_event(contexts):
    condition = {
        "op": "all",
        "children": [
            {
                "type": "drawing_line", "symbol": "AAPL", "timeframe": "1d",
                "operator": "crossing_up", "x0": "2026-08-08T10:00:00Z", "y0": 198,
                "x1": "2026-08-08T12:00:00Z", "y1": 200,
            },
            {
                "type": "event", "symbol": "AAPL", "timeframe": "1d", "field": "earnings",
                "operator": "happened_within", "window": 1, "unit": "hour",
            },
        ],
    }

    result = chart_conditions.evaluate_condition(condition, contexts, now="2026-08-08T12:00:00Z")

    assert result["matched"] is True
    drawing = next(row for row in result["trace"] if row.get("condition_type") == "drawing_line")
    assert drawing["threshold"] == pytest.approx(200.0)


def test_explanation_and_validation_are_human_readable():
    condition = {"op": "any", "children": [
        {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "greater_than", "value": 200},
        {"type": "indicator", "symbol": "AAPL", "timeframe": "1d", "field": "rsi_14", "operator": "less_than", "value": 30},
    ]}

    explanation = chart_conditions.explain_condition(condition)

    assert "ANY" in explanation
    assert "AAPL 1d close" in explanation
    assert "rsi_14" in explanation
    assert chart_conditions.validate_condition({"op": "none", "children": []})


def test_cross_symbol_operand_confirmation_expiration_and_session(contexts):
    contexts[("AAPL", "1d")] = {
        **contexts[("AAPL", "1d")],
        "confirmed": False,
        "session": "regular",
    }
    cross_symbol = {
        "type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close",
        "operator": "less_than",
        "value": {"symbol": "QQQ", "timeframe": "1h", "field": "close"},
        "confirmation": "intrabar",
    }
    waiting = {**cross_symbol, "confirmation": "bar_close"}
    expired = {**cross_symbol, "expires_at": "2026-08-07T12:00:00Z"}
    wrong_session = {**cross_symbol, "session": "extended"}

    assert chart_conditions.condition_requirements(
        cross_symbol, default_symbol="MSFT", default_timeframe="5m",
    ) == {("AAPL", "1d"), ("QQQ", "1h")}
    assert chart_conditions.evaluate_condition(cross_symbol, contexts)["matched"] is True
    assert chart_conditions.evaluate_condition(waiting, contexts)["status"] == "unknown"
    assert chart_conditions.evaluate_condition(
        expired, contexts, now="2026-08-08T12:00:00Z",
    )["status"] == "false"
    assert chart_conditions.evaluate_condition(wrong_session, contexts)["status"] == "unknown"


def test_malformed_evaluation_returns_unknown_trace_instead_of_raising():
    result = chart_conditions.evaluate_condition({"op": "all", "children": "bad"}, {})

    assert result["matched"] is False
    assert result["status"] == "unknown"
    assert "children" in result["reason"]
