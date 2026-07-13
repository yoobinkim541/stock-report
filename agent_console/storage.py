from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def db_path() -> Path:
    override = os.getenv("AGENT_CONSOLE_DB")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "stock-report" / "agent_console.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    owns = conn is None
    if conn is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_memory (
                id          TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                source      TEXT NOT NULL,
                kind        TEXT NOT NULL,
                title       TEXT NOT NULL,
                body        TEXT NOT NULL,
                symbols     TEXT NOT NULL,
                impact      TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 0.5,
                metadata    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_market_memory_observed
                ON market_memory(observed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_market_memory_source
                ON market_memory(source, kind);

            CREATE TABLE IF NOT EXISTS conversation_notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                role            TEXT NOT NULL,
                message         TEXT NOT NULL,
                context_surface TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_created
                ON conversation_notes(created_at DESC);

            CREATE TABLE IF NOT EXISTS portfolio_scenarios (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT NOT NULL,
                allocations TEXT NOT NULL,
                rules       TEXT NOT NULL,
                assumptions TEXT NOT NULL,
                metrics     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        if owns:
            conn.close()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def memory_id(event: dict) -> str:
    key = "|".join(
        [
            str(event.get("observed_at") or ""),
            str(event.get("source") or ""),
            str(event.get("kind") or ""),
            str(event.get("title") or ""),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def upsert_memory_events(events: Iterable[dict]) -> int:
    rows = []
    for raw in events:
        event = dict(raw)
        event.setdefault("id", memory_id(event))
        event.setdefault("observed_at", _now())
        event.setdefault("source", "stock-report")
        event.setdefault("kind", "market")
        event.setdefault("title", "")
        event.setdefault("body", "")
        event.setdefault("symbols", [])
        event.setdefault("impact", "unknown")
        event.setdefault("confidence", 0.5)
        event.setdefault("metadata", {})
        rows.append(event)
    if not rows:
        return 0
    with connect() as conn:
        before = conn.total_changes
        with conn:
            conn.executemany(
                """
                INSERT INTO market_memory
                    (id, observed_at, source, kind, title, body, symbols, impact,
                     confidence, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    source=excluded.source,
                    kind=excluded.kind,
                    title=excluded.title,
                    body=excluded.body,
                    symbols=excluded.symbols,
                    impact=excluded.impact,
                    confidence=excluded.confidence,
                    metadata=excluded.metadata
                """,
                [
                    (
                        event["id"],
                        str(event["observed_at"]),
                        str(event["source"]),
                        str(event["kind"]),
                        str(event["title"])[:500],
                        str(event["body"])[:5000],
                        _json(event.get("symbols") or []),
                        str(event.get("impact") or "unknown")[:80],
                        float(event.get("confidence") or 0.5),
                        _json(event.get("metadata") or {}),
                        _now(),
                    )
                    for event in rows
                ],
            )
        return max(0, conn.total_changes - before)


def list_memory_events(limit: int = 80, *, source: str | None = None, kind: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit or 80), 500))
    where = []
    args: list = []
    if source:
        where.append("source = ?")
        args.append(source)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    sql = "SELECT * FROM market_memory"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at DESC, created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_memory(row) for row in rows]


def _row_to_memory(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "observed_at": row["observed_at"],
        "source": row["source"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "symbols": json.loads(row["symbols"] or "[]"),
        "impact": row["impact"],
        "confidence": row["confidence"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "created_at": row["created_at"],
    }


def add_conversation(role: str, message: str, context_surface: str = "market") -> int:
    with connect() as conn:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO conversation_notes (role, message, context_surface, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (role, message, context_surface, _now()),
            )
            return int(cur.lastrowid)


def list_conversation(limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit or 30), 200))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_notes ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows][::-1]


def scenario_id(name: str, allocations) -> str:
    key = f"{name}|{_json(allocations)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def save_scenario(payload: dict) -> dict:
    name = str(payload.get("name") or "새 포트폴리오 시나리오").strip()[:120]
    allocations = payload.get("allocations") or []
    scenario = {
        "id": payload.get("id") or scenario_id(name, allocations),
        "name": name,
        "description": str(payload.get("description") or "").strip()[:2000],
        "allocations": allocations,
        "rules": payload.get("rules") or {},
        "assumptions": payload.get("assumptions") or {},
        "metrics": payload.get("metrics") or {},
    }
    now = _now()
    with connect() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO portfolio_scenarios
                    (id, name, description, allocations, rules, assumptions, metrics, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    allocations=excluded.allocations,
                    rules=excluded.rules,
                    assumptions=excluded.assumptions,
                    metrics=excluded.metrics,
                    updated_at=excluded.updated_at
                """,
                (
                    scenario["id"],
                    scenario["name"],
                    scenario["description"],
                    _json(scenario["allocations"]),
                    _json(scenario["rules"]),
                    _json(scenario["assumptions"]),
                    _json(scenario["metrics"]),
                    now,
                    now,
                ),
            )
    return scenario


def list_scenarios(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_scenarios ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "allocations": json.loads(row["allocations"] or "[]"),
            "rules": json.loads(row["rules"] or "{}"),
            "assumptions": json.loads(row["assumptions"] or "{}"),
            "metrics": json.loads(row["metrics"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
