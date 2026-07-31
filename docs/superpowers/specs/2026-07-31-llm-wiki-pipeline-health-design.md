# LLM Wiki Pipeline Health Design

## Goal

Build an operational health layer for the LLM wiki pipeline so we can tell, on a regular basis, whether:

1. source data collection is working,
2. source-backed wiki pages are being generated and curated,
3. stale, orphaned, unverified, or underused pages are being detected,
4. the AI console can surface the above in one place with actionable warnings.

This is a pipeline-health and recovery design, not a new wiki store or a manual review workflow.

## Current Findings

The repo already has the pieces, but they are split across too many surfaces:

- `reports/source_collector.py` collects raw source events and writes `source_health.json`.
- `reports/source_wiki_curator.py` turns recent source events into source-backed wiki pages.
- `reports/wiki_distillation.py` promotes source digests into reusable playbook/risk/concept cards.
- `agent_console/wiki.py` already exposes `stats()`, `lint_pages()`, `list_stale_pages()`, `list_unused_pages()`, and `archive_stale_pages()`.
- `reports/wiki_health_check.py` already runs a health check, but it mostly prints a summary and does not give the AI console a structured operational model.
- `dashboard/pages/ai_console.py` already has a wiki tab and a memory tab, but it does not show a compact pipeline health panel that ties collection failures to wiki quality problems.

The weak spots are:

- we do not have one structured model that joins source collection health and wiki health,
- source health is visible as a raw `source_health.json` dump, but not as a small set of actionable flags,
- wiki health can detect stale or unverified pages, but not whether those pages are being replenished by source collection,
- source-digest promotion exists, but there is no single “is the wiki absorbing new source material?” summary,
- there is no console surface that tells us whether the failure is in collection, curation, or retention.

## Design

### 1. Add a structured pipeline-health model

Create a new module, `reports/wiki_pipeline_health.py`, that reads:

- `reports.source_collector.load_source_health()`
- `reports.source_collector.stale_sources()`
- `reports.source_collector.load_recent_events()`
- `agent_console.wiki.stats()`
- `agent_console.wiki.lint_pages()`
- `agent_console.wiki.list_stale_pages()`
- `agent_console.wiki.list_unused_pages()`

and returns a single structured report with these sections:

- `source_health`
- `wiki_health`
- `curation_health`
- `recommendations`

The model should normalize:

- per-source last run, last success, last count, and last error,
- per-source zero-event streaks,
- source-backed vs unverified page counts,
- stale, unused, archived, and open-question counts,
- source-digest pages that have not yet been linked to judgment pages,
- a small list of prioritized action items.

The existing `reports/wiki_health_check.py` cron entry should consume this report builder so the
same scheduled job continues to run every 2 hours, but now emits a structured model instead of only
a flat text summary. The cron cadence itself stays unchanged.

### 2. Add explicit curation health checks

The new health layer should not just count pages. It should answer:

- Are source-backed pages being created?
- Are source digests being distilled into reusable judgment pages?
- Are reviewed/stable pages missing non-conversation source refs?
- Are there pages with too many open questions or no recent use?
- Are there source digests that are still isolated from playbooks/risk/concept cards?

This should be implemented as deterministic checks over the existing wiki records, not as a new LLM summarizer.

### 3. Surface the health model in AI Console

Add a compact “wiki pipeline health” panel in the AI console, likely under the AI Wiki tab or a nearby operational tab.

The panel should show:

- source collection status by source,
- stale source warnings,
- wiki total / source-backed / unverified counts,
- stale / unused / archived counts,
- unresolved source-digest counts,
- top recommendations.

The goal is to make it obvious whether the problem is:

- collection failure,
- curation failure,
- or retention / hygiene failure.

### 4. Keep existing curator behavior, but add safety rails

Do not replace the existing source curator or distiller. Instead:

- keep `source_wiki_curator` as the source-to-source-digest bridge,
- keep `wiki_distillation` as the source-digest-to-judgment bridge,
- use the new health layer to detect when those bridges are not active enough.

The health layer may also expose a “ready for promotion” list, but it should not auto-promote pages on its own in this change set.

### 5. Expand source collection visibility

The source-health model should include a small source coverage snapshot:

- `saveticker`
- `telegram:*`
- `arca`
- `polymarket`
- `yahoo_finance`
- `fred`
- `worldgovernmentbonds`

For each source, track:

- last success timestamp,
- last event count,
- last error,
- whether it is currently stale,
- whether it produced raw artifacts when expected.

This makes data collection problems visible without requiring deep log digging.

## Implementation Plan

1. Add `reports/wiki_pipeline_health.py` with a pure report builder.
2. Wire `reports/wiki_health_check.py` to the report builder so the scheduled health job emits the structured model.
3. Add dashboard/cache wrappers so the AI console can render the report cheaply.
4. Add a small health panel to the AI console.
5. Add focused tests for source-health aggregation, wiki-health aggregation, recommendation generation, scheduled health output, and UI-safe rendering.
6. If tests reveal a data-collection gap, fix the gap in the collector rather than hiding it in the UI.

## Success Criteria

- The repo can produce a single structured wiki pipeline health report.
- The scheduled wiki health job can emit the same report structure every 2 hours.
- The AI console can display source health, wiki health, and top recommendations together.
- The report flags stale sources, unverified or source-less promoted pages, and source digests that have not yet become reusable judgment pages.
- Tests cover the report model and the console rendering path.
- Existing wiki generation, distillation, and archive behavior still pass their current tests.

## Non-Goals

- No new database.
- No Obsidian integration.
- No manual approval workflow.
- No change to the existing trust model for source-backed vs conversation-only pages.
- No automatic page promotion beyond the existing curator/distiller flows.

## Risks

- A noisy health panel could overwhelm the console if too many checks are shown at once.
- A stale-source warning can be useful but needs a short grace period so new or intermittent sources do not look broken immediately.
- The report should remain cheap to compute; it must reuse existing health files and cached wiki records instead of re-parsing raw archives on every render.
