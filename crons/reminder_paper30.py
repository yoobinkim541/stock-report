#!/usr/bin/env python3
"""reminder_paper30.py — 1회성 리마인더: paper_track 30일 표본 확인 + 3-A 착수 검토

2026-07-13 08:00 KST에 실행되어 paper_track.json 표본 수를 세고
텔레그램으로 알린 뒤, 자신의 crontab 라인(# PAPER30_REMINDER 마커)을 제거한다.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TRACK_PATH = Path.home() / ".local" / "share" / "stock-report" / "paper_track.json"
MARKER     = "PAPER30_REMINDER"


def main() -> int:
    track = {}
    if TRACK_PATH.exists():
        try:
            track = json.loads(TRACK_PATH.read_text())
        except Exception:
            pass
    total  = len(track)
    filled = sum(1 for e in track.values() if "ret_meta_5d" in e and "ret_rule_5d" in e)
    ready  = filled >= 30

    msg = "\n".join([
        "⏰ 리마인더 — 페이퍼 트레이딩 30일 점검",
        "━━━━━━━━━━━━━━",
        f"기록 {total}일 / 실현수익 확정 {filled}일",
        ("✅ 표본 30일 충족 — 3-A(MetaAllocator Ridge 가중치 학습) 착수 가능"
         if ready else
         f"⏳ 표본 부족 ({filled}/30) — 부족분만큼 더 기다린 뒤 착수"),
        "오늘 월요일 A/B Sharpe 요약도 함께 발송됐는지 확인하세요.",
        "착수 시: Claude Code에서 'ML_ROADMAP 3-A 진행해줘'",
    ])

    token, chat = os.getenv("STOCK_BOT_TOKEN"), os.getenv("STOCK_BOT_CHAT_ID")
    if token and chat:
        import notify
        notify.send_telegram(msg, token=token, chat_id=chat, timeout=15)
        logger.info("리마인더 발송 완료 (filled=%d)", filled)
    else:
        logger.warning("봇 토큰 미설정 — 발송 생략:\n%s", msg)

    # 1회성: 자신의 crontab 라인 제거
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = "\n".join(l for l in cur.splitlines() if MARKER not in l)
        subprocess.run(["crontab", "-"], input=new + "\n", text=True, check=True)
        logger.info("crontab 자기 제거 완료")
    except Exception as e:
        logger.warning("crontab 자기 제거 실패: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
