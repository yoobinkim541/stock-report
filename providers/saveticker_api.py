"""providers/saveticker_api.py — SaveTicker 공개 API 호스트 단일 진실원.

2026-10-23 실측:
- ``https://saveticker.com/api/*`` 는 Cloudflare 챌린지로 403(HTML)을 돌려준다.
- ``https://api.saveticker.com/api`` 는 인증 없이 200 JSON — ``news/top-stories``
  (최근 기사 묶음)와 ``calendar/events`` 가 정상 응답한다.
- 같은 호스트의 ``news/list`` 는 어떤 파라미터(page·page_size·tickers)든 서비스 종료
  공지(``id=legacy-news-sunset-notice``) 1건만 반환한다. 이 공지를 기사로 저장하면 안 된다.

``SAVE_TICKER_API_BASE`` 환경변수로 기본 호스트를 바꿀 수 있다.
"""
from __future__ import annotations

import os

DEFAULT_API_BASE = "https://api.saveticker.com/api"


def api_base() -> str:
    return (os.getenv("SAVE_TICKER_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def is_article_row(row: object) -> bool:
    """서비스 공지(legacy-*) 등 기사가 아닌 행을 걸러낸다."""
    if not isinstance(row, dict) or not str(row.get("title") or "").strip():
        return False
    return not str(row.get("id") or "").startswith("legacy-")


def row_symbols(row: dict) -> list[str]:
    """기사 행의 티커 — ``tickers`` 객체/문자열과 ``$NVDA`` 형태 태그를 합친다.

    공개 호스트 top-stories 는 ``tickers`` 가 비고 심볼을 ``tag_names`` 의 ``$`` 태그로만 준다.
    """
    symbols: list[str] = []
    for ticker in row.get("tickers") or []:
        symbol = ticker.get("symbol") if isinstance(ticker, dict) else ticker
        if symbol:
            symbols.append(str(symbol).strip().upper())
    for tag in row.get("tag_names") or []:
        text = str(tag).strip()
        if text.startswith("$") and len(text) > 1:
            symbols.append(text[1:].upper())
    return list(dict.fromkeys(symbols))


def rows_for_tickers(rows: list, tickers) -> list[dict]:
    """top-stories 행 중 지정 티커가 태그된 기사만 (news/list?tickers 대체)."""
    wanted = {str(t).strip().upper() for t in tickers or [] if str(t).strip()}
    return [row for row in rows or []
            if is_article_row(row) and set(row_symbols(row)) & wanted]
