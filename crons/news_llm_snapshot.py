#!/usr/bin/env python3
"""news_llm_snapshot.py — LLM 뉴스 구조화 라벨 point-in-time 적재 크론 (opt-in).

수집된 뉴스(source-cache) 중 티커 태그가 있는 미라벨 이벤트만 골라 LLM 으로
{티커, 이벤트유형, 방향, 강도} 구조화 → ~/reports/ml-data/news_llm_labels.jsonl append.
이 라벨은 {us,kr}_mock_track 의 news 축 피처(기본 가중 0 — 수집 전용)가 되고,
주간 학습의 신규 축 게이트(최소 20쌍 + 안정성)를 통과해야만 가중 승격된다.

정직: LLM 라벨 = 피처 후보일 뿐 엣지 아님 — 승격은 기존 OOS 게이트가 결정.
안전: NEWS_LLM_LABELS_ENABLED=true 여야 동작(기본 off). 실패·미설치 → 조용히 스킵.
비용: 회당 NEWS_LLM_LABELS_MAX(기본 30)건 한 번의 배치 호출 — 일 2회 ≈ 수백 토큰/회.
크론 (평일 00:05·14:05 UTC — KR 00:30·US 15:00 모의 결정 직전):
    5 0,14 * * 1-5 cd <repo> && uv run python crons/news_llm_snapshot.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

ENABLED = os.getenv("NEWS_LLM_LABELS_ENABLED", "false").lower() == "true"
MAX_PER_RUN = int(os.getenv("NEWS_LLM_LABELS_MAX", "30"))


def pick_events(events: list[dict], already: set[str], cap: int) -> list[dict]:
    """티커 태그 보유 + 미라벨 이벤트만 최신순 cap 건 (비용 통제·순수)."""
    from providers.news_labels import _event_tickers
    from reports.source_collector import event_id
    out = []
    for e in sorted(events, key=lambda x: str(x.get("published_at", "")), reverse=True):
        eid = e.get("id") or event_id(e)
        e["id"] = eid
        if eid in already or not _event_tickers(e):
            continue
        out.append(e)
        if len(out) >= cap:
            break
    return out


def main() -> int:
    now = datetime.now(KST)
    logger.info("=== news_llm_snapshot [%s] ===", now.strftime("%Y-%m-%d %H:%M"))
    if not ENABLED:
        logger.info("NEWS_LLM_LABELS_ENABLED=false — 스킵 (opt-in)")
        return 0

    from reports.source_collector import load_recent_events
    from providers import news_labels

    try:
        events = load_recent_events(hours=36)
    except Exception as e:
        logger.warning("이벤트 로드 실패: %s", e)
        return 0

    targets = pick_events(events, news_labels.labeled_ids(), MAX_PER_RUN)
    logger.info("라벨 대상: %d건 (수집 %d건 중 티커태그·미라벨)", len(targets), len(events))
    if not targets:
        return 0

    labels = news_labels.label_events(targets)
    n = news_labels.append_labels(labels)
    logger.info("라벨 적재 %d/%d건 → %s (검증 폐기 %d건)",
                n, len(targets), news_labels.LABELS_PATH, len(targets) - n)

    # 월드 메모리 이슈 적재 (영구 축적 — dedupe 멱등·실패 무시)
    try:
        from lib.world_memory import ingest_from_labels
        added = ingest_from_labels(labels)
        logger.info("월드 메모리 이슈 +%d건", added)
    except Exception as e:
        logger.warning("월드 메모리 적재 실패(무시): %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
