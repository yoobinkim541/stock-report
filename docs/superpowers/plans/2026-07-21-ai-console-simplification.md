# AI Console Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI Console feel like a single chat-first surface by merging memory and wiki into one knowledge area and folding rarely used tools behind one advanced panel.

**Architecture:** Keep `dashboard/pages/ai_console.py` as the only visible AI Console surface in the Streamlit navigation. The page should center on chat, with a compact context rail on the right, a unified `기억·위키` drawer for World Memory and wiki pages, and a single `고급 도구` drawer for strategy canvas and local connectors. Remove the separate AI Wiki page from navigation so the console feels like one product instead of several loosely related screens.

**Tech Stack:** Python 3.11, Streamlit, pandas, Plotly, existing `agent_console` modules.

## Global Constraints

- Preserve `agent_console/wiki.py`, `agent_console/context.py`, and the wiki API routes.
- No new runtime dependencies.
- No change to World Memory or wiki storage formats.
- Keep the app local-first; do not add new external services or auth flows.
- Remove any page from `dashboard/app.py` navigation in the same change set if it is no longer meant to be user-facing.

---

### Task 1: Remove the standalone AI Wiki surface

**Files:**
- Modify: `dashboard/app.py`
- Delete: `dashboard/pages/retired_wiki_page.py`
- Modify: `docs/agent-console.md`

**Interfaces:**
- Consumes: `dashboard.pages.ai_console.render`, `st.navigation`
- Produces: a single visible AI Console entry in the Streamlit nav; wiki functionality remains reachable only inside AI Console

- [ ] **Step 1: Write the regression check**

Update the dashboard smoke coverage so the app still imports and renders when the standalone wiki page is no longer present. Keep the existing `ai_console` page render smoke test and remove any direct dependency on `dashboard.pages.ai_console`.

- [ ] **Step 2: Remove the nav entry**

Delete the `st.Page(retired_wiki_page.render, ...)` registration from `dashboard/app.py` and remove `retired_wiki_page` from the import list and `st.navigation([...])` page array.

- [ ] **Step 3: Delete the unused page file**

Remove `dashboard/pages/retired_wiki_page.py` once the AI Console owns the wiki workflow.

- [ ] **Step 4: Sync the docs**

Edit `docs/agent-console.md` so it says the console exposes one AI Console surface with a unified knowledge area instead of a separate AI Wiki page.

- [ ] **Step 5: Verify the app still starts**

Run: `./.venv/bin/python -m py_compile dashboard/app.py dashboard/pages/ai_console.py`
Expected: no syntax errors.

---

### Task 2: Rebuild AI Console as a chat-first page with two drawers

**Files:**
- Modify: `dashboard/pages/ai_console.py`
- Modify: `tests/test_agent_console.py`
- Modify: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `agent_console.agent.answer`, `agent_console.context.context_pack`, `agent_console.storage`, `agent_console.wiki`
- Produces: `render()`, `_chat_tab(...)`-style logic refactored into `chat`, `memory/wiki`, and `advanced tools` sections that stay in the same file

- [ ] **Step 1: Write the behavior tests**

Add or update a smoke test that renders `dashboard.pages.ai_console.render()` and confirms the page still loads after the tab simplification. Add a small unit test for any new helper that returns the unified quick prompt list so the page keeps the prompt count intentionally small.

- [ ] **Step 2: Replace the tab bar with sections**

Refactor `dashboard/pages/ai_console.py` so the main page becomes:
1. a chat-first center column,
2. a compact context rail,
3. a collapsed `기억·위키` drawer,
4. a collapsed `고급 도구` drawer.

Keep the existing chat history and context inference behavior, but stop exposing `시장 기억`, `기억·위키`, `전략 캔버스`, and `로컬 커넥터` as top-level tabs.

- [ ] **Step 3: Merge memory and wiki into one drawer**

Move the current `World Memory` table, manual memory entry form, wiki search/list/editor, and “promote current conversation to wiki” flow into the same `기억·위키` drawer. The drawer should let the user search or edit a wiki page without having to leave the chat surface.

- [ ] **Step 4: Fold advanced tools behind one expander**

Move the strategy canvas and local connector controls under a single `고급 도구` expander. Keep the RSI canvas and Arca proxy actions available, but no longer visible on the first scan of the page.

- [ ] **Step 5: Trim the fast prompts**

Reduce the quick prompt row to three short prompts that map cleanly to the main jobs of the page: chat, portfolio risk, and memory capture.

- [ ] **Step 6: Verify the page behavior**

Run: `./.venv/bin/pytest -q tests/test_dashboard_pages.py tests/test_agent_console.py -q`
Expected: the AI Console page renders cleanly and the wiki/context tests still pass.

---

### Task 3: Refresh the operator docs and confirm the cleanup

**Files:**
- Modify: `docs/agent-console.md`
- Modify: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: the simplified `dashboard/pages/ai_console.py` and the removed `dashboard/pages/retired_wiki_page.py`
- Produces: documentation and tests that describe the chat-first AI Console accurately

- [ ] **Step 1: Update the operator guide**

Rewrite the AI Console section in `docs/agent-console.md` so it explains the new layout in plain language: chat first, knowledge drawer second, advanced tools last.

- [ ] **Step 2: Remove stale references**

Search the repo for `기억·위키` as a standalone page label and delete or rewrite references that still suggest it is a separate destination.

- [ ] **Step 3: Run the final verification pass**

Run: `./.venv/bin/python -m pytest -q tests/test_dashboard_pages.py tests/test_agent_console.py -q`
Expected: all dashboard and agent-console tests pass with the simplified layout.

- [ ] **Step 4: Commit**

```bash
git add dashboard/app.py dashboard/pages/ai_console.py docs/agent-console.md tests/test_agent_console.py tests/test_dashboard_pages.py docs/superpowers/plans/2026-07-21-ai-console-simplification.md
git rm dashboard/pages/retired_wiki_page.py
git commit -m "add) AI 콘솔을 채팅 중심으로 단순화"
```
