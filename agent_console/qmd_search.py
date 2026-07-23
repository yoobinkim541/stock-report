from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable

from . import shared_memory

_FALSE_VALUES = {"0", "false", "no", "off"}


def enabled() -> bool:
    return os.getenv("AGENT_CONSOLE_QMD_ENABLED", "1").strip().lower() not in _FALSE_VALUES


def qmd_bin() -> str:
    return os.getenv("AGENT_CONSOLE_QMD_BIN", "qmd").strip() or "qmd"


def timeout_seconds() -> float:
    raw = os.getenv("AGENT_CONSOLE_QMD_TIMEOUT_SEC", "3").strip()
    try:
        return max(0.2, min(float(raw), 30.0))
    except ValueError:
        return 3.0


def collections() -> list[str]:
    raw = os.getenv("AGENT_CONSOLE_QMD_COLLECTIONS", "wiki")
    return [item.strip() for item in raw.split(",") if item.strip()]


def search_command() -> str:
    command = os.getenv("AGENT_CONSOLE_QMD_COMMAND", "search").strip().lower()
    return command if command in {"search", "query"} else "search"


def wiki_docs_dir() -> Path:
    override = os.getenv("AGENT_CONSOLE_QMD_WIKI_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(shared_memory.shared_memory_dir()) / "qmd-wiki"


def status() -> dict:
    binary = qmd_bin()
    return {
        "enabled": enabled(),
        "bin": binary,
        "installed": shutil.which(binary) is not None,
        "collections": collections(),
        "command": search_command(),
        "wiki_dir": str(wiki_docs_dir()),
    }


def export_pages(pages: list[dict]) -> dict:
    out_dir = wiki_docs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        page_id = _clean(page.get("id"), 100) or _hash_id(page)
        path = out_dir / f"{_safe_file_stem(page_id)}.md"
        path.write_text(_page_markdown(page, page_id), encoding="utf-8")
        files.append(str(path))
    return {"ok": True, "dir": str(out_dir), "files": files, "count": len(files)}


def search(
    query: str,
    *,
    limit: int = 10,
    surface: str = "all",
    status: str = "all",
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict]:
    query = _clean(query, 600)
    if not enabled() or not query:
        return []
    limit = max(1, min(int(limit or 10), 50))
    cmd = [qmd_bin(), search_command(), query, "--format", "json", "-n", str(limit)]
    for collection in collections():
        cmd.extend(["-c", collection])
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=timeout_seconds())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    raw = (getattr(result, "stdout", "") or "").strip()
    if not raw:
        return []
    payload = _parse_payload(raw)
    normalized = [_normalize_result(item, surface=surface, status=status) for item in payload]
    return [item for item in normalized if item][:limit]


def _parse_payload(raw: str) -> list[dict]:
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(raw))
    except json.JSONDecodeError:
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        candidates.append(rows)
    for parsed in candidates:
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("results", "matches", "documents", "items"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_result(item: dict, *, surface: str, status: str) -> dict:
    file_ref = _clean(
        item.get("file") or item.get("path") or item.get("displayPath") or item.get("uri") or item.get("url"),
        500,
    )
    title = _clean(item.get("title") or item.get("name") or _title_from_file(file_ref) or "qmd 문서", 180)
    snippet = _clean(
        item.get("snippet") or item.get("excerpt") or item.get("summary") or item.get("text") or item.get("content"),
        1800,
    )
    page_id = _page_id_from_file(file_ref) or _clean(item.get("id") or item.get("docid"), 120)
    if not page_id:
        page_id = "qmd-" + hashlib.sha256(f"{title}|{file_ref}|{snippet}".encode("utf-8")).hexdigest()[:16]
    score = item.get("score") or item.get("rankScore") or item.get("similarity") or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "id": page_id,
        "page_id": page_id,
        "title": title,
        "summary": snippet,
        "body": _clean(item.get("body") or item.get("content") or snippet, 4000),
        "snippet": snippet[:260],
        "surface": _clean(item.get("surface") or surface or "wiki", 60),
        "kind": _clean(item.get("kind") or "note", 40),
        "status": _clean(item.get("status") or status or "draft", 40),
        "tags": ["qmd"],
        "source_refs": [file_ref] if file_ref else [],
        "score": score,
        "provider": "qmd",
    }


def _page_markdown(page: dict, page_id: str) -> str:
    tags = [str(tag).strip() for tag in (page.get("tags") or []) if str(tag).strip()]
    source_refs = [str(ref).strip() for ref in (page.get("source_refs") or []) if str(ref).strip()]
    lines = [
        "---",
        f"id: {page_id}",
        f"title: {_yaml_scalar(page.get('title') or '위키 페이지')}",
        f"surface: {_clean(page.get('surface') or 'wiki', 80)}",
        f"kind: {_clean(page.get('kind') or 'note', 80)}",
        f"status: {_clean(page.get('status') or 'draft', 80)}",
        f"updated_at: {_clean(page.get('updated_at') or page.get('created_at') or '', 120)}",
        f"tags: [{', '.join(_yaml_scalar(tag) for tag in tags)}]",
        "---",
        "",
        f"# {_clean(page.get('title') or '위키 페이지', 180)}",
        "",
    ]
    summary = _clean(page.get("summary") or "", 2400)
    body = _clean(page.get("body") or "", 8000)
    if summary:
        lines += ["## Summary", "", summary, ""]
    if body:
        lines += ["## Body", "", body, ""]
    if source_refs:
        lines += ["## Source Refs", "", *[f"- {ref}" for ref in source_refs], ""]
    return "\n".join(lines).strip() + "\n"


def _page_id_from_file(file_ref: str) -> str:
    if not file_ref:
        return ""
    clean = file_ref.rstrip("/")
    match = re.search(r"([^/\\#?]+)\.md(?:$|[?#])", clean)
    if match:
        return _clean(match.group(1), 120)
    match = re.search(r"#([0-9a-zA-Z_-]{6,})", clean)
    if match:
        return _clean(match.group(1), 120)
    return ""


def _title_from_file(file_ref: str) -> str:
    page_id = _page_id_from_file(file_ref)
    if page_id:
        return page_id.replace("-", " ").strip()
    return ""


def _hash_id(page: dict) -> str:
    key = "|".join(str(page.get(field) or "") for field in ("title", "surface", "kind"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^0-9a-zA-Z가-힣_.-]+", "-", value).strip(".-")
    return stem or "wiki"


def _yaml_scalar(value: object) -> str:
    text = _clean(value, 300).replace('"', '\\"')
    return f'"{text}"'


def _clean(value: object, limit: int = 2200) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
