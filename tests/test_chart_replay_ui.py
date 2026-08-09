from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

from dashboard import chart_replay, chart_replay_ui


def _frame():
    index = pd.date_range("2026-08-03 09:30", periods=5, freq="5min", tz="America/New_York")
    return pd.DataFrame({
        "Open": [100] * 5, "High": [101] * 5, "Low": [99] * 5,
        "Close": [100] * 5, "Volume": [1] * 5,
    }, index=index)


def test_restore_cursor_uses_timestamp_when_rolling_window_changes():
    frame = _frame()
    session = chart_replay.new_session(
        symbol="MSFT", timeframe="5m", cursor=4, initial_cash=10_000, session_id="s1",
    )
    session["cursor_timestamp"] = frame.index[2].isoformat()

    restored = chart_replay_ui._restore_cursor(session, frame)

    assert restored["cursor"] == 2
    assert restored["cursor_timestamp"] == frame.index[2].isoformat()


def test_order_patch_url_targets_agent_console(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_URL", "https://agent.example")
    assert chart_replay_ui.order_patch_url("session-1") == (
        "https://agent.example/api/chart-replay/sessions/session-1/orders"
    )


def test_replay_scope_is_stable_and_separates_ticker_and_workspace():
    ticker_scope = chart_replay_ui._scope("MSFT", "5m", "", "ticker")
    workspace_scope = chart_replay_ui._scope("MSFT", "5m", "workspace 1", "workspace")
    assert ticker_scope == "ticker:MSFT:5m"
    assert workspace_scope == "workspace_1"


def test_replay_cutoff_filters_frames_and_records_in_utc():
    frame = _frame()
    cutoff = frame.index[2]
    sliced = chart_replay_ui.slice_until(frame, cutoff)
    records = chart_replay_ui.records_until([
        {"id": "past", "timestamp": frame.index[1].isoformat()},
        {"id": "future", "timestamp": frame.index[4].tz_convert("UTC").isoformat()},
    ], cutoff)
    assert len(sliced) == 3
    assert [row["id"] for row in records] == ["past"]


def test_replay_terminal_renders_active_persistent_session(tmp_path):
    script = f'''
import os
os.environ["AGENT_CONSOLE_DB"] = {str(tmp_path / "console.sqlite3")!r}
import pandas as pd
import streamlit as st
from dashboard import chart_replay_ui

index = pd.date_range("2026-08-03 09:30", periods=20, freq="5min", tz="America/New_York")
frame = pd.DataFrame({{
    "Open": [100.0] * 20, "High": [101.0] * 20, "Low": [99.0] * 20,
    "Close": [100.0] * 20, "Volume": [1.0] * 20,
}}, index=index)
st.session_state["demo_replay_enabled"] = True
context = chart_replay_ui.prepare_replay(
    frame, symbol="MSFT", timeframe="5m", key_prefix="demo", workspace_id="w1",
)
chart_replay_ui.render_terminal(context)
'''
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert [tab.label for tab in at.tabs] == [
        "Replay", "Orders", "Positions", "Strategy", "Events", "Diagnostics",
    ]
    assert any(button.label == "주문 제출" for button in at.button)
