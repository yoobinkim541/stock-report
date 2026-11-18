"""providers/econ_calendar.py — 경제 캘린더 (ForexFactory 이번 주 일정 JSON).

SaveTicker 일정 API(``api.saveticker.com/api/calendar/events``)는 2026-11-17부터 HTTP 426
("새로워진 세이브 뉴스로 업데이트해 주세요")만 돌려주고, ``saveticker.com`` 은 Cloudflare 403 이다.
대신 ForexFactory 가 공개하는 이번 주 일정 JSON 을 쓴다 (키 불요).
응답: ``[{title, country, date(ISO+오프셋), impact(High|Medium|Low|Holiday), forecast, previous}]``
제목은 영어이고 이번 주(일~토, 미 동부 기준) 일정만 담긴다. 미국 일정은 전부,
다른 나라는 고영향(High)만 남긴다 (SaveTicker 일정도 미국 위주였다).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_URL = "https://www.forexfactory.com/calendar"

_IMPORTANCE = {
    "High": ("high", "🔴"),
    "Medium": ("medium", "🟠"),
    "Low": ("low", "🟢"),
}


def _importance(impact: str | None) -> tuple[str, str]:
    return _IMPORTANCE.get(str(impact or "").strip().title(), ("info", "⚪"))


def upcoming_events(days: int = 14, *, start: date | None = None,
                    timeout: int = 15) -> list[dict]:
    """start(KST 날짜, 기본 오늘)부터 days 일 안의 일정, 시각순.

    반환 항목: {title, when(datetime), date_str, importance, marker, country, forecast, previous}
    응답 실패는 빈 목록이 아니라 예외다: 빈 목록은 '일정 없음'과 구분되지 않는다.
    """
    import requests

    from reports.source_exceptions import SourceProviderUnavailable

    response = requests.get(FEED_URL, timeout=timeout, headers={"User-Agent": "stock-report/1.0"})
    if not response.ok:
        raise SourceProviderUnavailable(
            f"ForexFactory calendar HTTP {response.status_code}",
            availability="blocked" if response.status_code in {403, 429, 451} else "error",
            status_code=response.status_code,
        )
    rows = response.json()
    if not isinstance(rows, list):
        raise TypeError("calendar feed is not a list")
    first = start or datetime.now(KST).date()
    return [event for event in _parse(rows)
            if first <= event["when"].date() < first + timedelta(days=max(1, days))]


def _parse(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        country = str(row.get("country") or "").strip().upper()
        if not title or (country != "USD" and str(row.get("impact") or "").title() != "High"):
            continue
        try:
            when = datetime.fromisoformat(str(row.get("date") or "").strip())
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        when = when.astimezone(KST)
        importance, marker = _importance(row.get("impact"))
        out.append({
            "title": title if country == "USD" else f"{country} {title}",
            "when": when,
            "date_str": when.strftime("%m/%d %H:%M"),
            "importance": importance,
            "marker": marker,
            "country": country,
            "forecast": str(row.get("forecast") or "").strip(),
            "previous": str(row.get("previous") or "").strip(),
        })
    out.sort(key=lambda event: event["when"])
    return out
