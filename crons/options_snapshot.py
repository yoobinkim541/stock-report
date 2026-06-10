#!/usr/bin/env python3
"""options_snapshot.py — 옵션 지표 point-in-time 일일 적재

옵션 데이터(ATM IV·풋콜비·스큐·기대변동폭)는 과거 조회가 불가능하므로
매일 스냅샷을 쌓아야 나중에 모델 학습 피처로 쓸 수 있다
(펀더멘털 스냅샷과 동일한 원리 — look-ahead 없는 데이터 축적).

크론 (평일 21:30 UTC = 미 장마감 직후 06:30 KST):
    30 21 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/options_snapshot.py >> /tmp/options_snapshot.log 2>&1

출력: ~/reports/ml-cache/options_snapshots.jsonl (1줄 = 1종목·1일)
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
SNAP_PATH = Path.home() / "reports" / "ml-cache" / "options_snapshots.jsonl"

# 포트폴리오 + 레버리지 + 시장 지표 ETF
TICKERS = ["MSFT", "NVDA", "GOOGL", "ORCL", "UNH", "SPMO", "QQQI",
           "QLD", "TQQQ", "UPRO", "QQQ", "SPY"]


def main() -> int:
    logger.info("=== options_snapshot 시작 ===")
    from ml.options_features import fetch_option_metrics

    today = datetime.now(KST).strftime("%Y-%m-%d")
    if SNAP_PATH.exists():
        with open(SNAP_PATH, encoding="utf-8") as f:
            if any(json.loads(l).get("date") == today for l in f if l.strip()):
                logger.info("%s 이미 적재됨 — 스킵", today)
                return 0

    SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    with open(SNAP_PATH, "a", encoding="utf-8") as f:
        for t in TICKERS:
            try:
                m = fetch_option_metrics(t, force=True)
                if m:
                    f.write(json.dumps({"date": today, "ticker": t, **m}, ensure_ascii=False) + "\n")
                    n_ok += 1
            except Exception as e:
                logger.warning("%s 옵션 스냅샷 실패: %s", t, e)

    logger.info("옵션 스냅샷 완료: %d/%d종목 → %s", n_ok, len(TICKERS), SNAP_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
