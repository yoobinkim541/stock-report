from __future__ import annotations

import json


def test_file_snapshot_store_round_trips_market_microstructure(tmp_path):
    from agent_console.market_snapshot_store import FileSnapshotStore

    path = tmp_path / "kr_market_microstructure.json"
    store = FileSnapshotStore(path)
    payload = {
        "as_of": "2026-07-27T05:00:00+00:00",
        "source": "kiwoom_collector",
        "indices": {"kospi": {"price": 3210.5, "change_pct": 0.7}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -50000000000}},
    }

    assert store.write(payload) is True
    loaded = store.read()

    assert loaded["indices"]["kospi"]["price"] == 3210.5
    assert loaded["investor_flow"]["kospi"]["foreign_net"] == 120000000000


def test_load_market_microstructure_rejects_stale_payload(tmp_path, monkeypatch):
    from agent_console import market_snapshot_store as store_mod

    path = tmp_path / "stale.json"
    path.write_text(
        json.dumps(
            {
                "as_of": "2026-07-27T05:00:00+00:00",
                "ts": 100.0,
                "max_age_s": 30,
                "indices": {"kospi": {"price": 3210.5}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store_mod.time, "time", lambda: 200.0)

    loaded = store_mod.load_market_microstructure(store_mod.FileSnapshotStore(path))

    assert loaded == {}


def test_redis_snapshot_store_missing_dependency_degrades(monkeypatch):
    from agent_console.market_snapshot_store import RedisSnapshotStore

    def fake_import(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("no redis")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    assert RedisSnapshotStore("redis://localhost:6379/0").read() == {}

def test_default_loader_falls_back_to_file_when_redis_empty(tmp_path, monkeypatch):
    from agent_console import market_snapshot_store as store_mod

    path = tmp_path / "kr_market_microstructure.json"
    path.write_text(
        json.dumps({"ts": 100.0, "max_age_s": 120, "indices": {"kospi": {"price": 3210.5}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_CACHE", str(path))
    monkeypatch.setattr(store_mod.time, "time", lambda: 110.0)
    monkeypatch.setattr(store_mod.RedisSnapshotStore, "read", lambda self: {})

    loaded = store_mod.load_market_microstructure()

    assert loaded["indices"]["kospi"]["price"] == 3210.5
