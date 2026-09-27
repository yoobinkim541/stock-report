from __future__ import annotations

import multiprocessing
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import safe_io
import reports.article_crawler as crawler

from reports.article_crawler import _eligible_seed_events, crawl_pending, import_ready_events, main
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


def _active_fetch_children():
    return [child for child in multiprocessing.active_children() if child.name == "article-http-fetch"]


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


def test_targeted_crawl_processes_only_requested_urls(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    first = _event(1)
    second = _event(2)
    enqueue_events([first, second], root=tmp_path, now=now)
    calls: list[str] = []

    result = crawl_pending(
        root=tmp_path,
        limit=20,
        urls=[second["url"]],
        fetcher=_article_fetcher(_html("선택 본문"), calls),
        now=now,
    )

    index = load_index(root=tmp_path)
    assert result["processed"] == 1
    assert index[first["url"]]["status"] == "pending"
    assert index[second["url"]]["status"] == "ready"
    assert second["url"] in calls
    assert first["url"] not in calls


def test_import_ready_events_promotes_existing_raw_body_without_http(tmp_path):
    event = _event(
        body_raw=("원문 저장소에 이미 보관된 기사 본문입니다. " * 30).strip(),
    )

    result = import_ready_events([event], root=tmp_path, now=datetime(2026, 9, 9, tzinfo=UTC))
    article = get_article(event["url"], root=tmp_path)

    assert result["imported"] == 1
    assert result["network_requests"] == 0
    assert article is not None
    assert article["text"] == event["body_raw"]
    record = load_index(root=tmp_path)[event["url"]]
    assert record["body_origin"] == "raw_source_event"
    assert record["raw_event_id"] == event["id"]


def test_extract_article_text_tolerates_tags_without_attrs(monkeypatch):
    class TagWithoutAttrs:
        attrs = None

        def get_text(self, *_args, **_kwargs):
            return "속성 없는 태그에서도 본문을 추출합니다. " * 20

    class FakeSoup:
        body = TagWithoutAttrs()

        def find_all(self, selector):
            return [] if selector != True else [TagWithoutAttrs()]

        def find(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(crawler, "BeautifulSoup", lambda *_args, **_kwargs: FakeSoup())

    text = crawler._extract_article_text("<html></html>", "제목")

    assert len(text) >= 120


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


def test_default_allowlist_covers_wiki_source_hosts(monkeypatch):
    monkeypatch.delenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", raising=False)

    hosts = crawler._allowed_hosts()

    assert {
        "saveticker.com",
        "www.saveticker.com",
        "arca.live",
        "t.me",
        "kalshi.com",
        "polymarket.com",
        "finance.yahoo.com",
        "www.sec.gov",
        "fred.stlouisfed.org",
        "www.worldgovernmentbonds.com",
    } <= hosts


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


def test_seed_accepts_discoverable_articles_on_approved_hosts(monkeypatch):
    monkeypatch.setenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", "news.example")
    accepted = _event(
        url="https://news.example/story/1",
        source="approved_feed",
        classification={"kind": "article", "wiki_eligible": True},
    )
    explicitly_excluded = _event(
        url="https://news.example/story/2",
        classification={
            "kind": "article",
            "wiki_eligible": False,
            "article_discovery_eligible": False,
        },
    )
    short_discovery = _event(
        url="https://saveticker.com/news/5",
        classification={"kind": "article", "wiki_eligible": False},
    )
    analysis = _event(
        url="https://news.example/story/6",
        source="approved_feed",
        classification={"source_family": "news", "kind": "analysis", "wiki_eligible": True},
    )
    not_article = _event(
        url="https://news.example/report/3",
        classification={"kind": "report", "wiki_eligible": True},
    )
    not_approved = _event(url="https://unapproved.example/story/4")

    result = _eligible_seed_events([
        accepted,
        explicitly_excluded,
        short_discovery,
        analysis,
        not_article,
        not_approved,
    ])

    assert result == [accepted, short_discovery, analysis]


def test_cli_loads_repo_dotenv_for_env_only_cache_and_allowed_hosts(tmp_path, monkeypatch, capsys):
    article_root = tmp_path / "article-cache"
    enqueue_events([_event()], root=article_root)
    (tmp_path / ".env").write_text(
        f"ARTICLE_CACHE_DIR={article_root}\nARTICLE_CRAWLER_ALLOWED_HOSTS=example.com\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ARTICLE_CACHE_DIR", raising=False)
    monkeypatch.delenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(crawler, "__file__", str(tmp_path / "reports" / "article_crawler.py"))

    assert main(["--status"]) == 0

    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["total"] == 1
    assert "example.com" in crawler._allowed_hosts()


def test_default_https_transport_connects_to_pinned_ip_with_original_tls_identity(monkeypatch):
    captured = {}

    class Socket:
        def settimeout(self, value):
            captured["socket_timeout"] = value

    class Response:
        status = 200
        headers = {"Content-Type": "text/html"}
        connection = type("Connection", (), {"sock": Socket()})()

        def __init__(self):
            self._chunks = [b"<html>ok</html>", b""]

        def read(self, _amount, decode_content=True):
            return self._chunks.pop(0)

        def release_conn(self):
            captured["released"] = True

    class Pool:
        def __init__(self, host, port, **kwargs):
            captured.update(host=host, port=port, pool_kwargs=kwargs)

        def request(self, method, path, **kwargs):
            captured.update(method=method, path=path, request_kwargs=kwargs)
            return Response()

    monkeypatch.setattr(crawler.urllib3, "HTTPSConnectionPool", Pool)
    target = crawler.ResolvedTarget(
        url="https://saveticker.com/news/1?x=1",
        hostname="saveticker.com",
        port=443,
        ip="203.0.113.10",
    )

    response = crawler._default_fetch_once(target, deadline=crawler.time.monotonic() + 15)

    assert captured["host"] == "203.0.113.10"
    assert captured["pool_kwargs"]["server_hostname"] == "saveticker.com"
    assert captured["pool_kwargs"]["assert_hostname"] == "saveticker.com"
    assert captured["request_kwargs"]["headers"]["Host"] == "saveticker.com"
    assert captured["path"] == "/news/1?x=1"
    assert response["text"] == "<html>ok</html>"
    assert captured["released"] is True


def test_default_transport_enforces_total_deadline_during_slow_trickle(monkeypatch):
    clock = [100.0]
    released = []

    class Socket:
        def settimeout(self, _value):
            pass

    class Response:
        status = 200
        headers = {"Content-Type": "text/html"}
        connection = type("Connection", (), {"sock": Socket()})()

        def read(self, _amount, decode_content=True):
            clock[0] += 2.0
            return b"slow"

        def release_conn(self):
            released.append(True)

    class Pool:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(crawler.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(crawler.urllib3, "HTTPSConnectionPool", Pool)
    target = crawler.ResolvedTarget(
        url="https://saveticker.com/news/1",
        hostname="saveticker.com",
        port=443,
        ip="203.0.113.10",
    )

    with pytest.raises(crawler.CrawlOutcome, match="deadline"):
        crawler._default_fetch_once(target, deadline=103.0)

    assert released == [True]


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_default_transport_interrupts_real_slow_stream_at_wallclock_deadline(phase):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]

    def serve_slow_response():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 64\r\n\r\n"
                if phase == "headers":
                    for byte in headers:
                        connection.sendall(bytes([byte]))
                        time.sleep(0.03)
                else:
                    connection.sendall(headers)
                    for _ in range(64):
                        connection.sendall(b"x")
                        time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            listener.close()

    server = threading.Thread(target=serve_slow_response, daemon=True)
    server.start()
    target = crawler.ResolvedTarget(
        url=f"http://localhost:{port}/slow",
        hostname="localhost",
        port=port,
        ip="127.0.0.1",
    )
    started = time.monotonic()

    with pytest.raises(crawler.CrawlOutcome, match="deadline"):
        crawler._default_fetch(target, deadline=started + 0.50)

    elapsed = time.monotonic() - started
    assert elapsed < 0.90
    server.join(timeout=2)
    assert not server.is_alive()
    assert _active_fetch_children() == []


def test_default_transport_returns_large_body_without_pipe_deadlock_and_cleans_child():
    body = b"x" * (512 * 1024)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve_large_response():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                headers = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                    + str(len(body)).encode("ascii")
                    + b"\r\n\r\n"
                )
                connection.sendall(headers + body)
        finally:
            listener.close()

    server = threading.Thread(target=serve_large_response, daemon=True)
    server.start()
    target = crawler.ResolvedTarget(
        url=f"http://localhost:{port}/large",
        hostname="localhost",
        port=port,
        ip="127.0.0.1",
    )

    response = crawler._default_fetch(target, deadline=time.monotonic() + 5)

    assert response["text"] == body.decode("ascii")
    server.join(timeout=2)
    assert not server.is_alive()
    assert _active_fetch_children() == []


def test_real_transport_rejects_missing_content_type(monkeypatch, tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    monkeypatch.setattr(crawler, "HOST_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        crawler.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    def fake_default(target, **_kwargs):
        url = getattr(target, "url", target)
        if url.endswith("/robots.txt"):
            return _response(404)
        return _response(200, _html("헤더 없는 실제 응답"))

    monkeypatch.setattr(crawler, "_default_fetch", fake_default)

    result = crawl_pending(root=tmp_path, now=now)

    assert result["unavailable"] == 1
    assert get_article(event["url"], root=tmp_path) is None


def test_redirect_target_robots_is_checked_before_fetching_disallowed_path(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    calls = []

    def fetch(url: str):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response(200, "User-agent: *\nDisallow: /private/")
        if url.endswith("/news/1"):
            return _response(302, Location="/private/2")
        raise AssertionError("disallowed redirect target was fetched")

    result = crawl_pending(root=tmp_path, fetcher=fetch, now=now)

    assert result["blocked"] == 1
    assert "https://saveticker.com/private/2" not in calls


def test_transient_robots_failure_fails_closed_but_remains_retryable(tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    calls = []

    def fetch(url: str):
        calls.append(url)
        assert url.endswith("/robots.txt")
        return _response(503, "temporarily unavailable")

    result = crawl_pending(root=tmp_path, fetcher=fetch, now=now)
    metadata = load_index(root=tmp_path)[event["url"]]

    assert result["retries"] == 1
    assert calls == ["https://saveticker.com/robots.txt"]
    assert metadata["status"] == "retry"
    assert metadata["next_attempt_at"] is not None
    assert "robots unavailable" in metadata["last_error"]


def test_cross_host_redirect_resolves_and_checks_new_robots_before_body(monkeypatch, tmp_path):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=now)
    monkeypatch.setenv("ARTICLE_CRAWLER_ALLOWED_HOSTS", "news.example")
    monkeypatch.setattr(crawler, "HOST_DELAY_SECONDS", 0)
    resolved = {
        "saveticker.com": "93.184.216.34",
        "news.example": "142.250.72.14",
    }
    monkeypatch.setattr(
        crawler.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [(2, 1, 6, "", (resolved[host], port))],
    )
    calls = []

    def fake_default(target, **_kwargs):
        calls.append((target.url, target.ip))
        if target.url == "https://saveticker.com/robots.txt":
            return _response(404)
        if target.url == event["url"]:
            return _response(302, Location="https://news.example/private/2")
        if target.url == "https://news.example/robots.txt":
            return _response(200, "User-agent: *\nDisallow: /private/")
        raise AssertionError("cross-host disallowed body was fetched")

    monkeypatch.setattr(crawler, "_default_fetch", fake_default)

    result = crawl_pending(root=tmp_path, now=now)

    assert result["blocked"] == 1
    assert calls == [
        ("https://saveticker.com/robots.txt", "93.184.216.34"),
        ("https://saveticker.com/news/1", "93.184.216.34"),
        ("https://news.example/robots.txt", "142.250.72.14"),
    ]


def test_same_url_rediscovery_during_fetch_returns_latest_metadata(tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=start)
    entered = threading.Event()
    release = threading.Event()

    def fetch(url: str):
        if url.endswith("/robots.txt"):
            return _response(404)
        entered.set()
        assert release.wait(timeout=5)
        return _response(200, _html("최신 메타데이터 본문"), **{"Content-Type": "text/html"})

    worker = threading.Thread(target=lambda: crawl_pending(root=tmp_path, fetcher=fetch, now=start))
    worker.start()
    assert entered.wait(timeout=5)
    enqueue_events(
        [_event(id="event-new", title="갱신된 제목", source="saveticker:revised", published_at="2026-09-09T01:00:00+00:00")],
        root=tmp_path,
        now=start + timedelta(minutes=1),
    )
    release.set()
    worker.join(timeout=5)

    article = get_article(event["url"], root=tmp_path)
    metadata = load_index(root=tmp_path)[event["url"]]
    assert not worker.is_alive()
    assert article["title"] == "갱신된 제목"
    assert article["source"] == "saveticker:revised"
    assert article["published_at"] == "2026-09-09T01:00:00+00:00"
    assert metadata["event_ids"] == ["event-1", "event-new"]


def test_body_artifact_budget_rejects_growth_and_preserves_last_good(monkeypatch, tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=start)
    crawl_pending(root=tmp_path, fetcher=_article_fetcher(_html("원래 본문"), []), now=start)
    original = get_article(event["url"], root=tmp_path)
    original_files = list((tmp_path / "bodies").rglob("*.json"))
    monkeypatch.setattr(crawler, "BODY_CACHE_MAX_ARTIFACTS", 10)
    monkeypatch.setattr(crawler, "BODY_CACHE_MAX_BYTES", original_files[0].stat().st_size)
    enqueue_events([event], root=tmp_path, now=start + timedelta(hours=25))

    result = crawl_pending(
        root=tmp_path,
        fetcher=_article_fetcher(_html("용량을 넘는 변경 본문"), []),
        now=start + timedelta(hours=25),
    )

    current = get_article(event["url"], root=tmp_path)
    assert result["capacity_errors"] == 1
    assert current["content_hash"] == original["content_hash"]
    assert len(list((tmp_path / "bodies").rglob("*.json"))) == 1
    assert "capacity" in load_index(root=tmp_path)[event["url"]]["last_error"]


def test_worker_sweeps_only_unreferenced_body_artifacts(monkeypatch, tmp_path):
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    enqueue_events([event], root=tmp_path, now=start)
    crawl_pending(root=tmp_path, fetcher=_article_fetcher(_html("참조 본문"), []), now=start)
    referenced = list((tmp_path / "bodies").rglob("*.json"))[0]
    orphan = tmp_path / "bodies" / "orphan.json"
    orphan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(crawler, "BODY_CACHE_MAX_ARTIFACTS", 1)
    monkeypatch.setattr(crawler, "BODY_CACHE_MAX_BYTES", referenced.stat().st_size)

    result = crawl_pending(root=tmp_path, limit=0, fetcher=lambda _url: None, now=start)

    assert referenced.exists()
    assert not orphan.exists()
    assert result["swept_artifacts"] == 1
    assert result["body_capacity"]["artifacts"] == 1
