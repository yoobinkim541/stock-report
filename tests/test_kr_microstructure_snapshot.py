from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


class MemoryStore:
    def __init__(self):
        self.payload = None

    def write(self, payload):
        self.payload = payload
        return True


def test_collect_once_writes_normalized_snapshot():
    from crons import kr_microstructure_snapshot as cron

    store = MemoryStore()
    now = datetime(2026, 7, 27, 10, 15, tzinfo=ZoneInfo("Asia/Seoul"))
    snapshot = cron.collect_once(
        now=now,
        store=store,
        fetchers={
            "indices": lambda: {"kospi": {"price": 3310.2}},
            "investor_flow": lambda: {"kospi": {"foreign_net": 120000000000}},
            "k200_futures": lambda: {"price": 452.2, "foreign_net": 1800},
            "breadth": lambda: {"advancers": 510, "decliners": 310},
            "fx": lambda: {"usdkrw": {"rate": 1387.2}},
        },
    )

    assert snapshot["written"] is True
    assert store.payload["schema"] == "kr-market-microstructure.v1"
    assert store.payload["indices"]["kospi"]["price"] == 3310.2
    assert store.payload["fx"]["rate"] == 1387.2
