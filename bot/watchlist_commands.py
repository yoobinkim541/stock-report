#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bot/watchlist_commands.py — /watch add|list|remove (관심종목 관리)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def cmd_watch(chat_id: str, args: list, send_fn) -> None:
    """
    /watch                          → 사용법
    /watch add TICKER [메모...]      → 수동 추가(source=manual)
    /watch list                     → 전체 목록(추가 사유·일자)
    /watch remove TICKER            → 삭제
    """
    from lib import watchlist

    if not args:
        send_fn(chat_id,
                "⭐ 관심종목 사용법\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "/watch add TICKER [메모]   수동 추가\n"
                "/watch list                전체 목록\n"
                "/watch remove TICKER       삭제\n"
                "\n"
                "예시:\n"
                "/watch add PLTR 실적 기대\n"
                "/watch remove PLTR\n"
                "\n"
                "※ 버크셔 13F 신규편입은 매주 월요일 크론이 자동 추가합니다.")
        return

    sub = args[0].lower()

    if sub == "add":
        if len(args) < 2:
            send_fn(chat_id, "❌ 티커를 입력하세요.\n예: /watch add PLTR 실적 기대")
            return
        ticker = args[1].upper()
        note = " ".join(args[2:]) if len(args) > 2 else None
        entry = watchlist.add_ticker(ticker, reason="수동 추가", source="manual", note=note)
        note_line = f" — {entry['note']}" if entry.get("note") else ""
        send_fn(chat_id, f"⭐ 관심종목 추가: {entry['ticker']}{note_line}")
        return

    if sub == "list":
        entries = watchlist.list_watchlist()
        if not entries:
            send_fn(chat_id, "관심종목이 없습니다.\n/watch add TICKER 로 추가하세요.")
            return
        lines = ["⭐ 관심종목 목록", "━━━━━━━━━━━━━━━━━━━━━━━"]
        for e in entries:
            note_part = f" — {e['note']}" if e.get("note") else ""
            lines.append(f"{e['ticker']}  {e['reason']}{note_part}  ({e['added_at'][:10]})")
        send_fn(chat_id, "\n".join(lines))
        return

    if sub == "remove":
        if len(args) < 2:
            send_fn(chat_id, "❌ 티커를 입력하세요.\n예: /watch remove PLTR")
            return
        ticker = args[1].upper()
        ok = watchlist.remove_ticker(ticker)
        if ok:
            send_fn(chat_id, f"🗑️ 관심종목 삭제: {ticker}")
        else:
            send_fn(chat_id, f"❌ 관심종목에 없습니다: {ticker}")
        return

    send_fn(chat_id, "❌ 알 수 없는 하위 명령입니다. /watch 로 사용법을 확인하세요.")
