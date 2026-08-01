from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dashboard import chart_workspace
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

            CREATE TABLE IF NOT EXISTS chart_workspaces (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                layout          TEXT NOT NULL,
                workspace_json  TEXT NOT NULL,
                current_version INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chart_workspaces_updated
                ON chart_workspaces(updated_at DESC);

            CREATE TABLE IF NOT EXISTS chart_workspace_versions (
                row_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id    TEXT NOT NULL,
                version         INTEGER NOT NULL,
                name            TEXT NOT NULL,
                workspace_json  TEXT NOT NULL,
                note            TEXT NOT NULL,
                source          TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                UNIQUE(workspace_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_chart_workspace_versions_workspace
                ON chart_workspace_versions(workspace_id, version DESC);

            CREATE TABLE IF NOT EXISTS chart_templates (
                id              TEXT PRIMARY KEY,
                kind            TEXT NOT NULL,
                name            TEXT NOT NULL,
                template_json   TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chart_templates_kind
                ON chart_templates(kind, updated_at DESC);

            CREATE TABLE IF NOT EXISTS chart_drawing_snapshots (
                workspace_id TEXT NOT NULL,
                store_key    TEXT NOT NULL,
                drawing_json TEXT NOT NULL,
                source       TEXT NOT NULL,
                version      INTEGER NOT NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY(workspace_id, store_key)
            );
            CREATE INDEX IF NOT EXISTS idx_chart_drawing_snapshots_workspace
                ON chart_drawing_snapshots(workspace_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS chart_alert_rules (
                id             TEXT PRIMARY KEY,
                workspace_id   TEXT NOT NULL,
                store_key      TEXT NOT NULL,
                symbol         TEXT NOT NULL,
                timeframe      TEXT NOT NULL,
                name           TEXT NOT NULL,
                condition_json TEXT NOT NULL,
                message        TEXT NOT NULL,
                frequency      TEXT NOT NULL,
                enabled        INTEGER NOT NULL,
                last_state_json TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chart_alert_rules_workspace
                ON chart_alert_rules(workspace_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chart_alert_rules_symbol
                ON chart_alert_rules(symbol, timeframe, enabled);

            CREATE TABLE IF NOT EXISTS chart_alert_runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id      TEXT NOT NULL,
                status            TEXT NOT NULL,
                rule_count        INTEGER NOT NULL,
                event_count       INTEGER NOT NULL,
                missing_bars_json TEXT NOT NULL,
                notification_json TEXT NOT NULL,
                result_json       TEXT NOT NULL,
                created_at        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chart_alert_runs_workspace
                ON chart_alert_runs(workspace_id, created_at DESC);
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


def _chart_workspace_row(row: sqlite3.Row) -> dict[str, Any]:
    workspace = json.loads(row["workspace_json"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "layout": row["layout"],
        "version": int(row["current_version"]),
        "workspace": workspace,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _chart_workspace_version_row(row: sqlite3.Row) -> dict[str, Any]:
    workspace = json.loads(row["workspace_json"] or "{}")
    return {
        "id": row["workspace_id"],
        "version_row_id": row["row_id"],
        "workspace_id": row["workspace_id"],
        "version": int(row["version"]),
        "name": row["name"],
        "workspace": workspace,
        "note": row["note"],
        "source": row["source"],
        "created_at": row["created_at"],
    }


def _chart_template_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["template_json"] or "{}")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "template": payload,
        "payload": payload.get("payload") if isinstance(payload, dict) else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _chart_drawing_snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "workspace_id": row["workspace_id"],
        "store_key": row["store_key"],
        "drawing": json.loads(row["drawing_json"] or "{}"),
        "source": row["source"],
        "version": int(row["version"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _chart_alert_rule_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "store_key": row["store_key"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "name": row["name"],
        "condition": json.loads(row["condition_json"] or "{}"),
        "message": row["message"],
        "frequency": row["frequency"],
        "enabled": bool(row["enabled"]),
        "last_state": json.loads(row["last_state_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _chart_alert_run_row(row: sqlite3.Row) -> dict[str, Any]:
    result = json.loads(row["result_json"] or "{}")
    return {
        "id": int(row["id"]),
        "workspace_id": row["workspace_id"],
        "status": row["status"],
        "rule_count": int(row["rule_count"]),
        "event_count": int(row["event_count"]),
        "missing_bars": json.loads(row["missing_bars_json"] or "[]"),
        "notification": json.loads(row["notification_json"] or "{}"),
        "result": result,
        "created_at": row["created_at"],
    }


def _next_chart_workspace_version(workspace_id: str) -> tuple[int, str]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM chart_workspace_versions
            WHERE workspace_id = ?
            """,
            (workspace_id,),
        ).fetchone()
    return int(row["version"] or 0) + 1, _now()


def save_chart_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    payload = chart_workspace.normalize_workspace(workspace)
    workspace_id = str(payload.get("id") or "").strip()
    if not workspace_id or workspace_id == "default":
        workspace_id = chart_workspace.workspace_id(payload)
    payload["id"] = workspace_id
    return save_chart_workspace_version(workspace_id, payload, source="create")


def save_chart_workspace_version(
    workspace_id: str,
    workspace: dict[str, Any],
    *,
    note: str = "",
    source: str = "ui",
) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    payload = chart_workspace.normalize_workspace({**(workspace or {}), "id": workspace_id})
    errors, _warnings = chart_workspace.validate_workspace(payload)
    if errors:
        raise ValueError("; ".join(errors))
    payload["id"] = workspace_id
    version, now = _next_chart_workspace_version(workspace_id)
    with connect() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO chart_workspace_versions
                    (workspace_id, version, name, workspace_json, note, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    version,
                    str(payload.get("name") or "Workspace"),
                    _json(payload),
                    str(note or ""),
                    str(source or "ui"),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO chart_workspaces
                    (id, name, layout, workspace_json, current_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    layout=excluded.layout,
                    workspace_json=excluded.workspace_json,
                    current_version=excluded.current_version,
                    updated_at=excluded.updated_at
                """,
                (
                    workspace_id,
                    str(payload.get("name") or "Workspace"),
                    str(payload.get("layout") or "1"),
                    _json(payload),
                    version,
                    now,
                    now,
                ),
            )
    return get_chart_workspace(workspace_id) or {
        "id": workspace_id,
        "version": version,
        "workspace": payload,
    }


def list_chart_workspaces(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chart_workspaces ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_chart_workspace_row(row) for row in rows]


def get_chart_workspace(
    workspace_id: str,
    version: int | None = None,
) -> dict[str, Any] | None:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return None
    with connect() as conn:
        if version is None:
            row = conn.execute(
                "SELECT * FROM chart_workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            return _chart_workspace_row(row) if row else None
        row = conn.execute(
            """
            SELECT row_id, workspace_id, version, name, workspace_json, note, source, created_at
            FROM chart_workspace_versions
            WHERE workspace_id = ? AND version = ?
            """,
            (workspace_id, int(version)),
        ).fetchone()
    return _chart_workspace_version_row(row) if row else None


def list_chart_workspace_versions(
    workspace_id: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return []
    limit = max(1, min(int(limit or 30), 200))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT row_id, workspace_id, version, name, workspace_json, note, source, created_at
            FROM chart_workspace_versions
            WHERE workspace_id = ?
            ORDER BY version DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return [_chart_workspace_version_row(row) for row in rows]


def save_chart_drawing_snapshot(
    workspace_id: str,
    store_key: str,
    drawing: dict[str, Any],
    *,
    source: str = "browser",
) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    store_key = str(store_key or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not store_key:
        raise ValueError("store_key is required")
    if not isinstance(drawing, dict):
        raise ValueError("drawing must be an object")
    drawing_json = _json(drawing)
    if len(drawing_json.encode("utf-8")) > 1_000_000:
        raise ValueError("drawing snapshot is too large")
    now = _now()
    with connect() as conn:
        with conn:
            row = conn.execute(
                """
                SELECT version, created_at
                FROM chart_drawing_snapshots
                WHERE workspace_id = ? AND store_key = ?
                """,
                (workspace_id, store_key),
            ).fetchone()
            version = int(row["version"] or 0) + 1 if row else 1
            created_at = row["created_at"] if row else now
            conn.execute(
                """
                INSERT INTO chart_drawing_snapshots
                    (workspace_id, store_key, drawing_json, source, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, store_key) DO UPDATE SET
                    drawing_json=excluded.drawing_json,
                    source=excluded.source,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    workspace_id,
                    store_key,
                    drawing_json,
                    str(source or "browser"),
                    version,
                    created_at,
                    now,
                ),
            )
    snapshot = get_chart_drawing_snapshot(workspace_id, store_key)
    if snapshot is None:
        raise RuntimeError("chart drawing snapshot was not saved")
    return snapshot


def get_chart_drawing_snapshot(workspace_id: str, store_key: str) -> dict[str, Any] | None:
    workspace_id = str(workspace_id or "").strip()
    store_key = str(store_key or "").strip()
    if not workspace_id or not store_key:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT workspace_id, store_key, drawing_json, source, version, created_at, updated_at
            FROM chart_drawing_snapshots
            WHERE workspace_id = ? AND store_key = ?
            """,
            (workspace_id, store_key),
        ).fetchone()
    return _chart_drawing_snapshot_row(row) if row else None


def list_chart_drawing_snapshots(workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return []
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT workspace_id, store_key, drawing_json, source, version, created_at, updated_at
            FROM chart_drawing_snapshots
            WHERE workspace_id = ?
            ORDER BY updated_at DESC, store_key ASC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return [_chart_drawing_snapshot_row(row) for row in rows]


def save_chart_alert_rule(rule: dict[str, Any]) -> dict[str, Any]:
    payload = dict(rule or {})
    workspace_id = str(payload.get("workspace_id") or "").strip()
    store_key = str(payload.get("store_key") or "").strip()
    symbol = str(payload.get("symbol") or "").upper().strip()
    timeframe = str(payload.get("timeframe") or "1d").strip().lower() or "1d"
    condition = payload.get("condition") if isinstance(payload.get("condition"), dict) else {}
    name = str(payload.get("name") or f"{symbol} alert").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not store_key:
        raise ValueError("store_key is required")
    if not symbol:
        raise ValueError("symbol is required")
    _validate_chart_alert_condition(condition)

    now = _now()
    rule_id = str(payload.get("id") or "").strip()
    if not rule_id:
        raw = _json({**payload, "created_at": now})
        rule_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    with connect() as conn:
        row = conn.execute("SELECT created_at FROM chart_alert_rules WHERE id = ?", (rule_id,)).fetchone()
        created_at = row["created_at"] if row else now
        with conn:
            conn.execute(
                """
                INSERT INTO chart_alert_rules
                    (id, workspace_id, store_key, symbol, timeframe, name, condition_json,
                     message, frequency, enabled, last_state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id=excluded.workspace_id,
                    store_key=excluded.store_key,
                    symbol=excluded.symbol,
                    timeframe=excluded.timeframe,
                    name=excluded.name,
                    condition_json=excluded.condition_json,
                    message=excluded.message,
                    frequency=excluded.frequency,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    rule_id,
                    workspace_id,
                    store_key,
                    symbol,
                    timeframe,
                    name,
                    _json(condition),
                    str(payload.get("message") or ""),
                    str(payload.get("frequency") or "once").strip().lower() or "once",
                    1 if bool(payload.get("enabled", True)) else 0,
                    _json(payload.get("last_state") if isinstance(payload.get("last_state"), dict) else {}),
                    created_at,
                    now,
                ),
            )
    saved = get_chart_alert_rule(rule_id)
    if saved is None:
        raise RuntimeError("chart alert rule was not saved")
    return saved


def _validate_chart_alert_condition(condition: dict[str, Any]) -> None:
    if not isinstance(condition, dict) or not condition:
        raise ValueError("condition object is required")
    leaves = condition.get("all") if isinstance(condition.get("all"), list) else [condition]
    if not leaves:
        raise ValueError("condition.all must not be empty")
    for item in leaves:
        if not isinstance(item, dict):
            raise ValueError("condition entries must be objects")
        ctype = str(item.get("type") or "price").strip().lower()
        operator = str(item.get("operator") or "").strip().lower()
        if operator not in {"crossing", "crossing_up", "crossing_down", "greater_than", "less_than"}:
            raise ValueError(f"unsupported alert operator: {operator}")
        if ctype in {"price", "indicator"}:
            if ctype == "indicator" and not str(item.get("field") or "").strip():
                raise ValueError("indicator condition.field is required")
            try:
                float(item.get("value"))
            except (TypeError, ValueError):
                raise ValueError("condition.value must be numeric") from None
        elif ctype == "drawing_line":
            for key in ("x0", "y0", "x1", "y1"):
                if item.get(key) in (None, ""):
                    raise ValueError(f"drawing_line condition.{key} is required")
        else:
            raise ValueError(f"unsupported alert condition type: {ctype}")


def get_chart_alert_rule(rule_id: str) -> dict[str, Any] | None:
    rule_id = str(rule_id or "").strip()
    if not rule_id:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, workspace_id, store_key, symbol, timeframe, name, condition_json,
                   message, frequency, enabled, last_state_json, created_at, updated_at
            FROM chart_alert_rules
            WHERE id = ?
            """,
            (rule_id,),
        ).fetchone()
    return _chart_alert_rule_row(row) if row else None


def list_chart_alert_rules(
    *,
    workspace_id: str | None = None,
    symbol: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id).strip())
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol).upper().strip())
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, workspace_id, store_key, symbol, timeframe, name, condition_json,
                   message, frequency, enabled, last_state_json, created_at, updated_at
            FROM chart_alert_rules
            {where}
            ORDER BY updated_at DESC, name ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_chart_alert_rule_row(row) for row in rows]


def update_chart_alert_state(rule_id: str, state: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(rule_id or "").strip()
    if not rule_id:
        raise ValueError("rule_id is required")
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    now = _now()
    with connect() as conn:
        with conn:
            conn.execute(
                """
                UPDATE chart_alert_rules
                SET last_state_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_json(state), now, rule_id),
            )
    saved = get_chart_alert_rule(rule_id)
    if saved is None:
        raise ValueError(f"chart alert rule not found: {rule_id}")
    return saved


def save_chart_alert_run(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    workspace_id = str(data.get("workspace_id") or "").strip()
    status = str(data.get("status") or "ok").strip().lower() or "ok"
    missing_bars = data.get("missing_bars") if isinstance(data.get("missing_bars"), list) else []
    notification = data.get("notification") if isinstance(data.get("notification"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else dict(data)
    rule_count = int(data.get("rule_count") or 0)
    event_count = int(data.get("event_count") or 0)
    now = str(data.get("created_at") or _now())
    with connect() as conn:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO chart_alert_runs
                    (workspace_id, status, rule_count, event_count, missing_bars_json,
                     notification_json, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    status,
                    rule_count,
                    event_count,
                    _json(missing_bars),
                    _json(notification),
                    _json(result),
                    now,
                ),
            )
            run_id = int(cur.lastrowid)
    rows = list_chart_alert_runs(workspace_id=workspace_id or None, limit=1)
    return next((row for row in rows if row["id"] == run_id), {
        "id": run_id,
        "workspace_id": workspace_id,
        "status": status,
        "rule_count": rule_count,
        "event_count": event_count,
        "missing_bars": missing_bars,
        "notification": notification,
        "result": result,
        "created_at": now,
    })


def list_chart_alert_runs(*, workspace_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id).strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit = max(1, min(int(limit or 20), 200))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, workspace_id, status, rule_count, event_count, missing_bars_json,
                   notification_json, result_json, created_at
            FROM chart_alert_runs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_chart_alert_run_row(row) for row in rows]


def save_chart_template(template: dict[str, Any]) -> dict[str, Any]:
    payload = dict(template or {})
    template_id = str(payload.get("id") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    name = str(payload.get("name") or template_id or "Template").strip()
    if not template_id:
        raw = _json(payload)
        template_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        payload["id"] = template_id
    if kind not in {"style", "indicators", "series"}:
        raise ValueError(f"unsupported chart template kind: {kind}")
    payload["kind"] = kind
    payload["name"] = name
    now = _now()
    with connect() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO chart_templates
                    (id, kind, name, template_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    name=excluded.name,
                    template_json=excluded.template_json,
                    updated_at=excluded.updated_at
                """,
                (template_id, kind, name, _json(payload), now, now),
            )
    rows = list_chart_templates(kind=kind, limit=200)
    return next((row for row in rows if row["id"] == template_id), {
        "id": template_id,
        "kind": kind,
        "name": name,
        "template": payload,
        "payload": payload.get("payload"),
        "created_at": now,
        "updated_at": now,
    })


def list_chart_templates(
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with connect() as conn:
        if kind:
            rows = conn.execute(
                """
                SELECT * FROM chart_templates
                WHERE kind = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(kind), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chart_templates ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_chart_template_row(row) for row in rows]
