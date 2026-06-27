"""lib/file_cache.py — 공유 TTL 파일캐시 프리미티브 (providers 중복 제거, 행위 보존).

5개 모듈(earnings_data·kr_market_data·index_membership·edgar·data_pipeline)이 반복하던
TTL 신선도 체크 + JSON get/put + 디렉터리 하드닝 + atomic write 를 통합. 포맷이 다른 parquet/csv/
pickle 캐시는 is_fresh 만 공유(get/put 은 각자) — data_pipeline 의 safe_unpickle 경로는 그대로.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def is_fresh(path, ttl_h: float) -> bool:
    """path 가 존재 + mtime 이 ttl_h 시간 이내면 True. (5곳 동일 1줄 통합.)"""
    try:
        p = Path(path)
        return p.exists() and (time.time() - p.stat().st_mtime) < ttl_h * 3600
    except Exception:
        return False


def harden_dir(directory: Path) -> None:
    """캐시 디렉터리 0700 하드닝(best-effort) + 생성. ml._safe_cache 재사용."""
    try:
        from ml._safe_cache import harden_cache_dir
        harden_cache_dir(directory)
    except Exception:
        Path(directory).mkdir(parents=True, exist_ok=True)


def read_json(path):
    """JSON 파일 → 객체. 실패 시 None."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_atomic(path, obj) -> bool:
    """디렉터리 하드닝 + tmp→rename atomic write. 실패 시 False(조용히)."""
    try:
        path = Path(path)
        harden_dir(path.parent)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False
