#!/usr/bin/env python3
"""Kiwoom mock-investment renewal reminder.

2026-10-28 09:00 KST에 국내 모의투자 3개월 만기 갱신 알림을 텔레그램으로 보낸다.
cron 라인이 남아 있어도 target date가 아니거나 이미 발송된 상태면 no-op이다.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TARGET_DATE = date(2026, 10, 28)
STATE_PATH = Path.home() / ".local" / "share" / "stock-report" / "kiwoom_mock_renewal_20261028.sent.json"


def build_message() -> str:
    return "\n".join([
        "🔔 키움 국내 모의투자 갱신 알림",
        "━━━━━━━━━━━━━━━━━━",
        "국내 모의투자 3개월 만기 도래일입니다.",
        "키움 모의투자 신청을 갱신하고 모의투자 App Key / Secret을 다시 확인하세요.",
        "확인 항목:",
        "1. 키움 REST API 포털 > 모의투자 App Key 관리",
        "2. 허용 IP 등록 상태",
        "3. KIWOOM_MOCK_API_KEY / KIWOOM_MOCK_API_SECRET 갱신",
        "4. 갱신 후 국내 모의 잔고 조회와 일일 현황 보고 정상화 확인",
    ])


def _mark_sent(path: Path, today: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import safe_io

    safe_io.atomic_write_json(str(path), {"sent_at": today.isoformat(), "target": TARGET_DATE.isoformat()})


def main(
    *,
    today: date | None = None,
    state_path: Path = STATE_PATH,
    send_fn: Callable[..., bool] | None = None,
) -> int:
    today = today or date.today()
    if today != TARGET_DATE:
        logger.info("갱신 리마인더 대상일 아님: today=%s target=%s", today, TARGET_DATE)
        return 0
    if state_path.exists():
        logger.info("갱신 리마인더 이미 발송됨: %s", state_path)
        return 0

    if send_fn is None:
        import notify

        send_fn = notify.send_telegram
    ok = send_fn(
        build_message(),
        token=os.getenv("STOCK_BOT_TOKEN"),
        chat_id=os.getenv("STOCK_BOT_CHAT_ID"),
        timeout=15,
    )
    if not ok:
        logger.error("키움 모의투자 갱신 리마인더 발송 실패")
        return 1
    _mark_sent(state_path, today)
    logger.info("키움 모의투자 갱신 리마인더 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
