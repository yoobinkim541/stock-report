# Source Wiki Curator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve raw news sources correctly and synthesize collected source-cache events into source-backed wiki pages.

**Architecture:** Keep collection, raw artifact storage, and wiki synthesis as separate units. `reports.source_collector` extracts events and archives raw/text artifacts; `reports.source_wiki_curator` reads recent events and writes deterministic wiki pages through `agent_console.wiki`.

**Tech Stack:** Python 3.11, pytest, BeautifulSoup, existing JSONL source-cache, existing agent_console wiki/shared-memory store.

## Global Constraints

- No new dependency.
- Use existing `reports.raw_archive` for raw/text/manifest artifact storage.
- Use existing `agent_console.wiki.upsert_page` for wiki writes.
- Use TDD: write failing tests before implementation.
- Keep raw payloads bounded through existing collector limits.

---

### Task 1: Raw Archive JSON Manifest Separation

**Files:**
- Modify: `reports/raw_archive.py`
- Modify: `tests/test_raw_archive.py`

**Interfaces:**
- Consumes: `save_raw_artifact(source, kind, fetched_at, title, url, payload, suffix, ttl_days=None) -> dict`
- Produces: `raw_path` and `manifest_path` that are different paths even when `suffix=".json"`

- [ ] Write `test_save_raw_artifact_keeps_json_raw_and_manifest_separate`.
- [ ] Run that single test and verify it fails because both paths are equal or raw content is manifest content.
- [ ] Change `manifest_path` to `base_name + ".manifest.json"`.
- [ ] Run `tests/test_raw_archive.py`.

### Task 2: Telegram Message-Scoped Raw Collection

**Files:**
- Modify: `reports/source_collector.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Produces: `_telegram_messages_from_html(html_text: str, channel: str) -> list[dict]`
- Produces: `fetch_telegram_channel_events(channels: list[str]) -> list[dict]` events with matching title/body/url and raw/text/manifest paths

- [ ] Write parser test proving two message cards keep their own title, body, url, and raw_html.
- [ ] Write fetch test proving archived Telegram events include `raw_path`, `text_path`, and `manifest_path`.
- [ ] Run the tests and verify RED.
- [ ] Implement message-card parser and raw artifact save helper.
- [ ] Run source collector tests.

### Task 3: SaveTicker Pagination

**Files:**
- Modify: `reports/source_collector.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Consumes env `STOCK_COLLECTOR_SAVETICKER_MAX_PAGES`
- Produces multiple `news/list` page requests until configured page cap, empty page, or duplicate exhaustion

- [ ] Write test with fake `requests.get` proving pages 1 and 2 are fetched when max pages is 2.
- [ ] Run the test and verify RED.
- [ ] Add max page helper and generate `news/list` requests for each page.
- [ ] Run source collector tests.

### Task 4: Source Cache to Wiki Curator

**Files:**
- Create: `reports/source_wiki_curator.py`
- Create: `tests/test_source_wiki_curator.py`

**Interfaces:**
- Produces: `build_wiki_pages_from_events(events: list[dict], now: datetime | None = None) -> list[dict]`
- Produces: `curate_recent_source_wiki(hours: int = 48, limit: int = 8) -> dict`

- [ ] Write test proving repeated AI/semiconductor SaveTicker and Telegram events create one source-backed topic page.
- [ ] Write test proving source refs include URLs and text/raw paths without duplicates.
- [ ] Run tests and verify RED.
- [ ] Implement deterministic grouping and wiki payload generation.
- [ ] Run curator tests.

### Task 5: Verification and Integration

**Files:**
- Modify as needed: `deploy/crontab.stock-report`
- Modify as needed: `docs/agent-console.md`

**Interfaces:**
- `python -m reports.source_wiki_curator --hours 48 --limit 8` can update wiki pages after source collection.

- [ ] Add CLI main to curator.
- [ ] Add optional cron line after source collection if an existing source collector cron is present.
- [ ] Run `pytest tests/test_raw_archive.py tests/test_source_collector.py tests/test_source_wiki_curator.py tests/test_agent_console.py tests/test_wiki_browser.py tests/test_qmd_search.py -q`.
- [ ] Run `python -m reports.source_wiki_curator --hours 48 --limit 8`.
