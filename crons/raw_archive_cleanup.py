#!/usr/bin/env python3
"""raw_archive_cleanup.py — SaveTicker 원본 아카이브 TTL 청소 크론."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from reports.raw_archive import cleanup_expired_raw_artifacts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def main() -> int:
    # 보존기간은 아티팩트 저장 시점에 소스별 정책(resolve_raw_ttl_days)으로 이미
    # 결정돼 각 매니페스트의 expires_at 에 기록됨 — 여기서 전역 override 는 없다
    # (과거 SAVE_TICKER_RAW_TTL_DAYS 는 실제로 아무 데도 안 쓰이던 죽은 설정이었음
    # — 감사 #34).
    now = datetime.now(KST)
    result = cleanup_expired_raw_artifacts(now=now)
    logger.info(
        "SaveTicker 원본 청소 완료: raw=%d manifests=%d bundles=%d bundle_entries=%d scanned=%d",
        result.get("deleted_raw", 0),
        result.get("deleted_manifests", 0),
        result.get("deleted_bundles", 0),
        result.get("deleted_bundle_entries", 0),
        result.get("scanned", 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
