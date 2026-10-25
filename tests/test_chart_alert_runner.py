from __future__ import annotations

import pandas as pd
import pytest

from agent_console import chart_alert_runner


# 일봉(8/3·8/4)과 1h 봉(8/4 18:00Z·19:00Z)이 모두 8/4 20:30Z(16:30 ET) 기준 확정·최신이다.
AS_OF = "2026-08-04T20:30:00Z"


def _bars(values, *, timeframe="1d"):
    # 2026-08-03(월) 14:00Z = 미국 정규장 10:00 ET — 세션 정책이 regular 로 분류하는 봉.
    frequency = "1h" if timeframe == "1h" else "1D"
    start = "2026-08-04T18:00:00Z" if timeframe == "1h" else "2026-08-03T14:00:00Z"
    index = pd.date_range(start, periods=len(values), freq=frequency)
    return pd.DataFrame({"close": values, "volume": range(100, 100 + len(values))}, index=index)


def _multi_rule(rule_id="multi"):
    return {
        "id": rule_id,
        "symbol": "AAPL",
        "timeframe": "1d",
        "name": "AAPL and QQQ",
        "enabled": True,
        "frequency": "repeating",
        "condition": {
            "op": "all",
            "children": [
                {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "greater_than", "value": 100},
                {"type": "price", "symbol": "QQQ", "timeframe": "1h", "field": "close", "operator": "crossing_up", "value": 500},
            ],
        },
    }


def test_build_condition_contexts_uses_every_symbol_timeframe_and_source_timestamp():
    contexts = chart_alert_runner.build_condition_contexts(
        _multi_rule(),
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
        as_of=AS_OF,
    )

    assert set(contexts) == {("AAPL", "1d"), ("QQQ", "1h")}
    assert contexts[("AAPL", "1d")]["current"]["close"] == 101
    assert contexts[("QQQ", "1h")]["previous"]["close"] == 499
    assert contexts[("QQQ", "1h")]["as_of"].endswith("+00:00")


def test_evaluate_alert_rules_cross_symbol_records_full_audit_but_compact_event():
    states: list[dict] = []
    events = chart_alert_runner.evaluate_alert_rules(
        [_multi_rule()],
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
        as_of=AS_OF,
        state_sink=states,
    )

    assert len(events) == 1
    assert events[0]["alert_id"] == "multi"
    assert "evaluation_trace" not in events[0]
    assert states[0]["matched"] is True
    assert states[0]["missing_contexts"] == []
    assert len(states[0]["trace"]) >= 3
    assert set(states[0]["source_timestamps"]) == {"AAPL:1d", "QQQ:1h"}


def test_missing_required_context_is_audited_and_one_bad_rule_does_not_stop_others():
    states: list[dict] = []
    valid = {
        **_multi_rule("valid"),
        "condition": {"type": "price", "operator": "greater_than", "value": 100},
    }
    events = chart_alert_runner.evaluate_alert_rules(
        [_multi_rule("missing"), {"id": "bad", "symbol": "AAPL", "condition": {"op": "all", "children": "bad"}}, valid],
        {"AAPL": _bars([99, 101])},
        as_of=AS_OF,
        state_sink=states,
    )

    assert [event["alert_id"] for event in events] == ["valid"]
    by_id = {state["rule_id"]: state for state in states}
    assert by_id["missing"]["missing_contexts"] == [{"symbol": "QQQ", "timeframe": "1h"}]
    assert by_id["bad"]["matched"] is False
    assert "children" in by_id["bad"]["reason"]


def test_once_only_rule_remains_idempotent_and_preserves_triggered_state():
    rule = {
        **_multi_rule("once"),
        "frequency": "once",
        "last_state": {"triggered": True, "last_checked_at": "2026-08-01T00:00:00Z"},
    }
    states: list[dict] = []

    events = chart_alert_runner.evaluate_alert_rules(
        [rule],
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
        as_of=AS_OF,
        state_sink=states,
    )
    assert events == []
    assert states[0]["triggered"] is True
    assert states[0]["reason"] == "already_triggered"


# ── C-fix-alerts: 봉 시각·세션·canonical tree ──

def _hourly(values, times):
    return pd.DataFrame({"close": values}, index=pd.DatetimeIndex(times))


def _price_rule(**leaf):
    return {
        "id": "p", "symbol": "AAPL", "timeframe": "1h", "enabled": True, "frequency": "repeating",
        "condition": {"type": "price", "operator": "crossing_up", "value": 100, **leaf},
    }


def test_C_fix_alerts_future_bars_are_excluded_from_evaluation():
    bars = _hourly([99, 101], ["2099-08-03T14:00:00Z", "2099-08-03T15:00:00Z"])
    states: list[dict] = []

    events = chart_alert_runner.evaluate_alert_rules(
        [_price_rule()], {("AAPL", "1h"): bars}, as_of="2026-08-03T16:00:00Z", state_sink=states,
    )

    assert events == []
    assert states[0]["status"] == "unknown"
    assert states[0]["missing_contexts"] == [{"symbol": "AAPL", "timeframe": "1h"}]


def test_C_fix_alerts_in_progress_bar_is_unconfirmed_and_bar_close_uses_last_closed_pair():
    bars = _hourly([99, 99.5, 101], ["2026-08-03T14:00:00Z", "2026-08-03T15:00:00Z", "2026-08-03T16:00:00Z"])
    as_of = "2026-08-03T16:30:00Z"  # 16:00Z 1h 봉은 17:00Z 에 마감 — 진행 중

    contexts = chart_alert_runner.build_condition_contexts(_price_rule(), {("AAPL", "1h"): bars}, as_of=as_of)
    context = contexts[("AAPL", "1h")]
    assert context["confirmed"] is False
    assert context["session"] == "regular"
    assert context["closed"]["confirmed"] is True
    assert context["closed"]["current"]["close"] == 99.5

    bar_close = chart_alert_runner.evaluate_alert_rules([_price_rule()], {("AAPL", "1h"): bars}, as_of=as_of)
    intrabar = chart_alert_runner.evaluate_alert_rules(
        [_price_rule(confirmation="intrabar")], {("AAPL", "1h"): bars}, as_of=as_of,
    )
    assert bar_close == []
    assert len(intrabar) == 1 and intrabar[0]["bar_time"] == "2026-08-03T16:00:00+00:00"


def test_C_fix_alerts_out_of_session_bar_does_not_fire_regular_session_alert():
    # 2026-08-01 은 토요일, 2026-08-03 01:00Z 는 미국 정규장 밖. 평가 시각(월 09:00 ET, 개장 전)
    # 기준으로 두 봉 모두 최신(정규장 경과 0분)이라 세션 규칙만 판정에 작용한다.
    weekend = _hourly([99, 101], ["2026-08-01T14:00:00Z", "2026-08-01T15:00:00Z"])
    overnight = _hourly([99, 101], ["2026-08-03T00:00:00Z", "2026-08-03T01:00:00Z"])

    for bars in (weekend, overnight):
        states: list[dict] = []
        events = chart_alert_runner.evaluate_alert_rules(
            [_price_rule()], {("AAPL", "1h"): bars}, as_of="2026-08-03T13:00:00Z", state_sink=states,
        )
        assert events == []
        assert states[0]["reason"] == "session mismatch"
    extended = chart_alert_runner.evaluate_alert_rules(
        [_price_rule(session="extended")], {("AAPL", "1h"): overnight}, as_of="2026-08-03T13:00:00Z",
    )
    assert len(extended) == 1


def test_C_fix_alerts_default_symbol_leaf_is_bound_when_other_symbols_are_present():
    rule = {
        "id": "mixed", "symbol": "AAPL", "timeframe": "1h", "enabled": True, "frequency": "repeating",
        "condition": {"op": "all", "children": [
            {"type": "price", "operator": "greater_than", "value": 100},
            {"type": "price", "symbol": "QQQ", "timeframe": "1h", "operator": "greater_than", "value": 100},
        ]},
    }
    times = ["2026-08-03T14:00:00Z", "2026-08-03T15:00:00Z"]
    states: list[dict] = []

    events = chart_alert_runner.evaluate_alert_rules(
        [rule], {("AAPL", "1h"): _hourly([101, 102], times), ("QQQ", "1h"): _hourly([101, 102], times)},
        as_of="2026-08-03T18:00:00Z", state_sink=states,
    )

    assert len(events) == 1
    assert [row["symbol"] for row in states[0]["trace"] if row["node"] == "leaf"] == ["AAPL", "QQQ"]
    assert states[0]["explanation"].startswith("ALL(AAPL 1h close greater_than 100; QQQ 1h close")
    assert chart_alert_runner.alert_requirements(rule) == {("AAPL", "1h"), ("QQQ", "1h")}


def test_C_fix_alerts_event_id_is_stable_per_bar_and_changes_with_new_bar_or_condition():
    times = ["2026-08-03T14:00:00Z", "2026-08-03T15:00:00Z", "2026-08-03T16:00:00Z"]
    two = {("AAPL", "1h"): _hourly([99, 101], times[:2])}
    three = {("AAPL", "1h"): _hourly([99, 101, 102], times)}
    rule = {**_price_rule(), "condition": {"type": "price", "operator": "greater_than", "value": 100}}
    other = {**rule, "condition": {"type": "price", "operator": "greater_than", "value": 100.5}}
    as_of = "2026-08-03T18:00:00Z"

    first = chart_alert_runner.evaluate_alert_rules([rule], two, as_of=as_of)[0]["event_id"]
    again = chart_alert_runner.evaluate_alert_rules([rule], two, as_of="2026-08-03T18:30:00Z")[0]["event_id"]
    new_bar = chart_alert_runner.evaluate_alert_rules([rule], three, as_of=as_of)[0]["event_id"]
    changed = chart_alert_runner.evaluate_alert_rules([other], two, as_of=as_of)[0]["event_id"]

    assert first == again
    assert len({first, new_bar, changed}) == 3


def test_C_fix_alerts_indicator_values_come_from_shared_chart_studies():
    from dashboard import chart_studies

    index = pd.date_range("2026-08-03T13:30:00Z", periods=30, freq="5min")
    bars = pd.DataFrame({
        "high": [101 + i for i in range(30)], "low": [99 + i for i in range(30)],
        "close": [100 + i for i in range(30)], "volume": [1000] * 30,
    }, index=index)
    rule = {"id": "i", "symbol": "AAPL", "timeframe": "5m",
            "condition": {"type": "indicator", "field": "rsi_14", "operator": "greater_than", "value": 0}}

    current = chart_alert_runner.build_condition_contexts(
        rule, {("AAPL", "5m"): bars}, as_of="2026-08-03T20:00:00Z",
    )[("AAPL", "5m")]["current"]

    frame = bars.rename(columns=str.title)
    assert current["rsi_14"] == 100.0  # 단조 상승은 과매수 100(공통 정의)
    assert current["rsi_14"] == chart_studies.rsi(bars["close"], 14).iloc[-1]
    assert current["vwap"] == pytest.approx(chart_studies.session_vwap(frame, "America/New_York").iloc[-1])
    assert current["atr_14"] == pytest.approx(chart_studies.atr(frame, 14).iloc[-1])


# ── C-fix2: 주봉 마감·이벤트 본문·오래된 시세 ──

def _weekly_w_fri(week_closes):
    """views.ohlc_tf 가 쓰는 실제 W-FRI 리샘플(주 마감 금요일 라벨) 결과."""
    from dashboard import views

    days = pd.bdate_range("2026-07-20", periods=5 * len(week_closes))
    closes = [close for close in week_closes for _ in range(5)]
    daily = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=days)
    return views._resample_ohlc_frame(daily, "W-FRI")


@pytest.mark.parametrize("symbol, before_close, after_close, next_monday", [
    # 금요일 15:00 / 17:00 현지, 다음 월요일 10:00 현지
    ("AAPL", "2026-08-07T19:00:00Z", "2026-08-07T21:00:00Z", "2026-08-10T14:00:00Z"),
    ("005930.KS", "2026-08-07T06:00:00Z", "2026-08-07T08:00:00Z", "2026-08-10T01:00:00Z"),
])
def test_C_fix2_weekly_friday_label_bar_is_judged_at_that_fridays_close(symbol, before_close, after_close, next_monday):
    weekly = _weekly_w_fri([90, 99, 101])
    assert list(weekly.index.dayofweek) == [4, 4, 4]  # 7/24·7/31·8/7 금요일 라벨
    rule = {"id": "w", "symbol": symbol, "timeframe": "1wk", "frequency": "repeating",
            "condition": {"type": "price", "operator": "crossing_up", "value": 100}}
    bars = {(symbol, "1wk"): weekly}

    before: list[dict] = []
    assert chart_alert_runner.evaluate_alert_rules([rule], bars, as_of=before_close, state_sink=before) == []
    assert before[0]["status"] == "false"  # 8/7 주봉은 아직 진행 중 → 확정 쌍 90→99

    after: list[dict] = []
    events = chart_alert_runner.evaluate_alert_rules([rule], bars, as_of=after_close, state_sink=after)
    assert len(events) == 1
    assert (events[0]["previous_price"], events[0]["current_price"]) == (99, 101)
    assert events[0]["bar_time"].startswith("2026-08-0")  # 8/7 현지 라벨(UTC 표기)
    monday = chart_alert_runner.evaluate_alert_rules([rule], bars, as_of=next_monday)
    assert [event["event_id"] for event in monday] == [events[0]["event_id"]]


def test_C_fix2_replay_cursor_on_weekly_bar_does_not_read_next_week():
    weekly = _weekly_w_fri([90, 99, 101, 50])
    cursor = weekly.index[2]  # 8/7 금요일 주봉 — chart_replay_rules 가 커서 as_of 로 쓰는 같은 규칙
    as_of = chart_alert_runner.bar_close_time("AAPL", "1wk", cursor)
    contexts = chart_alert_runner.build_condition_contexts(
        {"id": "r", "symbol": "AAPL", "timeframe": "1wk",
         "condition": {"type": "price", "operator": "crossing_up", "value": 100}},
        {("AAPL", "1wk"): weekly}, as_of=as_of.isoformat(),
    )
    context = contexts[("AAPL", "1wk")]
    assert context["confirmed"] is True and "stale" not in context
    assert context["current"]["close"] == 101 and context["source"]["future_bars_excluded"] == 1


def test_C_fix2_event_body_uses_the_judged_closed_bar_pair():
    from agent_console import chart_alert_dispatcher

    bars = _hourly([99, 101, 50], ["2026-08-03T13:30:00Z", "2026-08-03T13:35:00Z", "2026-08-03T13:40:00Z"])
    rule = {"id": "c", "symbol": "AAPL", "timeframe": "5m", "frequency": "repeating",
            "condition": {"type": "price", "operator": "crossing_up", "value": 100}}
    states: list[dict] = []

    events = chart_alert_runner.evaluate_alert_rules(
        [rule], {("AAPL", "5m"): bars}, as_of="2026-08-03T13:42:00Z", state_sink=states,
    )

    leaf = next(row for row in states[0]["trace"] if row["node"] == "leaf")
    event = events[0]
    assert leaf["as_of"] == event["bar_time"] == "2026-08-03T13:35:00+00:00"
    assert (event["previous_price"], event["current_price"]) == (leaf["previous_value"], leaf["current_value"]) == (99, 101)
    text = chart_alert_dispatcher.format_alert_message(event)
    assert "평가 봉 종가 101.00 · 이전 봉 99.00" in text
    assert "50.00" not in text and "2026-08-03T13:35:00+00:00" in text


def _five(values, start):
    return pd.DataFrame({"close": values}, index=pd.date_range(start, periods=len(values), freq="5min"))


def test_C_fix2_stale_snapshot_is_data_unavailable_not_an_alert():
    rule = {"id": "s", "symbol": "AAPL", "timeframe": "5m", "frequency": "repeating",
            "condition": {"type": "price", "operator": "crossing_up", "value": 100}}
    bars = {("AAPL", "5m"): _five([99, 101], "2026-08-03T13:30:00Z")}
    states: list[dict] = []

    stale = chart_alert_runner.evaluate_alert_rules([rule], bars, as_of="2026-10-23T15:00:00Z", state_sink=states)

    assert stale == []
    assert states[0]["status"] == "unknown"
    assert states[0]["reason"] == "stale data: AAPL 5m"
    assert states[0]["stale_contexts"][0]["symbol"] == "AAPL"
    # 대조군: 직전 마감 봉(1분 뒤)은 그대로 경보.
    assert len(chart_alert_runner.evaluate_alert_rules([rule], bars, as_of="2026-08-03T13:41:00Z")) == 1


def test_C_fix2_freshness_tolerates_overnight_weekend_but_not_missing_sessions():
    intraday = {"id": "i", "symbol": "AAPL", "timeframe": "5m", "frequency": "repeating",
                "condition": {"type": "price", "operator": "crossing_up", "value": 100}}
    daily = {**intraday, "id": "d", "timeframe": "1d"}
    # 정규장 마지막 두 5분봉(15:50·15:55 ET) → 다음 날 개장 전 09:00 ET 평가는 최신.
    late = {("AAPL", "5m"): _five([99, 101], "2026-08-03T19:50:00Z")}
    assert len(chart_alert_runner.evaluate_alert_rules([intraday], late, as_of="2026-08-04T13:00:00Z")) == 1
    # 개장 후 45분 동안 새 봉이 없으면 데이터 장애 → unknown.
    states: list[dict] = []
    assert chart_alert_runner.evaluate_alert_rules(
        [intraday], late, as_of="2026-08-04T14:15:00Z", state_sink=states,
    ) == []
    assert states[0]["reason"] == "stale data: AAPL 5m"

    days = pd.DataFrame({"close": [99, 101]}, index=pd.DatetimeIndex(["2026-08-06", "2026-08-07"]))
    weekend = chart_alert_runner.evaluate_alert_rules(
        [daily], {("AAPL", "1d"): days}, as_of="2026-08-10T12:00:00Z",  # 월 08:00 ET
    )
    assert len(weekend) == 1
    states = []
    missing_two = chart_alert_runner.evaluate_alert_rules(
        [daily], {("AAPL", "1d"): days}, as_of="2026-08-12T12:00:00Z", state_sink=states,  # 월·화 마감 누락
    )
    assert missing_two == [] and states[0]["reason"] == "stale data: AAPL 1d"
