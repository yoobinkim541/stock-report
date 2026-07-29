"""lib/watchlist.py — 관심종목 CRUD (store.py SQLite 컬렉션 "watchlist" 백엔드).

유명 투자자 13F 신규편입(reports/notable_investors_wiki.py) 감지·수동 추가(봇 /watch)가
공유하는 단일 진실원. 대시보드(dashboard/pages/watchlist.py)와 봇(bot/watchlist_commands.py)
은 이 모듈만 통해 읽고 쓴다.
"""
from __future__ import annotations

from datetime import datetime, timezone

_COLLECTION = "watchlist"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_ticker(ticker: str, reason: str, source: str, *, note: str | None = None) -> dict:
    """관심종목에 추가. 이미 있으면 upsert(added_at 은 최초값 유지, 나머지는 갱신)."""
    import store

    tk = (ticker or "").strip().upper()
    entries = store.all(_COLLECTION)
    existing = next((e for e in entries if e.get("ticker") == tk), None)
    now = _now()
    entry = {
        "ticker": tk,
        "reason": reason,
        "source": source,
        "note": note,
        "added_at": existing["added_at"] if existing else now,
        "updated_at": now,
    }
    if existing:
        entries = [entry if e.get("ticker") == tk else e for e in entries]
    else:
        entries.append(entry)
    store.replace_all(_COLLECTION, entries)
    return entry


def remove_ticker(ticker: str) -> bool:
    """관심종목에서 제거. 존재해서 지웠으면 True."""
    import store

    tk = (ticker or "").strip().upper()
    entries = store.all(_COLLECTION)
    remaining = [e for e in entries if e.get("ticker") != tk]
    if len(remaining) == len(entries):
        return False
    store.replace_all(_COLLECTION, remaining)
    return True


def list_watchlist() -> list[dict]:
    """전체 관심종목 — 최근 추가 순.

    added_at 문자열(초 단위) 로 정렬하면 같은 초에 여러 건이 들어올 때 동률로 묶여 원래
    삽입 순서가 그대로 유지된다(reverse=True 라도 안 뒤집힘) — store.all() 이 이미 삽입
    순서(seq 오름차순)를 보장하므로 그냥 리스트를 뒤집는 쪽이 타임스탬프 동률에 안전하다.
    """
    import store

    return list(reversed(store.all(_COLLECTION)))
