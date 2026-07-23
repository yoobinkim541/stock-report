# Source Wiki Curator Design

## Goal

Build the B option: preserve source originals correctly, collect SaveTicker and Telegram news with reliable raw/text artifacts, and maintain keyword/type wiki pages from the collected source cache so AI console answers can use source-backed wiki context.

## Current Findings

- SaveTicker events contain `body_raw` and `text_path`, but `save_raw_artifact(..., suffix=".json")` creates the same path for raw payload and manifest. The manifest overwrites the original JSON.
- SaveTicker collection only reads `news/top-stories` and page 1 of `news/list`, so repeated cron runs accumulate many items but do not intentionally exhaust API pages.
- Telegram `insidertracking` is configured and currently collecting, but it stores message text only in `source-cache` JSONL. It does not create raw vault artifacts.
- Telegram title/body matching can mix messages because jina-derived title lists are later paired with direct HTML bodies by index.
- Wiki auto-curation currently promotes chat-derived reusable decisions. It does not summarize source-cache clusters into source-backed wiki pages.

## Design

### Raw Artifact Safety

`reports.raw_archive.save_raw_artifact` must keep raw payload and manifest as separate files for every suffix. For a JSON raw payload, the raw file remains `.json` and the manifest becomes `.manifest.json`.

### SaveTicker Collection

`reports.source_collector.fetch_saveticker_events` will paginate `news/list` up to `STOCK_COLLECTOR_SAVETICKER_MAX_PAGES`, default 3. It still fetches `news/top-stories` first. Deduplication continues through `append_events`.

### Telegram Collection

Telegram direct HTML parsing will operate per message card. A message record contains `title`, `url`, `body_raw`, and `raw_html` from the same card. Each accepted message is archived under a `telegram:<channel>` source with text sidecar and manifest.

### Source Wiki Curator

Add `reports.source_wiki_curator` as a small source-cache to wiki bridge.

It will:
- Load recent source events.
- Group eligible events by topic, source type, and repeated ticker/theme.
- Generate deterministic wiki pages with stable ids such as `source-topic-ai-semiconductor`.
- Write source-backed pages through `agent_console.wiki.upsert_page`.
- Preserve source references from URLs, text paths, and raw paths.
- Keep pages concise: summary, evidence bullets, and open questions.

### Operational Path

`reports.source_wiki_curator.curate_recent_source_wiki(hours=48)` will be callable from tests, CLI, and later cron wiring. The source collector remains responsible for collecting; the curator is responsible for wiki synthesis.

## Constraints

- No new dependency.
- No vector store yet; keep qmd/local search compatibility through wiki pages.
- Telegram raw artifacts should be bounded and use the existing raw archive TTL policy.
- Source-backed wiki pages must not mark community-only Telegram clusters as `stable`; use `reviewed` for source-backed mixed clusters and `draft` for weak clusters.
