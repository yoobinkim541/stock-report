from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

import safe_io

from reports.article_crawler import _eligible_seed_events, crawl_pending, main
from reports.article_queue import enqueue_events, get_article, load_index


UTC = timezone.utc


def _event(number: int = 1, **overrides) -> dict:
    event = {
        "id": f"event-{number}",
        "source": "saveticker",
        "title": f"AI 인프라 투자 확대 {number}",
        "url": f"https://saveticker.com/news/{number}",
        "published_at": "2026-09-09T00:00:00+00:00",
        "classification": {"kind": "article", "wiki_eligible": True},
    }
    event.update(overrides)
    return event


def _html(marker: str) -> str:
    body = (f"{marker} 반도체 수요와 데이터센터 설비투자에 관한 구체적인 분석입니다. " * 8).strip()
    return f"<html><head><title>{marker}</title></head><body><nav>메뉴</nav><article><h1>{marker}</h1><p>{body}</p></article><footer>광고</footer></body></html>"


def _response(status: int, text: str = "", **headers) -> dict:
    return {"status_code": status, "text": text, "headers": headers}


def _article_fetcher(text: str, calls: list[str]):
    def fetch(url: str):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response(404)
        return _response(200, text, **{"Content-Type": "text/html; charset=utf-8"})

    return fetch


def test_success_persists_ready_body_and_second_run_does_not_fetch(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event, event], root=tmp_path, now=now)
    calls: list[str] = []
    fetcher = _article_fetcher(_html("첫 본문"), calls)

    first = crawl_pending(root=tmp_path, limit=20, fetcher=fetcher, now=now)
    article = get_article(event["url"], root=tmp_path)
    calls_after_first = list(calls)
    second = crawl_pending(root=tmp_path, limit=20, fetcher=fetcher, now=now)

    assert first["ready"] == 1
    assert article is not None
    assert set(article) == {
        "url",
        "title",
        "source",
        "published_at",
        "fetched_at",
        "text",
        "content_hash",
    }
    assert len(article["text"]) >= 120
    assert len(article["content_hash"]) == 64
    assert second["processed"] == 0
    assert calls == calls_after_first


def test_injected_fetcher_may_return_html_string_directly(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)

    result = crawl_pending(root=tmp_path, fetcher=lambda _url: _html("문자열 응답"), now=now)

    assert result["ready"] == 1
    assert get_article(event["url"], root=tmp_path)["text"].startswith("문자열 응답")


def test_retry_delay_increases_and_third_attempt_is_terminal(tmp_path):
    event = _event()
    start = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([event], root=tmp_path, now=start)

    def failing(url: str):
        if url.endswith("/robots.txt"):
            return _response(404)
        raise RuntimeError("temporary timeout")

    crawl_pending(root=tmp_path, fetcher=failing, now=start)
    first = load_index(root=tmp_path)[event["url"]]
    first_retry = datetime.fromisoformat(first["next_attempt_at"])
    crawl_pending(root=tmp_path, fetcher=failing, now=first_retry)
    second = load_index(root=tmp_path)[event["url"]]
    second_retry = datetime.fromisoformat(second["next_attempt_at"])
    crawl_pending(root=tmp_path, fetcher=failing, now=second_retry)
    terminal = load_index(root=tmp_path)[event["url"]]

    assert first_retry > start
    assert second_retry - first_retry > first_retry - start
    assert terminal["status"] == "failed"
    assert terminal["attempts"] == 3
    assert terminal["next_attempt_at"] is None


def test_concurrent_enqueue_during_fetch_preserves_both_urls(tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([_event(1)], root=tmp_path, now=start)
    entered = threading.Event()
    release = threading.Event()

    def blocked_fetch(url: str):
        if url.endswith("/robots.txt"):
            return _response(404)
        entered.set()
        assert release.wait(timeout=5)
        return _response(200, _html("동시성 본문"), **{"Content-Type": "text/html"})

    errors: list[BaseException] = []

    def run_worker():
        try:
            crawl_pending(root=tmp_path, fetcher=blocked_fetch, now=start)
        except BaseException as exc:  # pragma: no cover - assertion aid
            errors.append(exc)

    worker = threading.Thread(target=run_worker)
    worker.start()
    assert entered.wait(timeout=5)
    enqueue_events([_event(2)], root=tmp_path, now=start + timedelta(seconds=1))
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert set(load_index(root=tmp_path)) == {
        "https://saveticker.com/news/1",
        "https://saveticker.com/news/2",
    }


def test_expired_claim_left_by_killed_worker_is_recovered(tmp_path):
    class Killed(BaseException):
        pass

    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=start)

    def killed_fetch(url: str):
        if url.endswith("/robots.txt"):
            return _response(404)
        raise Killed()

    with pytest.raises(Killed):
        crawl_pending(root=tmp_path, fetcher=killed_fetch, now=start)

    claimed = load_index(root=tmp_path)[event["url"]]
    assert claimed["status"] == "fetching"
    expiry = datetime.fromisoformat(claimed["claim_expires_at"])
    result = crawl_pending(
        root=tmp_path,
        fetcher=_article_fetcher(_html("복구 본문"), []),
        now=expiry + timedelta(seconds=1),
    )

    assert result["recovered_claims"] == 1
    assert get_article(event["url"], root=tmp_path) is not None


def test_rediscovery_after_24_hours_revalidates_without_duplicate_unchanged_body(tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    calls: list[str] = []
    fetcher = _article_fetcher(_html("동일 본문"), calls)
    enqueue_events([event], root=tmp_path, now=start)
    crawl_pending(root=tmp_path, fetcher=fetcher, now=start)
    assert len(list((tmp_path / "bodies").rglob("*.json"))) == 1

    enqueue_events([event], root=tmp_path, now=start + timedelta(hours=23))
    calls_before = len(calls)
    assert crawl_pending(root=tmp_path, fetcher=fetcher, now=start + timedelta(hours=23))["processed"] == 0
    assert len(calls) == calls_before

    enqueue_events([event], root=tmp_path, now=start + timedelta(hours=25))
    crawl_pending(root=tmp_path, fetcher=fetcher, now=start + timedelta(hours=25))

    assert len(calls) > calls_before
    assert len(list((tmp_path / "bodies").rglob("*.json"))) == 1


@pytest.mark.parametrize(
    ("article_response", "expected_status"),
    [
        (_response(200, "<article>너무 짧은 본문</article>", **{"Content-Type": "text/html"}), "unavailable"),
        (_response(403, "Just a moment... Enable JavaScript and cookies to continue"), "needs_browser"),
    ],
)
def test_unusable_pages_get_explicit_non_ready_status(tmp_path, article_response, expected_status):
    event = _event()
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([event], root=tmp_path, now=now)

    def fetch(url: str):
        return _response(404) if url.endswith("/robots.txt") else article_response

    crawl_pending(root=tmp_path, fetcher=fetch, now=now)

    assert load_index(root=tmp_path)[event["url"]]["status"] == expected_status
    assert get_article(event["url"], root=tmp_path) is None


def test_redirect_to_private_address_is_blocked_before_following(tmp_path):
    event = _event()
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([event], root=tmp_path, now=now)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response(404)
        return _response(302, Location="http://127.0.0.1/private")

    crawl_pending(root=tmp_path, fetcher=fetch, now=now)

    record = load_index(root=tmp_path)[event["url"]]
    assert record["status"] == "blocked"
    assert all("127.0.0.1" not in called for called in calls)


def test_failed_revalidation_keeps_last_good_article(tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=start)
    crawl_pending(root=tmp_path, fetcher=_article_fetcher(_html("보존할 본문"), []), now=start)
    original = get_article(event["url"], root=tmp_path)
    enqueue_events([event], root=tmp_path, now=start + timedelta(hours=25))

    def failing(url: str):
        if url.endswith("/robots.txt"):
            return _response(404)
        raise RuntimeError("refresh timeout")

    attempt_time = start + timedelta(hours=25)
    for _ in range(3):
        crawl_pending(root=tmp_path, fetcher=failing, now=attempt_time)
        record = load_index(root=tmp_path)[event["url"]]
        if record["next_attempt_at"]:
            attempt_time = datetime.fromisoformat(record["next_attempt_at"])

    current = get_article(event["url"], root=tmp_path)
    metadata = load_index(root=tmp_path)[event["url"]]
    assert current is not None and current["content_hash"] == original["content_hash"]
    assert metadata["status"] == "ready"
    assert metadata["refresh_pending"] is False
    assert "refresh timeout" in metadata["last_error"]


def test_robots_denial_blocks_article_request(tmp_path):
    event = _event()
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([event], root=tmp_path, now=now)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        assert url.endswith("/robots.txt")
        return _response(200, "User-agent: *\nDisallow: /news/")

    crawl_pending(root=tmp_path, fetcher=fetch, now=now)

    assert calls == ["https://saveticker.com/robots.txt"]
    assert load_index(root=tmp_path)[event["url"]]["status"] == "blocked"


def test_non_allowlisted_host_is_blocked_without_transport_call(tmp_path):
    event = _event(url="https://example.com/news/1")
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([event], root=tmp_path, now=now)
    calls: list[str] = []

    crawl_pending(root=tmp_path, fetcher=lambda url: calls.append(url), now=now)

    assert calls == []
    assert load_index(root=tmp_path)["https://example.com/news/1"]["status"] == "blocked"


def test_worker_lock_prevents_a_second_active_worker(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([_event()], root=tmp_path, now=now)

    with safe_io.file_write_lock(str(tmp_path / "worker-lease"), timeout=0):
        result = crawl_pending(root=tmp_path, fetcher=_article_fetcher(_html("본문"), []), now=now)

    assert result["worker_locked"] is True
    assert result["processed"] == 0
    assert load_index(root=tmp_path)[_event()["url"]]["status"] == "pending"


def test_worker_hard_caps_each_run_at_twenty(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    enqueue_events([_event(i) for i in range(25)], root=tmp_path, now=now)

    result = crawl_pending(
        root=tmp_path,
        limit=100,
        fetcher=_article_fetcher(_html("상한 테스트"), []),
        now=now,
    )

    assert result["processed"] == 20
    assert result["ready"] == 20
    assert result["statuses"] == {"pending": 5, "ready": 20}


def test_status_cli_is_read_only_for_missing_root(tmp_path, capsys):
    missing = tmp_path / "missing-cache"

    assert main(["--status", "--root", str(missing)]) == 0
    payload = __import__("json").loads(capsys.readouterr().out)

    assert payload["total"] == 0
    assert payload["capacity"]["limit"] == 10_000
    assert not missing.exists()


def test_seed_accepts_only_wiki_eligible_articles_on_approved_hosts(monkeypatch):
    monkeypatch.setenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", "news.example")
    accepted = _event(
        url="https://news.example/story/1",
        source="approved_feed",
        classification={"kind": "article", "wiki_eligible": True},
    )
    not_wiki = _event(
        url="https://news.example/story/2",
        classification={"kind": "article", "wiki_eligible": False},
    )
    not_article = _event(
        url="https://news.example/report/3",
        classification={"kind": "report", "wiki_eligible": True},
    )
    not_approved = _event(url="https://unapproved.example/story/4")

    result = _eligible_seed_events([accepted, not_wiki, not_article, not_approved])

    assert result == [accepted]
