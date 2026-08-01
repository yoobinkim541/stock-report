from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ml.strategy_studio import StrategySpec, strategy_spec_hash


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
            CREATE TABLE IF NOT EXISTS strategy_specs (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                market          TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                base_symbol     TEXT NOT NULL,
                description     TEXT NOT NULL,
                spec_json       TEXT NOT NULL,
                spec_hash       TEXT NOT NULL,
                current_version INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_specs_updated
                ON strategy_specs(updated_at DESC);

            CREATE TABLE IF NOT EXISTS strategy_spec_versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                spec_id     TEXT NOT NULL,
                version     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                spec_json   TEXT NOT NULL,
                patch_json  TEXT NOT NULL,
                source      TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                UNIQUE(spec_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_spec_versions_spec
                ON strategy_spec_versions(spec_id, version DESC);
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


def list_conversation(limit: int = 30, *, context_surface: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit or 30), 200))
    where = ""
    args: list = []
    if context_surface:
        where = " WHERE context_surface = ?"
        args.append(str(context_surface))
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM conversation_notes{where} ORDER BY created_at DESC, id DESC LIMIT ?",
            args,
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


def _strategy_spec_payload(payload: dict[str, Any] | StrategySpec) -> dict[str, Any]:
    spec = StrategySpec.from_dict(payload).to_dict()
    spec.setdefault("id", None)
    return spec


def _strategy_spec_row(row: sqlite3.Row) -> dict[str, Any]:
    spec = json.loads(row["spec_json"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "market": row["market"],
        "timeframe": row["timeframe"],
        "base_symbol": row["base_symbol"],
        "description": row["description"],
        "version": row["current_version"],
        "spec_hash": row["spec_hash"],
        "spec": spec,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _strategy_version_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["spec_id"],
        "version_row_id": row["id"],
        "spec_id": row["spec_id"],
        "version": row["version"],
        "name": row["name"],
        "spec": json.loads(row["spec_json"] or "{}"),
        "patch": json.loads(row["patch_json"] or "{}"),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def strategy_spec_id(payload: dict[str, Any] | StrategySpec) -> str:
    spec = _strategy_spec_payload(payload)
    if spec.get("id"):
        return str(spec["id"])
    return strategy_spec_hash(spec)


def save_strategy_spec(payload: dict[str, Any] | StrategySpec) -> dict[str, Any]:
    spec = _strategy_spec_payload(payload)
    spec_id = str(spec.get("id") or strategy_spec_hash(spec))
    spec["id"] = spec_id
    return save_strategy_version(spec_id, spec, source="create")


def save_strategy_version(
    spec_id: str,
    spec: dict[str, Any] | StrategySpec,
    *,
    patch: dict[str, Any] | None = None,
    source: str = "ui",
) -> dict[str, Any]:
    spec_obj = StrategySpec.from_dict({**_strategy_spec_payload(spec), "id": spec_id})
    payload = spec_obj.to_dict()
    payload["id"] = spec_id
    version, now = _next_strategy_version(spec_id)
    patch_json = _json(patch or {})
    with connect() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO strategy_spec_versions
                    (spec_id, version, name, spec_json, patch_json, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec_id,
                    version,
                    payload["name"],
                    _json(payload),
                    patch_json,
                    source,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO strategy_specs
                    (id, name, market, timeframe, base_symbol, description, spec_json,
                     spec_hash, current_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    timeframe=excluded.timeframe,
                    base_symbol=excluded.base_symbol,
                    description=excluded.description,
                    spec_json=excluded.spec_json,
                    spec_hash=excluded.spec_hash,
                    current_version=excluded.current_version,
                    updated_at=excluded.updated_at
                """,
                (
                    spec_id,
                    payload["name"],
                    payload.get("market") or "us",
                    payload.get("timeframe") or "1d",
                    payload.get("base_symbol") or "",
                    str(payload.get("metadata", {}).get("description") or payload.get("description") or ""),
                    _json(payload),
                    strategy_spec_hash(payload),
                    version,
                    now,
                    now,
                ),
            )
    return get_strategy_spec(spec_id) or {"id": spec_id, "version": version, "spec": payload}


def _next_strategy_version(spec_id: str) -> tuple[int, str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM strategy_spec_versions WHERE spec_id = ?",
            (spec_id,),
        ).fetchone()
    return int(row["version"] or 0) + 1, _now()


def list_strategy_specs(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_specs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_strategy_spec_row(row) for row in rows]


def get_strategy_spec(spec_id: str, version: int | None = None) -> dict[str, Any] | None:
    spec_id = str(spec_id or "").strip()
    if not spec_id:
        return None
    with connect() as conn:
        if version is None:
            row = conn.execute(
                "SELECT * FROM strategy_specs WHERE id = ?",
                (spec_id,),
            ).fetchone()
            return _strategy_spec_row(row) if row else None
        row = conn.execute(
            """
            SELECT id, spec_id, version, name, spec_json, patch_json, source, created_at
            FROM strategy_spec_versions
            WHERE spec_id = ? AND version = ?
            """,
            (spec_id, int(version)),
        ).fetchone()
    return _strategy_version_row(row) if row else None


def list_strategy_versions(spec_id: str, limit: int = 20) -> list[dict[str, Any]]:
    spec_id = str(spec_id or "").strip()
    if not spec_id:
        return []
    limit = max(1, min(int(limit or 20), 200))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, spec_id, version, name, spec_json, patch_json, source, created_at
            FROM strategy_spec_versions
            WHERE spec_id = ?
            ORDER BY version DESC
            LIMIT ?
            """,
            (spec_id, limit),
        ).fetchall()
    return [_strategy_version_row(row) for row in rows]


def revert_strategy_version(spec_id: str, version: int) -> dict[str, Any]:
    snapshot = get_strategy_spec(spec_id, version=version)
    if not snapshot:
        raise ValueError(f"strategy version not found: {spec_id}@{version}")
    spec = snapshot.get("spec") or {}
    return save_strategy_version(spec_id, spec, patch={"revert_from": version}, source="revert")


def strategy_spec_catalog(limit: int = 50) -> dict[str, Any]:
    specs = list_strategy_specs(limit=limit)
    latest = specs[0] if specs else None
    return {
        "ok": True,
        "count": len(specs),
        "latest": latest,
        "specs": specs,
        "version_total": sum(len(list_strategy_versions(row["id"], limit=20)) for row in specs[:10]),
    }
