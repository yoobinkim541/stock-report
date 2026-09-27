# Wiki source-reference article backfill

This tool adds a bounded path for collecting readable bodies from URLs already
cited by wiki pages. It does not rewrite wiki pages. The normal distillation
pipeline may use a cached ready body later, while failed, blocked, or
browser-dependent pages remain unqualified.

## Safe sequence

First inspect a read-only plan. It reports unique URL counts by host, queued
state, URLs held back by the explicit host allowlist, and URLs skipped because
their query string appears to contain credentials. It does not create the
article cache, contact a site, or write a manifest.

~~~bash
uv run python -m reports.wiki_article_backfill --plan
~~~

After reviewing the host counts, explicitly seed a bounded batch. Seeding writes
a timestamped manifest under article-cache/backfill-manifests/ before
enqueueing any URLs. It does not make HTTP requests.

~~~bash
uv run python -m reports.wiki_article_backfill --seed --seed-limit 100
~~~

Run network requests only as a separate, explicit step:

~~~bash
uv run python -m reports.wiki_article_backfill --crawl --limit 20
~~~

The queue is URL-deduplicated, so repeating --seed skips URLs that are already
in the queue and advances to the next eligible URLs. Review each batch manifest
and status before continuing.

## Source and access policy

The input is the existing source_refs field from every wiki status, including
archived pages. Only canonical HTTP(S) references on ARTICLE_CRAWLER_ALLOWED_HOSTS
or the existing built-in SaveTicker allowlist are queued. The plan lists other
hosts as blocked; it never expands the allowlist automatically. Sensitive query
parameters are not fetched and their values are removed from the local manifest.

The worker still enforces public-IP/SSRF checks, robots.txt, redirect and byte
limits, request deadlines, host pacing, and article-body quality checks. It
does not send cookies, credentials, or browser sessions and does not bypass
site restrictions. Queue ready means a readable body was cached, not that the
wiki article has been rewritten or independently fact-checked.

A later live run requires explicit operator review of the plan, manifest,
allowlist, request volume, and resulting ready/blocked counts. No cron or
production runtime data is changed by this code change.
