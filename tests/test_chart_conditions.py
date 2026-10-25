from __future__ import annotations

import pytest

from dashboard import chart_conditions


@pytest.fixture
def contexts():
    return {
        ("AAPL", "1d"): {
            "previous": {"time": "2026-08-08T11:00:00Z", "close": 198.0, "rsi_14": 54.0, "forward_pe": 24.0, "relative_return": 2.0},
            "current": {"time": "2026-08-08T12:00:00Z", "close": 202.0, "rsi_14": 58.0, "forward_pe": 24.5, "relative_return": 3.5},
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
    extended_leaf = {**cross_symbol, "session": "extended"}

    assert chart_conditions.condition_requirements(
        cross_symbol, default_symbol="MSFT", default_timeframe="5m",
    ) == {("AAPL", "1d"), ("QQQ", "1h")}
    assert chart_conditions.evaluate_condition(cross_symbol, contexts)["matched"] is True
    assert chart_conditions.evaluate_condition(waiting, contexts)["status"] == "unknown"
    assert chart_conditions.evaluate_condition(
        expired, contexts, now="2026-08-08T12:00:00Z",
    )["status"] == "false"
    # extended 조건은 정규장 봉도 포함하고, regular 조건은 시간외 봉에서 판정하지 않는다.
    assert chart_conditions.evaluate_condition(extended_leaf, contexts)["matched"] is True
    contexts[("AAPL", "1d")] = {**contexts[("AAPL", "1d")], "session": "extended"}
    assert chart_conditions.evaluate_condition(cross_symbol, contexts)["status"] == "unknown"


def test_malformed_evaluation_returns_unknown_trace_instead_of_raising():
    result = chart_conditions.evaluate_condition({"op": "all", "children": "bad"}, {})

    assert result["matched"] is False
    assert result["status"] == "unknown"
    assert "children" in result["reason"]


# ── C-fix-alerts: 경사선 교차는 각 봉 시각의 선값으로, 기본 symbol 결합 ──

def _line_context(previous_close, current_close, *, previous_time="2026-08-03T14:00:00Z"):
    previous = {"close": previous_close}
    if previous_time:
        previous["time"] = previous_time
    return {("AAPL", "1h"): {
        "previous": previous,
        "current": {"close": current_close, "time": "2026-08-03T15:00:00Z"},
        "as_of": "2026-08-03T15:00:00Z",
    }}


def _line(operator, y0=100.0, y1=110.0):
    return {
        "type": "drawing_line", "symbol": "AAPL", "timeframe": "1h", "operator": operator,
        "x0": "2026-08-03T14:00:00Z", "y0": y0, "x1": "2026-08-03T15:00:00Z", "y1": y1,
    }


@pytest.mark.parametrize("operator, line, closes, status", [
    ("crossing_up", (100, 110), (105, 115), "false"),     # 위→위 (선 100→110)
    ("crossing_up", (100, 110), (95, 105), "false"),      # 아래→아래
    ("crossing_up", (100, 110), (99, 111), "true"),       # 아래→위
    ("crossing_up", (100, 110), (99, 110), "true"),       # 정확 접촉
    ("crossing_down", (110, 100), (111, 99), "true"),     # 하락선 위→아래
    ("crossing_down", (110, 100), (105, 95), "false"),    # 하락선 아래→아래
    ("crossing", (100, 100), (99, 101), "true"),          # 수평선
])
def test_C_fix_alerts_sloped_line_crossing_uses_line_value_at_each_bar(operator, line, closes, status):
    result = chart_conditions.evaluate_condition(_line(operator, *line), _line_context(*closes))

    assert result["status"] == status


def test_C_fix_alerts_sloped_line_crossing_without_previous_time_is_unknown():
    result = chart_conditions.evaluate_condition(
        _line("crossing_up"), _line_context(99, 111, previous_time=None),
    )

    assert result["status"] == "unknown"
    assert result["reason"] == "missing previous value"


def test_C_fix_alerts_bound_defaults_resolve_omitted_leaf_among_multiple_contexts():
    condition = {"op": "all", "children": [
        {"type": "price", "operator": "greater_than", "value": 100},
        {"type": "price", "timeframe": "1d", "operator": "greater_than", "value": 100},
        {"type": "price", "symbol": "QQQ", "timeframe": "1h", "operator": "greater_than",
         "value": {"field": "vwap"}},
    ]}
    bound = chart_conditions.bind_condition_defaults(condition, default_symbol="aapl", default_timeframe="1h")
    contexts = {
        ("AAPL", "1h"): {"previous": {"close": 100.0}, "current": {"close": 101.0}},
        ("AAPL", "1d"): {"previous": {"close": 100.0}, "current": {"close": 102.0}},
        ("QQQ", "1h"): {"previous": {"close": 500.0, "vwap": 499.0}, "current": {"close": 501.0, "vwap": 500.0}},
    }

    assert [(leaf["symbol"], leaf["timeframe"]) for leaf in bound["children"]] == [
        ("AAPL", "1h"), ("AAPL", "1d"), ("QQQ", "1h"),
    ]
    assert bound["children"][2]["value"] == {"field": "vwap", "symbol": "QQQ", "timeframe": "1h"}
    assert chart_conditions.evaluate_condition(bound, contexts)["status"] == "true"
    assert chart_conditions.condition_requirements(
        condition, default_symbol="AAPL", default_timeframe="1h",
    ) == set(contexts)
    assert chart_conditions.explain_condition(
        condition, default_symbol="AAPL", default_timeframe="1h",
    ).startswith("ALL(AAPL 1h close greater_than 100; AAPL 1d close")
