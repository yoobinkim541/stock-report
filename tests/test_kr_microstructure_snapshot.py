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


def test_main_skips_without_error_when_previous_run_still_holds_lock(monkeypatch, tmp_path):
    """이 크론은 1분마다 도는데 잠금이 없으면 실행이 60초를 넘길 때 다음 호출과
    겹쳐 같은 스냅샷 스토어에 동시 쓰기가 상호배제 없이 발생할 수 있다."""
    import safe_io
    from crons import kr_microstructure_snapshot as cron

    lock_target = str(tmp_path / "kr_microstructure_snapshot")
    monkeypatch.setattr(cron, "_LOCK_TARGET", lock_target)
    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "true")

    def _boom(*a, **k):
        raise AssertionError("collect_once 가 호출되면 안 됨 — 이전 실행이 아직 락을 쥐고 있어야 함")

    monkeypatch.setattr(cron, "collect_once", _boom)

    with safe_io.file_write_lock(lock_target, timeout=1):
        rc = cron.main()               # 겹치는 실행을 시뮬레이션 — 조용히 스킵돼야 함

    assert rc == 0
