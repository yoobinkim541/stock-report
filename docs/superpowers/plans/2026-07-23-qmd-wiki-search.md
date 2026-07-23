# qmd Wiki Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional qmd-backed local search for the LLM wiki without breaking the existing shared-memory wiki store.

**Architecture:** Add a focused `agent_console/qmd_search.py` adapter for CLI execution, markdown export, and result normalization. Wire `agent_console/wiki.py` so query searches prefer qmd results and fall back to current scoring when qmd is unavailable.

**Tech Stack:** Python stdlib, pytest, existing `agent_console.shared_memory`, optional `qmd` CLI.

## Global Constraints

- No hard dependency on qmd; missing binary must not break AI console or wiki pages.
- Existing wiki page APIs and page shapes must remain compatible.
- qmd search must be bounded by a short timeout.
- Tests must demonstrate RED before production code changes.

---

### Task 1: qmd Adapter

**Files:**
- Create: `agent_console/qmd_search.py`
- Test: `tests/test_qmd_search.py`

**Interfaces:**
- Produces: `enabled() -> bool`, `export_pages(pages: list[dict]) -> dict`, `search(query: str, *, limit: int = 10, surface: str = "all", status: str = "all", runner=subprocess.run) -> list[dict]`, `status() -> dict`

- [ ] **Step 1: Write failing tests** for enabled flags, markdown export, JSON parsing, and command fallback.
- [ ] **Step 2: Run tests and confirm failures** because `agent_console.qmd_search` does not exist.
- [ ] **Step 3: Implement minimal adapter** using `subprocess.run`, env config, flexible JSON parsing, and safe markdown filenames.
- [ ] **Step 4: Run tests and confirm pass** for `tests/test_qmd_search.py`.

### Task 2: Wiki Integration

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `qmd_search.search()` and `qmd_search.export_pages()`
- Produces: qmd-prioritized results from `wiki.list_pages()` and qmd-backed `[위키 지식]` sections.

- [ ] **Step 1: Write failing tests** showing qmd results outrank fallback pages and appear in `build_context_section()`.
- [ ] **Step 2: Run the targeted tests and confirm failures** because wiki does not call qmd.
- [ ] **Step 3: Integrate qmd lookup** after shared-memory page loading and before fallback sorting.
- [ ] **Step 4: Run targeted tests and confirm pass**.

### Task 3: Verification

**Files:**
- Verify: `agent_console/qmd_search.py`, `agent_console/wiki.py`, `tests/test_qmd_search.py`, `tests/test_agent_console.py`

- [ ] **Step 1: Run focused pytest**: `uv run pytest tests/test_qmd_search.py tests/test_agent_console.py -q -k "qmd or wiki_capture_and_context_section or agent_context_prompt_includes_wiki"`
- [ ] **Step 2: Run py_compile**: `python3 -m py_compile agent_console/qmd_search.py agent_console/wiki.py`
- [ ] **Step 3: Run git diff check**: `git diff --check`
