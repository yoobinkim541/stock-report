# Wiki Pipeline Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore real LLM news labeling, make blocked source providers observable without false freshness alarms, and drain only actionable wiki distillation backlog while preserving evidence and legitimate skips.

**Architecture:** Keep provider isolation and append-only raw data. Separate the news label model from the general agent model, classify provider HTTP blocks at the subprocess boundary, and make wiki distillation state-aware: archived judgment targets are re-created, missing article bodies can fall back to the stored source digest, and intentional skips are not treated as pending work.

**Tech Stack:** Python 3.11, pytest, JSONL source cache, Streamlit/agent wiki storage, Codex/Hermes CLI providers, existing cron modules.

**Spec:** User request in the active conversation: audit news labels, source gaps, curation promotion, and all wiki evidence; repair the pipeline and convert unchanged evidence-backed source pages into reusable wiki knowledge where justified.

## Global Constraints

- Do not delete raw source events or existing wiki pages.
- Preserve append-only label/source provenance and point-in-time timestamps.
- Never promote a single-source/community snapshot to reusable judgment without retaining source references and uncertainty.
- Treat provider HTTP blocks as availability state, not successful empty collection.
- Intentional LLM `skip` results remain valid and are not retried unless evidence changes.
- Do not include unrelated in-progress ML/dashboard changes in the implementation commits.

## Review Focus

- A general agent model unsupported by the Codex account must not silently become the news-label model; test command construction and a successful parsed label.
- A provider returning Cloudflare 403 must update availability without being reported as a healthy empty fetch; test worker and health serialization.
- A source digest whose previous judgment page was archived must be eligible for a replacement judgment; test active-link detection and retry selection.
- A digest with no archived article body but a rich stored body must still be distillable from the digest evidence; test the fallback prompt path.
- An intentional `skip` with unchanged evidence must not inflate the actionable backlog; test health counts and candidate selection.

### Task 1: Isolate the news-label LLM route

**Files:**
- Modify: `providers/news_labels.py`
- Test: `tests/test_news_labels.py`

**Interfaces:**
- Produces `_codex_command()` that only adds `--model` when `NEWS_LLM_LABELS_CODEX_MODEL` is explicitly configured.

- [ ] Write a failing test asserting `AGENT_CONSOLE_CODEX_MODEL` does not leak into the news-label Codex command and that the default route remains usable.
- [ ] Run `pytest tests/test_news_labels.py -q` and observe the new test fail against the current fallback-to-agent-model behavior.
- [ ] Remove the implicit `AGENT_CONSOLE_CODEX_MODEL` fallback and improve error detail to retain provider/model/status context.
- [ ] Run the focused news-label tests and verify all pass, including LLM provenance and fallback tests.

### Task 2: Make blocked SaveTicker/calendar sources explicit

**Files:**
- Create: `reports/source_exceptions.py`
- Modify: `reports/source_collector.py`
- Modify: `providers/econ_calendar.py`
- Test: `tests/test_source_collector.py`
- Test: `tests/test_operational_events.py`

**Interfaces:**
- Produces `SourceProviderUnavailable(reason, availability, status_code)` with attributes consumed by `reports.source_provider_worker` and `reports.source_pipeline`.

- [ ] Add tests for 403 responses from news and calendar providers asserting the exception is marked `availability="blocked"` and retains status code.
- [ ] Run the focused tests and observe failure because both providers currently swallow HTTP errors and return `[]`.
- [ ] Raise the shared exception for 403/451, preserve graceful empty results for other transient failures, and keep existing successful parsing behavior.
- [ ] Run source/provider tests and verify blocked status flows through the worker envelope and health model.

### Task 3: Repair wiki distillation eligibility and evidence fallback

**Files:**
- Modify: `reports/wiki_distillation.py`
- Modify: `reports/wiki_pipeline_health.py`
- Test: `tests/test_wiki_distillation.py`
- Test: `tests/test_wiki_pipeline_health.py`

**Interfaces:**
- `_has_judgment_link()` must ignore archived targets when deciding whether a source digest is connected.
- `_distill_one_with_status()` may use the stored source digest body when archived article bodies are unavailable, while retaining `source_refs` and evidence IDs.
- Health reports distinguish `unlinked_count` from `actionable_unlinked_count` and `intentional_skip_count`.

- [ ] Add failing tests for archived target re-selection, rich digest-body fallback, and unchanged intentional skip classification.
- [ ] Run the focused wiki tests and observe the expected failures.
- [ ] Implement the smallest state-aware eligibility and fallback changes; do not auto-promote skipped snapshots.
- [ ] Run all wiki distillation/pipeline-health tests and verify counts and links behave as specified.

### Task 4: Run the repair and evidence-backed backlog audit

**Files:**
- Create: `docs/reports/wiki-pipeline-recovery-2026-09-24.md`
- Runtime data: `/home/ubuntu/reports/source-cache`, `/home/ubuntu/.local/share/stock-report/shared-memory`

- [ ] Run the source provider groups and capture current provider availability and raw-event counts.
- [ ] Run the news-label snapshot once with the repaired route and verify `llm_count > 0` before allowing further backfill.
- [ ] Process actionable distillation candidates in bounded batches, preserving intentional skips and source references.
- [ ] Rebuild wiki/QMD artifacts and run a full health report; record before/after counts, remaining blocked sources, skipped snapshots, failed candidates, and evidence coverage.
- [ ] Run focused tests plus the full project test command; report any unrelated pre-existing failures without hiding them.

