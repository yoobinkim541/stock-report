#!/usr/bin/env python3
"""entry_signal_feedback.py — 진입 후보 추천 사후성과 백필/회고.

매일 실행해 20/60거래일이 지난 추천 후보의 실제 성과를 outcome 원장에 추가한다.
추천 스냅샷은 telegram_bot.notify_entry_signals / /entry 명령에서 적재한다.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from lib.cron_common import send_cron_telegram
from ml import entry_feedback

KST = timezone(timedelta(hours=9))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== entry_signal_feedback 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    added = entry_feedback.backfill_outcomes()
    s20 = entry_feedback.summarize_feedback(horizon=20)
    s60 = entry_feedback.summarize_feedback(horizon=60)
    adjust = entry_feedback.learn_feedback_adjustments(horizon=20)
    logger.info(
        "백필 %d건 · 20d n=%s · 60d n=%s · 보정학습 adopted=%s (%s)",
        added, s20.get("n"), s60.get("n"), adjust.get("adopted"), adjust.get("reason"),
    )

    if added or os.getenv("ENTRY_FEEDBACK_ALWAYS_SEND", "false").lower() == "true":
        send_cron_telegram("\n".join([
            "🧪 진입 후보 사후검증",
            f"신규 성숙 outcome: {added}건",
            entry_feedback.format_feedback_summary(s20),
            entry_feedback.format_feedback_summary(s60),
            f"점수 보정학습: {'채택' if adjust.get('adopted') else '대기'} — {adjust.get('reason')}",
            "⚠️ 정보형 추천 검증 — 실거래 주문 아님",
        ]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
