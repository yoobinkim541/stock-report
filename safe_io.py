"""safe_io.py — 멀티프로세스 안전 파일 I/O (원자적 쓰기 + 교차 프로세스 쓰기 락).

portfolio_snapshot.json 처럼 봇·여러 크론(kiwoom_sync_rest, portfolio_sync_server,
holding_manager)이 동시에 read-modify-write 하는 파일을 보호한다.

- atomic_write_json: temp 파일에 쓴 뒤 os.replace 로 원자적 교체. 독자는 항상 옛/새
  둘 중 '완전한' 파일만 보게 되어 torn read(부분 읽기)가 원천 불가능해진다.
- file_write_lock: sidecar '<path>.lock' 에 배타 flock 을 걸어 writer 들을 직렬화.
  read-modify-write 를 통째로 감싸면 두 writer 가 같은 순간 서로의 섹션을 덮어쓰는
  lost update 를 막는다. flock 은 fd close/프로세스 종료 시 자동 해제 → 데드락에 강함.

기존 holding_manager._save 의 temp→rename 패턴을 단일 소스로 통합한 것.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time


def atomic_write_json(path: str, obj, *, indent: int = 2) -> None:
    """obj 를 path 에 원자적으로 기록 (temp→fsync→rename). 실패 시 원본 보존."""
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)   # 원자적 교체
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class LockTimeout(RuntimeError):
    """제한 시간 내 파일 쓰기 락을 잡지 못함."""


@contextlib.contextmanager
def file_write_lock(target_path: str, *, timeout: float = 30.0, poll: float = 0.1):
    """target_path 의 sidecar '.lock' 에 배타 flock 을 잡아 교차 프로세스 쓰기 직렬화.

    timeout 초 안에 못 잡으면 LockTimeout. with 블록 안에서 read-modify-write 수행.
    """
    lock_path = os.path.abspath(target_path) + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lf = open(lock_path, "w")
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"파일 쓰기 락 획득 실패(>{timeout}s): {lock_path}")
                time.sleep(poll)
        yield
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()
