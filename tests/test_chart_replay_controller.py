from __future__ import annotations

import pandas as pd
import pytest

from dashboard import chart_document, chart_replay_controller


def _timeline():
    return pd.date_range("2026-08-03 09:30", periods=6, freq="5min", tz="America/New_York")


def _frame(index):
    return pd.DataFrame({"Close": range(100, 100 + len(index))}, index=index)


def test_workspace_snapshot_shares_one_as_of_without_future_rows():
    timeline = _timeline()
    controller = chart_replay_controller.new_controller("session-1", cursor=2)
    panels = {
        "p1": _frame(timeline),
        "p2": _frame(pd.DatetimeIndex([timeline[0], timeline[3], timeline[5]])),
    }

    snapshot = chart_replay_controller.workspace_snapshot(controller, timeline, panels)

    assert snapshot["as_of"] == timeline[2].isoformat()
    assert list(snapshot["panels"]["p1"].index) == list(timeline[:3])
    assert list(snapshot["panels"]["p2"].index) == [timeline[0]]
    assert all(index <= timeline[2] for frame in snapshot["panels"].values() for index in frame.index)


def test_controller_play_pause_step_speed_and_jump_live_are_deterministic():
    timeline = _timeline()
    controller = chart_replay_controller.new_controller("session-1", cursor=0)
    controller = chart_replay_controller.apply_command(controller, timeline, "speed", value=2)
    controller = chart_replay_controller.apply_command(controller, timeline, "play")
    controller = chart_replay_controller.tick(controller, timeline)
    assert controller["cursor"] == 2
    assert controller["playing"] is True

    controller = chart_replay_controller.apply_command(controller, timeline, "pause")
    assert chart_replay_controller.tick(controller, timeline)["cursor"] == 2
    controller = chart_replay_controller.apply_command(controller, timeline, "step")
    assert controller["cursor"] == 3

    controller = chart_replay_controller.apply_command(controller, timeline, "jump_live")
    assert controller["cursor"] == len(timeline) - 1
    assert controller["playing"] is False


def test_rewind_requires_an_explicit_controller_branch():
    timeline = _timeline()
    controller = chart_replay_controller.new_controller("session-1", cursor=4)

    with pytest.raises(ValueError, match="branch"):
        chart_replay_controller.apply_command(controller, timeline, "seek", value=1)

    branch = chart_replay_controller.branch_controller(controller, cursor=1, session_id="branch-1")
    assert branch["session_id"] == "branch-1"
    assert branch["parent_id"] == "session-1"
    assert branch["cursor"] == 1


def test_timezone_normalization_handles_naive_and_aware_panel_indexes():
    timeline = _timeline()
    controller = chart_replay_controller.new_controller("session-1", cursor=1)
    naive = pd.date_range("2026-08-03 13:30", periods=4, freq="5min")

    snapshot = chart_replay_controller.workspace_snapshot(controller, timeline, {"naive": _frame(naive)})

    assert len(snapshot["panels"]["naive"]) == 2


def test_chart_document_receives_replay_state_without_touching_alerts():
    document = chart_document.default_chart_document("MSFT")
    document["alerts"] = [{"id": "live-alert", "namespace": "realtime"}]
    controller = chart_replay_controller.new_controller("session-1", cursor=2)
    controller = chart_replay_controller.synchronize(controller, _timeline())

    replay_document = chart_replay_controller.apply_to_document(document, controller)

    assert replay_document["replay"] == {
        "active": True,
        "cursor": 2,
        "as_of": _timeline()[2].isoformat(),
        "session_id": "session-1",
    }
    assert replay_document["alerts"] == document["alerts"]
    assert replay_document is not document
