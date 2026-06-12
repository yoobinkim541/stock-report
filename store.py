#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py — user_id 스코프 SQLite 저장소 (멀티유저 확장 기반)

흩어진 JSON 파일 상태를 단일 SQLite DB로 통합한다.
- 멀티프로세스 안전: WAL 모드 + busy_timeout (봇 상시 프로세스 + 크론 동시 접근).
- atomic: 모든 쓰기는 트랜잭션(`with conn:`) — 중간 크래시 시 원본 보호.
- user_id 스코프: 모든 레코드에 user_id 차원 → 향후 멀티유저 확장 시 컬럼만 분기.

두 가지 저장 모델:
  1. 컬렉션(collection)  — append-log 리스트 (tax_records, portfolio_history 등).
     각 항목이 한 행(seq 순서 보존). 전체 파일 재작성 없이 append.
  2. 문서(document)      — 단일 JSON blob (설정·상태). key 1개당 1행.

레거시 호환: 기존 JSON 파일은 첫 접근 시 자동 import (원본은 보존 — 롤백 가능).

이 모듈은 표준 라이브러리만 사용한다 (무거운 의존성 import 금지).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# ── 기본 사용자 (단일 사용자 모드) ──────────────────────────────────────
# 코드에 실제 chat_id 하드코딩 금지 (프로젝트 규칙) — 리터럴 "default" 사용.
DEFAULT_USER = "default"

# ── DB 경로 (테스트는 STOCK_REPORT_DB 로 override) ──────────────────────
_DEFAULT_DB = Path.home() / ".local" / "share" / "stock-report" / "stock_report.db"


def db_path() -> Path:
    override = os.environ.get("STOCK_REPORT_DB")
    return Path(override) if override else _DEFAULT_DB


_init_lock = threading.Lock()
_initialized: set[str] = set()


@contextmanager
def _connect():
    """WAL + busy_timeout 적용된 sqlite3 연결 (호출당 1개, 스레드·프로세스 안전)."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn, str(path))
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection, key: str):
    if key in _initialized:
        return
    with _init_lock:
        if key in _initialized:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collections (
                user_id    TEXT    NOT NULL,
                name       TEXT    NOT NULL,
                seq        INTEGER NOT NULL,
                item       TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                PRIMARY KEY (user_id, name, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_collections_un
                ON collections(user_id, name);

            CREATE TABLE IF NOT EXISTS documents (
                user_id    TEXT NOT NULL,
                key        TEXT NOT NULL,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            CREATE TABLE IF NOT EXISTS migrations (
                user_id TEXT NOT NULL,
                name    TEXT NOT NULL,
                done_at TEXT NOT NULL,
                PRIMARY KEY (user_id, name)
            );
            """
        )
        conn.commit()
        _initialized.add(key)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════
#  컬렉션 API (append-log 리스트)
# ══════════════════════════════════════════════════════════════════════

def append(name: str, item: dict, *, user: str = DEFAULT_USER) -> int:
    """컬렉션 끝에 항목 추가. 부여된 seq 반환."""
    with _connect() as conn:
        with conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM collections WHERE user_id=? AND name=?",
                (user, name),
            ).fetchone()
            seq = int(row[0]) + 1
            conn.execute(
                "INSERT INTO collections (user_id, name, seq, item, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user, name, seq, json.dumps(item, ensure_ascii=False), _now()),
            )
    return seq


def all(name: str, *, user: str = DEFAULT_USER) -> list[dict]:
    """컬렉션 전체 항목 (seq 오름차순 = 삽입 순서)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT item FROM collections WHERE user_id=? AND name=? ORDER BY seq",
            (user, name),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def replace_all(name: str, items: list[dict], *, user: str = DEFAULT_USER) -> None:
    """컬렉션 전체 교체 (삭제·수정용). 트랜잭션으로 원자 처리."""
    with _connect() as conn:
        with conn:
            conn.execute(
                "DELETE FROM collections WHERE user_id=? AND name=?", (user, name)
            )
            now = _now()
            conn.executemany(
                "INSERT INTO collections (user_id, name, seq, item, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (user, name, i, json.dumps(it, ensure_ascii=False), now)
                    for i, it in enumerate(items)
                ],
            )


def count(name: str, *, user: str = DEFAULT_USER) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM collections WHERE user_id=? AND name=?",
            (user, name),
        ).fetchone()
    return int(row[0])


# ══════════════════════════════════════════════════════════════════════
#  문서 API (단일 JSON blob — 설정·상태용, Phase 2 확장 대비)
# ══════════════════════════════════════════════════════════════════════

def get_doc(key: str, default=None, *, user: str = DEFAULT_USER):
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM documents WHERE user_id=? AND key=?", (user, key)
        ).fetchone()
    return json.loads(row[0]) if row else default


def put_doc(key: str, data, *, user: str = DEFAULT_USER) -> None:
    with _connect() as conn:
        with conn:
            conn.execute(
                "INSERT INTO documents (user_id, key, data, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at",
                (user, key, json.dumps(data, ensure_ascii=False), _now()),
            )


# ══════════════════════════════════════════════════════════════════════
#  레거시 JSON 마이그레이션 + 편의 로더
# ══════════════════════════════════════════════════════════════════════

def _is_migrated(conn: sqlite3.Connection, user: str, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM migrations WHERE user_id=? AND name=?", (user, name)
    ).fetchone() is not None


def _mark_migrated(conn: sqlite3.Connection, user: str, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO migrations (user_id, name, done_at) VALUES (?, ?, ?)",
        (user, name, _now()),
    )


def ensure_migrated(name: str, legacy_path, *, user: str = DEFAULT_USER) -> None:
    """레거시 JSON 리스트를 컬렉션으로 1회 import. 원본 파일은 보존 (롤백 대비).

    멱등: migrations 테이블로 1회만 수행. 이미 DB에 행이 있으면 import 생략.
    """
    with _connect() as conn:
        if _is_migrated(conn, user, name):
            return
        has_rows = conn.execute(
            "SELECT 1 FROM collections WHERE user_id=? AND name=? LIMIT 1",
            (user, name),
        ).fetchone() is not None

        items: list = []
        if not has_rows:
            try:
                p = Path(legacy_path)
                if p.exists():
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        items = loaded
            except Exception:
                items = []

        with conn:
            now = _now()
            if items:
                conn.executemany(
                    "INSERT INTO collections (user_id, name, seq, item, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (user, name, i, json.dumps(it, ensure_ascii=False), now)
                        for i, it in enumerate(items)
                    ],
                )
            _mark_migrated(conn, user, name)


def load_collection(name: str, legacy_path, *, user: str = DEFAULT_USER) -> list[dict]:
    """레거시 자동 마이그레이션 후 컬렉션 전체 반환 (기록로그 모듈용 헬퍼)."""
    ensure_migrated(name, legacy_path, user=user)
    return all(name, user=user)
