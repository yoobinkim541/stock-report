#!/usr/bin/env python3
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_intraday_bar_start_grace_covers_kr_open_file_absence():
    from tests import bot_healthcheck as health

    now = datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc)  # 09:05 KST

    assert health._intraday_bar_start_grace(kr_open=True, us_open=False, now_utc=now) is True


def test_intraday_bar_start_grace_expires_after_kr_open_window():
    from tests import bot_healthcheck as health

    now = datetime(2026, 7, 27, 0, 25, tzinfo=timezone.utc)  # 09:25 KST

    assert health._intraday_bar_start_grace(kr_open=True, us_open=False, now_utc=now) is False


def test_intraday_bar_start_grace_covers_us_open_file_absence():
    from tests import bot_healthcheck as health

    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 7, 27, 9, 35, tzinfo=ny).astimezone(timezone.utc)

    assert health._intraday_bar_start_grace(kr_open=False, us_open=True, now_utc=now) is True



class _SnapshotStore:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


def test_market_microstructure_healthcheck_warns_on_stale_snapshot(monkeypatch):
    from tests import bot_healthcheck as health

    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "true")
    monkeypatch.setattr(health.time, "time", lambda: 1_000.0)

    class StoreMod:
        @staticmethod
        def default_store():
            return _SnapshotStore({"ts": 100.0, "max_age_s": 120, "field_status": {}})

    monkeypatch.setitem(sys.modules, "agent_console.market_snapshot_store", StoreMod)
    monkeypatch.setitem(sys.modules, "agent_console", type("AgentConsole", (), {"market_snapshot_store": StoreMod}))

    result = health.check_market_microstructure_snapshot()

    assert result is not None
    assert result[0] == "market_microstructure_stale"


def test_market_microstructure_healthcheck_warns_on_missing_required_field(monkeypatch):
    from tests import bot_healthcheck as health

    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "true")
    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_REQUIRED_FIELDS", "indices,breadth")
    monkeypatch.setattr(health.time, "time", lambda: 1_000.0)

    class StoreMod:
        @staticmethod
        def default_store():
            return _SnapshotStore({
                "ts": 990.0,
                "max_age_s": 120,
                "indices": {"kospi": {"price": 3310.2}},
                "field_status": {"indices": {"ok": True}, "breadth": {"ok": False}},
            })

    monkeypatch.setitem(sys.modules, "agent_console.market_snapshot_store", StoreMod)
    monkeypatch.setitem(sys.modules, "agent_console", type("AgentConsole", (), {"market_snapshot_store": StoreMod}))

    result = health.check_market_microstructure_snapshot()

    assert result is not None
    assert result[0] == "market_microstructure_partial"
    assert "breadth" in result[1]


def test_orderflow_healthcheck_warns_on_capture_loss(monkeypatch):
    from providers import orderflow_store
    from tests import bot_healthcheck as health

    monkeypatch.setenv("ORDERFLOW_CAPTURE_ENABLED", "true")
    monkeypatch.setattr(orderflow_store, "load_capture_status", lambda: {
        "capture_enabled": True,
        "dropped_events": 43127,
        "byte_limit_events": 43127,
        "queue_overflow_events": 0,
        "write_failures": 0,
        "dropped_by_session": {"2026-08-31": 43127},
    })

    result = health.check_orderflow_capture()

    assert result is not None
    assert result[0] == "orderflow_capture_degraded"
    assert "43,127건" in result[1]
    assert "2026-08-31" in result[1]


def test_orderflow_healthcheck_is_quiet_when_capture_is_clean(monkeypatch):
    from providers import orderflow_store
    from tests import bot_healthcheck as health

    monkeypatch.setenv("ORDERFLOW_CAPTURE_ENABLED", "true")
    monkeypatch.setattr(orderflow_store, "load_capture_status", lambda: {
        "capture_enabled": True,
        "dropped_events": 0,
        "byte_limit_events": 0,
        "queue_overflow_events": 0,
        "write_failures": 0,
    })

    assert health.check_orderflow_capture() is None
