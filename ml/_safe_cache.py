"""ml/_safe_cache.py — pickle 역직렬화 안전 로더 (공용 헬퍼)

pickle.loads() 는 임의 코드 실행이 가능한 위험한 역직렬화다. 캐시·모델 파일이
공격자에 의해 교체되면(심볼릭 링크 스왑, 다른 사용자 소유 파일 주입 등) 로드만
해도 코드가 실행될 수 있다. 이 모듈은 ml 패키지 전역에서 쓰는 단일 안전 로더를
제공해, 신뢰할 수 있는(=내 소유, 심링크 아님) 파일만 역직렬화하도록 한다.

방침:
  - 기능은 기존과 동일하게 유지(json/parquet 전면 이관 아님). pickle 포맷 그대로.
  - 검증 실패 시 None 반환 + logger.warning → 호출부가 "캐시 미스"로 처리.
"""
from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def safe_unpickle(path: "str | os.PathLike[str] | Path") -> Optional[Any]:
    """소유자·심링크 검증을 거친 안전한 pickle 로드.

    검증 순서:
      (a) 심볼릭 링크면 거부 — 링크 스왑으로 신뢰 경계를 우회하는 공격 차단.
      (b) 파일 소유자(st_uid)가 현재 프로세스 uid 와 다르면 거부 — 다른 사용자가
          심어 둔 페이로드 로드 방지.
      (c) 통과 시에만 pickle.loads() 수행.

    어떤 단계든 실패하면 None 반환(+경고 로그). 호출부는 이를 캐시 미스로 처리해
    원본 데이터를 재계산/재다운로드하면 된다.
    """
    p = Path(path)
    try:
        # (a) 심볼릭 링크 거부 — lstat 기반 검사라 경로 자체가 링크면 즉시 차단.
        if os.path.islink(p):
            logger.warning("pickle 로드 거부(심볼릭 링크): %s", p)
            return None

        st = os.stat(p)  # 심링크 통과 후 실파일 stat

        # (b) 소유자 검증 — 내 소유 파일이 아니면 신뢰하지 않는다.
        if st.st_uid != os.getuid():
            logger.warning(
                "pickle 로드 거부(소유자 불일치 uid=%s, 내 uid=%s): %s",
                st.st_uid, os.getuid(), p,
            )
            return None

        # (c) 검증 통과 — 역직렬화 수행.
        with open(p, "rb") as f:
            return pickle.loads(f.read())
    except FileNotFoundError:
        return None
    except Exception as e:  # 손상 파일·역직렬화 오류 등 → 캐시 미스 취급
        logger.warning("pickle 로드 실패(%s): %s", p, e)
        return None


def harden_cache_dir(directory: "str | os.PathLike[str] | Path") -> None:
    """캐시 디렉터리를 0700(소유자 전용)으로 best-effort 보장.

    다른 사용자가 캐시 디렉터리에 파일을 심거나 교체하지 못하도록 권한을 좁힌다.
    실패해도(권한 부족·플랫폼 차이) 기존 동작을 깨지 않도록 조용히 넘어간다.
    """
    try:
        d = Path(directory)
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
    except Exception:
        pass
