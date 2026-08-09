from __future__ import annotations

import copy

import pytest

from agent_console import storage
from dashboard import chart_replay


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "console.sqlite3"))


def _session(session_id="replay-1"):
    return chart_replay.new_session(
        symbol="MSFT", timeframe="5m", cursor=0, initial_cash=10_000,
        session_id=session_id,
    )


def test_replay_session_round_trips_across_connections(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    created = storage.save_chart_replay_session(_session(), workspace_id="workspace-1")
    fetched = storage.get_chart_replay_session("replay-1")

    assert created["revision"] == 1
    assert fetched["session"] == created["session"]
    assert fetched["workspace_id"] == "workspace-1"
    assert storage.list_chart_replay_sessions(workspace_id="workspace-1")[0]["id"] == "replay-1"


def test_replay_session_uses_optimistic_revision_and_idempotency_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    session = _session()

    first = storage.save_chart_replay_session(session, request_id="create-1")
    repeated = storage.save_chart_replay_session(session, request_id="create-1")

    assert repeated == first
    assert storage.get_chart_replay_session("replay-1")["revision"] == 1

    changed = copy.deepcopy(session)
    changed["cursor"] = 1
    saved = storage.save_chart_replay_session(changed, expected_revision=1, request_id="step-1")
    assert saved["revision"] == 2

    with pytest.raises(storage.ReplayRevisionConflict):
        storage.save_chart_replay_session(changed, expected_revision=1, request_id="stale-1")

    with pytest.raises(ValueError, match="request_id"):
        storage.save_chart_replay_session(changed, request_id="create-1")


def test_replay_event_history_is_append_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    session = _session()
    storage.save_chart_replay_session(session)

    rewritten = copy.deepcopy(session)
    rewritten["events"][0]["type"] = "rewritten"

    with pytest.raises(ValueError, match="event history"):
        storage.save_chart_replay_session(rewritten, expected_revision=1)


def test_replay_branch_is_independent_and_delete_removes_its_events(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    session = _session()
    session["cursor"] = 3
    session["events"].extend([
        {"type": "cursor_advanced", "cursor": 1},
        {"type": "cursor_advanced", "cursor": 2},
        {"type": "cursor_advanced", "cursor": 3},
    ])
    storage.save_chart_replay_session(session, workspace_id="workspace-1")

    branch = storage.branch_chart_replay_session("replay-1", cursor=1, session_id="branch-1")

    assert branch["session"]["parent_id"] == "replay-1"
    assert branch["session"]["cursor"] == 1
    assert all(int(row.get("cursor") or 0) <= 1 for row in branch["session"]["events"])
    assert storage.delete_chart_replay_session("branch-1") is True
    assert storage.get_chart_replay_session("branch-1") is None


def test_replay_session_api_crud_conflict_and_branch(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console.server import create_app

    client = create_app().test_client()
    created = client.post("/api/chart-replay/sessions", json={
        "session": _session("api-1"), "workspace_id": "w1", "request_id": "create-api",
    })
    assert created.status_code == 200
    assert created.json["replay"]["revision"] == 1
    assert client.get("/api/chart-replay/sessions?workspace_id=w1").json["sessions"][0]["id"] == "api-1"
    assert client.get("/api/chart-replay/sessions/api-1").json["replay"]["session"]["symbol"] == "MSFT"

    stale = client.post("/api/chart-replay/sessions", json={
        "session": _session("api-1"), "expected_revision": 0, "request_id": "stale-api",
    })
    assert stale.status_code == 409

    branch = client.post("/api/chart-replay/sessions/api-1/branch", json={
        "cursor": 0, "session_id": "api-branch",
    })
    assert branch.status_code == 200
    assert branch.json["replay"]["session"]["parent_id"] == "api-1"

    deleted = client.delete("/api/chart-replay/sessions/api-branch")
    assert deleted.status_code == 200
    assert deleted.json["deleted"] is True


def test_replay_order_price_api_previews_then_applies_with_revision(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console.server import create_app

    session = chart_replay.submit_order(
        _session("risk-api"),
        {"id": "risk", "type": "stop", "side": "buy", "qty": 1, "price": 105},
    )
    saved = storage.save_chart_replay_session(session)
    client = create_app().test_client()
    url = "/api/chart-replay/sessions/risk-api/orders/risk/price"

    preview = client.post(url, json={
        "price": 106, "expected_revision": saved["revision"], "preview_only": True,
    })
    assert preview.status_code == 200
    assert preview.json["preview"]["after"] == 106
    assert storage.get_chart_replay_session("risk-api")["revision"] == 1

    applied = client.post(url, json={
        "price": 106, "expected_revision": saved["revision"], "request_id": "drag-1",
    })
    assert applied.status_code == 200
    assert applied.json["replay"]["revision"] == 2
    assert applied.json["preview"]["order_id"] == "risk"

    repeated = client.post(url, json={
        "price": 106, "expected_revision": saved["revision"], "request_id": "drag-1",
    })
    assert repeated.status_code == 200
    assert repeated.json["replay"]["revision"] == 2

    reused = client.post(url, json={
        "price": 108, "expected_revision": 2, "request_id": "drag-1",
    })
    assert reused.status_code == 400
    assert "request_id" in reused.json["error"]

    stale = client.post(url, json={
        "price": 107, "expected_revision": 1, "request_id": "drag-stale",
    })
    assert stale.status_code == 409
