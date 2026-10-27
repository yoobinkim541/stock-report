from __future__ import annotations

import json
from datetime import datetime, timezone

from reports import article_queue
from reports import wiki_article_backfill as backfill
from reports.article_queue import enqueue_events, load_index
from reports.wiki_article_backfill import import_raw_cache, plan_backfill, seed_backfill


UTC = timezone.utc


def _page(page_id: str, refs: list[str], **overrides) -> dict:
    page = {
        "id": page_id,
        "title": f"Investment note {page_id}",
        "status": "reviewed",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "source_refs": refs,
    }
    page.update(overrides)
    return page


def test_plan_deduplicates_refs_and_reports_blocked_sensitive_and_queued_urls(tmp_path):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    enqueue_events(
        [{
            "id": "existing",
            "url": "https://allowed.test/a?x=1&utm_medium=feed",
            "title": "Already queued",
            "source": "test",
        }],
        root=tmp_path,
        now=now,
    )
    pages = [
        _page("page-1", [
            "https://allowed.test/a?x=1&utm_source=feed",
            "[same source](https://allowed.test/a?x=1#section)",
            "https://blocked.test/b",
            "conversation:42",
            "https://allowed.test/private?access_token=do-not-persist",
        ]),
        _page("page-2", ["https://allowed.test/c"]),
    ]

    plan = plan_backfill(
        pages,
        root=tmp_path,
        allowed_hosts={"allowed.test"},
        limit=10,
        now=now,
    )

    assert plan["pages_considered"] == 2
    assert plan["url_reference_count"] == 5
    assert plan["unique_url_count"] == 4
    assert plan["sensitive_url_count"] == 1
    assert plan["blocked_by_host"] == {"blocked.test": 1}
    assert plan["already_queued_count"] == 1
    assert plan["eligible_count"] == 1
    assert plan["selected_count"] == 1
    assert plan["events"][0]["url"] == "https://allowed.test/c"
    assert not (tmp_path / "backfill-manifests").exists()
    assert "do-not-persist" not in json.dumps(plan["manifest"], ensure_ascii=False)
    assert "access_token" not in json.dumps(plan["manifest"], ensure_ascii=False)


def test_seed_writes_review_manifest_before_queueing_without_fetching(tmp_path, monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    original_enqueue = article_queue.enqueue_events
    manifest_observed_before_enqueue = []

    def check_manifest_before_enqueue(events, *, root=None, now=None):
        manifests = list((tmp_path / "backfill-manifests").glob("*.json"))
        manifest_observed_before_enqueue.append(bool(manifests))
        assert manifests, "review manifest must exist before enqueueing URLs"
        return original_enqueue(events, root=root, now=now)

    monkeypatch.setattr(article_queue, "enqueue_events", check_manifest_before_enqueue)

    result = seed_backfill(
        [_page("page-7", ["https://allowed.test/report/7"])],
        root=tmp_path,
        allowed_hosts={"allowed.test"},
        limit=10,
        now=now,
    )

    assert result["selected_count"] == 1
    assert result["manifest_path"].startswith("backfill-manifests/")
    manifest_path = tmp_path / result["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["records"][0]["decision"] == "selected"
    assert manifest["records"][0]["source_page_ids"] == ["page-7"]
    assert manifest_observed_before_enqueue == [True]
    assert load_index(root=tmp_path)["https://allowed.test/report/7"]["status"] == "pending"


def test_plan_leaves_disabled_host_visible_until_explicitly_allowlisted(tmp_path):
    plan = plan_backfill(
        [_page("page-8", ["https://new-public-host.test/article"])],
        root=tmp_path,
        allowed_hosts={"allowed.test"},
    )

    assert plan["blocked_by_host"] == {"new-public-host.test": 1}
    assert plan["selected_count"] == 0
    assert not (tmp_path / "index.json").exists()


def test_plan_queues_known_public_source_hosts_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", raising=False)
    pages = [
        _page("page-9", [
            "https://arca.live/b/stock/123",
            "https://t.me/insidertracking/456",
            "https://kalshi.com/markets/example",
            "https://unknown.example/article",
        ])
    ]

    plan = plan_backfill(pages, root=tmp_path, limit=20)

    selected = {event["url"] for event in plan["events"]}
    assert {
        "https://arca.live/b/stock/123",
        "https://t.me/insidertracking/456",
        "https://kalshi.com/markets/example",
    } <= selected
    assert plan["blocked_by_host"] == {"unknown.example": 1}


def test_import_raw_cache_promotes_existing_source_body_without_network(tmp_path):
    article_root = tmp_path / "article-cache"
    source_root = tmp_path / "source-cache"
    event = {
        "id": "raw-1",
        "source": "saveticker",
        "title": "저장된 원문",
        "url": "https://saveticker.com/news/raw-1",
        "body_raw": "이미 수집된 원문 본문입니다. " * 30,
    }
    enqueue_events([event], root=article_root)
    source_root.mkdir()
    (source_root / "events-2026-09-24.jsonl").write_text(
        json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    result = import_raw_cache(root=article_root, source_cache=source_root, limit=10)

    assert result["matched"] == 1
    assert result["imported"] == 1
    assert result["network_requests"] == 0
    assert load_index(root=article_root)[event["url"]]["status"] == "ready"


def test_reference_coverage_distinguishes_ready_blocked_retry_and_unqueued(tmp_path):
    ready_url = "https://saveticker.com/news/ready"
    blocked_url = "https://arca.live/b/stock/blocked"
    retry_url = "https://kalshi.com/markets/retry"
    pages = [_page("coverage", [ready_url, blocked_url, retry_url, "https://t.me/post/missing"])]
    enqueue_events(
        [{"id": url, "url": url, "title": url, "source": "test"} for url in (ready_url, blocked_url, retry_url)],
        root=tmp_path,
    )
    with article_queue.locked_index(root=tmp_path) as (_root, index, _pruned):
        index[ready_url].update(status="ready", content_hash="ready-hash", body_path="bodies/ready.json")
        index[blocked_url].update(status="blocked", last_error="robots unavailable: HTTP 403")
        index[retry_url].update(status="retry", next_attempt_at="2099-01-01T00:00:00+00:00")

    coverage = backfill.reference_coverage(pages, root=tmp_path)

    assert coverage["url_count"] == 4
    assert coverage["status_counts"] == {
        "ready_missing_body": 1,
        "blocked": 1,
        "retry": 1,
        "missing": 1,
    }
    assert coverage["missing_count"] == 1
    assert coverage["blocked_count"] == 1
    assert coverage["retry_count"] == 1
    assert coverage["coverage_complete"] is False
