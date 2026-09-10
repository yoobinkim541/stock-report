"""Bounded, crash-recoverable HTTP worker for queued investment articles."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import multiprocessing
import os
import re
import socket
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import certifi
import urllib3
from bs4 import BeautifulSoup

import safe_io
from reports.article_queue import (
    _as_utc,
    _iso,
    _parse_time,
    body_relative_path,
    cache_root,
    canonicalize_url,
    enqueue_events,
    load_index,
    locked_index,
    status_snapshot,
)


USER_AGENT = "StockReportArticleCrawler/1.0"
MAX_RUN_LIMIT = 20
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_WALLCLOCK_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 5.0
BODY_CACHE_MAX_BYTES = 512 * 1024 * 1024
BODY_CACHE_MAX_ARTIFACTS = 10_000
CLAIM_LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30))
HOST_DELAY_SECONDS = 2.0
ALLOWED_HOSTS_ENV = "ARTICLE_CRAWLER_ALLOWED_HOSTS"
DEFAULT_ALLOWED_HOSTS = {"saveticker.com", "www.saveticker.com"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_REMOVE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "iframe", "form", "button", "svg", "canvas")
_NOISE = re.compile(r"(?:^|[-_\s])(ad|ads|advert|cookie|promo|related|share|social|newsletter|sidebar)(?:$|[-_\s])", re.I)


class CrawlOutcome(RuntimeError):
    def __init__(self, status: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.message = message[:500]
        self.retryable = retryable


def _allowed_hosts() -> set[str]:
    configured = {
        value.strip().lower().rstrip(".")
        for value in os.getenv(ALLOWED_HOSTS_ENV, "").split(",")
        if value.strip()
    }
    return DEFAULT_ALLOWED_HOSTS | configured


def _public_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    ip: str | None


def _validate_target(url: str, *, resolve_dns: bool) -> ResolvedTarget:
    canonical = canonicalize_url(url)
    if not canonical:
        raise CrawlOutcome("blocked", "invalid HTTP URL")
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _allowed_hosts():
        raise CrawlOutcome("blocked", f"host is not allowlisted: {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise CrawlOutcome("blocked", "private or non-public address")
    addresses: set[str] = set()
    if resolve_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise CrawlOutcome("failed", f"DNS unavailable: {exc}", retryable=True) from exc
        if not addresses or any(not _public_ip(address) for address in addresses):
            raise CrawlOutcome("blocked", "DNS resolved to a non-public address")
    ordered = sorted(addresses, key=lambda value: (ipaddress.ip_address(value).version, value))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return ResolvedTarget(canonical, host, port, ordered[0] if ordered else None)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CrawlOutcome("failed", "HTTP wall-clock deadline exceeded", retryable=True)
    return remaining


def _response_socket(response):
    connection = getattr(response, "connection", None)
    return getattr(connection, "sock", None)


def _response_encoding(headers: dict) -> str:
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
    match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    return match.group(1).strip("\"'") if match else "utf-8"


def _default_fetch_once(target: ResolvedTarget, *, deadline: float) -> dict:
    """Connect to the validated IP while retaining the URL host as HTTP/TLS identity."""
    if not target.ip:
        raise CrawlOutcome("failed", "validated connection IP is missing", retryable=True)
    parsed = urlsplit(target.url)
    remaining = _remaining(deadline)
    pool_kwargs = {
        "host": target.ip,
        "port": target.port,
        "timeout": urllib3.Timeout(
            total=remaining,
            connect=min(CONNECT_TIMEOUT_SECONDS, remaining),
            read=remaining,
        ),
    }
    if parsed.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            **pool_kwargs,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            server_hostname=target.hostname,
            assert_hostname=target.hostname,
        )
    else:
        pool = urllib3.HTTPConnectionPool(**pool_kwargs)
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = {
        "Host": parsed.netloc,
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    }
    response = None
    try:
        response = pool.request(
            "GET",
            path,
            headers=headers,
            redirect=False,
            retries=False,
            preload_content=False,
            decode_content=False,
            timeout=pool_kwargs["timeout"],
        )
        response_headers = dict(response.headers)
        declared = response_headers.get("Content-Length") or response_headers.get("content-length")
        if declared:
            try:
                if int(declared) > MAX_RESPONSE_BYTES:
                    raise CrawlOutcome("unavailable", "response exceeds size limit")
            except ValueError:
                pass
        chunks = []
        size = 0
        while True:
            remaining = _remaining(deadline)
            sock = _response_socket(response)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read(64 * 1024, decode_content=True)
            _remaining(deadline)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise CrawlOutcome("unavailable", "response exceeds size limit")
            chunks.append(chunk)
        data = b"".join(chunks)
        return {
            "status_code": int(response.status),
            "headers": response_headers,
            "text": data.decode(_response_encoding(response_headers), errors="replace"),
        }
    finally:
        if response is not None:
            response.release_conn()
        close = getattr(pool, "close", None)
        if close:
            close()


def _fetch_process_entry(send_connection, target: ResolvedTarget, deadline: float, result_path: str) -> None:
    """Run one HTTP request in a process that the parent can cancel at its deadline."""
    try:
        result = _default_fetch_once(target, deadline=deadline)
        Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        message = ("ok",)
    except CrawlOutcome as exc:
        message = ("crawl_outcome", exc.status, exc.message, exc.retryable)
    except BaseException as exc:
        message = ("error", type(exc).__name__, str(exc)[:500])
    try:
        send_connection.send(message)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        send_connection.close()


def _stop_fetch_process(process) -> None:
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=0.25)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.25)
    if process.is_alive():  # pragma: no cover - SIGKILL failure is an OS-level fault
        raise RuntimeError("HTTP transport process could not be stopped")
    process.close()


def _default_fetch(target: ResolvedTarget, *, deadline: float) -> dict:
    """Enforce one absolute deadline over DNS-pinned connect, headers, and body."""
    _remaining(deadline)
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    with tempfile.TemporaryDirectory(prefix="article-http-fetch-") as temporary_directory:
        result_path = str(Path(temporary_directory) / "result.json")
        process = context.Process(
            target=_fetch_process_entry,
            args=(send_connection, target, deadline, result_path),
            name="article-http-fetch",
        )
        try:
            process.start()
            send_connection.close()
            if not receive_connection.poll(_remaining(deadline)):
                raise CrawlOutcome("failed", "HTTP wall-clock deadline exceeded", retryable=True)
            try:
                message = receive_connection.recv()
            except EOFError as exc:
                raise CrawlOutcome("failed", "HTTP transport process ended unexpectedly", retryable=True) from exc
            if message[0] == "crawl_outcome":
                raise CrawlOutcome(message[1], message[2], retryable=bool(message[3]))
            if message[0] == "error":
                raise CrawlOutcome("failed", f"HTTP transport failed: {message[1]}: {message[2]}", retryable=True)
            if message[0] != "ok":
                raise CrawlOutcome("failed", "invalid HTTP transport result", retryable=True)
            _remaining(deadline)
            try:
                result = json.loads(Path(result_path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CrawlOutcome("failed", "invalid HTTP transport result", retryable=True) from exc
            _remaining(deadline)
            return result
        finally:
            send_connection.close()
            receive_connection.close()
            _stop_fetch_process(process)


def _normalize_response(response) -> dict:
    if isinstance(response, (str, bytes)):
        status = 200
        headers = {}
        body = response
    elif isinstance(response, dict):
        status = response.get("status_code", response.get("status", 200))
        headers = response.get("headers") or {}
        body = response.get("text", response.get("content", ""))
    else:
        status = getattr(response, "status_code", 200)
        headers = getattr(response, "headers", {}) or {}
        body = getattr(response, "text", getattr(response, "content", ""))
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    text = str(body or "")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise CrawlOutcome("unavailable", "response exceeds size limit")
    normalized_headers = {str(key).lower(): str(value) for key, value in dict(headers).items()}
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        raise CrawlOutcome("failed", "invalid HTTP status", retryable=True)
    return {"status_code": status_code, "headers": normalized_headers, "text": text}


def _rate_limit(url: str, host_last: dict[str, float]) -> None:
    host = (urlsplit(url).hostname or "").lower()
    previous = host_last.get(host)
    if previous is not None:
        remaining = HOST_DELAY_SECONDS - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)
    host_last[host] = time.monotonic()


def _fetch_redirects(
    url: str,
    *,
    fetcher,
    resolve_dns: bool,
    host_last: dict[str, float],
    before_request=None,
) -> tuple[dict, str]:
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        target = _validate_target(current, resolve_dns=resolve_dns)
        if before_request is not None:
            before_request(target.url)
        if resolve_dns:
            _rate_limit(target.url, host_last)
            response = _normalize_response(
                fetcher(target, deadline=time.monotonic() + HTTP_WALLCLOCK_SECONDS)
            )
        else:
            response = _normalize_response(fetcher(target.url))
        if response["status_code"] not in REDIRECT_STATUSES:
            return response, target.url
        location = response["headers"].get("location", "").strip()
        if not location:
            raise CrawlOutcome("unavailable", "redirect has no location")
        if redirect_count >= MAX_REDIRECTS:
            raise CrawlOutcome("blocked", "redirect limit exceeded")
        current = urljoin(target.url, location)
    raise CrawlOutcome("blocked", "redirect limit exceeded")


def _robots_allowed(
    url: str,
    *,
    fetcher,
    resolve_dns: bool,
    host_last: dict[str, float],
    cache: dict[str, RobotFileParser | None],
) -> None:
    parsed = urlsplit(url)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin not in cache:
        robots_url = f"{origin}/robots.txt"
        try:
            response, final_url = _fetch_redirects(
                robots_url, fetcher=fetcher, resolve_dns=resolve_dns, host_last=host_last
            )
        except CrawlOutcome as exc:
            if exc.retryable:
                raise CrawlOutcome(
                    "failed", f"robots unavailable: {exc.message}", retryable=True
                ) from exc
            raise CrawlOutcome("blocked", f"robots unavailable: {exc.message}") from exc
        except Exception as exc:
            raise CrawlOutcome(
                "failed", f"robots unavailable: {exc}", retryable=True
            ) from exc
        status = response["status_code"]
        if status == 404:
            cache[origin] = None
        elif status == 408 or status == 429 or status >= 500:
            raise CrawlOutcome(
                "failed", f"robots unavailable: HTTP {status}", retryable=True
            )
        elif 200 <= status < 300:
            parser = RobotFileParser()
            parser.set_url(final_url)
            parser.parse(response["text"].splitlines())
            cache[origin] = parser
        else:
            raise CrawlOutcome("blocked", f"robots unavailable: HTTP {status}")
    parser = cache[origin]
    if parser is not None and not parser.can_fetch(USER_AGENT, url):
        raise CrawlOutcome("blocked", "robots rules deny crawling")


def _looks_browser_dependent(status: int, text: str) -> bool:
    lower = text.lower()
    markers = (
        "enable javascript", "just a moment", "cf-chl-", "cloudflare", "captcha", "checking your browser",
    )
    return status in {401, 403, 429, 503} and any(marker in lower for marker in markers)


def _extract_article_text(html: str, title: str) -> str:
    if not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(_REMOVE_TAGS):
        element.decompose()
    for element in soup.find_all(True):
        labels = " ".join(
            [str(element.get("id") or ""), *[str(value) for value in (element.get("class") or [])]]
        )
        if labels and _NOISE.search(labels):
            element.decompose()
    main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body
    if main is None:
        return ""
    lines = []
    for raw in main.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    text = "\n".join(lines).strip()
    try:
        from reports.source_collector import _strip_saveticker_boilerplate

        text = _strip_saveticker_boilerplate(text, title)
    except ImportError:
        pass
    return text.strip()


def _fetch_article(record: dict, *, fetcher, resolve_dns: bool, host_last: dict[str, float], robots_cache: dict) -> str:
    def check_robots(url: str) -> None:
        _robots_allowed(
            url,
            fetcher=fetcher,
            resolve_dns=resolve_dns,
            host_last=host_last,
            cache=robots_cache,
        )

    response, _final_url = _fetch_redirects(
        str(record.get("url") or ""),
        fetcher=fetcher,
        resolve_dns=resolve_dns,
        host_last=host_last,
        before_request=check_robots,
    )
    status = response["status_code"]
    text = response["text"]
    if _looks_browser_dependent(status, text):
        raise CrawlOutcome("needs_browser", "page requires a browser")
    if status == 429 or status >= 500:
        raise CrawlOutcome("failed", f"HTTP {status}", retryable=True)
    if not 200 <= status < 300:
        raise CrawlOutcome("unavailable", f"HTTP {status}")
    content_type = response["headers"].get("content-type", "").lower()
    if resolve_dns and not content_type:
        raise CrawlOutcome("unavailable", "missing Content-Type")
    if content_type and "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise CrawlOutcome("unavailable", f"non-HTML content type: {content_type[:100]}")
    cleaned = _extract_article_text(text, str(record.get("title") or ""))
    title = re.sub(r"\s+", " ", str(record.get("title") or "")).strip()
    comparable = re.sub(r"\s+", " ", cleaned).strip()
    if len(comparable) < 120 or (title and comparable == title):
        raise CrawlOutcome("unavailable", "readable article body unavailable")
    return cleaned


def _recover_expired_claims(*, root, now: datetime) -> int:
    recovered = 0
    with locked_index(root=root, now=now) as (_root, index, _pruned):
        for record in index.values():
            claim_id = record.get("claim_id")
            expiry = _parse_time(record.get("claim_expires_at"))
            if not claim_id or not expiry or expiry > now:
                continue
            if record.get("content_hash"):
                record.update(status="ready", refresh_pending=True, next_attempt_at=_iso(now))
            else:
                record.update(status="retry", next_attempt_at=_iso(now))
            record.update(claim_id=None, claim_expires_at=None, last_error="expired worker claim recovered")
            recovered += 1
    return recovered


def _due(record: dict, now: datetime) -> bool:
    if record.get("claim_id"):
        return False
    status = record.get("status")
    selectable = status in {"pending", "retry"} or (status == "ready" and record.get("refresh_pending"))
    if not selectable:
        return False
    due = _parse_time(record.get("next_attempt_at"))
    return due is None or due <= now


def _claim_next(*, root, now: datetime) -> tuple[dict, str, bool] | None:
    with locked_index(root=root, now=now) as (_root, index, _pruned):
        candidates = sorted(
            ((url, record) for url, record in index.items() if _due(record, now)),
            key=lambda item: (str(item[1].get("next_attempt_at") or ""), str(item[1].get("first_discovered_at") or ""), item[0]),
        )
        if not candidates:
            return None
        url, record = candidates[0]
        claim_id = uuid.uuid4().hex
        was_refresh = bool(record.get("content_hash"))
        record["attempts"] = int(record.get("attempts") or 0) + 1
        record["claim_id"] = claim_id
        record["claim_expires_at"] = _iso(now + CLAIM_LEASE)
        record["next_attempt_at"] = None
        if not was_refresh:
            record["status"] = "fetching"
        return dict(record), claim_id, was_refresh


def _body_payload(record: dict, text: str, now: datetime) -> dict:
    return {
        "url": record["url"],
        "title": str(record.get("title") or ""),
        "source": str(record.get("source") or ""),
        "published_at": str(record.get("published_at") or ""),
        "fetched_at": _iso(now),
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _body_files(root_path: Path) -> list[Path]:
    body_root = root_path / "bodies"
    if not body_root.exists():
        return []
    return [path for path in body_root.rglob("*.json") if path.is_file()]


def _referenced_body_paths(root_path: Path) -> set[Path]:
    referenced = set()
    for record in load_index(root=root_path).values():
        relative = Path(str(record.get("body_path") or ""))
        if not relative or relative.is_absolute() or ".." in relative.parts:
            continue
        referenced.add(root_path / relative)
    return referenced


def _body_capacity(root_path: Path) -> dict:
    files = _body_files(root_path)
    used_bytes = sum(path.stat().st_size for path in files)
    return {
        "bytes": used_bytes,
        "byte_limit": BODY_CACHE_MAX_BYTES,
        "available_bytes": max(0, BODY_CACHE_MAX_BYTES - used_bytes),
        "artifacts": len(files),
        "artifact_limit": BODY_CACHE_MAX_ARTIFACTS,
        "available_artifacts": max(0, BODY_CACHE_MAX_ARTIFACTS - len(files)),
    }


def _sweep_body_cache(root_path: Path) -> dict:
    referenced = _referenced_body_paths(root_path)
    removed = 0
    removed_bytes = 0
    for path in _body_files(root_path):
        if path in referenced:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed += 1
        removed_bytes += size
    return {
        "swept_artifacts": removed,
        "swept_bytes": removed_bytes,
        "body_capacity": _body_capacity(root_path),
    }


def _write_body(root_path: Path, record: dict, text: str, now: datetime) -> tuple[str, str, bool]:
    _sweep_body_cache(root_path)
    payload = _body_payload(record, text, now)
    content_hash = str(payload["content_hash"])
    relative = body_relative_path(str(record["url"]), content_hash)
    path = root_path / relative
    existed = path.exists()
    encoded_size = len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    capacity = _body_capacity(root_path)
    previous_size = path.stat().st_size if existed else 0
    projected_bytes = capacity["bytes"] - previous_size + encoded_size
    projected_artifacts = capacity["artifacts"] + (0 if existed else 1)
    if projected_bytes > BODY_CACHE_MAX_BYTES or projected_artifacts > BODY_CACHE_MAX_ARTIFACTS:
        raise CrawlOutcome("capacity", "body cache capacity exceeded")
    safe_io.atomic_write_json(str(path), payload)
    return content_hash, relative, existed


def _finish_success(*, root, url: str, claim_id: str, text: str, snapshot: dict, now: datetime) -> tuple[bool, bool]:
    root_path = cache_root(root, create=True)
    with locked_index(root=root, now=now) as (_root, index, _pruned):
        current = index.get(url)
        if not current or current.get("claim_id") != claim_id:
            return False, False
        current = dict(current)
    content_hash, relative, existed = _write_body(root_path, current, text, now)
    committed = False
    unchanged = bool(current.get("content_hash") == content_hash)
    with locked_index(root=root, now=now) as (_root, index, _pruned):
        record = index.get(url)
        if record and record.get("claim_id") == claim_id:
            record.update(
                status="ready",
                content_hash=content_hash,
                body_path=relative,
                fetched_at=_iso(now),
                completed_at=_iso(now),
                refresh_pending=False,
                next_attempt_at=None,
                last_error="",
                claim_id=None,
                claim_expires_at=None,
            )
            committed = True
    if not committed and not existed:
        try:
            (root_path / relative).unlink()
        except OSError:
            pass
    return committed, unchanged


def _finish_failure(*, root, url: str, claim_id: str, was_refresh: bool, outcome: CrawlOutcome, now: datetime) -> str:
    final_status = outcome.status
    with locked_index(root=root, now=now) as (_root, index, _pruned):
        record = index.get(url)
        if not record or record.get("claim_id") != claim_id:
            return "stale"
        attempts = int(record.get("attempts") or 0)
        retry = outcome.retryable and attempts < MAX_ATTEMPTS
        record.update(last_error=outcome.message, claim_id=None, claim_expires_at=None)
        if retry:
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
            record["next_attempt_at"] = _iso(now + delay)
            if was_refresh:
                record.update(status="ready", refresh_pending=True)
                final_status = "retry"
            else:
                record["status"] = "retry"
                final_status = "retry"
        elif was_refresh:
            record.update(status="ready", refresh_pending=False, next_attempt_at=None, completed_at=_iso(now))
            final_status = outcome.status
        else:
            record.update(status=outcome.status, next_attempt_at=None, completed_at=_iso(now))
    return final_status


def crawl_pending(*, root=None, limit=20, fetcher=None, now=None) -> dict:
    """Claim and crawl at most twenty due URLs with network outside metadata locks."""
    current = _as_utc(now)
    bounded_limit = min(MAX_RUN_LIMIT, max(0, int(limit)))
    result = {
        "processed": 0,
        "ready": 0,
        "unchanged": 0,
        "retries": 0,
        "failed": 0,
        "unavailable": 0,
        "needs_browser": 0,
        "blocked": 0,
        "capacity_errors": 0,
        "stale": 0,
        "recovered_claims": 0,
        "worker_locked": False,
        "swept_artifacts": 0,
        "swept_bytes": 0,
    }
    root_path = cache_root(root, create=True)
    worker_target = root_path / "worker-lease"
    transport = fetcher or _default_fetch
    resolve_dns = fetcher is None
    host_last: dict[str, float] = {}
    robots_cache: dict[str, RobotFileParser | None] = {}
    try:
        with safe_io.file_write_lock(str(worker_target), timeout=0):
            result["recovered_claims"] = _recover_expired_claims(root=root_path, now=current)
            swept = _sweep_body_cache(root_path)
            result["swept_artifacts"] += swept["swept_artifacts"]
            result["swept_bytes"] += swept["swept_bytes"]
            while result["processed"] < bounded_limit:
                claimed = _claim_next(root=root_path, now=current)
                if claimed is None:
                    break
                record, claim_id, was_refresh = claimed
                url = str(record["url"])
                result["processed"] += 1
                try:
                    text = _fetch_article(
                        record,
                        fetcher=transport,
                        resolve_dns=resolve_dns,
                        host_last=host_last,
                        robots_cache=robots_cache,
                    )
                    committed, unchanged = _finish_success(
                        root=root_path,
                        url=url,
                        claim_id=claim_id,
                        text=text,
                        snapshot=record,
                        now=current,
                    )
                    if committed:
                        result["ready"] += 1
                        result["unchanged"] += int(unchanged)
                    else:
                        result["stale"] += 1
                except CrawlOutcome as exc:
                    status = _finish_failure(
                        root=root_path,
                        url=url,
                        claim_id=claim_id,
                        was_refresh=was_refresh,
                        outcome=exc,
                        now=current,
                    )
                    key = (
                        "retries"
                        if status == "retry"
                        else "capacity_errors"
                        if status == "capacity"
                        else status
                    )
                    if key in result:
                        result[key] += 1
                except Exception as exc:
                    outcome = CrawlOutcome("failed", f"{type(exc).__name__}: {exc}", retryable=True)
                    status = _finish_failure(
                        root=root_path,
                        url=url,
                        claim_id=claim_id,
                        was_refresh=was_refresh,
                        outcome=outcome,
                        now=current,
                    )
                    key = (
                        "retries"
                        if status == "retry"
                        else "capacity_errors"
                        if status == "capacity"
                        else status
                    )
                    if key in result:
                        result[key] += 1
            swept = _sweep_body_cache(root_path)
            result["swept_artifacts"] += swept["swept_artifacts"]
            result["swept_bytes"] += swept["swept_bytes"]
            result["body_capacity"] = swept["body_capacity"]
    except safe_io.LockTimeout:
        result["worker_locked"] = True
    if "body_capacity" not in result:
        result["body_capacity"] = _body_capacity(root_path)
    result.update({key: value for key, value in status_snapshot(root=root_path, now=current).items() if key in {"capacity", "retry_counts", "statuses"}})
    return result


def _eligible_seed_events(events: list[dict]) -> list[dict]:
    eligible = []
    allowed = _allowed_hosts()
    for event in events:
        if not isinstance(event, dict):
            continue
        classification = event.get("classification") or {}
        kind = str(classification.get("kind") or "").lower()
        wiki_eligible = classification.get("wiki_eligible") is not False
        url = canonicalize_url(event.get("url"))
        host = (urlsplit(url).hostname or "").lower() if url else ""
        if url and kind == "article" and wiki_eligible and host in allowed:
            eligible.append(event)
    return eligible


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--status", action="store_true", help="print queue status without writes or network")
    parser.add_argument("--root")
    parser.add_argument("--seed-hours", type=int)
    parser.add_argument("--seed-limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(status_snapshot(root=args.root), ensure_ascii=False, sort_keys=True))
        return 0
    seeded = None
    if args.seed_hours is not None:
        from reports.source_collector import DEFAULT_CACHE_DIR, load_recent_events

        events = load_recent_events(
            DEFAULT_CACHE_DIR,
            hours=max(0, args.seed_hours),
            limit=max(0, args.seed_limit),
        )
        seeded = enqueue_events(_eligible_seed_events(events), root=args.root)
    result = crawl_pending(root=args.root, limit=args.limit)
    if seeded is not None:
        result["seed"] = seeded
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
