#!/usr/bin/env python3
"""
daily_ranking.py — NASDAQ100 LightGBM 종목 랭킹 일일 자동 발송

크론 (미국 장 마감 후, 평일 22:00 UTC = 07:00 KST):
    0 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python daily_ranking.py >> /tmp/daily_ranking.log 2>&1
"""
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID")


def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 미설정")
        return False
    return notify.send_telegram(text, token=BOT_TOKEN, chat_id=CHAT_ID, timeout=15)


def main() -> int:
    logger.info("=== daily_ranking 시작 ===")
    try:
        from ml.ranker import rank_today, load_ranker, format_ranking_report
        ranking = rank_today(mode="nasdaq100", top_n=15)
        result  = load_ranker()
        if ranking.empty or result is None:
            _send("❌ 일일 랭킹 생성 실패")
            return 1
        report = format_ranking_report(ranking, result)
        if _send(report):
            logger.info("랭킹 발송 완료 (%d종목)", len(ranking))
        return 0
    except Exception as e:
        msg = f"❌ daily_ranking 오류: {e}"
        logger.error(msg)
        _send(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
