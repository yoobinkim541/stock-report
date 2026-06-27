#!/usr/bin/env python3
"""naver_flow_snapshot.py — KR 투자자 수급 + KOSPI200 멤버십 forward 스냅샷 (Phase B+ / pykrx 공백 복구).

pykrx 가 이 서버에서 막혀(KRX 403) 과거 시점별 KOSPI200 멤버십·일별 수급을 무료로 backfill 할 수
없다. 대신 **지금부터 매일 Naver 스냅샷을 append** 해 두면 수개월~수년 뒤 point-in-time 멤버십(편입/
편출)·수급 추세를 학습 피처로 쓸 수 있다(earnings_snapshot 과 동일 원리).

크론 (평일 07:30 UTC = 16:30 KST, KRX 마감 후):
    30 7 * * 1-5 cd <repo> && uv run python crons/naver_flow_snapshot.py >> /tmp/naver_flow_snapshot.log 2>&1

출력: ~/reports/ml-data/kospi200_members.jsonl (1줄=1일 멤버십)
      ~/reports/ml-data/kr_flow_snapshots.jsonl (1줄=1종목·1일 수급)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_DIR = Path.home() / "reports" / "ml-data"
MEMBERS_PATH = _DIR / "kospi200_members.jsonl"
FLOW_PATH = _DIR / "kr_flow_snapshots.jsonl"


def _already(path: Path, today: str) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("date") == today:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def _flow_universe() -> list[str]:
    """수급 기록 대상 — KR 모의 유니버스(보유 + KR_TOP30). KOSPI200 전체는 호출 과다라 제외."""
    from ml.data_pipeline import KR_TOP30
    uni = list(KR_TOP30)
    try:
        from portfolio_universe import load_portfolio_tickers
        uni += [t for t in load_portfolio_tickers() if t.endswith(".KS") or t.endswith(".KQ")]
    except Exception:
        pass
    # 6자리 코드로 정규화(.KS 제거)
    from providers.kr_market_data import norm_code
    return list(dict.fromkeys(norm_code(t) for t in uni))


def main() -> int:
    logger.info("=== naver_flow_snapshot 시작 ===")
    from providers import naver_kr as nk
    today = datetime.now(KST).strftime("%Y-%m-%d")
    _DIR.mkdir(parents=True, exist_ok=True)

    # 1) KOSPI200 멤버십 스냅샷(forward point-in-time 이력)
    if not _already(MEMBERS_PATH, today):
        members = nk.kospi200_members()
        if members:
            with open(MEMBERS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": today, "n": len(members), "members": members},
                                   ensure_ascii=False) + "\n")
            logger.info("KOSPI200 멤버십 기록: %d종목", len(members))
        else:
            logger.warning("KOSPI200 멤버십 비어있음 — 기록 생략")
    else:
        logger.info("KOSPI200 %s 이미 기록 — 스킵", today)

    # 2) 종목별 수급 스냅샷(외인/기관/개인 순매수)
    if _already(FLOW_PATH, today):
        logger.info("수급 %s 이미 기록 — 스킵", today)
        return 0
    n_ok = 0
    with open(FLOW_PATH, "a", encoding="utf-8") as f:
        for code in _flow_universe():
            try:
                feat = nk.investor_flow_features(code)
                if feat.get("n"):
                    f.write(json.dumps({"date": today, "code": code, **feat}, ensure_ascii=False) + "\n")
                    n_ok += 1
                time.sleep(0.2)        # Naver throttle
            except Exception as e:
                logger.warning("%s 수급 실패: %s", code, e)
    logger.info("수급 스냅샷 완료: %d종목", n_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
