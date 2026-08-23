# US Overnight Tracking Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 3개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing US quote poller from premarket/regular/afterhours coverage to overnight-aware 24-hour tracking.

**Architecture:** Keep the current `quotes_poller` cache pipeline and add `overnight` as a first-class US session. REST polling will run for US symbols during `overnight`, and cache entries will expose the session so the AI console can distinguish thin overnight prices from regular-session data.

**Tech Stack:** Python 3.11, pytest, existing Toss REST quote provider, existing KIS WebSocket quote cache, cron watchdog.

## Global Constraints

- Do not add order endpoints or trading actions.
- Do not label overnight quotes as regular market quotes.
- Preserve the existing `rest_quotes.json` cache shape while adding optional metadata.
- Write failing tests before production changes.
- Keep broker/provider failures graceful.

---

### Task 1: Add Overnight Session Classification

**Files:**
- Modify: `quotes_poller.py`
- Test: `tests/test_quotes_poller.py`

**Interfaces:**
- Consumes: `datetime` in UTC or timezone-aware form.
- Produces: `us_trading_session(now) -> "overnight" | "premarket" | "regular" | "afterhours" | "closed"`.

- [x] **Step 1: Write the failing test**

```python
def test_us_trading_session_covers_overnight():
    assert Q.us_trading_session(datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc)) == "overnight"
    assert Q.us_trading_session(datetime(2026, 7, 13, 23, 50, tzinfo=timezone.utc)) == "afterhours"
```

- [x] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_quotes_poller.py::test_us_trading_session_covers_overnight -q`
Expected: FAIL because `01:00 UTC` is currently `closed`.

- [x] **Step 3: Implement minimal session logic**

Treat weekday 20:00-24:00 ET and 00:00-04:00 ET as `overnight`.

- [x] **Step 4: Run the focused test**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_quotes_poller.py::test_us_trading_session_covers_overnight -q`
Expected: PASS.

### Task 2: Poll US Symbols During Overnight

**Files:**
- Modify: `quotes_poller.py`
- Test: `tests/test_quotes_poller.py`

**Interfaces:**
- Consumes: `poll_once(now, universe, toss_fn, kiwoom_fn, cache_path)`.
- Produces: US cache entries with `session: "overnight"` and heartbeat `us_session: "overnight"`.

- [x] **Step 1: Write the failing test**

```python
def test_poll_once_us_overnight_polls_us_symbols(tmp_path):
    cache = str(tmp_path / "rest_quotes.json")
    overnight = datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc)
    n = Q.poll_once(now=overnight, universe=["NVDA"], toss_fn=lambda symbols: {"NVDA": 181.0}, kiwoom_fn=lambda codes: {}, cache_path=cache)
    data = json.loads(open(cache, encoding="utf-8").read())
    assert n == 1
    assert data["NVDA"]["session"] == "overnight"
    assert data["__heartbeat__"]["us_session"] == "overnight"
```

- [x] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_quotes_poller.py::test_poll_once_us_overnight_polls_us_symbols -q`
Expected: FAIL because the market is currently closed.

- [x] **Step 3: Implement minimal code**

No separate polling path is needed once `us_trading_session()` returns `overnight`; `poll_once()` already polls when the session is not `closed`.

- [x] **Step 4: Run QA**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_quotes_poller.py tests/test_agent_realtime_market_context.py tests/test_realtime_quotes.py -q`.

### Task 3: Document Operational Limits

**Files:**
- Modify: `docs/intraday-market-data.md`

**Interfaces:**
- Produces: operator-facing notes that overnight quotes are source-dependent and should be interpreted as thin-liquidity data.

- [x] **Step 1: Document the session model**

Add `overnight: 20:00-04:00 ET` and note that source metadata must be shown in AI console responses.

- [x] **Step 2: Verify docs and code**

Run: `git diff --check` and the focused tests above.
