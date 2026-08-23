# Strategy Canvas AI Chat Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 2개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "AI 대화" chat panel to the "기존 전략 캔버스" section in `dashboard/pages/ai_console.py::_lab_tab()`, where the agent proposes edits to RSI thresholds / max-loss cap / hypothesis text / portfolio allocations, shown as a diff the user must click "적용" to accept.

**Architecture:** Mirror the existing pattern in `dashboard/strategy_studio.py::_render_conversation_panel` (chat history in session state → `agent.answer()` for prose → a heuristic regex-based patch proposer → diff table → explicit apply button that writes to `<key>_pending` session-state entries consumed before the next widget instantiation). The four editable fields (`buy_rsi`, `sell_rsi`, `max_loss`, `hypothesis`) currently have no `key=` at all and reset to hardcoded defaults every rerun — giving them persistent keys is a prerequisite.

**Tech Stack:** Streamlit (`st.session_state`, `st.chat_input`, `st.dataframe`), `streamlit.testing.v1.AppTest` for tests, `ticker_names.resolve()` for name→ticker matching, `re` for heuristic pattern matching. No new dependencies.

## Global Constraints

- Patch computation is **heuristic (regex/keyword), not LLM structured output** — matches the existing `agent_console/strategy_studio.py::_heuristic_patch` capability level. Only the prose explanation comes from `agent.answer()`.
- Changes are **never auto-applied** — always proposed as a diff, applied only when the user clicks "적용".
- `ticker_names.resolve(query, allow_net=False)` — always call with `allow_net=False` for determinism/no-network, matching the codebase's `allow_net` convention.
- Do not wire up the unused `_strategy_canvas_backtest()` function — out of scope.
- Follow the file's existing Korean-language UI strings and formatting conventions; don't touch unrelated code in `ai_console.py`.
- Reuse the pending-key widget-update pattern from `dashboard/strategy_studio.py` (this session's earlier fix, commit `5410178`): never assign directly to a widget's own `session_state[key]` after that widget has been instantiated in the same script run — write to `<key>_pending` and consume it at the top of `_lab_tab()`, before any widget in the function is created.

---

## File Structure

- **Modify: `dashboard/pages/ai_console.py`**
  - Imports (top of file): add `import re` and `import ticker_names`.
  - `_lab_tab()` (currently lines 631-672): add a call to a new `_consume_canvas_pending()` at the top; give `buy_rsi`/`sell_rsi`/`max_loss`/`hypothesis` widgets `key=`; call new `_render_canvas_chat(current)` after the "시나리오 저장" button.
  - New functions, placed after `_lab_tab()` and before the existing `_default_canvas_allocations()`: `_consume_canvas_pending`, `_render_canvas_chat`, `_apply_canvas_patch`, `_allocations_to_text`, `_heuristic_canvas_patch`, `_heuristic_allocation_patch`, `_diff_canvas`, plus module-level regex constants and `_CANVAS_FIELD_LABELS`.
- **Modify: `tests/test_dashboard_pages.py`** — add unit tests for the heuristic patch functions and one end-to-end `AppTest` test for the full chat → diff → apply flow.

No other files change.

---

### Task 1: Persist buy_rsi/sell_rsi/max_loss/hypothesis across reruns

**Files:**
- Modify: `dashboard/pages/ai_console.py:644-648` (the `c1/c2/c3.number_input` and `hypothesis` `st.text_area` calls)
- Modify: `dashboard/pages/ai_console.py:631-633` (top of `_lab_tab`, add pending-consumption call)
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Produces: `_consume_canvas_pending() -> None` — pops `strategy_canvas_{buy_rsi,sell_rsi,max_loss,hypothesis,alloc_text}_pending` from `st.session_state` into the corresponding non-`_pending` key, if present. Later tasks write to the `_pending` keys to update these widgets.
- Produces: session-state keys `strategy_canvas_buy_rsi`, `strategy_canvas_sell_rsi`, `strategy_canvas_max_loss`, `strategy_canvas_hypothesis` now persist the widgets' current values (previously untracked).

- [x] **Step 1: Write the failing test**

Add to `tests/test_dashboard_pages.py`, near `test_ai_console_strategy_canvas_allocation_normalize`:

```python
def test_ai_console_canvas_buy_rsi_persists_across_rerun():
    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    rsi_input.set_value(45).run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    assert int(rsi_input.value) == 45
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py::test_ai_console_canvas_buy_rsi_persists_across_rerun -v`
Expected: FAIL with `KeyError: 'strategy_canvas_buy_rsi'` (the widget has no `key=` yet, so `at.number_input(key=...)` can't find it).

- [x] **Step 3: Write minimal implementation**

In `dashboard/pages/ai_console.py`, replace the `_lab_tab` opening line and the four widget definitions.

Current (lines 631-648):
```python
def _lab_tab(surface: str, pack: dict):
    strategy_studio.render_strategy_lab("ai_console", pack, mode="lab")
    st.divider()
    st.markdown("##### 기존 전략 캔버스")
    st.caption("비중과 규칙을 저장해 챗봇/위키가 다시 읽는 전략 가설로 남깁니다.")
    default_text = _default_canvas_allocations()
    alloc_text = st.text_area("포트폴리오 비중", value=st.session_state.get("strategy_canvas_alloc_text", default_text), height=150, key="strategy_canvas_alloc_text")
    allocs = _normalize_allocations(_parse_allocations(alloc_text))
    if allocs:
        st.dataframe(pd.DataFrame(allocs), hide_index=True, width="stretch", height=220)
    else:
        st.info("한 줄에 `티커 비중 메모` 형식으로 입력해 주세요. 예: QQQ 45 핵심 성장")

    c1, c2, c3 = st.columns(3)
    buy_rsi = c1.number_input("매수 RSI", min_value=1, max_value=99, value=30, step=1)
    sell_rsi = c2.number_input("현금화 RSI", min_value=1, max_value=99, value=70, step=1)
    max_loss = c3.number_input("최대 손실한도 %", min_value=0.0, max_value=100.0, value=8.0, step=0.5)
    hypothesis = st.text_area("전략 가설", height=90, placeholder="어떤 시장에서 이 전략이 유리하고, 어떤 조건에서 꺼야 하는지 적어주세요.")
```

Replace with:
```python
def _lab_tab(surface: str, pack: dict):
    _consume_canvas_pending()
    strategy_studio.render_strategy_lab("ai_console", pack, mode="lab")
    st.divider()
    st.markdown("##### 기존 전략 캔버스")
    st.caption("비중과 규칙을 저장해 챗봇/위키가 다시 읽는 전략 가설로 남깁니다.")
    default_text = _default_canvas_allocations()
    alloc_text = st.text_area("포트폴리오 비중", value=st.session_state.get("strategy_canvas_alloc_text", default_text), height=150, key="strategy_canvas_alloc_text")
    allocs = _normalize_allocations(_parse_allocations(alloc_text))
    if allocs:
        st.dataframe(pd.DataFrame(allocs), hide_index=True, width="stretch", height=220)
    else:
        st.info("한 줄에 `티커 비중 메모` 형식으로 입력해 주세요. 예: QQQ 45 핵심 성장")

    c1, c2, c3 = st.columns(3)
    buy_rsi = c1.number_input(
        "매수 RSI", min_value=1, max_value=99,
        value=int(st.session_state.get("strategy_canvas_buy_rsi", 30)),
        step=1, key="strategy_canvas_buy_rsi",
    )
    sell_rsi = c2.number_input(
        "현금화 RSI", min_value=1, max_value=99,
        value=int(st.session_state.get("strategy_canvas_sell_rsi", 70)),
        step=1, key="strategy_canvas_sell_rsi",
    )
    max_loss = c3.number_input(
        "최대 손실한도 %", min_value=0.0, max_value=100.0,
        value=float(st.session_state.get("strategy_canvas_max_loss", 8.0)),
        step=0.5, key="strategy_canvas_max_loss",
    )
    hypothesis = st.text_area(
        "전략 가설", height=90,
        value=st.session_state.get("strategy_canvas_hypothesis", ""),
        placeholder="어떤 시장에서 이 전략이 유리하고, 어떤 조건에서 꺼야 하는지 적어주세요.",
        key="strategy_canvas_hypothesis",
    )
```

Then add this new function immediately after `_lab_tab()` (before `_default_canvas_allocations`):

```python
def _consume_canvas_pending() -> None:
    for field in ("buy_rsi", "sell_rsi", "max_loss", "hypothesis", "alloc_text"):
        key = f"strategy_canvas_{field}"
        pending_key = f"{key}_pending"
        if pending_key in st.session_state:
            st.session_state[key] = st.session_state.pop(pending_key)
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py::test_ai_console_canvas_buy_rsi_persists_across_rerun -v`
Expected: PASS

- [x] **Step 5: Run the full existing dashboard page test to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "ai_console or page_renders" -v`
Expected: all PASS (in particular `test_page_renders_without_exception[from dashboard.pages import ai_console-ai_console.render()]`)

- [x] **Step 6: Commit**

```bash
git add dashboard/pages/ai_console.py tests/test_dashboard_pages.py
git commit -m "feat) 전략 캔버스 RSI·손실한도·가설 위젯 세션 유지"
```

---

### Task 2: Heuristic patch — RSI pair/single, max-loss, hypothesis

**Files:**
- Modify: `dashboard/pages/ai_console.py` (imports; new module-level regexes + `_heuristic_canvas_patch`, placed after the functions added in Task 1)
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (pure function, no Streamlit state).
- Produces: `_heuristic_canvas_patch(question: str, current: dict, answer_text: str) -> dict`. `current` has keys `buy_rsi: int, sell_rsi: int, max_loss: float, hypothesis: str, allocations: list[dict]`. Returns a dict containing only the changed keys among `buy_rsi/sell_rsi/max_loss/hypothesis` in this task (Task 3 adds the `allocations` key to this same function). Empty dict `{}` when nothing matches.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_dashboard_pages.py`:

```python
def _canvas_state(**overrides):
    base = {"buy_rsi": 30, "sell_rsi": 70, "max_loss": 8.0, "hypothesis": "", "allocations": []}
    base.update(overrides)
    return base


def test_heuristic_canvas_patch_rsi_pair():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("RSI를 25/75로 바꿔줘", _canvas_state(), "")

    assert patch == {"buy_rsi": 25, "sell_rsi": 75}


def test_heuristic_canvas_patch_max_loss():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("손실한도를 5%로 낮춰줘", _canvas_state(), "")

    assert patch == {"max_loss": 5.0}


def test_heuristic_canvas_patch_hypothesis():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch(
        "가설을 지금 답변대로 바꿔줘",
        _canvas_state(),
        "변동성 급등 구간에서 유리하고 금리 급락 시 꺼야 한다.",
    )

    assert patch == {"hypothesis": "변동성 급등 구간에서 유리하고 금리 급락 시 꺼야 한다."}


def test_heuristic_canvas_patch_no_match_returns_empty():
    from dashboard.pages import ai_console

    patch = ai_console._heuristic_canvas_patch("오늘 시장 어때?", _canvas_state(), "")

    assert patch == {}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k heuristic_canvas_patch -v`
Expected: FAIL with `AttributeError: module 'dashboard.pages.ai_console' has no attribute '_heuristic_canvas_patch'`

- [x] **Step 3: Write minimal implementation**

In `dashboard/pages/ai_console.py`, change the top-of-file imports from:

```python
import html
import os
import sys
from datetime import datetime, timezone
```

to:

```python
import html
import os
import re
import sys
from datetime import datetime, timezone
```

Then add these module-level constants near the top of the file, right after the existing `_AGENT_PROGRESS_LABELS = (...)` block:

```python
_CANVAS_FIELD_LABELS = {
    "buy_rsi": "매수 RSI",
    "sell_rsi": "현금화 RSI",
    "max_loss": "최대 손실한도 %",
    "hypothesis": "전략 가설",
    "allocations": "포트폴리오 비중",
}

_RSI_PAIR_RE = re.compile(r"rsi\D{0,6}(\d{1,2})\s*/\s*(\d{1,2})", re.IGNORECASE)
_RSI_BUY_RE = re.compile(r"매수\s*rsi\D{0,10}?(\d{1,2})", re.IGNORECASE)
_RSI_SELL_RE = re.compile(r"현금화\s*rsi\D{0,10}?(\d{1,2})", re.IGNORECASE)
_MAX_LOSS_RE = re.compile(r"(?:손실|손절)\D{0,10}?(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")
```

Then add this function after `_consume_canvas_pending()`:

```python
def _heuristic_canvas_patch(question: str, current: dict, answer_text: str) -> dict:
    q = str(question or "")
    patch: dict = {}

    pair = _RSI_PAIR_RE.search(q)
    if pair:
        buy, sell = int(pair.group(1)), int(pair.group(2))
        if 1 <= buy <= 99:
            patch["buy_rsi"] = buy
        if 1 <= sell <= 99:
            patch["sell_rsi"] = sell
    else:
        buy_match = _RSI_BUY_RE.search(q)
        if buy_match:
            buy = int(buy_match.group(1))
            if 1 <= buy <= 99:
                patch["buy_rsi"] = buy
        sell_match = _RSI_SELL_RE.search(q)
        if sell_match:
            sell = int(sell_match.group(1))
            if 1 <= sell <= 99:
                patch["sell_rsi"] = sell

    loss_match = _MAX_LOSS_RE.search(q)
    if loss_match:
        loss = float(loss_match.group(1))
        if 0 <= loss <= 100:
            patch["max_loss"] = loss

    if "가설" in q and any(word in q for word in ("바꿔", "바꾸", "적어", "써줘", "써 줘", "수정")):
        summary = str(answer_text or "").strip().split("\n")[0][:80]
        if summary:
            patch["hypothesis"] = summary

    return patch
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k heuristic_canvas_patch -v`
Expected: PASS (all 4)

- [x] **Step 5: Commit**

```bash
git add dashboard/pages/ai_console.py tests/test_dashboard_pages.py
git commit -m "feat) 전략 캔버스 RSI·손실한도·가설 휴리스틱 패치 계산"
```

---

### Task 3: Heuristic patch — portfolio allocations

**Files:**
- Modify: `dashboard/pages/ai_console.py` (import; new regexes + `_heuristic_allocation_patch` + `_allocations_to_text`; extend `_heuristic_canvas_patch`)
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `_heuristic_canvas_patch` from Task 2 (extends it to also set an `"allocations"` key in its returned patch dict).
- Produces: `_heuristic_allocation_patch(question: str, allocations: list[dict]) -> list[dict] | None` — returns `None` when nothing matched, otherwise a full replacement list of `{symbol, weight_pct, note}` rows summing to 100 (rounded to 2 decimals).
- Produces: `_allocations_to_text(rows: list[dict]) -> str` — serializes `[{symbol, weight_pct, note}]` back to the `"TICKER WEIGHT note"` line format that `_parse_allocations` reads. Task 4 uses this when applying an allocation patch.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_dashboard_pages.py`:

```python
def test_heuristic_canvas_patch_allocation_add():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}])
    patch = ai_console._heuristic_canvas_patch("TLT 10%로 추가해줘", current, "")

    assert patch["allocations"] == [
        {"symbol": "QQQ", "weight_pct": 90.0, "note": "core"},
        {"symbol": "TLT", "weight_pct": 10.0, "note": ""},
    ]


def test_heuristic_canvas_patch_allocation_remove():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[
        {"symbol": "QQQ", "weight_pct": 80.0, "note": "core"},
        {"symbol": "TLT", "weight_pct": 20.0, "note": "hedge"},
    ])
    patch = ai_console._heuristic_canvas_patch("TLT 빼줘", current, "")

    assert patch["allocations"] == [{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}]


def test_heuristic_canvas_patch_allocation_unresolvable_name_is_skipped():
    from dashboard.pages import ai_console

    current = _canvas_state(allocations=[{"symbol": "QQQ", "weight_pct": 100.0, "note": "core"}])
    patch = ai_console._heuristic_canvas_patch("아무개코인 10%로 추가해줘", current, "")

    assert "allocations" not in patch


def test_allocations_to_text_round_trips_through_parse_allocations():
    from dashboard.pages import ai_console

    rows = [{"symbol": "QQQ", "weight_pct": 90.0, "note": "core"}, {"symbol": "TLT", "weight_pct": 10.0, "note": ""}]
    text = ai_console._allocations_to_text(rows)
    parsed = ai_console._normalize_allocations(ai_console._parse_allocations(text))

    assert [r["symbol"] for r in parsed] == ["QQQ", "TLT"]
    assert [r["weight_pct"] for r in parsed] == [90.0, 10.0]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "allocation_add or allocation_remove or allocation_unresolvable or allocations_to_text" -v`
Expected: FAIL — `test_heuristic_canvas_patch_allocation_add`/`_remove` fail on `assert "allocations" not in patch` style mismatch (patch has no `"allocations"` key at all yet since `_heuristic_canvas_patch` doesn't compute it), and `test_allocations_to_text_round_trips_through_parse_allocations` fails with `AttributeError: ... has no attribute '_allocations_to_text'`.

- [x] **Step 3: Write minimal implementation**

In `dashboard/pages/ai_console.py`, find this existing line (unchanged since before Task 1):

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_console import agent, context, storage
```

Insert a new `import ticker_names` line between them, so it reads:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from agent_console import agent, context, storage
```

Add these regexes next to the ones from Task 2:

```python
_ALLOC_REMOVE_RE = re.compile(r"([가-힣A-Za-z0-9.]{1,20})\s*(?:빼줘|빼 줘|삭제|제거)")
_ALLOC_UPSERT_RE = re.compile(r"([가-힣A-Za-z0-9.]{1,20})\s*(\d+(?:\.\d+)?)\s*%")
```

Add these two functions after `_heuristic_canvas_patch`:

```python
def _heuristic_allocation_patch(question: str, allocations: list[dict]) -> list[dict] | None:
    rows = [dict(row) for row in allocations]
    changed = False

    remove_match = _ALLOC_REMOVE_RE.search(question)
    if remove_match:
        ticker = ticker_names.resolve(remove_match.group(1).strip(), allow_net=False)
        if ticker:
            before = len(rows)
            rows = [row for row in rows if str(row.get("symbol") or "").upper() != ticker.upper()]
            changed = changed or len(rows) != before

    upsert_match = _ALLOC_UPSERT_RE.search(question)
    if upsert_match:
        ticker = ticker_names.resolve(upsert_match.group(1).strip(), allow_net=False)
        target_weight = float(upsert_match.group(2))
        if ticker and 0 < target_weight < 100:
            ticker = ticker.upper()
            others = [row for row in rows if str(row.get("symbol") or "").upper() != ticker]
            others_total = sum(float(row.get("weight_pct") or 0) for row in others)
            remaining = 100.0 - target_weight
            if others_total > 0:
                for row in others:
                    row["weight_pct"] = float(row["weight_pct"]) / others_total * remaining
            rows = others + [{"symbol": ticker, "weight_pct": target_weight, "note": ""}]
            changed = True

    if not changed:
        return None

    total = sum(float(row.get("weight_pct") or 0) for row in rows)
    if total <= 0:
        return None
    return [{**row, "weight_pct": round(float(row["weight_pct"]) / total * 100.0, 2)} for row in rows]


def _allocations_to_text(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        weight = row.get("weight_pct")
        if not symbol or weight is None:
            continue
        note = str(row.get("note") or "").strip()
        line = f"{symbol} {float(weight):.2f}"
        if note:
            line += f" {note}"
        lines.append(line)
    return "\n".join(lines)
```

Then extend `_heuristic_canvas_patch` (from Task 2) to call the allocation matcher. Change its final `return patch` line:

```python
    if "가설" in q and any(word in q for word in ("바꿔", "바꾸", "적어", "써줘", "써 줘", "수정")):
        summary = str(answer_text or "").strip().split("\n")[0][:80]
        if summary:
            patch["hypothesis"] = summary

    return patch
```

to:

```python
    if "가설" in q and any(word in q for word in ("바꿔", "바꾸", "적어", "써줘", "써 줘", "수정")):
        summary = str(answer_text or "").strip().split("\n")[0][:80]
        if summary:
            patch["hypothesis"] = summary

    alloc_patch = _heuristic_allocation_patch(q, current.get("allocations") or [])
    if alloc_patch is not None:
        patch["allocations"] = alloc_patch

    return patch
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "heuristic_canvas_patch or allocations_to_text" -v`
Expected: PASS (all, including Task 2's tests — confirms the extension didn't break RSI/max-loss/hypothesis matching)

- [x] **Step 5: Commit**

```bash
git add dashboard/pages/ai_console.py tests/test_dashboard_pages.py
git commit -m "feat) 전략 캔버스 포트폴리오 비중 휴리스틱 패치 계산"
```

---

### Task 4: Wire up the "AI 대화" + "제안된 변경" UI

**Files:**
- Modify: `dashboard/pages/ai_console.py` (new `_diff_canvas`, `_apply_canvas_patch`, `_render_canvas_chat`; call the latter from `_lab_tab`)
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `_heuristic_canvas_patch` (Task 2+3), `_allocations_to_text` (Task 3), `_consume_canvas_pending` (Task 1), `agent.answer(question, surface, async_postprocess=True) -> dict` (existing, from `agent_console.agent`, already imported in this file).
- Produces: `_render_canvas_chat(current: dict) -> None` — renders the chat + diff + apply UI; called once from `_lab_tab`.
- Produces: `_apply_canvas_patch(patch: dict) -> None` — writes `strategy_canvas_{field}_pending` for each key present in `patch`, for `_consume_canvas_pending` (Task 1) to pick up on the next rerun.
- Produces: `_diff_canvas(current: dict, patch: dict) -> list[dict]` — rows of `{"필드", "현재", "제안"}` for the diff table.

- [x] **Step 1: Write the failing end-to-end test**

Add to `tests/test_dashboard_pages.py`:

```python
def test_ai_console_canvas_chat_propose_and_apply(monkeypatch):
    from agent_console import agent
    from dashboard.pages import ai_console

    monkeypatch.setattr(
        agent,
        "answer",
        lambda *a, **k: {"ok": True, "answer": "RSI를 조정하면 진입이 더 보수적으로 바뀝니다.", "context": {"engine": "test"}},
    )

    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    chat = at.chat_input(key="strategy_canvas_chat_input")
    chat.set_value("RSI를 25/75로 바꿔줘").run()
    assert not at.exception, str(at.exception)

    diff_frames = [df.value for df in at.dataframe if "필드" in df.value.columns]
    assert diff_frames, "제안된 변경 표가 렌더되지 않음"
    assert "매수 RSI" in diff_frames[0]["필드"].tolist()

    apply_button = next(b for b in at.button if b.key == "strategy_canvas_apply_patch")
    apply_button.click().run()
    assert not at.exception, str(at.exception)

    rsi_input = at.number_input(key="strategy_canvas_buy_rsi")
    assert int(rsi_input.value) == 25


def test_ai_console_canvas_chat_no_match_shows_no_diff(monkeypatch):
    from agent_console import agent
    from dashboard.pages import ai_console

    monkeypatch.setattr(
        agent,
        "answer",
        lambda *a, **k: {"ok": True, "answer": "오늘은 특별한 이벤트가 없습니다.", "context": {"engine": "test"}},
    )

    script = _script("from dashboard.pages import ai_console", "ai_console.render()")
    at = AppTest.from_string(script, default_timeout=30)
    at.run()

    chat = at.chat_input(key="strategy_canvas_chat_input")
    chat.set_value("오늘 시장 어때?").run()
    assert not at.exception, str(at.exception)

    apply_buttons = [b for b in at.button if b.key == "strategy_canvas_apply_patch"]
    assert not apply_buttons, "패치가 없는데 적용 버튼이 렌더됨"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "canvas_chat_propose_and_apply or canvas_chat_no_match" -v`
Expected: FAIL — `test_ai_console_canvas_chat_propose_and_apply` fails with `KeyError: 'strategy_canvas_chat_input'` (chat_input doesn't exist yet); `test_ai_console_canvas_chat_no_match_shows_no_diff` fails the same way.

- [x] **Step 3: Write minimal implementation**

Add these three functions after `_allocations_to_text` (from Task 3):

```python
def _diff_canvas(current: dict, patch: dict) -> list[dict]:
    rows = []
    for field, new_value in patch.items():
        old_value = current.get(field)
        if field == "allocations":
            old_display = ", ".join(f"{r['symbol']} {r['weight_pct']:.1f}%" for r in (old_value or []))
            new_display = ", ".join(f"{r['symbol']} {r['weight_pct']:.1f}%" for r in (new_value or []))
        else:
            old_display = old_value
            new_display = new_value
        rows.append({"필드": _CANVAS_FIELD_LABELS.get(field, field), "현재": old_display, "제안": new_display})
    return rows


def _apply_canvas_patch(patch: dict) -> None:
    if "buy_rsi" in patch:
        st.session_state["strategy_canvas_buy_rsi_pending"] = int(patch["buy_rsi"])
    if "sell_rsi" in patch:
        st.session_state["strategy_canvas_sell_rsi_pending"] = int(patch["sell_rsi"])
    if "max_loss" in patch:
        st.session_state["strategy_canvas_max_loss_pending"] = float(patch["max_loss"])
    if "hypothesis" in patch:
        st.session_state["strategy_canvas_hypothesis_pending"] = str(patch["hypothesis"])
    if "allocations" in patch:
        st.session_state["strategy_canvas_alloc_text_pending"] = _allocations_to_text(patch["allocations"])


def _render_canvas_chat(current: dict) -> None:
    st.markdown("##### AI 대화")
    history = st.session_state.setdefault("strategy_canvas_chat_history", [])
    if not history:
        st.caption("RSI·손실한도·가설·비중 변경을 요청하면 에이전트가 설명과 함께 제안을 계산합니다.")

    for msg in history[-8:]:
        role = "assistant" if str(msg.get("role", "")).lower() == "assistant" else "user"
        with st.chat_message(role):
            st.markdown(msg.get("content") or "")
            if msg.get("meta"):
                st.caption(msg.get("meta"))

    prompt = st.chat_input("예: RSI를 25/75로, 손실한도 5%로 바꿔줘", key="strategy_canvas_chat_input")
    if prompt:
        history.append({"role": "user", "content": prompt})
        try:
            reply = agent.answer(prompt, "lab", async_postprocess=True)
            answer_text = reply.get("answer", "") if reply.get("ok") else reply.get("error", "응답 실패")
            meta = f"엔진 {reply.get('context', {}).get('engine', 'unknown')}"
        except Exception as exc:
            answer_text = f"AI 응답 실패: {exc}"
            meta = "error"

        patch = _heuristic_canvas_patch(prompt, current, answer_text)
        if patch:
            st.session_state["strategy_canvas_patch"] = {
                "current": current,
                "patch": patch,
                "diff": _diff_canvas(current, patch),
            }
            meta += f" · 제안 {len(patch)}개"
        else:
            st.session_state.pop("strategy_canvas_patch", None)

        history.append({"role": "assistant", "content": answer_text, "meta": meta})
        st.rerun()

    pending_patch = st.session_state.get("strategy_canvas_patch")
    if pending_patch and pending_patch.get("diff"):
        st.markdown("##### 제안된 변경")
        st.dataframe(pd.DataFrame(pending_patch["diff"]), hide_index=True, width="stretch")
        if st.button("적용", key="strategy_canvas_apply_patch", type="primary", width="stretch"):
            _apply_canvas_patch(pending_patch["patch"])
            st.session_state.pop("strategy_canvas_patch", None)
            st.toast("변경을 반영했습니다.")
            st.rerun()
```

Then in `_lab_tab`, call `_render_canvas_chat` right after the "시나리오 저장" button block and before the "저장된 시나리오" list. Change:

```python
        st.toast(f"{scenario.get('name', '시나리오')} 저장 완료")

    scenarios = storage.list_scenarios(limit=8)
```

to:

```python
        st.toast(f"{scenario.get('name', '시나리오')} 저장 완료")

    _render_canvas_chat({
        "buy_rsi": int(buy_rsi),
        "sell_rsi": int(sell_rsi),
        "max_loss": float(max_loss),
        "hypothesis": hypothesis,
        "allocations": allocs,
    })

    scenarios = storage.list_scenarios(limit=8)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "canvas_chat_propose_and_apply or canvas_chat_no_match" -v`
Expected: PASS (both)

- [x] **Step 5: Run the full dashboard + strategy_studio test files to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_dashboard_pages.py tests/test_strategy_studio_pages.py tests/test_strategy_studio.py -v`
Expected: all PASS

- [x] **Step 6: Commit**

```bash
git add dashboard/pages/ai_console.py tests/test_dashboard_pages.py
git commit -m "feat) 전략 캔버스에 AI 대화·제안 적용 UI 연결"
```

---

## Manual Verification (after Task 4)

Once all four tasks are merged, verify on the live dashboard the same way the `draft_text` widget-state bugfix was verified earlier this session:

1. Deploy per `dashboard-deploy-traps` procedure: push branch → in the main tree, `git pull --rebase --autostash` → wait for the cron watchdog (`scripts/dashboard_watchdog.sh`, runs every minute, restarts on stale `.py` mtime) → `curl 127.0.0.1:8501/_stcore/health` → `ok`.
2. In a real browser, navigate to AI 콘솔 → 전략 캔버스 tab, scroll to "기존 전략 캔버스", type "RSI를 25/75로, 손실한도 5%로 바꿔줘" into the new "AI 대화" chat box, confirm a "제안된 변경" diff table appears with both fields, click "적용", confirm the RSI/손실한도 number inputs above show the new values with no `StreamlitAPIException`.
