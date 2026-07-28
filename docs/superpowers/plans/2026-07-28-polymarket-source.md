# Polymarket Source Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Polymarket public market data to source collection and AI console context.

**Architecture:** Reuse the existing source collector contract. A new Polymarket fetcher normalizes Gamma API event/market rows into source-cache events; context code derives a compact prediction-market summary from recent source events.

**Tech Stack:** Python, `requests`, existing JSONL source cache, pytest.

## Global Constraints

- Read-only Polymarket integration.
- No wallet auth, no trading endpoints, no new required dependency.
- Preserve raw payload and explicit data timestamp/source.
- Prediction-market probabilities are auxiliary signals, not verified facts.

---

### Task 1: Source Collector Provider

**Files:**
- Modify: `reports/source_collector.py`
- Test: `tests/test_source_collector.py`

**Interfaces:**
- Produces: `fetch_polymarket_events(limit: int | None = None, *, min_volume: float | None = None, keywords: list[str] | None = None) -> list[dict]`
- Consumes: `_classify_event(event: dict) -> dict`, `append_events`, `collect_once`

- [ ] Write failing tests for Gamma payload parsing and `collect_once` inclusion.
- [ ] Run targeted pytest and confirm failures.
- [ ] Add Polymarket classification, fetcher, health threshold, expected source, and collect_once hook.
- [ ] Run targeted pytest and confirm pass.

### Task 2: AI Console Context Summary

**Files:**
- Modify: `agent_console/context.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `prediction_market_state(events: list[dict] | None = None, limit: int = 8) -> dict`
- Consumes: `recent_source_events`, `context_pack`

- [ ] Write failing test that context pack exposes compact Polymarket summary.
- [ ] Run targeted pytest and confirm failure.
- [ ] Add `prediction_markets` summary to `context_pack`.
- [ ] Run targeted pytest and confirm pass.

### Task 3: Prompt Grounding

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `pack["prediction_markets"]`

- [ ] Write failing test that prompt includes Polymarket probability lines and caveat.
- [ ] Run targeted pytest and confirm failure.
- [ ] Add compact prediction-market lines to market/ticker prompt context.
- [ ] Run targeted pytest and confirm pass.

### Task 4: Verification

**Files:**
- No additional files expected.

- [ ] Run `python -m pytest tests/test_source_collector.py tests/test_agent_console.py -q`.
- [ ] Run `git diff --check`.
- [ ] Review diff for scope and raw-data preservation.
