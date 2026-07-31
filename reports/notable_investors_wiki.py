#!/usr/bin/env python3
"""reports/notable_investors_wiki.py — 유명 투자자(현재: 워런 버핏/버크셔) 13F → 위키 카드.

SEC EDGAR 13F-HR 을 무료·API키 없이 직접 파싱(providers/thirteenf.py)해 분기 포트폴리오를
위키 페이지 1건(필러당, 매 분기 갱신)으로 기록한다. 이전 스냅샷과 비교해 신규 편입/청산
종목을 감지 — 신규 편입은 "관심종목 후보" 신호로 텔레그램 알림도 보낸다.

과거 스냅샷은 ~/reports/ml-data/notable_investors_13f.jsonl 에 append-only 로 쌓인다
(다음 분기 필링과 비교할 기준선 + 학습/분석용 이력).

사용법:
    uv run python -m reports.notable_investors_wiki --dry-run
    uv run python -m reports.notable_investors_wiki
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports import institution_watch as iw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HISTORY_PATH = Path.home() / "reports" / "ml-data" / "notable_investors_13f.jsonl"

# 추적 대상 — providers.thirteenf.FILERS 에 등록된 필러 중 여기 나열된 것만 처리
TRACKED_FILERS = ["berkshire"]


def diff_holdings(prev: list[dict] | None, cur: list[dict]) -> dict:
    return iw._legacy_diff_holdings(prev, cur)


def _fmt_holding(h: dict) -> str:
    return iw._legacy_fmt_holding(h)


def build_wiki_page(snapshot: dict, diff: dict) -> dict:
    return iw.build_legacy_investor_page(snapshot, diff)


def run(filer_key: str, *, dry_run: bool = False) -> dict:
    return iw.run_legacy_investor(filer_key, dry_run=dry_run, history_path=HISTORY_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    notify_lines = []
    for filer_key in TRACKED_FILERS:
        result = run(filer_key, dry_run=args.dry_run)
        if not result.get("ok"):
            logger.warning("13F 조회 실패: %s", filer_key)
            continue
        if result["status"] == "unchanged":
            logger.info("%s — 신규 필링 없음", filer_key)
            continue
        logger.info("%s 위키 갱신 — 신규 %d · 청산 %d", filer_key,
                    len(result["new"]), len(result["exited"]))
        if result["new"]:
            names = ", ".join(_fmt_holding(h) for h in result["new"][:5])
            notify_lines.append(f"🆕 {result['filer_name']} 신규 편입: {names}")
        if result["exited"]:
            names = ", ".join(_fmt_holding(h) for h in result["exited"][:5])
            notify_lines.append(f"📤 {result['filer_name']} 청산: {names}")

    if notify_lines and not args.dry_run:
        try:
            import notify
            text = "🏛️ 유명 투자자 포트폴리오 변경 감지\n" + "\n".join(notify_lines)
            notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                                 chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
        except Exception as e:
            logger.warning("텔레그램 발송 실패: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
