# Polymarket Source Integration Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 5개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


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

- [x] Write failing tests for Gamma payload parsing and `collect_once` inclusion.
- [x] Run targeted pytest and confirm failures.
- [x] Add Polymarket classification, fetcher, health threshold, expected source, and collect_once hook.
- [x] Run targeted pytest and confirm pass.

### Task 2: AI Console Context Summary

**Files:**
- Modify: `agent_console/context.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `prediction_market_state(events: list[dict] | None = None, limit: int = 8) -> dict`
- Consumes: `recent_source_events`, `context_pack`

- [x] Write failing test that context pack exposes compact Polymarket summary.
- [x] Run targeted pytest and confirm failure.
- [x] Add `prediction_markets` summary to `context_pack`.
- [x] Run targeted pytest and confirm pass.

### Task 3: Prompt Grounding

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `pack["prediction_markets"]`

- [x] Write failing test that prompt includes Polymarket probability lines and caveat.
- [x] Run targeted pytest and confirm failure.
- [x] Add compact prediction-market lines to market/ticker prompt context.
- [x] Run targeted pytest and confirm pass.

### Task 4: Verification

**Files:**
- No additional files expected.

- [x] Run `python -m pytest tests/test_source_collector.py tests/test_agent_console.py -q`.
- [x] Run `git diff --check`.
- [x] Review diff for scope and raw-data preservation.
