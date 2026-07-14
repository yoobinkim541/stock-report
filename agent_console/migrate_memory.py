"""콘솔 로컬 메모리 → 단일 진실원(lib) 1회 이관 (재실행 안전 — dedupe 멱등).

이관 대상:
  1. agent_console.sqlite3 의 market_memory 행 → lib.world_memory.log_issue
  2. 구 공유 메모리(레포 안 data/shared-memory)의 events.jsonl → 신 디렉토리 events.jsonl append
     (record id 보존 — append-only 로그라 중복 append 는 무해하나 id 기준으로 스킵)

노트북(user_memory_notebook.md)은 포맷이 달라 자동 병합하지 않는다 — 구 파일은 원위치에
그대로 보존되며, 필요한 항목만 수동 메모(수동 기억 추가)로 옮기면 된다.

사용: uv run python -m agent_console.migrate_memory [--dry-run]
"""
from __future__ import annotations

import json
import sys

from . import shared_memory, storage


def migrate_world_memory(dry_run: bool = False) -> dict:
    rows = storage.list_memory_events(limit=500)
    moved = skipped = 0
    try:
        from agent_console.context import log_world_issue
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": str(exc), "moved": 0, "skipped": 0, "total": len(rows)}
    for r in rows:
        if dry_run:
            continue
        ok = log_world_issue(
            str(r.get("title") or ""),
            category=str(r.get("kind") or "수집"),
            importance=str(r.get("impact") or "low"),
            tickers=[str(t) for t in (r.get("symbols") or [])][:8],
            body=str(r.get("body") or "")[:1200],
            source=str(r.get("source") or "console:migrated"),
            observed_at=str(r.get("observed_at") or ""),
        )
        moved += 1 if ok else 0
        skipped += 0 if ok else 1
    return {"ok": True, "moved": moved, "skipped_dup": skipped, "total": len(rows)}


def migrate_shared_events(dry_run: bool = False) -> dict:
    old_dir = shared_memory.legacy_shared_memory_dir()
    new_dir = shared_memory.shared_memory_dir()
    old_events = old_dir / "events.jsonl"
    if old_dir == new_dir or not old_events.exists():
        return {"ok": True, "moved": 0, "note": "구 events.jsonl 없음(또는 동일 디렉토리)"}
    new_events = new_dir / "events.jsonl"
    existing_ids = set()
    if new_events.exists():
        for line in new_events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                existing_ids.add(json.loads(line).get("id"))
            except Exception:
                continue
    moved = 0
    lines_out = []
    for line in old_events.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict) or row.get("id") in existing_ids:
            continue
        lines_out.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        moved += 1
    if lines_out and not dry_run:
        new_dir.mkdir(parents=True, exist_ok=True)
        with new_events.open("a", encoding="utf-8") as f:
            for line in lines_out:
                f.write(line + "\n")
        shared_memory.refresh_context_memory_summary()
    return {"ok": True, "moved": moved, "source": str(old_events), "target": str(new_events)}


def main(argv: list[str] | None = None) -> int:
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    world = migrate_world_memory(dry_run=dry)
    shared = migrate_shared_events(dry_run=dry)
    print(f"[world]  콘솔 market_memory {world.get('total', 0)}건 중 "
          f"{world.get('moved', 0)}건 이관 · 중복 스킵 {world.get('skipped_dup', 0)}건"
          + (" (dry-run)" if dry else ""))
    print(f"[shared] events.jsonl {shared.get('moved', 0)}건 append — {shared.get('note', '')}"
          + (" (dry-run)" if dry else ""))
    print("[note]   구 user_memory_notebook.md 는 자동 병합하지 않음 (원본 보존 — 필요 항목만 수동 이관)")
    return 0 if world.get("ok") and shared.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
