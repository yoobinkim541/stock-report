#!/usr/bin/env python3
"""오래된 원문·추출텍스트·매니페스트를 날짜별 gzip 번들로 묶는다."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from reports.raw_archive import compact_raw_artifacts


KST = timezone(timedelta(hours=9))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-age-days", type=int, default=2, help="이 일수보다 오래된 파일만 번들화")
    parser.add_argument("--dry-run", action="store_true", help="예상치만 계산하고 파일은 변경하지 않음")
    parser.add_argument("--max-files", type=int, default=None, help="이번 실행에서 묶을 최대 개별 파일 수")
    parser.add_argument("--max-bundles", type=int, default=None, help="이번 실행에서 만들 최대 번들 수")
    args = parser.parse_args()
    result = compact_raw_artifacts(
        now=datetime.now(KST),
        min_age_days=max(0, args.min_age_days),
        dry_run=args.dry_run,
        max_files=args.max_files,
        max_bundles=args.max_bundles,
    )
    logger.info(
        "원문 번들화 완료: groups=%d bundles=%d files=%d before=%dB after=%dB errors=%d dry_run=%s",
        result["groups"], result["bundles"], result["files_packed"],
        result["bytes_before"], result["bytes_after"], len(result["errors"]), result["dry_run"],
    )
    for error in result["errors"][:10]:
        logger.error("번들화 실패: %s", error)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
