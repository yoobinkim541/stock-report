#!/usr/bin/env python3
"""earnings_snapshot.py — 어닝 컨센서스·서프라이즈·밸류에이션 point-in-time 적재 (Phase 1 / §G5).

yfinance .info / earnings_estimate / eps_revisions 는 현재값만 → 과거 학습에 그대로 쓰면
look-ahead bias. 매일 스냅샷을 날짜와 함께 쌓아 두면 수개월 뒤 실적예측(§G3)·주가반응예측(§G4)
모델의 **무(無)룩어헤드 학습데이터**가 된다. ★리비전 모멘텀의 시간 추이가 핵심 신호라 일별 적재.

크론 (평일 22:10 UTC = 07:10 KST, US 마감 후):
    10 22 * * 1-5 cd <repo> && uv run python crons/earnings_snapshot.py >> /tmp/earnings_snapshot.log 2>&1

출력: ~/reports/ml-cache/earnings_snapshots.jsonl (1줄 = 1종목·1일, append-only)
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

KST = timezone(timedelta(hours=9))
SNAP_PATH = Path.home() / "reports" / "ml-cache" / "earnings_snapshots.jsonl"


def _universe() -> list[str]:
    """포트폴리오 + 美 주요기업 + 韓 대형주(열화모드). 중복 제거."""
    from ml.data_pipeline import US_TOP50, KR_TOP10_META
    from ml.entry_analyzer import PORTFOLIO_STOCKS
    return list(dict.fromkeys(list(PORTFOLIO_STOCKS) + list(US_TOP50) + list(KR_TOP10_META.keys())))


def _row(ticker: str, today: str) -> dict:
    """1종목·1일 스냅샷 행 — earnings_data.summary 평탄화(point-in-time)."""
    from providers import earnings_data as ed
    s = ed.summary(ticker, force=True, today=today)
    v = s.get("valuation", {}) or {}
    c = s.get("consensus", {}) or {}
    n = s.get("next_earnings", {}) or {}
    last = s.get("last_surprise") or {}
    return {
        "date": today, "ticker": ticker,
        "market_type": s.get("market_type"), "degraded": s.get("degraded"),
        "next_earnings_date": n.get("date"), "days_until": n.get("days_until"),
        # 컨센서스·★리비전(시간추이가 핵심 학습신호)
        "eps_fwd_avg": c.get("eps_fwd_avg"), "n_analysts": c.get("n_analysts"),
        "rev_fwd_avg": c.get("rev_fwd_avg"), "revision_momentum": c.get("revision_momentum"),
        "eps_rev_up_30d": c.get("eps_rev_up_30d"), "eps_rev_down_30d": c.get("eps_rev_down_30d"),
        "target_upside_pct": c.get("target_upside_pct"),
        "last_eps_surprise_pct": last.get("surprise_pct"),
        # 밸류에이션
        "per": v.get("per"), "forward_pe": v.get("forward_pe"), "pbr": v.get("pbr"),
        "psr": v.get("psr"), "roe": v.get("roe"), "eps_ttm": v.get("eps_ttm"),
        "div_yield": v.get("div_yield"), "div_growth_1y": v.get("div_growth_1y"),
    }


def _already_done(today: str) -> bool:
    if not SNAP_PATH.exists():
        return False
    try:
        with open(SNAP_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("date") == today:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def main() -> int:
    logger.info("=== earnings_snapshot 시작 ===")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _already_done(today):
        logger.info("%s 스냅샷 이미 존재 — 스킵", today)
        return 0
    tickers = _universe()
    SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    with open(SNAP_PATH, "a", encoding="utf-8") as f:
        for t in tickers:
            try:
                f.write(json.dumps(_row(t, today), ensure_ascii=False) + "\n")
                n_ok += 1
            except Exception as e:
                logger.warning("%s 어닝 스냅샷 실패: %s", t, e)
    logger.info("어닝 스냅샷 완료: %d/%d종목 → %s", n_ok, len(tickers), SNAP_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
