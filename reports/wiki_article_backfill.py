"""Plan and seed a bounded crawl of URLs already cited by wiki pages.

Planning is read-only. Seeding only writes the article queue and a review
manifest; network access requires the separate explicit --crawl option.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import safe_io
from reports import article_crawler, article_queue


MAX_PAGE_LIMIT = 10_000
MAX_SEED_LIMIT = 500
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "secret",
    "session",
    "sig",
    "signature",
    "token",
}
_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"


def _as_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _refs(page: dict) -> list[str]:
    raw = page.get("source_refs") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(value or "").strip() for value in raw if str(value or "").strip()]



def _sensitive_query(url: str) -> bool:
    try:
        keys = {key.strip().lower() for key, _value in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    except ValueError:
        return True
    return any(
        key in SENSITIVE_QUERY_KEYS
        or key.endswith("_token")
        or key.endswith("_secret")
        or key.endswith("_key")
        for key in keys
    )


def _manifest_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (
            key.strip().lower() in SENSITIVE_QUERY_KEYS
            or key.strip().lower().endswith(("_token", "_secret", "_key"))
        )
    ]
    from urllib.parse import urlencode

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))


def plan_backfill(
    pages,
    *,
    root=None,
    allowed_hosts: set[str] | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> dict:
    """Build a no-write/no-network plan for unique HTTP(S) wiki references."""
    current = _as_utc(now)
    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in (allowed_hosts if allowed_hosts is not None else article_crawler._allowed_hosts())
        if str(host).strip()
    }
    page_list = [page for page in pages or [] if isinstance(page, dict)]
    discovered: dict[str, dict] = {}
    url_reference_count = invalid_ref_count = 0
    for page in sorted(
        page_list,
        key=lambda row: (str(row.get("id") or ""), str(row.get("title") or "")),
    ):
        page_id = str(page.get("id") or "").strip()[:120]
        title = str(page.get("title") or "").strip()[:240]
        for ref in _refs(page):
            matches = _URL_PATTERN.findall(ref)
            if not matches:
                continue
            for match in matches:
                raw_url = match.rstrip(_TRAILING_URL_PUNCTUATION)
                canonical = article_queue.canonicalize_url(raw_url)
                if not canonical:
                    invalid_ref_count += 1
                    continue
                url_reference_count += 1
                row = discovered.setdefault(
                    canonical,
                    {
                        "url": canonical,
                        "host": (urlsplit(canonical).hostname or "").lower(),
                        "title": title,
                        "source_page_ids": [],
                        "source_page_statuses": {},
                    },
                )
                if page_id and page_id not in row["source_page_ids"]:
                    row["source_page_ids"].append(page_id)
                if page_id:
                    row["source_page_statuses"][page_id] = str(page.get("status") or "draft")[:40]

    current_index = copy.deepcopy(article_queue.load_index(root=root))
    article_queue._prune_expired(current_index, current)
    queue_capacity = max(0, article_queue.MAX_INDEX_RECORDS - len(current_index))
    blocked_by_host: Counter[str] = Counter()
    host_counts: Counter[str] = Counter()
    sensitive_url_count = 0
    already_queued_count = 0
    eligible = []
    records = []
    for url in sorted(discovered):
        row = discovered[url]
        host = row["host"]
        host_counts[host] += 1
        is_sensitive = _sensitive_query(url)
        in_queue = url in current_index
        if is_sensitive:
            decision = "sensitive_query"
            sensitive_url_count += 1
        elif host not in hosts:
            decision = "host_not_allowlisted"
            blocked_by_host[host] += 1
        elif in_queue:
            decision = "already_queued"
            already_queued_count += 1
        else:
            decision = "eligible"
            eligible.append(row)
        records.append({
            "url": _manifest_url(url),
            "host": host,
            "decision": decision,
            "existing_status": str((current_index.get(url) or {}).get("status") or ""),
            "source_page_ids": row["source_page_ids"],
            "source_page_statuses": row["source_page_statuses"],
        })

    bounded_limit = min(MAX_SEED_LIMIT, max(0, int(limit)))
    available = min(queue_capacity, bounded_limit)
    selected_urls = {row["url"] for row in eligible[:available]}
    for record in records:
        if record["decision"] == "eligible":
            record["decision"] = "selected" if record["url"] in selected_urls else (
                "queue_capacity" if queue_capacity == 0 else "deferred_by_limit"
            )
    events = []
    for row in eligible[:available]:
        page_ids = row["source_page_ids"]
        events.append({
            "id": f"wiki-ref:{page_ids[0]}" if page_ids else "",
            "url": row["url"],
            "title": row["title"] or row["host"],
            "source": "wiki-backfill",
        })

    manifest = {
        "created_at": current.isoformat(timespec="seconds"),
        "purpose": "wiki_source_reference_article_backfill",
        "pages_considered": len(page_list),
        "url_reference_count": url_reference_count,
        "unique_url_count": len(discovered),
        "invalid_ref_count": invalid_ref_count,
        "sensitive_url_count": sensitive_url_count,
        "already_queued_count": already_queued_count,
        "eligible_count": len(eligible),
        "selected_count": len(events),
        "queue_capacity_available": queue_capacity,
        "seed_limit": bounded_limit,
        "allowlisted_hosts": sorted(hosts),
        "blocked_by_host": dict(sorted(blocked_by_host.items())),
        "host_counts": dict(sorted(host_counts.items())),
        "records": records,
    }
    return {
        **{key: value for key, value in manifest.items() if key != "records"},
        "eligible_count": len(eligible),
        "selected_count": len(events),
        "events": events,
        "manifest": manifest,
    }


def seed_backfill(
    pages,
    *,
    root=None,
    allowed_hosts: set[str] | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> dict:
    """Persist a review manifest, then enqueue the selected URLs without fetching."""
    plan = plan_backfill(
        pages,
        root=root,
        allowed_hosts=allowed_hosts,
        limit=limit,
        now=now,
    )
    root_path = article_queue.cache_root(root, create=True)
    manifest_dir = root_path / "backfill-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _as_utc(now).strftime("%Y%m%dT%H%M%SZ")
    manifest_file = manifest_dir / f"wiki-refs-{stamp}.json"
    if manifest_file.exists():
        raise FileExistsError("backfill manifest already exists for this timestamp")
    safe_io.atomic_write_json(str(manifest_file), plan["manifest"])
    queued = article_queue.enqueue_events(plan["events"], root=root, now=now)
    return {
        **{key: value for key, value in plan.items() if key not in {"events", "manifest"}},
        "seed": queued,
        "manifest_path": str(Path("backfill-manifests") / manifest_file.name),
    }


def _load_pages() -> list[dict]:
    from agent_console import wiki

    return wiki.list_pages(query="", surface="all", status="all", limit=MAX_PAGE_LIMIT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="read wiki refs and queue status without writes or network")
    parser.add_argument("--seed", action="store_true", help="write a review manifest and enqueue refs, without fetching")
    parser.add_argument("--crawl", action="store_true", help="run the crawler; requires explicit --crawl")
    parser.add_argument("--root")
    parser.add_argument("--seed-limit", type=int, default=100)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    article_crawler._load_cli_env()
    if args.plan and (args.seed or args.crawl):
        parser.error("--plan cannot be combined with --seed or --crawl")
    if not (args.plan or args.seed or args.crawl):
        parser.error("choose --plan, --seed, and/or --crawl")

    if args.plan:
        result = plan_backfill(_load_pages(), root=args.root, limit=args.seed_limit)
        print(json.dumps(
            {key: value for key, value in result.items() if key != "events"},
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 0

    result = {}
    if args.seed:
        result["seed"] = seed_backfill(
            _load_pages(),
            root=args.root,
            limit=args.seed_limit,
        )
    if args.crawl:
        result["crawl"] = article_crawler.crawl_pending(
            root=args.root,
            limit=args.limit,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
