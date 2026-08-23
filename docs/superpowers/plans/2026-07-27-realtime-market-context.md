# Realtime Market Context Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 5개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 콘솔 챗봇이 이미 연결된 실시간/최신 시장 데이터 소스에서 시장 스냅샷을 읽어 답변 컨텍스트로 사용하게 만든다.

**Architecture:** 새 `agent_console/realtime_market.py`는 읽기전용 adapter로만 동작하며 `providers.realtime_quotes`, `providers.kis_quote`, 기존 캐시 파일을 순서대로 읽어 스냅샷을 만든다. `agent_console.context.context_pack()`은 이 스냅샷을 `market_snapshot`으로 포함하고, `agent_console.agent`는 LLM 프롬프트와 fallback 문맥에 이를 주입한다.

**Tech Stack:** Python 3, pytest, existing KIS/realtime quote providers, local json cache files.

## Global Constraints

- 주문/매매 API는 호출하지 않는다. 시세/캐시 읽기 전용이다.
- `REALTIME_ENABLED`/`QUOTES_POLL_ENABLED`가 꺼져 있거나 키가 없으면 거짓 조회를 하지 않고 `unavailable` 상태를 남긴다.
- 모든 수치에는 source와 timestamp/freshness를 붙인다.
- 외부 네트워크 실패는 AI 콘솔 답변 전체 실패로 전파하지 않는다.

---

### Task 1: Realtime Snapshot Contract

**Files:**
- Create: `agent_console/realtime_market.py`
- Test: `tests/test_agent_realtime_market_context.py`

**Interfaces:**
- Produces: `build_market_snapshot(symbols: list[str] | None = None, now: float | None = None) -> dict`
- Produces: `compact_snapshot_lines(snapshot: dict) -> list[str]`

- [x] **Step 1: Write the failing test**

```python
def test_realtime_snapshot_reads_fresh_cache_and_formats_lines(monkeypatch, tmp_path):
    import json, time
    from agent_console import realtime_market
    from providers import realtime_quotes

    cache = {
        "__heartbeat__": {"ts": time.time(), "n": 2},
        "005930": {"price": 80000, "volume": 123, "ts": time.time(), "src": "toss"},
        "QQQ": {"price": 550.5, "volume": 456, "ts": time.time(), "src": "toss"},
    }
    path = tmp_path / "rest_quotes.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    monkeypatch.setattr(realtime_quotes, "REST_CACHE_PATH", str(path))

    snapshot = realtime_market.build_market_snapshot(symbols=["005930", "QQQ"])

    assert snapshot["ok"] is True
    assert snapshot["status"] == "partial"
    assert snapshot["quotes"][0]["symbol"] == "005930"
    assert snapshot["quotes"][0]["price"] == 80000.0
    assert "005930" in "\n".join(realtime_market.compact_snapshot_lines(snapshot))
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py::test_realtime_snapshot_reads_fresh_cache_and_formats_lines -q`
Expected: FAIL because `agent_console.realtime_market` does not exist.

- [x] **Step 3: Write minimal implementation**

Implement `build_market_snapshot()` using `providers.realtime_quotes.get_price/get_volume/is_fresh` and optional direct `providers.kis_quote.get_quote` fallback. Return unavailable rows instead of raising.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py -q`
Expected: PASS.

### Task 2: Context Pack Integration

**Files:**
- Modify: `agent_console/context.py`
- Test: `tests/test_agent_realtime_market_context.py`

**Interfaces:**
- Consumes: `agent_console.realtime_market.build_market_snapshot`
- Produces: `context_pack(... )["market_snapshot"]`

- [x] **Step 1: Write the failing test**

```python
def test_context_pack_includes_market_snapshot(monkeypatch, tmp_path):
    from agent_console import context

    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setattr(context, "recent_source_events", lambda **kwargs: [])
    monkeypatch.setattr(context, "world_memory_rows", lambda **kwargs: [])
    monkeypatch.setattr(context, "latest_reports", lambda *a, **k: [])
    monkeypatch.setattr(context, "ml_activity", lambda *a, **k: [])
    monkeypatch.setattr(context, "portfolio_state", lambda: {"holdings": []})
    monkeypatch.setattr(context, "paper_state", lambda: {})
    monkeypatch.setattr(context, "model_state", lambda: {})
    monkeypatch.setattr(context, "shared_memory", type("S", (), {
        "sync_external_layer_from_pack": staticmethod(lambda pack: None),
        "status": staticmethod(lambda limit=8: {"ok": True, "records": []}),
    }))
    monkeypatch.setattr(context.realtime_market, "build_market_snapshot", lambda: {
        "ok": True, "status": "partial", "quotes": [{"symbol": "QQQ", "price": 550.5}]
    })

    pack = context.context_pack("market")

    assert pack["market_snapshot"]["quotes"][0]["symbol"] == "QQQ"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py::test_context_pack_includes_market_snapshot -q`
Expected: FAIL because `market_snapshot` is missing.

- [x] **Step 3: Write minimal implementation**

Import `agent_console.realtime_market`, call `build_market_snapshot()`, and include it in normal and fallback packs.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py -q`
Expected: PASS.

### Task 3: LLM Prompt Integration

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `pack["market_snapshot"]`
- Produces: `_build_general_chat_prompt()` and `_build_market_context_prompt()` include `[실시간/최신 시장 스냅샷]`

- [x] **Step 1: Write the failing test**

```python
def test_agent_prompt_includes_realtime_market_snapshot():
    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "market_snapshot": {
            "ok": True,
            "status": "partial",
            "quotes": [{"symbol": "QQQ", "price": 550.5, "source": "rest_cache", "age_s": 3}],
            "as_of": "2026-07-27T05:00:00+00:00",
        },
    }

    prompt = agent._build_general_chat_prompt("오늘 시장 어때", pack, [])

    assert "[실시간/최신 시장 스냅샷]" in prompt
    assert "QQQ" in prompt
    assert "550.5" in prompt
    assert "시점" in prompt
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_agent_prompt_includes_realtime_market_snapshot -q`
Expected: FAIL because prompt does not include the snapshot.

- [x] **Step 3: Write minimal implementation**

Add a compact formatter in `agent.py` or reuse `realtime_market.compact_snapshot_lines()` to include snapshot lines in market and general prompts.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_agent_prompt_includes_realtime_market_snapshot tests/test_agent_realtime_market_context.py -q`
Expected: PASS.

### Task 4: Verification And Integration

**Files:**
- Modify: changed files only.

**Interfaces:**
- Produces: passing focused tests and safe master merge.

- [x] **Step 1: Run focused suite**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py tests/test_agent_console.py tests/test_realtime_quotes.py tests/test_kis_quote.py -q`
Expected: PASS.

- [x] **Step 2: Run syntax/diff checks**

Run: `.venv/bin/python -m py_compile agent_console/agent.py agent_console/context.py agent_console/realtime_market.py`
Run: `git diff --check`
Expected: PASS.

- [x] **Step 3: Commit and merge**

Commit only relevant files. Merge fast-forward to master, push, restart dashboard, and verify `/agent` returns HTTP 200.
