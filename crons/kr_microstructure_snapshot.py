#!/usr/bin/env python3
"""Collect Korean intraday market microstructure into the AI console snapshot store."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import safe_io
from agent_console import market_snapshot_store
from providers import kr_microstructure

_LOCK_TARGET = os.path.expanduser("~/.cache/stock-report/kr_microstructure_snapshot")


def collect_once(now: datetime | None = None, store=None, fetchers: dict | None = None) -> dict:
    snapshot = kr_microstructure.build_snapshot(now=now, fetchers=fetchers)
    written = market_snapshot_store.write_market_microstructure(snapshot, store=store)
    snapshot["written"] = bool(written)
    return snapshot


def main() -> int:
    if os.getenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        print("KR_MARKET_MICROSTRUCTURE_ENABLED=false — skip")
        return 0
    # 이 크론은 1분마다 도는데 순차 네이버 호출이 60초 주기를 넘기면 다음 실행과
    # 겹쳐 같은 스냅샷 스토어에 동시 쓰기가 날 수 있음 — 비차단 락으로 겹치면 스킵.
    try:
        with safe_io.file_write_lock(_LOCK_TARGET, timeout=0):
            snapshot = collect_once()
    except safe_io.LockTimeout:
        print("kr_microstructure previous run still in progress — skip")
        return 0
    fields = ",".join(k for k, v in (snapshot.get("field_status") or {}).items() if v.get("ok")) or "none"
    print(f"kr_microstructure written={snapshot.get('written')} as_of={snapshot.get('as_of')} fields={fields}")
    return 0 if snapshot.get("written") else 1


if __name__ == "__main__":
    raise SystemExit(main())
