#!/usr/bin/env python3
"""Collect stock-report source events into a daily JSONL cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

KST = timezone(timedelta(hours=9))
DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/reports/source-cache"))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}
ARCA_LABELS = ("🧠분석", "📰뉴스", "ℹ️정보", "실적")
PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "NOW", "CRM", "SAP", "UNH", "SGOV", "CPNG", "NVDA", "GOOGL", "SPMO"]


def event_id(event: dict) -> str:
    key = event.get("url") or f"{event.get('source', '')}:{event.get('title', '')}"
    return hashlib.sha256(str(key).strip().lower().encode("utf-8")).hexdigest()[:16]


def _event_file(cache_dir: Path, dt: datetime) -> Path:
    return cache_dir / f"events-{dt.astimezone(KST).strftime('%Y-%m-%d')}.jsonl"


def append_events(events: Iterable[dict], cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> int:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _event_file(cache_dir, now)

    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("id"))
            except json.JSONDecodeError:
                continue

    rows = []
    for event in events:
        row = dict(event)
        row.setdefault("source", "unknown")
        row.setdefault("title", "")
        row["id"] = event_id(row)
        if row["id"] in seen:
            continue
        row["collected_at"] = now.astimezone(KST).isoformat(timespec="seconds")
        seen.add(row["id"])
        rows.append(row)

    if rows:
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def load_recent_events(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None, hours: int = 24) -> list[dict]:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cutoff = now.astimezone(KST) - timedelta(hours=hours)
    events = []
    seen = set()

    for days_back in range((hours // 24) + 3):
        path = _event_file(cache_dir, now - timedelta(days=days_back))
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row.get("collected_at", ""))
            except Exception:
                continue
            row_id = row.get("id") or event_id(row)
            if ts < cutoff or row_id in seen:
                continue
            seen.add(row_id)
            events.append(row)

    return sorted(events, key=lambda e: e.get("collected_at", ""))


def build_digest(events: list[dict], limit: int = 12) -> str:
    if not events:
        return "## 누적 수집 자료\n\n- 최근 24시간 누적 캐시 없음\n"

    source_counts = Counter(e.get("source", "unknown") for e in events)
    ticker_counts = Counter(t for e in events for t in (e.get("tickers") or []))
    lines = ["## 누적 수집 자료", ""]
    lines.append("- " + ", ".join(f"{src} {cnt}건" for src, cnt in source_counts.most_common()))
    if ticker_counts:
        lines.append("- 반복 등장 종목: " + ", ".join(f"{t} {c}건" for t, c in ticker_counts.most_common(8)))
    lines.append("")

    for event in sorted(events, key=lambda e: e.get("collected_at", ""), reverse=True)[:limit]:
        title = event.get("title") or "[제목 없음]"
        source = event.get("source", "unknown")
        url = event.get("url") or ""
        tickers = ", ".join(event.get("tickers") or [])
        suffix = f" · {tickers}" if tickers else ""
        lines.append(f"- [{source}] {title}{suffix}" + (f" — {url}" if url else ""))
    return "\n".join(lines) + "\n"


def _extract_tickers(text: str, universe: Iterable[str] = PORTFOLIO_TICKERS) -> list[str]:
    upper = f" {text.upper()} "
    return [t for t in universe if f" {t.upper()} " in upper]


def fetch_saveticker_events() -> list[dict]:
    base = os.getenv("SAVE_TICKER_API_BASE", "https://saveticker.com/api").rstrip("/")
    paths = [
        ("news/top-stories", None),
        ("news/list", {"page": 1, "page_size": 30, "sort": "created_at_desc"}),
    ]
    events = []
    for path, params in paths:
        try:
            resp = requests.get(f"{base}/{path}", headers=HEADERS, params=params, timeout=12)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        for item in data.get("news_list") or data.get("data") or []:
            title = item.get("title") or ""
            if not title:
                continue
            text = " ".join(str(item.get(k) or "") for k in ("title", "content", "group_summary"))
            events.append({
                "source": "saveticker",
                "title": title,
                "url": item.get("url") or item.get("link") or "",
                "published_at": item.get("created_at") or item.get("published_at") or "",
                "tickers": item.get("tickers") or _extract_tickers(text),
                "tags": item.get("tag_names") or [],
            })
    return events


def fetch_arca_events(max_pages: int = 2) -> list[dict]:
    import re

    events = []
    link_pat = re.compile(r"\[([^\]]+)\]\(https://arca\.live/b/stock/(\d+)\?p=(\d+)\)")
    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(f"https://r.jina.ai/http://arca.live/b/stock?p={page}", headers=HEADERS, timeout=20)
            resp.raise_for_status()
            markdown = resp.text
        except Exception:
            continue
        for match in link_pat.finditer(markdown):
            text = " ".join(match.group(1).split()).replace("**", "").strip()
            if not any(label in text for label in ARCA_LABELS):
                continue
            post_id = match.group(2)
            events.append({
                "source": "arca",
                "title": text[:140],
                "url": f"https://arca.live/b/stock/{post_id}",
                "category": next((label for label in ARCA_LABELS if label in text), ""),
                "tickers": _extract_tickers(text),
            })
    return events


def collect_once(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> tuple[int, int]:
    events = fetch_saveticker_events() + fetch_arca_events(max_pages=int(os.getenv("STOCK_COLLECTOR_ARCA_PAGES", "2")))
    return len(events), append_events(events, cache_dir=cache_dir, now=now)


def prune_old(cache_dir: Path | str = DEFAULT_CACHE_DIR, days: int = 14, now: datetime | None = None) -> int:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    cutoff = now.astimezone(KST).date() - timedelta(days=days)
    removed = 0
    for path in cache_dir.glob("events-*.jsonl"):
        try:
            day = datetime.strptime(path.stem.replace("events-", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--digest", action="store_true")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args()

    if args.digest:
        print(build_digest(load_recent_events(args.cache_dir, hours=args.hours)))
        return 0

    fetched, written = collect_once(args.cache_dir)
    removed = prune_old(args.cache_dir)
    print(f"stock source collector: fetched={fetched} new={written} pruned={removed} cache={args.cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
