"""lib/world_memory.py — 월드 메모리: 시장 이슈 영구 축적 + 타임라인/체인 검색.

FinanceAgentGUI World Memory 엔진 이식 (스키마 `config/world-memory.schema.sql` ·
`scripts/world_memory_cli.py` — Copyright (c) 2026, devninjadev · BSD 3-Clause, 고지 유지).
이 포팅의 개작점: ① 임베딩(SentenceTransformer·torch 의존)은 제외하고 **SQLite FTS5
어휘 검색**으로 대체(무의존·서버 즉시 구동 — 시맨틱은 추후 업그레이드 가능) ② 수집원을
이 프로젝트 자산(뉴스 LLM 라벨·속보 감지)에 직결 ③ 표시·컨텍스트 전용.

목적: "무슨 일이 어디서 시작해 여기까지 왔는가"의 재료 — 이슈(entries)를 영구
append 하고 스토리 상태 체인(states: caused_by·supersedes)으로 흐름을 잇는다.
14일 프루닝되는 뉴스 캐시와 달리 이 저장소는 **영구**(불변 append-only 지향).

정직 규율: 산출물은 인과 *서술*의 재료이지 인과 *증명*이 아니다 — 판단 반영은
여전히 news 축 게이트 경유만. 이 저장소는 /ask·대시보드·메모리 패킷의 컨텍스트 전용.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DB_PATH = Path(os.getenv("WORLD_MEMORY_DB",
                         "~/.local/share/stock-report/shared-memory/world_issue_log.sqlite3")).expanduser()

# 스키마 — 원본 world-memory.schema.sql 의 entries/states 를 충실 이식(임베딩 테이블 제외)
_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS world_issue_entries (
  event_id TEXT PRIMARY KEY,
  as_of TEXT NOT NULL,
  issue_date TEXT NOT NULL,
  category TEXT NOT NULL,
  region TEXT NOT NULL,
  importance TEXT NOT NULL,
  entry_mode TEXT NOT NULL DEFAULT 'issue',
  dedupe_key TEXT NOT NULL DEFAULT '',
  logged_at TEXT NOT NULL,
  title TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_issue_entries_as_of
  ON world_issue_entries(as_of DESC);
CREATE INDEX IF NOT EXISTS idx_world_issue_entries_filters
  ON world_issue_entries(issue_date, category, region, importance);
CREATE INDEX IF NOT EXISTS idx_world_issue_entries_dedupe_key
  ON world_issue_entries(dedupe_key, issue_date DESC);
CREATE TABLE IF NOT EXISTS world_issue_states (
  state_id TEXT PRIMARY KEY,
  state_key TEXT NOT NULL,
  state_label TEXT NOT NULL,
  state_status TEXT NOT NULL,
  state_bias TEXT NOT NULL,
  net_effect TEXT NOT NULL,
  summary TEXT NOT NULL,
  rationale TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  caused_by_event_id TEXT,
  supersedes_state_id TEXT,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  confidence REAL NOT NULL,
  source_kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_issue_states_key_status
  ON world_issue_states(state_key, state_status, effective_from DESC);
"""
_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS world_issue_fts
  USING fts5(event_id UNINDEXED, title, body, tickers);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS)
    except sqlite3.OperationalError:            # FTS5 미지원 빌드 → LIKE 폴백 검색
        pass
    return conn


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _dedupe_key(title: str, issue_date: str) -> str:
    norm = re.sub(r"\s+", " ", str(title or "").strip().lower())[:160]
    return hashlib.sha256(f"{issue_date}|{norm}".encode()).hexdigest()[:16]


def log_issue(title: str, *, category: str = "기타", region: str = "GLOBAL",
              importance: str = "medium", issue_date: str | None = None,
              tickers: list[str] | None = None, body: str = "",
              source: str = "manual", payload: dict | None = None) -> str | None:
    """이슈 1건 영구 기록 (dedupe_key 로 같은 날 동일 제목 중복 차단). 반환 event_id|None(중복)."""
    issue_date = issue_date or datetime.now(KST).strftime("%Y-%m-%d")
    dk = _dedupe_key(title, issue_date)
    conn = _connect()
    try:
        dup = conn.execute("SELECT event_id FROM world_issue_entries WHERE dedupe_key=? LIMIT 1",
                           (dk,)).fetchone()
        if dup:
            return None
        eid = uuid.uuid4().hex[:16]
        pj = json.dumps({**(payload or {}), "tickers": tickers or [], "body": body[:4000],
                         "source": source}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO world_issue_entries (event_id, as_of, issue_date, category, region,"
            " importance, entry_mode, dedupe_key, logged_at, title, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (eid, _now_iso(), issue_date, category, region, importance, "issue", dk,
             _now_iso(), str(title)[:300], pj))
        try:
            conn.execute("INSERT INTO world_issue_fts (event_id, title, body, tickers)"
                         " VALUES (?,?,?,?)",
                         (eid, str(title)[:300], body[:4000], " ".join(tickers or [])))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        return eid
    finally:
        conn.close()


def link_state(state_key: str, summary: str, *, label: str = "", bias: str = "중립",
               source_event_id: str = "", caused_by_event_id: str | None = None,
               confidence: float = 0.5, source_kind: str = "deterministic") -> str:
    """스토리 상태 추가 — 같은 state_key 의 기존 active 상태를 자동 종료(supersede 체인)."""
    now = _now_iso()
    conn = _connect()
    try:
        prev = conn.execute(
            "SELECT state_id FROM world_issue_states WHERE state_key=? AND state_status='active'"
            " ORDER BY effective_from DESC LIMIT 1", (state_key,)).fetchone()
        sid = uuid.uuid4().hex[:16]
        if prev:
            conn.execute("UPDATE world_issue_states SET state_status='superseded',"
                         " effective_to=?, updated_at=? WHERE state_id=?", (now, now, prev[0]))
        conn.execute(
            "INSERT INTO world_issue_states (state_id, state_key, state_label, state_status,"
            " state_bias, net_effect, summary, rationale, source_event_id, caused_by_event_id,"
            " supersedes_state_id, effective_from, effective_to, confidence, source_kind,"
            " created_at, updated_at, payload_json)"
            " VALUES (?,?,?,'active',?,'',?,'',?,?,?,?,NULL,?,?,?,?,'{}')",
            (sid, state_key, label or state_key, bias, summary[:600], source_event_id,
             caused_by_event_id, prev[0] if prev else None, now, confidence, source_kind,
             now, now))
        conn.commit()
        return sid
    finally:
        conn.close()


# ── 검색 (bounded retrieval — FTS5 우선·LIKE 폴백) ───────────────────────────

def _rows_to_issues(rows) -> list[dict]:
    out = []
    for r in rows:
        payload = {}
        try:
            payload = json.loads(r[5] or "{}")
        except Exception:
            pass
        out.append({"event_id": r[0], "issue_date": r[1], "category": r[2],
                    "importance": r[3], "title": r[4],
                    "tickers": payload.get("tickers") or [],
                    "body": (payload.get("body") or "")[:4000], "source": payload.get("source")})
    return out


def timeline(query: str = "", *, days: int = 3650, limit: int = 8) -> list[dict]:
    """이슈 타임라인 (최신순·bounded). query: 티커/키워드 — 빈값이면 최근 이슈 전체."""
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _connect()
    try:
        base_cols = ("e.event_id, e.issue_date, e.category, e.importance, e.title, e.payload_json")
        q = str(query or "").strip()
        if q:
            try:                                 # FTS5 — 토큰 정리(따옴표 안전)
                fq = " OR ".join(f'"{t}"' for t in re.findall(r"[\w가-힣.]+", q)[:6]) or f'"{q}"'
                rows = conn.execute(
                    f"SELECT {base_cols} FROM world_issue_fts f"
                    " JOIN world_issue_entries e ON e.event_id = f.event_id"
                    " WHERE world_issue_fts MATCH ? AND e.issue_date >= ?"
                    " ORDER BY e.issue_date DESC LIMIT ?", (fq, since, limit)).fetchall()
            except sqlite3.OperationalError:     # FTS 미지원/문법 → LIKE 폴백
                like = f"%{q}%"
                rows = conn.execute(
                    f"SELECT {base_cols} FROM world_issue_entries e"
                    " WHERE (e.title LIKE ? OR e.payload_json LIKE ?) AND e.issue_date >= ?"
                    " ORDER BY e.issue_date DESC LIMIT ?", (like, like, since, limit)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {base_cols} FROM world_issue_entries e WHERE e.issue_date >= ?"
                " ORDER BY e.issue_date DESC LIMIT ?", (since, limit)).fetchall()
        return _rows_to_issues(rows)
    finally:
        conn.close()


def story_chain(state_key: str, limit: int = 10) -> list[dict]:
    """상태 체인 (오래된→최신 — '어디서 시작해 여기까지' 서술 재료)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT state_id, state_status, state_bias, summary, effective_from, effective_to,"
            " confidence FROM world_issue_states WHERE state_key=?"
            " ORDER BY effective_from ASC, rowid ASC LIMIT ?", (state_key, limit)).fetchall()
        return [{"state_id": r[0], "status": r[1], "bias": r[2], "summary": r[3],
                 "from": r[4], "to": r[5], "confidence": r[6]} for r in rows]
    finally:
        conn.close()


def timeline_text(query: str, *, limit: int = 6, days: int = 3650) -> str:
    """프롬프트 주입용 타임라인 요약 (없으면 빈 문자열 — 호출부 무영향)."""
    try:
        items = timeline(query, days=days, limit=limit)
    except Exception as e:
        logger.warning("월드 메모리 검색 실패(무시): %s", e)
        return ""
    if not items:
        return ""
    lines = [f"- {i['issue_date']} [{i['category']}·{i['importance']}] {i['title']}"
             + (f" ({','.join(i['tickers'][:3])})" if i["tickers"] else "")
             for i in reversed(items)]           # 오래된→최신 (체인 서술 순서)
    return "\n".join(lines)


def stats() -> dict:
    conn = _connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM world_issue_entries").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM world_issue_states").fetchone()[0]
        first = conn.execute("SELECT MIN(issue_date) FROM world_issue_entries").fetchone()[0]
        return {"issues": n, "states": s, "since": first, "db": str(DB_PATH)}
    finally:
        conn.close()


# ── 수집원 배선 (이 프로젝트 자산 → 이슈) ────────────────────────────────────

_IMPORTANCE_BY_STRENGTH = {5: "high", 4: "high", 3: "medium", 2: "medium", 1: "low"}


def ingest_from_labels(labels: list[dict] | None = None) -> int:
    """뉴스 LLM 구조화 라벨 → 이슈 적재 (dedupe 로 멱등 — 크론 후처리 훅). 반환 = 신규 수."""
    if labels is None:
        try:
            from providers.news_labels import load_labels
            labels = load_labels()
        except Exception:
            return 0
    added = 0
    for lb in labels or []:
        try:
            tickers = [str(t).upper() for t in (lb.get("tickers") or [])]
            region = "KR" if any(t.isdigit() for t in tickers) else ("US" if tickers else "GLOBAL")
            direction = int(lb.get("direction", 0) or 0)
            title = str(lb.get("title_head") or "").strip()
            if not title:
                continue
            eid = log_issue(
                title, category=str(lb.get("event_type") or "기타"), region=region,
                importance=_IMPORTANCE_BY_STRENGTH.get(int(lb.get("strength", 0) or 0), "low"),
                issue_date=str(lb.get("published_at") or "")[:10] or None,
                tickers=tickers, source="news_llm_label", body=str(lb.get("body") or ""),
                payload={"direction": direction, "strength": lb.get("strength")})
            if eid:
                added += 1
                for t in tickers[:3]:            # 티커별 스토리 상태 체인 (방향 있으면)
                    if direction:
                        link_state(f"ticker:{t}",
                                   f"{lb.get('event_type')} {'호재' if direction > 0 else '악재'}: {title[:80]}",
                                   label=t, bias="긍정" if direction > 0 else "부정",
                                   source_event_id=eid, confidence=0.4,
                                   source_kind="news_llm_label")
        except Exception as e:
            logger.warning("라벨 이슈 적재 실패(1건 무시): %s", e)
    return added


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--ingest", action="store_true", help="뉴스 LLM 라벨 → 이슈 적재")
    ap.add_argument("--search")
    ap.add_argument("--chain", help="state_key (예: ticker:NVDA)")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.init:
        _connect().close()
        print(f"초기화 완료: {DB_PATH}")
    if a.ingest:
        print(f"이슈 적재: {ingest_from_labels()}건")
    if a.search:
        for i in timeline(a.search, limit=12):
            print(f"{i['issue_date']} [{i['category']}·{i['importance']}] {i['title']} {i['tickers']}")
    if a.chain:
        for s in story_chain(a.chain):
            print(f"{s['from'][:10]} [{s['bias']}·{s['status']}] {s['summary']}")
    if a.stats:
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
