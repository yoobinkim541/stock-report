import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_collector as sc

KST = timezone(timedelta(hours=9))


def test_event_id_prefers_url_and_dedupes_when_appending(tmp_path):
    cache_dir = tmp_path / "cache"
    now = datetime(2026, 6, 4, 10, 30, tzinfo=KST)
    events = [
        {"source": "saveticker", "title": "NVDA rallies", "url": "https://example.com/nvda"},
        {"source": "saveticker", "title": "NVDA rallies", "url": "https://example.com/nvda"},
    ]

    written = sc.append_events(events, cache_dir=cache_dir, now=now)

    assert written == 1
    rows = [json.loads(line) for line in (cache_dir / "events-2026-06-04.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["id"] == sc.event_id(events[0])
    assert rows[0]["collected_at"] == "2026-06-04T10:30:00+09:00"


def test_load_recent_events_reads_multiple_days_and_dedupes(tmp_path):
    cache_dir = tmp_path / "cache"
    now = datetime(2026, 6, 4, 8, 0, tzinfo=KST)
    sc.append_events([{"source": "arca", "title": "old", "url": "https://e/old"}], cache_dir=cache_dir, now=now - timedelta(days=2))
    sc.append_events([{"source": "arca", "title": "fresh", "url": "https://e/fresh"}], cache_dir=cache_dir, now=now - timedelta(hours=23))
    sc.append_events([{"source": "arca", "title": "fresh", "url": "https://e/fresh"}], cache_dir=cache_dir, now=now)

    events = sc.load_recent_events(cache_dir=cache_dir, now=now, hours=24)

    assert [e["title"] for e in events] == ["fresh"]


def test_build_digest_groups_by_source_and_limits_items():
    events = [
        {"source": "saveticker", "title": "AI chip demand", "url": "https://e/1", "tickers": ["NVDA"]},
        {"source": "arca", "title": "환율 경계", "url": "https://e/2", "category": "📰뉴스"},
    ]

    digest = sc.build_digest(events, limit=5)

    assert "누적 수집 자료" in digest
    assert "saveticker 1건" in digest
    assert "arca 1건" in digest
    assert "AI chip demand" in digest
    assert "NVDA" in digest
