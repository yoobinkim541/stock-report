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
DEFAULT_TTL_DAYS = int(os.getenv("SAVE_TICKER_RAW_TTL_DAYS", "30"))


def main() -> int:
    now = datetime.now(KST)
    result = cleanup_expired_raw_artifacts(now=now, ttl_days=DEFAULT_TTL_DAYS)
    logger.info(
        "SaveTicker 원본 청소 완료: raw=%d manifests=%d scanned=%d ttl=%d",
        result.get("deleted_raw", 0),
        result.get("deleted_manifests", 0),
        result.get("scanned", 0),
        result.get("ttl_days", DEFAULT_TTL_DAYS),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
