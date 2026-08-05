# Strategy Canvas AI Chat Design

Date: 2026-08-05

## Purpose

`dashboard/pages/ai_console.py` → `_lab_tab()` renders two stacked sections inside the "전략 캔버스" tab:

1. **전략 스튜디오** (`strategy_studio.render_strategy_lab(...)`) — already has an "AI 대화" panel: chat with the agent, agent proposes a patch to the StrategySpec JSON, user reviews a diff and clicks "패치 반영" to apply it.
2. **기존 전략 캔버스** (`_lab_tab()` body, lines ~631-672) — a simpler, older feature: free-text portfolio allocations, buy/sell RSI thresholds, a max-loss cap, a hypothesis text box, and a "시나리오 저장" button that persists a scenario via `storage.save_scenario(...)`. It has no chat UI at all.

The user wants the same conversational pattern added to section 2, so they can ask the agent to change RSI thresholds, the max-loss cap, the hypothesis text, or portfolio weights, review the proposed change, and apply it with one click — instead of hand-editing each widget.

## Current State

- `_lab_tab()` renders `buy_rsi`, `sell_rsi`, `max_loss` as `st.number_input(...)` and `hypothesis` as `st.text_area(...)` **without a `key=`**. They reset to their hardcoded defaults (30 / 70 / 8.0 / empty) on every rerun — nothing persists them today.
- `alloc_text` (the raw multi-line "TICKER WEIGHT note" block) already has `key="strategy_canvas_alloc_text"` and is parsed by `_parse_allocations` / `_normalize_allocations` into `[{symbol, weight_pct, note}]` rows, normalized to sum to 100.
- `_strategy_canvas_backtest()` exists in the same file but is currently unused (dead code) — out of scope here, not touched.
- The existing "AI 대화" pattern in `strategy_studio.py` (`_render_conversation_panel`) is the template to follow:
  - `agent.answer(prompt, surface, async_postprocess=True)` produces the prose explanation.
  - `views.strategy_studio.propose_strategy_patch(...)` (→ `agent_console/strategy_studio.py::propose_strategy_patch`) computes the structured change via `_heuristic_patch()` — **keyword/regex matching, not LLM function-calling**. Only the prose answer comes from the real LLM; the structured patch is deterministic pattern matching.
  - The proposed patch is stored in session state and rendered in a separate "패치" panel with a diff table and an "패치 반영" button; nothing is auto-applied.
- `ticker_names.resolve(query, allow_net=False) -> str | None` already resolves a Korean/English company name or ticker string to a canonical ticker — reusable for parsing allocation edits.
- This session just fixed a Streamlit trap in `strategy_studio.py`: writing to a widget's `session_state[key]` after that widget has already been instantiated in the same script run raises `StreamlitAPIException`. The fix routes writes through a `<key>_pending` session-state entry that's consumed **before** the widget is created on the next rerun. `buy_rsi`/`sell_rsi`/`max_loss`/`hypothesis` don't have this problem yet only because they have no `key=` at all (so nothing persists) — giving them a `key=` to support chat-driven edits reintroduces the exact same trap unless the pending-key pattern is reused from the start.

## Design Decision

Mirror the existing 전략 스튜디오 AI 대화 pattern for this section, with a canvas-specific (not StrategySpec-specific) propose/apply pair:

- New function `_propose_canvas_patch(question, current, history) -> dict` in `dashboard/pages/ai_console.py`, analogous to `propose_strategy_patch`. `current` is `{"buy_rsi", "sell_rsi", "max_loss", "hypothesis", "allocations"}`.
- New function `_apply_canvas_patch(current, patch) -> dict` that returns the merged values.
- Patch computation is heuristic (regex/keyword), matching the existing codebase's actual capability level — not a new LLM structured-output integration. This keeps behavior predictable and testable, and avoids inventing new agent infrastructure for one feature.
- Values are proposed, never auto-applied. The user reviews a diff and clicks "적용".

## Fields & Heuristic Patch Rules

| Field | Trigger examples | Rule |
|---|---|---|
| `buy_rsi` | "매수 RSI 20으로", "RSI를 25/75로" | Regex for 1-2 integers near `RSI`/`매수`/`현금화`. Clamp to `[1, 99]`. When "RSI를 A/B로" form is used, first number → `buy_rsi`, second → `sell_rsi`. |
| `sell_rsi` | "현금화 RSI 80", "RSI를 25/75로" | Same as above. |
| `max_loss` | "손실한도 5%로", "최대손실 10퍼센트" | Regex for a number near `손실`/`손절` + `%`/`퍼센트`. Clamp to `[0, 100]`. |
| `hypothesis` | "가설을 ~로 바꿔줘", "가설 적어줘" | Only fires on an explicit ask (`가설` + an edit verb: 바꿔/적어/써줘/수정). Uses a one-line summary derived from the agent's own prose answer. Free-form statements that merely *sound* like a hypothesis are **not** auto-captured — too noisy. |
| `allocations` | "삼성전자 20%로", "QQQ 추가해줘", "TLT 빼줘" | Regex extracts a `(name-or-ticker, weight?, action)` tuple; `ticker_names.resolve()` maps the name to a ticker. `추가`/weight-given → upsert row. `빼줘`/`삭제`/`제거` → remove row. After any change, remaining rows are rescaled proportionally so weights still sum to 100. Unresolvable names are skipped (no partial/garbled patch). |

If nothing matches, no patch is proposed — same as today's `_heuristic_patch` behavior. The chat still shows the agent's prose answer.

## UI Layout

Inside `_lab_tab()`, between the "시나리오 저장" button and the "저장된 시나리오" list:

```text
##### AI 대화
[chat history — same rendering as strategy_studio._render_conversation_panel]
[chat_input: "예: RSI를 25/75로, 손실한도 5%로 바꿔줘"]

##### 제안된 변경                (only rendered when a patch is pending)
[diff table: 필드 | 현재 | 제안]
[적용] 버튼
```

## Widget State Changes (prerequisite)

`buy_rsi`, `sell_rsi`, `max_loss`, `hypothesis` gain `key=` params tied to session state (`strategy_canvas_buy_rsi`, etc.), defaulting to their current hardcoded values on first render. Applying a patch writes to `<key>_pending`, consumed at the top of `_lab_tab()` before the widgets are instantiated — the same pattern just applied in `strategy_studio.py::_render_editor_panel`. `allocations` patches rewrite `strategy_canvas_alloc_text` (already keyed) via the same pending-key indirection, serializing the updated rows back to the "TICKER WEIGHT note" text format.

## Out of Scope

- Wiring up the unused `_strategy_canvas_backtest()` function — not requested, kept as-is.
- True LLM structured-output/function-calling for patch computation — heuristic matching only, consistent with the existing `_heuristic_patch`.
- Auto-apply without user confirmation — explicitly rejected during brainstorming.

## Testing

In `tests/test_dashboard_pages.py` (or a new `tests/test_ai_console_canvas_chat.py`):

1. Unit tests for the heuristic patch function: one case per field (RSI pair, max-loss, hypothesis, allocation add/update/remove), plus a no-match case that returns an empty patch.
2. An `AppTest`-driven end-to-end test: type a chat message, verify the diff panel renders the expected proposed change, click "적용", verify the underlying widgets reflect the new value on rerun — with no `StreamlitAPIException`.
3. A regression case mirroring the exact bug just fixed: apply a patch, confirm the rerun doesn't crash (proves the pending-key pattern was applied correctly to the newly-keyed widgets).
