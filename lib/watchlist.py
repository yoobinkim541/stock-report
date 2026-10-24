"""lib/watchlist.py — 관심종목 CRUD (store.py SQLite 컬렉션 "watchlist" 백엔드).

유명 투자자 13F 신규편입(reports/notable_investors_wiki.py) 감지·수동 추가(봇 /watch)가
공유하는 단일 진실원. 대시보드(dashboard/pages/watchlist.py)와 봇(bot/watchlist_commands.py)
은 이 모듈만 통해 읽고 쓴다.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_COLLECTION = "watchlist"
_DEFAULT_FOLDER = "미분류"
# 미국 티커(BRK.B·BF-B), 지수(^GSPC), 선물·환율(GC=F·KRW=X), 국내 6자리(.KS/.KQ).
_TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")


def normalize_ticker(ticker: str) -> str:
    """봇 /watch 와 대시보드가 공유하는 티커 검증·정규화.

    대문자화하고, 국내 6자리 코드는 종목 분석페이지와 같은 ``.KS`` 접미사를 붙인다.
    공백·특수문자·과도한 길이는 ValueError — 오타가 관심종목에 저장되지 않게 한다.
    """
    tk = (ticker or "").strip().upper()
    if tk.isdigit() and len(tk) == 6:
        tk += ".KS"
    if not _TICKER_RE.match(tk):
        raise ValueError(f"티커 형식이 아닙니다: {ticker!r}")
    return tk


def _lookup_keys(ticker: str) -> set[str]:
    """조회·삭제용 키 — 정규화 이전에 저장된 원형(예: '005930')도 찾는다."""
    raw = (ticker or "").strip().upper()
    try:
        return {raw, normalize_ticker(raw)}
    except ValueError:
        return {raw}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_ticker(ticker: str, reason: str, source: str, *, note: str | None = None,
               folder: str | None = None) -> dict:
    """관심종목에 추가. 이미 있으면 upsert(added_at 은 최초값 유지, 나머지는 갱신).

    folder 미지정 시: 기존 항목이면 기존 폴더 유지, 신규면 '미분류'(감사 후속 —
    종목 분석페이지 ⭐ 추가·폴더 분류 기능)."""
    import store

    tk = normalize_ticker(ticker)
    entries = store.all(_COLLECTION)
    existing = next((e for e in entries if e.get("ticker") == tk), None)
    now = _now()
    fd = (folder or "").strip() or (existing.get("folder") if existing else "") or _DEFAULT_FOLDER
    entry = {
        "ticker": tk,
        "reason": reason,
        "source": source,
        "note": note,
        "folder": fd,
        "added_at": existing["added_at"] if existing else now,
        "updated_at": now,
    }
    if existing:
        entries = [entry if e.get("ticker") == tk else e for e in entries]
    else:
        entries.append(entry)
    store.replace_all(_COLLECTION, entries)
    return entry


def set_folder(ticker: str, folder: str) -> bool:
    """관심종목을 다른 폴더로 이동. 존재해서 옮겼으면 True. 빈 폴더명은 '미분류'로."""
    import store

    keys = _lookup_keys(ticker)
    fd = (folder or "").strip() or _DEFAULT_FOLDER
    entries = store.all(_COLLECTION)
    found = False
    updated = []
    for e in entries:
        if e.get("ticker") in keys:
            e = {**e, "folder": fd, "updated_at": _now()}
            found = True
        updated.append(e)
    if found:
        store.replace_all(_COLLECTION, updated)
    return found


def list_folders() -> list[str]:
    """존재하는 폴더 목록(가나다순, 중복제거) — '미분류'는 항상 포함(빈 관심종목도)."""
    import store

    entries = store.all(_COLLECTION)
    names = {e.get("folder") or _DEFAULT_FOLDER for e in entries}
    names.add(_DEFAULT_FOLDER)
    return sorted(names)


def remove_ticker(ticker: str) -> bool:
    """관심종목에서 제거. 존재해서 지웠으면 True."""
    import store

    keys = _lookup_keys(ticker)
    entries = store.all(_COLLECTION)
    remaining = [e for e in entries if e.get("ticker") not in keys]
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
