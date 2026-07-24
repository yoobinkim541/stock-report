from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import safe_io


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "finance-agent-gui.shared-memory.v1"
DEFAULT_PROVIDER = "codex-cli"


def shared_memory_dir() -> Path:
    """공유 메모리 단일 디렉토리 — lib/agent_memory(AGENT_MEMORY_DIR)와 같은 곳이 기본.

    codex(hermes)·Antigravity·텔레그램 /ask·AI 콘솔이 전부 한 기억을 읽고 쓴다.
    구 위치(레포 안 data/shared-memory)에 기록이 남아 있으면
    `uv run python -m agent_console.migrate_memory` 로 1회 이관.
    """
    override = os.getenv("AGENT_CONSOLE_SHARED_MEMORY_DIR") or os.getenv("AGENT_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "stock-report" / "shared-memory"


def legacy_shared_memory_dir() -> Path:
    """통합 전 콘솔 전용 위치 (마이그레이션 소스)."""
    return PROJECT_ROOT / "data" / "shared-memory"


def _paths() -> dict[str, Path]:
    root = shared_memory_dir()
    return {
        "directory": root,
        "events": root / "events.jsonl",
        "index": root / "index.json",
        "summary": root / "memory_summary.md",
        "user_notebook": root / "user_memory_notebook.md",
        "user_state": root / "user_memory_state.json",
        "external_briefing": root / "external_memory_briefing.md",
        "external_state": root / "external_memory_state.json",
        "schema": PROJECT_ROOT / "config" / "shared-memory.schema.json",
        "docs": PROJECT_ROOT / "docs" / "shared-agent-memory.md",
    }


def ensure_store() -> None:
    paths = _paths()
    paths["directory"].mkdir(parents=True, exist_ok=True)
    for key in ("events", "index", "summary", "user_notebook", "external_briefing"):
        paths[key].touch(exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value, limit: int = 1800) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = _redact_sensitive(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _redact_sensitive(text: str) -> str:
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", text)
    text = re.sub(r"/home/[A-Za-z0-9._/@+-][^\s,)]*", "<local-path>", text)
    text = re.sub(r"/Users/[A-Za-z0-9._/@+-][^\s,)]*", "<local-path>", text)
    return text


def _record_id(record: dict) -> str:
    key = "|".join(
        [
            str(record.get("createdAt") or ""),
            str(record.get("title") or ""),
            str(record.get("summary") or ""),
            str((record.get("source") or {}).get("surface") or ""),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _normalize_source(source: dict | None, *, screen: str = "", provider: str = DEFAULT_PROVIDER) -> dict:
    source = dict(source or {})
    source.setdefault("app", "FinanceAgentGUI")
    source.setdefault("surface", screen or source.get("screen") or "sidebar-chat")
    source.setdefault("screen", screen or source.get("screen") or source.get("surface") or "agent")
    source.setdefault("provider", provider or DEFAULT_PROVIDER)
    source.setdefault("providerLabel", "Codex CLI" if source.get("provider") == "codex-cli" else source.get("provider"))
    source.setdefault("writer", source.get("provider") or provider or DEFAULT_PROVIDER)
    return source


def normalize_record(payload: dict) -> dict:
    created = _clean_text(payload.get("createdAt") or _now(), 80)
    screen = _clean_text(payload.get("screen") or (payload.get("source") or {}).get("screen") or "", 80)
    provider = _clean_text(
        payload.get("provider") or (payload.get("source") or {}).get("provider") or DEFAULT_PROVIDER,
        80,
    )
    record = dict(payload)
    record["schemaVersion"] = SCHEMA_VERSION
    record["createdAt"] = created
    record["updatedAt"] = _clean_text(payload.get("updatedAt") or created, 80)
    record["visibility"] = "local-only"
    record["title"] = _clean_text(payload.get("title") or "공유 메모리", 120)
    record["summary"] = _clean_text(payload.get("summary") or "", 1800)
    record["decisions"] = [_clean_text(x, 300) for x in payload.get("decisions") or []][:12]
    record["openQuestions"] = [_clean_text(x, 300) for x in payload.get("openQuestions") or []][:12]
    record["tags"] = [_clean_text(x, 60).lower() for x in payload.get("tags") or []][:20]
    record["artifacts"] = [_clean_text(x, 180) for x in payload.get("artifacts") or []][:20]
    record["messages"] = [
        {
            "role": _clean_text(msg.get("role"), 32),
            "text": _clean_text(msg.get("text"), 2200),
            "createdAt": _clean_text(msg.get("createdAt") or created, 80),
        }
        for msg in payload.get("messages") or []
        if isinstance(msg, dict) and msg.get("text")
    ][:8]
    record["source"] = _normalize_source(payload.get("source"), screen=screen, provider=provider)
    if record.get("contextPacket") is not None and not isinstance(record.get("contextPacket"), dict):
        record["contextPacket"] = None
    record["id"] = _clean_text(payload.get("id") or _record_id(record), 80)
    return record


def append_record(payload: dict) -> dict:
    ensure_store()
    record = normalize_record(payload)
    with _events_lock():
        with _paths()["events"].open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        _write_index_locked()
    refresh_context_memory_summary()   # 락 밖 — 다른 파일을 쓰고 오래 걸린다
    return record


def append_chat_exchange(question: str, answer: str, surface: str = "market",
                         provider: str = DEFAULT_PROVIDER) -> dict | None:
    if os.getenv("AGENT_CONSOLE_SHARED_MEMORY_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return None
    question = _clean_text(question, 500)
    answer = _clean_text(answer, 1800)
    if not question or not answer:
        return None
    title = question[:88] or "AI 콘솔 대화"
    record = append_record(
        {
            "provider": provider,
            "screen": surface,
            "title": title,
            "summary": answer,
            "tags": _guess_tags(question + "\n" + answer, surface),
            "messages": [
                {"role": "user", "text": question},
                {"role": "assistant", "text": answer},
            ],
            "source": {
                "surface": "sidebar-chat",
                "provider": provider,
                "writer": provider,
                "screen": surface,
            },
        }
    )
    # 노트북 기록은 lib/agent_memory 포맷(### 날짜 헤딩·일별 롤업 파서 호환)이 단일 진실원.
    # lib 사용 불가 시에만 콘솔 자체 포맷 폴백 — 두 포맷이 섞이는 것 방지.
    if not _record_chat_via_lib(question, answer, surface):
        _append_user_notebook(record, question, answer)
    refresh_context_memory_summary()
    return record


def _record_chat_via_lib(question: str, answer: str, surface: str) -> bool:
    try:
        from lib import agent_memory as _lib_mem
        if not _lib_mem.enabled():
            return False
        _lib_mem.record_chat(question, answer, source=f"console:{surface}")
        return True
    except Exception:
        return False


def _guess_tags(text: str, surface: str) -> list[str]:
    q = text.lower()
    tags = {str(surface or "agent").lower(), "chat"}
    mapping = {
        "market": ("시장", "증시", "금리", "유가", "달러", "vix", "risk"),
        "portfolio": ("포트폴리오", "비중", "보유", "allocation", "mdd"),
        "trading": ("단기", "트레이딩", "매수", "매도", "진입", "청산", "손절"),
        "memory": ("메모리", "기억", "컨텍스트", "context"),
        "crypto": ("btc", "eth", "sol", "crypto", "코인"),
        "code": ("코드", "서버", "커밋", "푸시", "배포"),
    }
    for tag, words in mapping.items():
        if any(word in q for word in words):
            tags.add(tag)
    return sorted(tags)


def _append_user_notebook(record: dict, question: str, answer: str) -> None:
    paths = _paths()
    line = "\n".join(
        [
            f"## {record.get('createdAt')} · {record.get('source', {}).get('screen', 'agent')}",
            f"- 질문: {question}",
            f"- 답변: {_clean_text(answer, 900)}",
            "",
        ]
    )
    with paths["user_notebook"].open("a", encoding="utf-8") as f:
        f.write(line)


def _events_lock():
    """events.jsonl 사이드카 락.

    lib/agent_memory 도 같은 경로로 이 락을 잡으므로 두 모듈의 쓰기가 직렬화된다.
    주의: flock 은 재진입 불가 — 이 블록 안에서 다시 _events_lock() 을 호출하면
    LockTimeout 이 난다. 내부 호출은 _write_index_locked() 처럼 락을 잡지 않는
    버전을 쓴다.
    """
    return safe_io.file_write_lock(str(_paths()["events"]), timeout=30.0)


def _write_jsonl_locked(rows: list[dict]) -> None:
    """전체 재작성 — 락 보유 중에만 호출."""
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    safe_io.atomic_write_text(str(_paths()["events"]), text)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def list_records(limit: int = 50, offset: int = 0) -> list[dict]:
    ensure_store()
    limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))
    rows = _read_jsonl(_paths()["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    return rows[offset: offset + limit]


def all_records() -> list[dict]:
    """전체 레코드를 createdAt 내림차순으로 반환한다. 창(window) 없음.

    list_records() 는 limit/offset 페이지네이션 계약이라 최대 100 으로 클램프한다.
    위키 같은 지식층은 '전수'가 필요하므로 이 함수를 쓴다. _read_jsonl 이 어차피
    파일 전체를 읽으므로 추가 I/O 비용은 없다.
    """
    ensure_store()
    rows = _read_jsonl(_paths()["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    return rows


def delete_record(record_id: str) -> bool:
    ensure_store()
    record_id = str(record_id or "").strip()
    if not record_id:
        return False
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        kept = [row for row in rows if row.get("id") != record_id]
        if len(kept) == len(rows):
            return False
        _write_jsonl_locked(kept)
        _write_index_locked()
    refresh_context_memory_summary()
    return True


def upsert_record(record: dict) -> dict:
    """id 기준 치환-또는-추가를 한 번의 원자적 재작성으로 수행한다.

    delete_record + append_record 조합은 그 사이에 죽으면 레코드가 사라지는
    창이 있었다. 이 함수는 그 창을 없앤다.
    """
    ensure_store()
    normalized = normalize_record(record)
    record_id = str(normalized.get("id") or "").strip()
    if not record_id:
        return append_record(record)
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        kept = [row for row in rows if row.get("id") != record_id]
        kept.append(normalized)
        _write_jsonl_locked(kept)
        _write_index_locked()
    refresh_context_memory_summary()
    return normalized


def batch_upsert_delete(*, upserts: list[dict], deletes: list[str]) -> dict:
    """원자적 배치: 모든 upsert + delete를 한 번의 파일 재작성으로 처리한다.

    merge/split처럼 여러 upsert_record/delete_record 호출을 순차로 하면 그 사이에
    죽었을 때 부분 적용된 상태가 남는다. 이 함수는 하나의 락 구간에서 전부 반영한다.
    """
    ensure_store()
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        delete_set = {str(d) for d in (deletes or []) if d}
        rows = [row for row in rows if row.get("id") not in delete_set]
        upsert_ids = {str(r["id"]) for r in (upserts or []) if r.get("id")}
        rows = [row for row in rows if row.get("id") not in upsert_ids]
        rows.extend(normalize_record(r) for r in (upserts or []))
        _write_jsonl_locked(rows)
        _write_index_locked()
    refresh_context_memory_summary()
    return {"ok": True, "upserted": len(upserts or []), "deleted": len(deletes or [])}


def _write_index_locked() -> None:
    """index.json 갱신 — 락 보유 중에만 호출.

    lib/agent_memory 가 같은 파일에 latestAt/latestTitle/count 를 쓰므로,
    기존 내용을 읽어 자기 키만 덮어쓰고 상대 키는 보존한다.
    """
    paths = _paths()
    rows = _read_jsonl(paths["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    latest = rows[:200]
    try:
        existing = json.loads(paths["index"].read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}
    existing.update({
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": _now(),
        "recordCount": len(rows),
        "latestRecordAt": latest[0].get("createdAt") if latest else "",
        "records": latest,
    })
    safe_io.atomic_write_json(str(paths["index"]), existing)


def query_shared_memories(query: str = "", screen: str = "", provider: str = DEFAULT_PROVIDER,
                          limit: int = 6) -> list[dict]:
    rows = list_records(limit=100)
    query_tokens = _tokens(query)
    screen = str(screen or "").lower()
    provider = str(provider or "").lower()
    scored = []
    for idx, row in enumerate(rows):
        haystack = " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                " ".join(row.get("tags") or []),
                " ".join(row.get("decisions") or []),
                " ".join(str((msg or {}).get("text") or "") for msg in row.get("messages") or []),
            ]
        ).lower()
        score = 0
        for token in query_tokens:
            if token in haystack:
                score += 3 if len(token) > 2 else 1
        source = row.get("source") or {}
        if screen and screen == str(source.get("screen") or source.get("surface") or "").lower():
            score += 2
        if provider and provider == str(source.get("provider") or "").lower():
            score += 1
        scored.append((score, -idx, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for score, _, row in scored if score > 0]
    if not selected:
        selected = rows
    return selected[: max(1, min(int(limit or 6), 20))]


def _tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    return {
        token
        for token in re.findall(r"[0-9a-zA-Z가-힣_.$+-]{2,}", text)
        if token not in {"그리고", "그러면", "어떻게", "지금", "the", "and", "for"}
    }


def sync_external_layer_from_pack(pack: dict) -> None:
    if os.getenv("AGENT_CONSOLE_SHARED_MEMORY_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return
    ensure_store()
    reports = pack.get("reports") or []
    events = (pack.get("sources") or {}).get("events") or []
    lines = [
        "# External Memory Layer",
        "",
        "FinanceAgentGUI shared-memory 계약의 외부 메모리 레이어입니다.",
        "현재 stock-report의 최신 리포트, World Memory, 뉴스 피드 요약을 기준으로 생성됩니다.",
        "",
    ]
    if reports:
        latest = reports[0]
        lines.extend(
            [
                "## Latest Report",
                f"- 파일: {latest.get('name', '')}",
                f"- 시각: {latest.get('mtime', '')}",
                f"- 요약: {_strip_world_memory_suggestions(latest.get('summary') or latest.get('title') or '')}",
                "",
            ]
        )
    if events:
        lines.append("## Current Feed Briefing")
        for item in events[:12]:
            title = _clean_text(item.get("title") or item.get("summary") or "", 220)
            if title:
                lines.append(f"- {item.get('source', 'source')}: {title}")
        lines.append("")
    if not reports and not events:
        lines.append("아직 외부 메모리로 요약할 최신 리포트/피드가 없습니다.")
    path = _paths()["external_briefing"]
    next_text = "\n".join(lines).strip() + "\n"
    try:
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except Exception:
        current = ""
    if current != next_text:
        path.write_text(next_text, encoding="utf-8")
        _paths()["external_state"].write_text(
            json.dumps(
                {
                    "updatedAt": _now(),
                    "source": "stock-report.context_pack",
                    "eventsConsidered": len(events),
                    "latestReport": reports[0].get("name") if reports else "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        refresh_context_memory_summary()


def _strip_world_memory_suggestions(text: str) -> str:
    text = _clean_text(text, 1600)
    marker = "월드 메모리 변경 제안"
    if marker in text:
        text = text.split(marker, 1)[0].strip()
    return text


def refresh_context_memory_summary() -> str:
    """memory_summary.md 단일 writer = lib/agent_memory (사용자+외부 2계층 패킷).

    통합 디렉토리에서 콘솔이 자체 포맷으로 덮어쓰면 /ask·codex·agy 가 읽는 패킷이
    퇴화하므로 lib 에 위임. lib 사용 불가 시에만 콘솔 자체 빌더 폴백.
    """
    try:
        from lib import agent_memory as _lib_mem
        if _lib_mem.enabled():
            return _lib_mem.refresh_memory_summary(force=True)
    except Exception:
        pass
    return _legacy_refresh_summary()


def _legacy_refresh_summary() -> str:
    ensure_store()
    paths = _paths()
    user_layer = _read_bounded(paths["user_notebook"], 4500)
    external_layer = _read_bounded(paths["external_briefing"], 4500)
    recent = list_records(limit=8)
    lines = [
        "# Shared Local Memory",
        "",
        "이 내용은 FinanceAgentGUI의 local-only shared-memory 계약을 따르는 참고 컨텍스트다.",
        "현재 사용자 요청, 화면 Context Packet, 승인 경계가 항상 이 메모리보다 우선한다.",
        "",
        "## User Memory Layer",
        user_layer or "아직 사용자 메모리 레이어가 비어 있습니다.",
        "",
        "## External Memory Layer",
        external_layer or "아직 외부 메모리 레이어가 비어 있습니다.",
    ]
    if recent:
        lines.extend(["", "## Recent Shared Records"])
        for row in recent:
            tags = ", ".join(row.get("tags") or [])
            lines.append(f"- {row.get('createdAt', '')} · {row.get('title', '')} · {tags}")
    summary = "\n".join(lines).strip() + "\n"
    paths["summary"].write_text(summary, encoding="utf-8")
    return summary


def _read_bounded(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()


def build_context_packet(payload: dict | None = None) -> dict:
    payload = payload or {}
    ensure_store()
    context_memory_summary = refresh_context_memory_summary()
    query = _clean_text(payload.get("query") or payload.get("prompt") or payload.get("userIntent") or "", 1200)
    screen = _clean_text(payload.get("screen") or (payload.get("contextPacket") or {}).get("screen") or "", 80)
    provider = _clean_text(payload.get("provider") or (payload.get("contextPacket") or {}).get("provider")
                           or DEFAULT_PROVIDER, 80)
    memories = query_shared_memories(query=query, screen=screen, provider=provider, limit=payload.get("limit") or 6)
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now(),
        "query": query,
        "screen": screen,
        "provider": provider,
        "contextMemorySummary": context_memory_summary,
        "memories": memories,
    }


def build_context_section(payload: dict | None = None) -> str:
    payload = payload or {}
    if payload.get("includeSharedMemory") is False:
        return ""
    packet = build_context_packet(payload)
    summary_section = ""
    if packet.get("contextMemorySummary"):
        summary_section = "\n\n".join(
            [
                "[컨텍스트 메모리]",
                (
                    "아래 내용은 FinanceAgentGUI의 local-only memory_summary.md에서 온 사용자 메모리 "
                    "레이어와 외부 메모리 레이어다. 현재 사용자 요청, 화면 Context Packet, "
                    "승인 경계가 항상 우선한다."
                ),
                packet["contextMemorySummary"],
            ]
        )
    memories = packet.get("memories") or []
    if not memories:
        return summary_section
    items = []
    for idx, record in enumerate(memories, start=1):
        source = (record.get("source") or {}).get("providerLabel") or (record.get("source") or {}).get("provider")
        decisions = record.get("decisions") or []
        questions = record.get("openQuestions") or []
        tags = record.get("tags") or []
        lines = [
            f"{idx}. {record.get('title', '공유 메모리')} ({source or 'agent'}, {record.get('createdAt', '')})",
            f"요약: {record.get('summary', '')}" if record.get("summary") else "",
            f"결정: {' / '.join(decisions[:4])}" if decisions else "",
            f"남은 질문: {' / '.join(questions[:3])}" if questions else "",
            f"태그: {', '.join(tags[:8])}" if tags else "",
        ]
        items.append("\n".join(x for x in lines if x))
    return "\n\n".join(
        x
        for x in [
            summary_section,
            "[공유 작업 메모리]",
            (
                "아래 항목은 FinanceAgentGUI의 로컬 공유 메모리에서 검색된 참고 맥락이다. "
                "현재 사용자 요청, 화면 컨텍스트, 명시적 지시가 이 메모리보다 우선한다."
            ),
            *items,
        ]
        if x
    )


def status(limit: int = 8, offset: int = 0) -> dict:
    ensure_store()
    rows = _read_jsonl(_paths()["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    latest = rows[0].get("createdAt") if rows else ""
    paths = _paths()
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "recordCount": len(rows),
        "latestRecordAt": latest,
        "records": list_records(limit=limit, offset=offset),
        "contextMemory": {
            "marketSummary": {
                "status": "ready" if paths["external_briefing"].read_text(encoding="utf-8", errors="replace").strip()
                else "empty",
                "text": _read_bounded(paths["external_briefing"], 1600),
                "updatedAt": latest,
                "alertLevel": "none",
                "severityKo": "",
                "shouldCreateReport": False,
                "pushSummary": "",
            }
        },
        "paths": {
            "directory": str(paths["directory"]),
            "events": str(paths["events"]),
            "index": str(paths["index"]),
            "memorySummary": str(paths["summary"]),
            "userNotebook": str(paths["user_notebook"]),
            "externalBriefing": str(paths["external_briefing"]),
            "schema": "config/shared-memory.schema.json",
            "docs": "docs/shared-agent-memory.md",
        },
        "clients": [
            {"id": "codex-cli", "label": "Codex CLI", "access": "read/write via shared memory API"},
            {"id": "antigravity-cli", "label": "Antigravity CLI", "access": "read/write via shared memory API"},
        ],
        "gitPolicy": {
            "tracked": False,
            "detail": "Runtime records under data/shared-memory are ignored by Git.",
        },
    }
