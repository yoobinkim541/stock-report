# Agent Console Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce perceived and actual AI console answer completion time while making loading progress clear.

**Architecture:** Keep the existing synchronous JSON API for compatibility, add a streaming-style endpoint that emits an immediate stage event and final answer, and move wiki auto-curation to background post-processing so it no longer blocks the user response. The browser chat form uses the stream endpoint when possible and rotates explicit status labels while waiting.

**Tech Stack:** Python 3.11, Flask, pytest, vanilla JavaScript fetch streaming, existing `agent_console.agent`, `agent_console.server`, and `agent_console/static/app.js`.

## Global Constraints

- Existing `/api/agent/chat` JSON endpoint must keep working.
- No real trading or broker behavior changes.
- LLM answer quality must not be reduced by disabling web search or context by default.
- Wiki auto-curation failures must never affect chat response delivery.
- Loading copy must show changing stages, not one repeated static label.

---

### Task 1: Async Post-Processing

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `answer(question, surface="market", async_postprocess=True) -> dict`
- Produces: `_run_postprocess_async(question, response, surface, pack, history) -> bool`

- [ ] **Step 1: Write failing test**

Add a pytest that monkeypatches `wiki.auto_curate_from_chat` to block on an event, calls `agent.answer(..., async_postprocess=True)`, asserts the answer returns before the block is released, then releases and joins the background thread.

- [ ] **Step 2: Implement minimal async post-processing**

Add a daemon `threading.Thread` around wiki auto-curation, store the last thread for tests, and include `postprocess: {wiki_autocurate: "queued"}` in the response context when queued.

- [ ] **Step 3: Run focused test**

Run `uv run pytest tests/test_agent_console.py::<new-test-name> -q` and expect pass.

---

### Task 2: Stream Endpoint And Loading Stages

**Files:**
- Modify: `agent_console/server.py`
- Modify: `agent_console/static/app.js`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `POST /api/agent/chat/stream` returning newline-delimited `event: stage` and `event: answer` SSE-style frames.
- Browser `sendChat()` first tries the stream endpoint, updates one assistant placeholder with changing stage text, then replaces it with the final answer.

- [ ] **Step 1: Write failing endpoint test**

Add a Flask test that posts to `/api/agent/chat/stream`, monkeypatches `agent.answer`, and asserts the response contains `event: stage` before `event: answer`.

- [ ] **Step 2: Implement endpoint**

Use Flask `Response` with `mimetype="text/event-stream"`. Yield a Korean stage message immediately, then call `agent.answer(..., async_postprocess=True)`, then yield the final JSON answer event.

- [ ] **Step 3: Implement frontend fallback**

Add a placeholder assistant message, rotate labels such as `맥락 읽는 중`, `필요 데이터 확인 중`, `답변 압축 중`, `거의 완료`, parse stream frames when supported, and fallback to `/api/agent/chat` on stream failure.

- [ ] **Step 4: Run focused tests**

Run `uv run pytest tests/test_agent_console.py::<stream-test-name> tests/test_agent_console.py::<async-test-name> -q` and expect pass.

---

### Task 3: Verification And Release

**Files:**
- Verify changed Python and JavaScript paths.

- [ ] **Step 1: Run regression tests**

Run `uv run pytest tests/test_agent_console.py -q` and expect pass.

- [ ] **Step 2: Run syntax checks**

Run `python3 -m py_compile agent_console/agent.py agent_console/server.py` and expect exit 0.

- [ ] **Step 3: Commit and push**

Commit with Korean `fix)` message, push `codex/llm-console-fallback-fixes`, deploy production with `vercel deploy --prod --yes --project stock-report`, and verify `/ai-console` plus `/api/agent/chat/stream` respond successfully.
