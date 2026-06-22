#!/usr/bin/env python3
"""institutional_snapshot.py — 기관 매집·13F 지표 point-in-time 주간 적재

매집 강도(OBV·CMF·up/down·A/D·거래량)와 13F 보유 지표는 그때그때의
현재 값만 얻을 수 있어, 매주 스냅샷을 날짜와 함께 쌓아 두어야
시간이 지나며 실제 변화량(델타)을 추적하고 학습 피처로 쓸 수 있다
(fundamental_snapshot·options_snapshot 과 동일한 look-ahead 없는 축적 원리).

크론 (토요일 01:30 UTC = 10:30 KST, fundamental_snapshot 01:00 UTC 이후):
    30 1 * * 6 cd /home/ubuntu/projects/stock-report && uv run python crons/institutional_snapshot.py >> /tmp/institutional_snapshot.log 2>&1

출력: ~/reports/ml-cache/institutional_snapshots.jsonl (1줄 = 1종목·1주)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST       = timezone(timedelta(hours=9))
SNAP_PATH = Path.home() / "reports" / "ml-cache" / "institutional_snapshots.jsonl"


def _name_fn(ticker: str):
    """KOSPI는 한글명(KR_TOP10_META), 그 외는 티커 그대로."""
    from ml.data_pipeline import KR_TOP10_META
    meta = KR_TOP10_META.get(ticker)
    return meta[0] if meta else ticker


def main() -> int:
    logger.info("=== institutional_snapshot 시작 ===")
    from ml.data_pipeline import US_TOP100, KR_TOP10_META
    from portfolio_universe import load_portfolio_tickers
    from reports.institutional_flow import rank_accumulation, clean_entry, accumulation_mobile_block

    # 추적 유니버스: 보유 티커 + 미국 대형주 + KOSPI Top10 (순서 보존 중복 제거)
    universe = list(dict.fromkeys(
        list(load_portfolio_tickers()) + list(US_TOP100) + list(KR_TOP10_META)
    ))

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 같은 날짜 중복 적재 방지
    if SNAP_PATH.exists():
        with open(SNAP_PATH, encoding="utf-8") as f:
            if any(json.loads(l).get("date") == today for l in f if l.strip()):
                logger.info("%s 스냅샷 이미 존재 — 스킵", today)
                return 0

    # 전 종목 점수 + 상위 25개만 13F enrich
    ranked = rank_accumulation(universe, min_score=0, limit=9999,
                               enrich=True, enrich_top=25)
    logger.info("매집 분석 완료: %d/%d종목", len(ranked), len(universe))

    SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    with open(SNAP_PATH, "a", encoding="utf-8") as f:
        for e in ranked:
            try:
                row = {"date": today, **clean_entry(e, name_fn=_name_fn)}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_ok += 1
            except Exception as ex:
                logger.warning("%s 직렬화 실패: %s", e.get("ticker"), ex)

    logger.info("스냅샷 완료: %d종목 → %s", n_ok, SNAP_PATH)

    # 주간 다이제스트: 매집 강도 상위 5개 (ranked 는 accum_score 내림차순)
    BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
    CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")
    if BOT_TOKEN and ranked:
        import notify
        block = accumulation_mobile_block(ranked[:5], title="🏛️ 기관 매집 주간 다이제스트",
                                          limit=5, name_fn=_name_fn)
        msg = "\n".join([f"📅 {today}"] + block)
        if notify.send_telegram(msg, token=BOT_TOKEN, chat_id=CHAT_ID):
            logger.info("주간 다이제스트 발송 완료")
    else:
        logger.info("STOCK_BOT_TOKEN 없음 또는 결과 없음 — 발송 스킵")

    return 0


if __name__ == "__main__":
    sys.exit(main())
