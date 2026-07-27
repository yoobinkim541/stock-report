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



def test_normalize_market_microstructure_preserves_core_fields():
    from agent_console import market_snapshot_store as s

    payload = {
        "as_of": "2026-07-27T10:15:00+09:00",
        "source": "krx_public",
        "indices": {"kospi": {"price": 3310.2, "change_pct": 0.42}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -40000000000}},
        "k200_futures": {"price": 452.2, "change_pct": 0.31, "foreign_net": 1800},
        "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74},
        "fx": {"usdkrw": {"rate": 1387.2, "change": -2.1}},
    }

    got = s.normalize_market_microstructure(payload, now=1_000.0)

    assert got["schema"] == "kr-market-microstructure.v1"
    assert got["ts"] == 1_000.0
    assert got["max_age_s"] == 120
    assert got["indices"]["kospi"]["price"] == 3310.2
    assert got["investor_flow"]["kospi"]["foreign_net"] == 120000000000
    assert got["breadth"]["advancers"] == 510


def test_write_market_microstructure_normalizes_before_write(tmp_path):
    from agent_console import market_snapshot_store as s

    path = tmp_path / "micro.json"
    assert s.write_market_microstructure({"indices": {"kospi": {"price": 3310.2}}}, store=s.FileSnapshotStore(path))

    got = s.FileSnapshotStore(path).read()
    assert got["schema"] == "kr-market-microstructure.v1"
    assert got["indices"]["kospi"]["price"] == 3310.2
