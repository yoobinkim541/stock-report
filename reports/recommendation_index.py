from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from reports.source_collector import DEFAULT_CACHE_DIR, _classify_event
from reports.source_identity import normalize_event_identity


DEFAULT_LIMIT = 500
MAX_LIMIT = 2_000
TRUST_PRIORS = {"A": 0.90, "B": 0.75, "C": 0.55, "D": 0.35}
DEFAULT_INDEX_PATH = Path.home() / "reports" / "ml-cache" / "recommendation_index.sqlite3"
TRUST_PRIOR_SQL = "CASE trust " + " ".join(
    f"WHEN '{trust}' THEN {prior:.2f}" for trust, prior in TRUST_PRIORS.items()
) + " ELSE 0.55 END"

_FEATURES_SQL = """
WITH eligible AS (
    SELECT ticker, event_at, source, source_family, event_kind, trust, body_available,
        CASE WHEN content_hash != '' THEN content_hash
             WHEN observation_id != '' THEN observation_id ELSE event_id END AS evidence_id
    FROM event_index {where}
), evidence AS (
    SELECT ticker, substr(event_at, 1, 10) AS day, evidence_id,
        MAX(CASE WHEN event_kind IN ('article', 'report', 'analysis', 'news') THEN 1 ELSE 0 END) AS article_count,
        MAX(CASE WHEN source_family = 'community' THEN 1 ELSE 0 END) AS community_count,
        MAX(""" + TRUST_PRIOR_SQL + """) AS trust_prior,
        MAX(body_available) AS body_available_count, MAX(event_at) AS latest_event_at
    FROM eligible GROUP BY ticker, substr(event_at, 1, 10), evidence_id
), sources AS (
    SELECT ticker, substr(event_at, 1, 10) AS day, COUNT(DISTINCT source) AS source_diversity
    FROM eligible GROUP BY ticker, substr(event_at, 1, 10)
)
SELECT evidence.ticker, evidence.day, COUNT(*) AS event_count,
    SUM(evidence.article_count) AS article_count, SUM(evidence.community_count) AS community_count,
    sources.source_diversity, ROUND(AVG(evidence.trust_prior), 2) AS trust_prior,
    SUM(evidence.body_available_count) AS body_available_count, MAX(evidence.latest_event_at) AS latest_event_at
FROM evidence JOIN sources ON sources.ticker = evidence.ticker AND sources.day = evidence.day
GROUP BY evidence.ticker, evidence.day, sources.source_diversity ORDER BY evidence.day ASC
"""


class RecommendationIndex:
    def __init__(self, db_path: Path | None = None, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.db_path = Path(db_path or DEFAULT_INDEX_PATH)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingest_checkpoint (
                    raw_path TEXT PRIMARY KEY,
                    inode INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    offset INTEGER NOT NULL,
                    line_number INTEGER NOT NULL,
                    errors INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_index (
                    event_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    ticker TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    published_at TEXT,
                    observed_at TEXT,
                    collected_at TEXT,
                    source TEXT NOT NULL,
                    source_family TEXT NOT NULL,
                    record_kind TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    trust TEXT NOT NULL,
                    body_available INTEGER NOT NULL,
                    raw_path TEXT NOT NULL,
                    raw_line INTEGER NOT NULL,
                    raw_offset INTEGER NOT NULL,
                    PRIMARY KEY (event_id, ticker)
                );
                CREATE INDEX IF NOT EXISTS event_index_ticker_at ON event_index (ticker, event_at DESC);
                CREATE INDEX IF NOT EXISTS event_index_family_at ON event_index (source_family, event_at DESC);
                CREATE INDEX IF NOT EXISTS event_index_source_at ON event_index (source, event_at DESC);
                CREATE TABLE IF NOT EXISTS ticker_day_features (
                    ticker TEXT NOT NULL,
                    day TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    article_count INTEGER NOT NULL,
                    community_count INTEGER NOT NULL,
                    source_diversity INTEGER NOT NULL,
                    trust_prior REAL NOT NULL,
                    body_available_count INTEGER NOT NULL,
                    latest_event_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, day)
                );
                """
            )
            self._ensure_columns(connection, "event_index", {
                "observation_id": "TEXT NOT NULL DEFAULT ''",
                "content_hash": "TEXT NOT NULL DEFAULT ''",
                "published_at": "TEXT",
                "observed_at": "TEXT",
                "collected_at": "TEXT",
            })
            connection.execute("CREATE INDEX IF NOT EXISTS event_index_content_hash ON event_index (content_hash)")

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def ingest(self, paths: Iterable[Path] | None = None) -> dict:
        self.initialize()
        files = self._event_files(paths)
        indexed = errors = fallback_count = 0
        with self._connect() as connection:
            for path in files:
                result = self._ingest_file(connection, path)
                indexed += result["indexed"]
                errors += result["errors"]
                fallback_count += result["fallback_count"]
            self._rebuild_features(connection)
        return {"files": len(files), "indexed": indexed, "errors": errors, "fallback_count": fallback_count}

    def _event_files(self, paths: Iterable[Path] | None) -> list[Path]:
        candidates = paths if paths is not None else self.cache_dir.glob("events-*.jsonl")
        return sorted(
            (Path(path) for path in candidates if Path(path).name.startswith("events-") and Path(path).suffix == ".jsonl" and not Path(path).name.endswith(".lock")),
            key=lambda path: str(path),
        )

    def _ingest_file(self, connection: sqlite3.Connection, path: Path) -> dict:
        path = path.resolve()
        stat = path.stat()
        raw_path = str(path)
        checkpoint = connection.execute(
            "SELECT inode, size, offset, line_number, errors FROM ingest_checkpoint WHERE raw_path = ?", (raw_path,)
        ).fetchone()
        reset = checkpoint is not None and (checkpoint["inode"] != stat.st_ino or stat.st_size < checkpoint["size"])
        if reset:
            connection.execute("DELETE FROM event_index WHERE raw_path = ?", (raw_path,))
            checkpoint = None
        offset = int(checkpoint["offset"]) if checkpoint else 0
        line_number = int(checkpoint["line_number"]) if checkpoint else 0
        total_errors = int(checkpoint["errors"]) if checkpoint else 0
        indexed = errors = fallback_count = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            while True:
                raw_offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    break
                line_number += 1
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(event, dict):
                        raise ValueError("event is not an object")
                    rows, used_fallback = self._index_rows(event, raw_path, line_number, raw_offset)
                    fallback_count += used_fallback
                    for row in rows:
                        before = connection.total_changes
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO event_index (
                                event_id, observation_id, content_hash, ticker, event_at,
                                published_at, observed_at, collected_at, source, source_family, record_kind,
                                event_kind, trust, body_available, raw_path, raw_line, raw_offset
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            row,
                        )
                        indexed += int(connection.total_changes > before)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    errors += 1
                    total_errors += 1
                offset = handle.tell()
                connection.execute(
                    """
                    INSERT INTO ingest_checkpoint (raw_path, inode, size, offset, line_number, errors, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(raw_path) DO UPDATE SET inode=excluded.inode, size=excluded.size,
                        offset=excluded.offset, line_number=excluded.line_number, errors=excluded.errors,
                        updated_at=excluded.updated_at
                    """,
                    (raw_path, stat.st_ino, stat.st_size, offset, line_number, total_errors, _utc_now()),
                )
        return {"indexed": indexed, "errors": errors, "fallback_count": fallback_count}

    def _index_rows(self, event: dict, raw_path: str, raw_line: int, raw_offset: int) -> tuple[list[tuple], int]:
        published_at, observed_at, collected_at, event_at = _timestamp_metadata(event)
        classified = _classify_event(event)
        normalized = normalize_event_identity(classified, event_at)
        classification = normalized.get("classification") or {}
        family = str(classification.get("source_family") or normalized.get("source_family") or "other")
        trust = str(classification.get("trust") or normalized.get("trust") or "C").upper()
        tickers = _tickers(normalized)
        body_available = int(bool(normalized.get("body") or normalized.get("body_raw") or normalized.get("body_excerpt")))
        content_hash = _content_hash(normalized) if normalized.get("record_kind") == "content" else ""
        observation_id = str(normalized["id"]) if normalized.get("record_kind") == "observation" else ""
        source = str(normalized.get("source") or "unknown")
        event_id = f"{source}:{normalized['id']}"
        return (
            [
                (
                    event_id, observation_id, content_hash, ticker, event_at.isoformat(timespec="seconds"),
                    published_at, observed_at, collected_at,
                    source, family, str(normalized.get("record_kind") or "content"),
                    str(classification.get("kind") or normalized.get("kind") or "event"), trust, body_available,
                    raw_path, raw_line, raw_offset,
                )
                for ticker in (tickers or [""])
            ],
            int(family == "other"),
        )

    def _rebuild_features(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM ticker_day_features")
        connection.execute(
            """
            INSERT INTO ticker_day_features (
                ticker, day, event_count, article_count, community_count, source_diversity,
                trust_prior, body_available_count, latest_event_at
            )
            """ + _FEATURES_SQL.replace("{where}", "WHERE ticker != ''")
        )

    def query_events(self, tickers: Iterable[str] | None = None, start: datetime | None = None,
                     end: datetime | None = None, source_families: Iterable[str] | None = None,
                     limit: int | None = None, sources: Iterable[str] | None = None) -> list[dict]:
        self.initialize()
        clauses, values = [], []
        _add_in_clause(clauses, values, "ticker", _normalized_strings(tickers))
        if start:
            clauses.append("event_at >= ?")
            values.append(_as_utc(start))
        if end:
            clauses.append("event_at <= ?")
            values.append(_as_utc(end))
        _add_in_clause(clauses, values, "source_family", _normalized_strings(source_families, upper=False))
        _add_in_clause(clauses, values, "source", _normalized_strings(sources, upper=False))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(_bounded_limit(limit))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id, observation_id, content_hash, ticker, event_at, published_at, observed_at, collected_at, "
                "source, source_family, record_kind, event_kind, trust, body_available, raw_path, raw_line, raw_offset"
                f" FROM event_index{where} ORDER BY event_at ASC, raw_path ASC, raw_offset ASC LIMIT ?", values
            ).fetchall()
        return [dict(row) for row in rows]

    def ticker_day_features(self, ticker: str, asof: datetime | None = None, window_days: int = 7) -> list[dict]:
        self.initialize()
        reference = _event_time({"collected_at": (asof or datetime.now(timezone.utc)).isoformat()})
        start_day = reference.date().fromordinal(reference.date().toordinal() - max(0, int(window_days) - 1)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                _FEATURES_SQL.replace("{where}", "WHERE ticker = ? AND event_at >= ? AND event_at <= ?"),
                (str(ticker).upper(), f"{start_day}T00:00:00+00:00", reference.isoformat(timespec="seconds")),
            ).fetchall()
        return [
            {**{key: row[key] for key in row.keys() if key != "latest_event_at"},
             "freshness_seconds": max(0, int((reference - _parse_time(row["latest_event_at"])).total_seconds()))}
            for row in rows
        ]

    def status(self) -> dict:
        self.initialize()
        with self._connect() as connection:
            event_count = connection.execute("SELECT COUNT(*) FROM event_index").fetchone()[0]
            latest = connection.execute("SELECT MAX(event_at) FROM event_index").fetchone()[0]
            checkpoints = connection.execute("SELECT COUNT(*) FROM ingest_checkpoint").fetchone()[0]
            errors = connection.execute("SELECT COALESCE(SUM(errors), 0) FROM ingest_checkpoint").fetchone()[0]
            fallback_count = connection.execute("SELECT COUNT(*) FROM event_index WHERE source_family = 'other'").fetchone()[0]
        lag = max(0, int((datetime.now(timezone.utc) - _parse_time(latest)).total_seconds())) if latest else None
        return {"event_count": event_count, "checkpoint_count": checkpoints, "lag_seconds": lag, "errors": errors, "fallback_count": fallback_count}


def _timestamp_metadata(event: dict) -> tuple[str | None, str | None, str | None, datetime]:
    timestamps = {key: _timestamp_text(event.get(key)) for key in ("published_at", "observed_at", "collected_at")}
    for key in ("published_at", "observed_at", "collected_at", "timestamp"):
        if event.get(key):
            return timestamps["published_at"], timestamps["observed_at"], timestamps["collected_at"], _parse_time(event[key])
    return timestamps["published_at"], timestamps["observed_at"], timestamps["collected_at"], datetime.now(timezone.utc)


def _event_time(event: dict) -> datetime:
    return _timestamp_metadata(event)[3]


def _timestamp_text(value: object) -> str | None:
    if not value:
        return None
    return _parse_time(value).isoformat(timespec="seconds")


def _content_hash(event: dict) -> str:
    body = event.get("body_raw") or event.get("body") or event.get("body_excerpt") or ""
    normalized = " ".join(str(body).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _tickers(event: dict) -> list[str]:
    values = event.get("tickers") or event.get("ticker") or event.get("symbol") or []
    if isinstance(values, str):
        values = [values]
    return [str(value).strip().upper() for value in values if str(value).strip()]


def _normalized_strings(values: Iterable[str] | None, *, upper: bool = True) -> list[str]:
    return [(str(value).strip().upper() if upper else str(value).strip().lower()) for value in values or [] if str(value).strip()]


def _add_in_clause(clauses: list[str], values: list, column: str, items: list[str]) -> None:
    if items:
        clauses.append(f"{column} IN ({', '.join('?' for _ in items)})")
        values.extend(items)


def _as_utc(value: datetime) -> str:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat(timespec="seconds")


def _bounded_limit(limit: int | None) -> int:
    return max(1, min(MAX_LIMIT, DEFAULT_LIMIT if limit is None else int(limit)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
