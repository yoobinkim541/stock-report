#!/usr/bin/env python3
"""Build source-backed wiki pages from collected source-cache events."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

KST = timezone(timedelta(hours=9))
MIN_GROUP_EVENTS = 2
MAX_EVENTS_PER_PAGE = 8
MAX_SOURCE_REFS = 12
MAX_CURATOR_LINKS = 12
GENERIC_TOPICS = {"기타", "saveticker", "텔레그램", "시장데이터"}
GENERIC_KINDS = {"article", "community_signal", "snapshot", "macro_snapshot", "event", "report"}


def _clean(value: object, limit: int = 600) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _slug(value: object) -> str:
    text = _clean(value, 80).lower()
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", text)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "source"


def _root_source(source: object) -> str:
    return str(source or "unknown").split(":", 1)[0].strip().lower() or "unknown"


def _event_topic(event: dict) -> str:
    classification = event.get("classification") or {}
    for value in (event.get("topic"), classification.get("topic")):
        topic = _clean(value, 80)
        if topic:
            return topic
    tags = event.get("tags") or []
    if tags:
        return _clean(tags[0], 80)
    return "기타"


def _event_text(event: dict) -> str:
    return " ".join(
        part for part in [
            _clean(event.get("title"), 220),
            _clean(event.get("body_raw") or event.get("body") or event.get("body_excerpt"), 500),
        ]
        if part
    )


def _dedupe(values: Iterable[object], *, limit: int = MAX_SOURCE_REFS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = _clean(raw, 260)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _source_refs(events: list[dict]) -> list[str]:
    refs = []
    for event in events:
        refs.extend([event.get("url"), event.get("text_path"), event.get("raw_path")])
    return _dedupe(refs)


def _event_key(event: dict) -> str:
    return _clean(event.get("url"), 300) or _clean(event.get("title"), 220)


def _is_strong_group(events: list[dict]) -> bool:
    if len(events) >= MIN_GROUP_EVENTS:
        return True
    return any(bool((event.get("classification") or {}).get("wiki_eligible")) for event in events)


def _status_for(events: list[dict], refs: list[str]) -> str:
    if not refs:
        return "draft"
    roots = {_root_source(event.get("source")) for event in events}
    if roots - {"telegram", "arca"}:
        return "reviewed"
    return "draft"


def _summary_for(topic: str, events: list[dict]) -> str:
    tickers = Counter(t for event in events for t in (event.get("tickers") or []) if isinstance(t, str) and t.strip())
    sources = Counter(_root_source(event.get("source")) for event in events)
    bits = [f"{topic} 관련 수집 이벤트 {len(events)}건"]
    if tickers:
        bits.append(", ".join(f"{ticker} {count}건" for ticker, count in tickers.most_common(5)))
    if sources:
        bits.append("출처 " + ", ".join(f"{src} {count}" for src, count in sources.most_common(5)))
    return " · ".join(bits)


def _body_for(topic: str, events: list[dict], now: datetime) -> str:
    lines = [
        f"수집 기준: {now.astimezone(KST).isoformat(timespec='minutes')}",
        "",
        "핵심 근거:",
    ]
    for event in events[:MAX_EVENTS_PER_PAGE]:
        source = event.get("source") or "unknown"
        title = _clean(event.get("title"), 180)
        body = _clean(event.get("body_raw") or event.get("body") or event.get("body_excerpt"), 260)
        url = _clean(event.get("url"), 220)
        line = f"- [{source}] {title}"
        if body and body != title:
            line += f" — {body}"
        if url:
            line += f" ({url})"
        lines.append(line)
    lines.extend([
        "",
        "답변 사용법:",
        f"- {topic} 질문에서는 위 근거를 최신 수집 신호로 먼저 확인합니다.",
        "- 커뮤니티/텔레그램 단독 신호는 가격·재무·공식 자료와 교차확인 전에는 보조 근거로 둡니다.",
    ])
    return "\n".join(lines).strip()


def _group_label(key: str) -> tuple[str, str, str]:
    group_type, label = key.split(":", 1)
    if group_type == "topic":
        return group_type, label, f"{label}"
    if group_type == "type":
        return group_type, label, f"유형:{label}"
    if group_type == "ticker":
        return group_type, label, f"종목:{label}"
    return group_type, label, label


def _link_pages_sharing_events(pages: list[dict], page_event_keys: dict[str, set[str]]) -> None:
    for left in pages:
        left_id = left.get("id")
        left_keys = page_event_keys.get(left_id) or set()
        if not left_keys:
            left["links"] = []
            continue
        linked: list[str] = []
        for right in pages:
            right_id = right.get("id")
            if right_id == left_id:
                continue
            right_keys = page_event_keys.get(right_id) or set()
            if left_keys & right_keys:
                linked.append(right_id)
            if len(linked) >= MAX_CURATOR_LINKS:
                break
        left["links"] = linked


def build_wiki_pages_from_events(events: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(KST)
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if not _clean(event.get("title")):
            continue
        topic = _event_topic(event)
        if topic.lower() not in GENERIC_TOPICS:
            groups[f"topic:{topic}"].append(event)
        kind = _clean((event.get("classification") or {}).get("kind") or event.get("kind"), 60).lower()
        if kind and kind not in GENERIC_KINDS:
            groups[f"type:{kind}"].append(event)
        for ticker in event.get("tickers") or []:
            if isinstance(ticker, str) and ticker.strip():
                groups[f"ticker:{ticker.strip().upper()}"].append(event)

    pages: list[dict] = []
    page_event_keys: dict[str, set[str]] = {}
    for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        group_type, label, display = _group_label(key)
        rows = sorted(rows, key=lambda event: str(event.get("published_at") or event.get("collected_at") or ""), reverse=True)
        if not _is_strong_group(rows):
            continue
        refs = _source_refs(rows)
        source_roots = sorted({_root_source(row.get("source")) for row in rows})
        ticker_counts = Counter(t for row in rows for t in (row.get("tickers") or []) if isinstance(t, str) and t.strip())
        tags = _dedupe([
            "wiki",
            "market",
            "source_digest",
            f"{group_type}:{label}",
            *(f"source:{src}" for src in source_roots),
            *(f"ticker:{ticker}" for ticker, _count in ticker_counts.most_common(8)),
        ], limit=20)
        page_id = f"source-{group_type}-{_slug(label)}"
        pages.append({
            "id": page_id,
            "title": f"수집 소스 위키: {display}",
            "surface": "market",
            "kind": "source_digest",
            "status": _status_for(rows, refs),
            "tags": tags,
            "summary": _summary_for(display, rows),
            "body": _body_for(display, rows, now),
            "source_refs": refs,
            "openQuestions": [
                f"{display} 신호가 가격·크레딧·환율 데이터에서도 확인되는가?",
                "공식 자료와 충돌하는 커뮤니티성 단서가 있는가?",
            ],
            "confidence": 0.78 if _status_for(rows, refs) == "reviewed" else 0.55,
        })
        page_event_keys[page_id] = {key for key in (_event_key(row) for row in rows) if key}
    _link_pages_sharing_events(pages, page_event_keys)
    return pages


def curate_recent_source_wiki(hours: int = 48, limit: int = 8) -> dict:
    from agent_console import wiki
    from reports.source_collector import load_recent_events

    events = load_recent_events(hours=max(1, int(hours)))
    pages = build_wiki_pages_from_events(events)
    saved = []
    for page in pages[: max(1, int(limit))]:
        saved.append(wiki.upsert_page(page))
    return {"ok": True, "events": len(events), "pages": len(pages), "saved": len(saved), "page_ids": [p.get("id") for p in saved]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build source-backed wiki pages from recent source-cache events.")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    result = curate_recent_source_wiki(hours=args.hours, limit=args.limit)
    print(f"source wiki curator: events={result['events']} pages={result['pages']} saved={result['saved']}")
    for page_id in result.get("page_ids") or []:
        print(f"- {page_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
