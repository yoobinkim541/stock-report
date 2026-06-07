#!/usr/bin/env python3
"""
daily_leverage_retrain.py — 레버리지 ETF 모델 일일 재학습 + 신호 발송

크론 (미국 장 마감 후, 평일 22:15 UTC = 07:15 KST):
    15 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python daily_leverage_retrain.py >> /tmp/daily_leverage.log 2>&1
"""
import logging
import os
import sys
import warnings

warnings.filterwarnings("ignore")

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
    import requests
    try:
        for i in range(0, len(text), 4000):
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text[i:i+4000]},
                timeout=15,
            ).raise_for_status()
        return True
    except Exception as e:
        logger.error("텔레그램 발송 실패: %s", e)
        return False


def main() -> int:
    logger.info("=== daily_leverage_retrain 시작 ===")

    try:
        from ml.leverage_signal import get_entry_signal, format_leverage_report

        # 강제 재학습 (최신 종가 반영)
        logger.info("LeverageModel 재학습 중...")
        sig    = get_entry_signal(retrain=True)
        report = format_leverage_report(sig)

        logger.info(
            "재학습 완료 — 낙폭 %.1f%% | 진입조언: %s",
            sig.current_drawdown * 100,
            sig.entry_advice[:20],
        )

        if _send(report):
            logger.info("레버리지 리포트 발송 완료")
        return 0

    except Exception as e:
        msg = f"❌ daily_leverage_retrain 오류: {e}"
        logger.error(msg)
        _send(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
