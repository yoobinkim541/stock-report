#!/usr/bin/env python3
"""kiwoom_mock_close_generation.py — 국내 모의투자 계좌 만기 갱신 전 세대 마감.

키움 국내 모의투자는 3개월마다 만기 리셋된다. 리셋 후 새 앱키로 교체하면
브로커 측 NAV·보유종목이 강제로 초기화되는데, 이를 그대로 두면 리포트의
누적수익률·MDD 계산(lib.mock_generations)이 리셋을 전략 손실로 오인한다.

**실행 시점이 중요하다**: 만료 예정 계좌의 KIWOOM_MOCK_API_KEY/SECRET 을
아직 교체하기 전, 구 계좌가 살아있는 상태에서 실행해야 실제 최종
NAV·보유종목을 정확히 캡처한다. (갱신 리마인더가 이 스크립트 실행을
먼저 안내한다 — crons/kiwoom_mock_renewal_reminder.py 참고)

실행 후:
  1. 이 스크립트가 현재 세대의 성과(누적수익률·MDD·기간·마감 시점 보유종목)를
     kr_mock_history_generations 에 아카이브하고, kr_mock_history 에 세대 경계
     마커를 남긴다.
  2. 그 다음 키움 모의투자를 재신청하고 .env 의 앱키/시크릿을 교체한다.
  3. 이후 리포트는 새 세대의 스냅샷만으로 누적수익률·MDD 를 계산한다.

사용:
    uv run python crons/kiwoom_mock_close_generation.py --reason "3개월 만기 갱신"
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", default="3개월 만기 갱신", help="세대 마감 사유")
    args = parser.parse_args(argv)

    import kiwoom_mock
    from lib import mock_generations

    if not kiwoom_mock.is_enabled():
        logger.error("KIWOOM_MOCK_ENABLED 아님 — 마감 생략")
        return 1

    try:
        summary = mock_generations.close_generation(
            "kr_mock_history",
            get_balance_fn=kiwoom_mock.get_balance,
            reason=args.reason,
        )
    except RuntimeError as e:
        logger.error("세대 마감 실패: %s", e)
        return 1

    logger.info(
        "세대 %s 마감 완료 · %s~%s · 누적 %.2f%% · MDD %.2f%% · 보유 %d종목",
        summary["generation"], summary["start_date"], summary["end_date"],
        summary["cum_return_pct"], summary["mdd_pct"], len(summary["holdings_at_close"]),
    )

    try:
        import notify
        lines = [
            f"🧬 국내 모의투자 세대 {summary['generation']} 마감",
            f"기간 {summary['start_date']} ~ {summary['end_date']}",
            f"누적 {summary['cum_return_pct']:+.2f}% · MDD {summary['mdd_pct']:.2f}%",
            f"마감 시 보유 {len(summary['holdings_at_close'])}종목",
            "",
            "이제 키움 모의투자를 재신청하고 .env 앱키/시크릿을 교체하세요.",
        ]
        notify.send_telegram("\n".join(lines), token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
