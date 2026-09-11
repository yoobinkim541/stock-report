from __future__ import annotations

from datetime import datetime, timedelta, timezone

from reports.article_queue import enqueue_events, get_article, load_index
from reports.evidence_cards import event_to_evidence_card


UTC = timezone.utc


def _event(url: str = "https://saveticker.com/news/1", **overrides) -> dict:
    event = {
        "id": "event-1",
        "source": "saveticker",
        "title": "AI 설비투자 확대",
        "url": url,
        "published_at": "2026-09-09T00:00:00+00:00",
        "classification": {"kind": "article", "wiki_eligible": True},
    }
    event.update(overrides)
    return event


def test_enqueue_deduplicates_canonical_url_without_losing_meaningful_query(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    first = _event("https://SaveTicker.com/news/Alpha?symbol=NVDA&utm_source=feed#top")
    duplicate = _event(
        "https://saveticker.com/news/Alpha?symbol=NVDA&fbclid=tracking",
        id="event-2",
    )

    result = enqueue_events([first, duplicate], root=tmp_path, now=now)
    index = load_index(root=tmp_path)

    assert result["enqueued"] == 1
    assert len(index) == 1
    assert "https://saveticker.com/news/Alpha?symbol=NVDA" in index
    assert index["https://saveticker.com/news/Alpha?symbol=NVDA"]["event_ids"] == [
        "event-1",
        "event-2",
    ]
    assert get_article(first["url"], root=tmp_path) is None


def test_terminal_failure_is_reopened_only_by_changed_discovery_metadata(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    index_path = tmp_path / "index.json"

    import json

    index = json.loads(index_path.read_text(encoding="utf-8"))
    record = index[event["url"]]
    record.update(
        status="failed",
        attempts=3,
        completed_at=now.isoformat(),
        last_error="timeout",
    )
    index_path.write_text(json.dumps(index), encoding="utf-8")

    enqueue_events([event], root=tmp_path, now=now + timedelta(hours=1))
    assert load_index(root=tmp_path)[event["url"]]["status"] == "failed"

    enqueue_events(
        [_event(title="AI 설비투자 전망 상향")],
        root=tmp_path,
        now=now + timedelta(hours=2),
    )
    reopened = load_index(root=tmp_path)[event["url"]]
    assert reopened["status"] == "pending"
    assert reopened["attempts"] == 0
    assert reopened["last_error"] == ""


def test_capacity_is_exposed_when_index_is_full(monkeypatch, tmp_path):
    import reports.article_queue as queue

    monkeypatch.setattr(queue, "MAX_INDEX_RECORDS", 2)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    result = enqueue_events(
        [_event(f"https://saveticker.com/news/{i}", id=f"event-{i}") for i in range(3)],
        root=tmp_path,
        now=now,
    )

    assert len(load_index(root=tmp_path)) == 2
    assert result["capacity"] == {"limit": 2, "used": 2, "available": 0}
    assert result["capacity_rejected"] == 1


def test_enqueue_prunes_completed_metadata_older_than_fourteen_days(tmp_path):
    import json

    start = datetime(2026, 9, 1, tzinfo=UTC)
    old = _event("https://saveticker.com/news/old", id="old")
    enqueue_events([old], root=tmp_path, now=start)
    index_path = tmp_path / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[old["url"]].update(status="unavailable", completed_at=start.isoformat())
    index_path.write_text(json.dumps(index), encoding="utf-8")

    result = enqueue_events(
        [_event("https://saveticker.com/news/new", id="new")],
        root=tmp_path,
        now=start + timedelta(days=15),
    )

    assert result["pruned"] == 1
    assert set(load_index(root=tmp_path)) == {"https://saveticker.com/news/new"}


def test_same_url_merges_bounded_ids_used_by_evidence_cards(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    first = _event(id="source-event-1")
    rediscovered = _event(id="source-event-2", title="새 분석 제목")

    enqueue_events([first, rediscovered], root=tmp_path, now=now)
    metadata = load_index(root=tmp_path)[first["url"]]

    assert metadata["event_ids"] == ["source-event-1", "source-event-2"]
    assert metadata["evidence_ids"] == [
        event_to_evidence_card(first, now=now).id,
        event_to_evidence_card(rediscovered, now=now).id,
    ]
    assert "raw_payload" not in metadata
    assert "body_raw" not in metadata


def test_get_article_uses_supplied_index_snapshot_without_reloading(monkeypatch, tmp_path):
    from reports import article_crawler as crawler
    from reports import article_queue as queue

    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    crawler.crawl_pending(
        root=tmp_path,
        fetcher=lambda _url: (
            "<article><p>" + "본문 캐시 스냅샷 검증 문장입니다. " * 20 + "</p></article>"
        ),
        now=now,
    )
    snapshot = load_index(root=tmp_path)
    monkeypatch.setattr(
        queue,
        "load_index",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not reload metadata")),
    )

    article = queue.get_article(event["url"], root=tmp_path, index=snapshot)

    assert article is not None
    assert article["url"] == event["url"]
