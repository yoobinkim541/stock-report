"""Regression coverage for bounded JSONL input buffering during append."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reports import source_collector as sc


def test_append_streams_existing_file_and_preserves_duplicates(tmp_path, monkeypatch):
    now = datetime(2026, 8, 21, 10, 5, tzinfo=timezone(timedelta(hours=9)))
    event = {"source": "saveticker", "title": "existing", "url": "https://example.com/1"}
    assert sc.append_events([event], cache_dir=tmp_path, now=now) == 1
    event_file = tmp_path / "events-2026-08-21.jsonl"
    # Blank and malformed records must retain the existing skip behavior.
    with event_file.open("a", encoding="utf-8") as stream:
        stream.write("\nnot-json\n")
    original_read_text = Path.read_text

    def reject_full_read(path, *args, **kwargs):
        if path == event_file:
            raise AssertionError("append must stream existing events, not buffer the whole file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_full_read)
    fresh = {"source": "saveticker", "title": "fresh", "url": "https://example.com/2"}
    assert sc.append_events([event, fresh, fresh], cache_dir=tmp_path, now=now) == 1
    stats = sc._LAST_APPEND_STATS["saveticker"]
    assert (stats["fetched"], stats["persisted"], stats["deduped"], stats["collisions"]) == (3, 1, 2, 0)
    rows = []
    with event_file.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    assert [row["title"] for row in rows] == ["existing", "fresh"]
    assert sc.append_events([event, fresh], cache_dir=tmp_path, now=now) == 0
    assert sc._LAST_APPEND_STATS["saveticker"]["deduped"] == 2
