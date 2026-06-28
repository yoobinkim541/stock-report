"""providers/econ_calendar.py — 경제 캘린더 (saveticker /calendar/events).

saveticker 공개 API(키 불요·한국어·기존 통합)에서 연준·경제지표 일정을 가져온다.
엔드포인트: GET {base}/calendar/events?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
응답: {"events": [{id,title,event_date,color,...}], "total_count": N}
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

_BASE = "https://saveticker.com/api"

# saveticker color → 중요도 (관찰: #EF4444=고위험/중요)
_IMPORTANCE = {
    "#EF4444": ("high", "🔴"),
    "#F59E0B": ("medium", "🟠"),
    "#FACC15": ("medium", "🟡"),
    "#10B981": ("low", "🟢"),
}


def _importance(color: str) -> tuple[str, str]:
    return _IMPORTANCE.get((color or "").upper(), ("info", "⚪"))


def _api_base() -> str:
    return os.getenv("SAVE_TICKER_API_BASE", _BASE).rstrip("/")


def upcoming_events(days: int = 14, *, start: date | None = None,
                    timeout: int = 15) -> list[dict]:
    """향후 days 일 경제 일정 (정렬됨). 실패 시 [] (graceful).

    반환 항목: {title, when(datetime|None), date_str, importance, marker, color}
    """
    import requests
    s = start or date.today()
    e = s + timedelta(days=max(1, days))
    url = f"{_api_base()}/calendar/events"
    try:
        r = requests.get(url, params={"start_date": s.isoformat(), "end_date": e.isoformat()},
                         timeout=timeout)
        if not r.ok:
            return []
        events = r.json().get("events", []) or []
    except Exception:
        return []
    return _parse(events)


def _parse(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        when = None
        raw = ev.get("event_date")
        if raw:
            try:
                when = datetime.fromisoformat(str(raw).replace("Z", ""))
            except (ValueError, TypeError):
                when = None
        imp, marker = _importance(ev.get("color"))
        out.append({
            "title": (ev.get("title") or "").strip(),
            "when": when,
            "date_str": when.strftime("%m/%d %H:%M") if when else (ev.get("event_date") or "—"),
            "importance": imp,
            "marker": marker,
            "color": ev.get("color"),
        })
    out.sort(key=lambda x: (x["when"] is None, x["when"] or datetime.max))
    return out
