#!/usr/bin/env python3
"""fundamental_snapshot.py — 펀더멘털 point-in-time 스냅샷 주간 적재

yfinance `.info`는 현재 값만 제공하므로 과거 학습에 그대로 쓰면 look-ahead
bias가 생긴다. 매주 스냅샷을 날짜와 함께 쌓아 두면 수개월 뒤부터
랭커의 진짜 학습 피처(ML_ROADMAP 2-D)로 사용할 수 있다.

크론 (토요일 01:00 UTC = 10:00 KST):
    0 1 * * 6 cd /home/ubuntu/projects/stock-report && uv run python crons/fundamental_snapshot.py >> /tmp/fundamental_snapshot.log 2>&1

출력: ~/reports/ml-cache/fundamental_snapshots.jsonl (1줄 = 1종목·1주)
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
SNAP_PATH = Path.home() / "reports" / "ml-cache" / "fundamental_snapshots.jsonl"


def main() -> int:
    logger.info("=== fundamental_snapshot 시작 ===")
    from ml.data_pipeline import US_TOP50
    from ml.entry_analyzer import PORTFOLIO_STOCKS
    from reports.fundamental_score import score_ticker

    import yfinance as yf

    tickers = list(dict.fromkeys(list(PORTFOLIO_STOCKS) + list(US_TOP50)))
    today   = datetime.now(KST).strftime("%Y-%m-%d")

    # 같은 날짜 중복 적재 방지
    if SNAP_PATH.exists():
        with open(SNAP_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("date") == today:
                        logger.info("%s 스냅샷 이미 존재 — 스킵", today)
                        return 0
                except Exception:
                    continue

    SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    with open(SNAP_PATH, "a", encoding="utf-8") as f:
        for t in tickers:
            try:
                r    = score_ticker(t)
                info = yf.Ticker(t).info or {}
                row = {
                    "date":            today,
                    "ticker":          t,
                    "total_score":     r.get("total_score", 0),
                    "grade":           r.get("grade", ""),
                    # ML_ROADMAP 2-D 지정 피처
                    "roe_ttm":         info.get("returnOnEquity"),
                    "earnings_growth": info.get("earningsGrowth"),
                    "revenue_growth":  info.get("revenueGrowth"),
                    "fwd_pe":          info.get("forwardPE"),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_ok += 1
            except Exception as e:
                logger.warning("%s 스냅샷 실패: %s", t, e)

    logger.info("스냅샷 완료: %d/%d종목 → %s", n_ok, len(tickers), SNAP_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
