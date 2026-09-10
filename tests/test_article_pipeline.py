from __future__ import annotations

from datetime import datetime, timezone


def _article_event(url: str = "https://saveticker.com/news/42", **overrides) -> dict:
    event = {
        "id": "source-event-42",
        "source": "saveticker",
        "title": "반도체 공급 제약",
        "url": url,
        "published_at": "2026-09-09T00:00:00+00:00",
        "body_raw": "API 미리보기",
        "classification": {"kind": "article", "wiki_eligible": True},
    }
    event.update(overrides)
    return event


def test_news_ingress_enqueues_each_source_batch_and_canonicalizes_duplicates(tmp_path, monkeypatch):
    from reports.article_queue import load_index
    from reports.source_pipeline import ProviderSpec, run_providers

    article_root = tmp_path / "article-cache"
    monkeypatch.setenv("ARTICLE_CACHE_DIR", str(article_root))
    first = _article_event("https://SaveTicker.com/news/42?utm_source=feed#top")
    duplicate = _article_event("https://saveticker.com/news/42", id="source-event-duplicate")

    result = run_providers(
        registry=[ProviderSpec("saveticker", ("saveticker",), "news", lambda: [first, duplicate])],
        group="news",
        cache_dir=tmp_path / "source-cache",
        now=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )

    assert list(load_index(root=article_root)) == ["https://saveticker.com/news/42"]
    assert result["article_queue"] == {
        "enqueued": 1,
        "updated": 1,
        "invalid": 0,
        "capacity_rejected": 0,
        "errors": [],
    }
    assert result["providers"]["saveticker"]["article_queue"]["enqueued"] == 1
    assert result["health"]["saveticker"]["article_queue"]["updated"] == 1


def test_market_snapshots_are_persisted_but_not_enqueued(tmp_path, monkeypatch):
    from reports.article_queue import load_index
    from reports.source_pipeline import ProviderSpec, run_providers

    article_root = tmp_path / "article-cache"
    monkeypatch.setenv("ARTICLE_CACHE_DIR", str(article_root))
    snapshot = _article_event(
        source="yahoo_finance",
        classification={"kind": "market_snapshot", "wiki_eligible": True},
    )

    result = run_providers(
        registry=[ProviderSpec("market", ("yahoo_finance",), "market", lambda: [snapshot], mutable=True)],
        group="market",
        cache_dir=tmp_path / "source-cache",
    )

    assert result["persisted"] == 1
    assert load_index(root=article_root) == {}
    assert result["article_queue"]["enqueued"] == 0
    assert result["article_queue"]["errors"] == []


def test_queue_failure_is_bounded_and_does_not_lose_persisted_source_event(tmp_path, monkeypatch):
    from reports import article_queue, source_collector
    from reports.source_pipeline import ProviderSpec, run_providers

    def fail_queue(events, *, root=None, now=None):
        assert events[0]["body_raw"] == "API 미리보기"
        raise RuntimeError("queue unavailable " + ("x" * 1000))

    monkeypatch.setattr(article_queue, "enqueue_events", fail_queue)
    cache_dir = tmp_path / "source-cache"
    event = _article_event()

    result = run_providers(
        registry=[ProviderSpec("saveticker", ("saveticker",), "news", lambda: [event])],
        group="news",
        cache_dir=cache_dir,
    )

    persisted = source_collector.load_recent_events(cache_dir, hours=1, limit=10)
    assert any(row["url"] == event["url"] and row["body_raw"] == event["body_raw"] for row in persisted)
    assert result["persisted"] == 1
    assert len(result["article_queue"]["errors"]) == 1
    assert result["article_queue"]["errors"][0].startswith("RuntimeError: queue unavailable")
    assert len(result["article_queue"]["errors"][0]) <= 500
    queue_health = result["health"]["saveticker"]["article_queue"]
    assert queue_health["error"].startswith("RuntimeError: queue unavailable")
    assert len(queue_health["error"]) <= 500


def test_ingressed_article_becomes_usable_only_after_cache_is_ready(tmp_path, monkeypatch):
    from reports.article_crawler import crawl_pending
    from reports.article_queue import get_article
    from reports.source_pipeline import ProviderSpec, run_providers

    article_root = tmp_path / "article-cache"
    monkeypatch.setenv("ARTICLE_CACHE_DIR", str(article_root))
    event = _article_event()
    run_providers(
        registry=[ProviderSpec("saveticker", ("saveticker",), "news", lambda: [event])],
        group="news",
        cache_dir=tmp_path / "source-cache",
    )
    assert get_article(event["url"], root=article_root) is None

    full_text = "기업의 공급 제약과 고객 수요가 매출 인식 시점을 바꾼다. " * 8
    crawl_pending(
        root=article_root,
        fetcher=lambda _url: f"<html><body><article><p>{full_text}</p></article></body></html>",
        limit=20,
    )

    ready = get_article(event["url"], root=article_root)
    assert ready is not None
    assert ready["text"] == full_text.strip()
    assert ready["text"] != event["body_raw"]


def test_normal_saveticker_polling_never_fetches_full_article_body(monkeypatch, tmp_path):
    from reports import source_collector

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"news_list": [{
                "id": 42,
                "title": "반도체 공급 제약",
                "content": "짧은 API 미리보기",
                "group_summary": "",
                "created_at": "2026-09-09",
                "tag_names": ["반도체"],
            }]}

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(source_collector.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        source_collector,
        "_fetch_saveticker_article_body",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("synchronous article fetch")),
    )

    events = source_collector.fetch_saveticker_events()

    assert events[0]["body_raw"] == "짧은 API 미리보기"
    assert events[0]["body_excerpt"] == "짧은 API 미리보기"
    assert events[0]["url"] == "https://saveticker.com/news/42"


def test_explicit_legacy_saveticker_record_still_fetches_full_body(monkeypatch, tmp_path):
    from reports import source_collector

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        source_collector,
        "_fetch_saveticker_article_body",
        lambda _url, title="": "명시적으로 가져온 전체 기사 본문",
    )

    record = source_collector._saveticker_article_record(
        {"id": 43, "title": "레거시 호출", "content": "짧은 미리보기"},
        "https://saveticker.com/api",
    )

    assert record["body_raw"] == "명시적으로 가져온 전체 기사 본문"


def test_crontab_drift_reports_a_missing_article_crawler_job():
    from scripts.check_crontab_drift import drift_report

    crawler = (
        "8,18,28,38,48,58 * * * * cd /home/ubuntu/projects/stock-report && "
        "flock -n /tmp/article_crawler.lock uv run python -m reports.article_crawler --limit 20"
    )

    report = drift_report(crawler + "\n", "")

    assert report["ok"] is False
    assert report["missing"] == [crawler]
