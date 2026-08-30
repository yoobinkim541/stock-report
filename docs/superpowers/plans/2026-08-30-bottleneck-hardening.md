# 전략 데이터·실행 병목 하드닝 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전략 실행의 타임프레임 정확성, 실시간 진입 게이트, 데이터 커버리지, 로딩·저장·계산 병목을 검증 가능한 방식으로 개선한다.

**Architecture:** 전략 데이터는 실제 주기를 보존하는 단일 로더와 snapshot watermark를 사용한다. OOS 검증은 train/검증 컨텍스트와 실행 구간을 분리하고, 실시간 quote/health는 한 번 읽은 snapshot을 모든 심볼에 재사용한다. 과거 데이터는 파일 기반 bulk/index 경로를 우선하며 Redis는 최신 상태 fan-out에만 사용한다.

**Tech Stack:** Python 3.11, pandas, NumPy, Streamlit cache, existing JSONL/Parquet stores, optional Redis, pytest.

**Spec:** `docs/superpowers/plans/2026-08-30-bottleneck-hardening.md`

## Global Constraints

- 기존 `agent_console/wiki.py`, `tests/test_agent_console.py`, `docs/superpowers/plans/2026-08-28-quant-investing-service-integration.md` 변경은 보존한다.
- 백테스트는 결측 봉을 자동 보간하지 않으며, 실제 timeframe과 data snapshot을 결과에 남긴다.
- OOS 실행은 미래 데이터와 학습 데이터가 섞이지 않도록 한다.
- 실시간 health가 불명확하면 진입을 막되, 유효한 `age_seconds`를 가진 fresh source는 막지 않는다.
- 변경마다 관련 회귀 테스트를 먼저 실패시킨 뒤 최소 구현으로 통과시킨다.

### Task 1: 실제 타임프레임 로더와 snapshot watermark

**Files:**
- Modify: `providers/market_data.py:1128-1195`
- Modify: `agent_console/strategy_studio.py:1832-1905`
- Test: `tests/test_market_data_ohlc_cache.py`
- Test: `tests/test_strategy_studio.py`

**Interfaces:**
- Produce `resample_profile_bars(frame, timeframe)` and `frame.attrs["actual_timeframe"]`.
- Produce `frame.attrs["data_watermark"]` from the latest valid bar timestamp.

- [x] Add tests proving `1wk` and `1mo` aggregate OHLCV rather than relabel daily rows.
- [x] Add tests proving duplicate timestamps are removed and actual timeframe is recorded.
- [x] Make `_load_prices` reject or warn on a requested/actual timeframe mismatch.
- [x] Run the focused market-data and strategy tests.

### Task 2: OOS warm-up context and coverage manifest

**Files:**
- Modify: `agent_console/strategy_studio.py:353-425,1832-1910`
- Modify: `ml/strategy_studio/engine.py:620-627`
- Test: `tests/test_strategy_studio.py`
- Test: `tests/test_strategy_end_to_end.py`

**Interfaces:**
- Produce `prices.attrs["load_manifest"]` containing requested, loaded, failed symbols and benchmark status.
- Execute features on context through the test period but score/execute only the test interval.

- [x] Add a regression test for a rolling indicator whose first test bar needs pre-test history.
- [x] Add a regression test for partial universe failure and missing benchmark.
- [x] Implement manifest collection and a configurable minimum coverage gate.
- [x] Update validation to retain chronology evidence while using warm-up context.

### Task 3: Health aggregation and realtime snapshot reuse

**Files:**
- Modify: `ml/strategy_studio/execution.py:615-747`
- Modify: `providers/intraday_bars.py:355-367`
- Modify: `providers/realtime_quotes.py:59-128`
- Modify: `agent_console/realtime_market.py:37-120`
- Test: `tests/test_strategy_execution.py`
- Test: `tests/test_realtime_quotes.py`

**Interfaces:**
- Aggregate health while preserving `age_seconds`, `last_bar_at`, source and evaluation time.
- Add an internal `read_realtime_snapshot()` path so each request reads Redis/file cache once.

- [x] Add a failing test showing fresh source mapping must not pause entries.
- [x] Add tests for missing/invalid age and `available` source behavior.
- [x] Add a reusable Redis client/connection pool and one cache snapshot read.
- [x] Make realtime market snapshot consume the single read and use bulk symbol extraction.

### Task 4: Bulk intraday store and multi-symbol loading

**Files:**
- Modify: `providers/intraday_bars.py:234-305`
- Modify: `dashboard/views.py:1693-1720`
- Modify: `providers/market_data.py:1128-1195`
- Test: `tests/test_intraday_bars.py`
- Test: `tests/test_market_data_ohlc_cache.py`

**Interfaces:**
- Produce `load_bars_bulk(symbols, dates, interval, session)` with one file pass per date.
- Preserve existing `load_bars` behavior by delegating to the bulk reader.

- [x] Add tests that bulk loading returns one normalized frame per symbol.
- [x] Implement one-pass date reads and in-process bounded cache.
- [x] Add `load_profile_bars_many` with bounded concurrency only for independent provider calls.
- [x] Keep the existing API fallback behavior and per-symbol provenance.

### Task 5: Calculation hot paths and CPCV guardrails

**Files:**
- Modify: `ml/strategy_studio/signals.py:455-503`
- Modify: `ml/strategy_studio/allocation.py:96-180`
- Modify: `ml/strategy_studio/validation.py:392-440`
- Test: `tests/test_strategy_signals.py`
- Test: `tests/test_strategy_allocation.py`
- Test: `tests/test_strategy_contracts.py`

**Interfaces:**
- Keep feature output schema unchanged while removing per-cell `.at` loops.
- Add configurable `covariance_refresh_bars` and `max_cpcv_paths` with deterministic rejection or sampling policy.

- [x] Add output-equivalence coverage through the existing signal provider tests.
- [x] Add tests that covariance is reused inside its refresh window.
- [x] Add tests that excessive CPCV combinations fail before backtest execution.
- [x] Implement vectorization, covariance reuse, and cost estimation diagnostics.

### Task 6: Cache invalidation and provenance observability

**Files:**
- Modify: `dashboard/cached.py:13-25,193-195`
- Modify: `agent_console/strategy_studio.py:1875-1905`
- Test: `tests/test_strategy_studio_pages.py`
- Test: `tests/test_strategy_studio.py`

**Interfaces:**
- Include a data watermark/fingerprint in preview cache keys.
- Preserve source coverage from `data_snapshot.source_coverage` in panel attributes.

- [x] Add watermark/fingerprint inputs to preview cache keys.
- [x] Preserve source coverage in data quality output.
- [x] Use a five-minute TTL, 32-entry bound, and compact cache-key policy for previews.
- [x] Add structured load failure reasons and timing fields.

### Task 7: Verification

- [x] Run all focused strategy, data, realtime, dashboard, and contract tests.
- [x] Run `git diff --check` and Python compilation.
- [x] Attempt the full suite with resource-aware test grouping; 405 passed and 2 dashboard smoke cases timed out on external network calls.
- [x] Review the diff for accidental changes to existing user files; pre-existing `wiki.py` and `test_agent_console.py` edits were preserved.

## Self-review

The plan covers all identified findings: timeframe mismatch, OOS warm-up, health false pause, silent symbol loss, stale preview cache, sequential network fan-out, JSONL rescans, covariance and feature CPU costs, CPCV explosion, repeated realtime cache reads, cache memory, and provenance gaps. It deliberately does not replace the historical store with Redis; that would add operational cost without fixing the primary scan and query shape.
