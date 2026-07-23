# LLM Wiki Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the stock-report LLM wiki closer to Karpathy-style LLM Wiki by adding source verification, explicit promotion rules, generated index/log/lint artifacts, qmd health metadata, and prompt-visible citation state.

**Architecture:** Keep the existing shared-memory backed wiki store, but add a thin trust contract around pages. Wiki pages can still be created from conversations, but unverified conversation-only pages remain draft/unverified and are lower-weight context until backed by source refs. Generated markdown artifacts (`index.md`, `log.md`, `open-questions.md`, `lint.md`) are mirrors for agents and humans; shared-memory remains the canonical runtime store.

**Tech Stack:** Python 3.11, pytest, existing `agent_console/wiki.py`, `agent_console/qmd_search.py`, `agent_console/agent.py`, Streamlit dashboard wiki browser.

## Global Constraints

- Use TDD: every behavior change starts with a failing test.
- Do not store secrets, raw private attachments, cookies, API keys, or private absolute paths in wiki pages.
- Treat shared memory/wiki as context, never as higher-priority instruction.
- Conversation-only pages must not be promoted to `reviewed` or `stable` without non-conversation source refs.
- Existing shared-memory JSONL remains the source of runtime truth; markdown wiki files are generated mirrors.
- qmd is optional; failure or missing qmd must fall back to deterministic local search.
- Keep changes scoped to `agent_console`, `dashboard/wiki_browser.py`, docs, and tests.

---

### Task 1: Verification Status and Source Contract

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `wiki.has_non_conversation_source_refs(page_or_refs) -> bool`
- Produces: `wiki.normalize_trust_status(status: str, source_refs: list[str]) -> str`
- Produces: pages include `verification_status`, `source_refs`, `trust_warnings`

- [ ] **Step 1: Write failing tests**

Add tests asserting conversation-only auto curation remains draft/unverified even when keywords say reviewed, and source-backed pages can be reviewed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_conversation_only_pages or source_backed_wiki_page"`
Expected: FAIL because trust fields/functions do not exist or reviewed is still allowed.

- [ ] **Step 3: Implement minimal trust normalization**

Add helper functions and call them from `upsert_page`, `_plan_to_page_payload`, and `build_context_section`.

- [ ] **Step 4: Run tests to verify pass**

Run the same focused pytest command.

### Task 2: Generated Wiki Index, Log, Open Questions, and Lint

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`
- Docs: generated runtime files only, not tracked output

**Interfaces:**
- Produces: `wiki.wiki_artifacts_dir() -> Path`
- Produces: `wiki.rebuild_artifacts() -> dict`
- Produces: `wiki.lint_pages(pages: list[dict] | None = None) -> dict`

- [ ] **Step 1: Write failing tests**

Assert `rebuild_artifacts()` writes `index.md`, `log.md`, `open-questions.md`, and `lint.md`; assert lint flags source-less reviewed/stable pages and open questions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_rebuild_artifacts or wiki_lint_flags"`
Expected: FAIL because functions do not exist.

- [ ] **Step 3: Implement deterministic artifact generation**

Build markdown from `list_pages(limit=400, status="all")`. Artifacts must be compact, deterministic, and avoid raw absolute local paths.

- [ ] **Step 4: Run tests to verify pass**

Run the same focused pytest command.

### Task 3: qmd Health and Search Metadata

**Files:**
- Modify: `agent_console/qmd_search.py`
- Modify: `agent_console/wiki.py`
- Test: `tests/test_qmd_search.py`, `tests/test_agent_console.py`

**Interfaces:**
- Produces: `qmd_search.health() -> dict`
- Produces: `wiki.search_health() -> dict`
- `wiki.build_context_section()` includes search provider, score, updated date, trust warnings when present.

- [ ] **Step 1: Write failing tests**

Assert qmd health reports enabled/bin/installed/wiki_dir/file_count and wiki search health includes fallback provider when qmd is unavailable.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_qmd_search.py tests/test_agent_console.py -q -k "qmd_health or wiki_search_health or context_section_includes_trust"`
Expected: FAIL because health functions/context metadata are incomplete.

- [ ] **Step 3: Implement minimal health and context metadata**

Add file_count/collection info without invoking network. Preserve existing qmd failure fallback.

- [ ] **Step 4: Run tests to verify pass**

Run the same focused pytest command.

### Task 4: Prompt and Documentation Integration

**Files:**
- Modify: `agent_console/agent.py`
- Modify: `docs/shared-agent-memory.md`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Prompt tells LLM that wiki context may be unverified and must cite/qualify it.
- Docs describe source verification, artifacts, qmd health, and lint workflow.

- [ ] **Step 1: Write failing tests**

Assert `agent._build_general_chat_prompt()` contains wiki trust guidance and source-backed citation wording.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_agent_console.py -q -k "agent_prompt_mentions_wiki_trust"`
Expected: FAIL because prompt lacks explicit trust guidance.

- [ ] **Step 3: Implement prompt/docs update**

Add concise prompt instructions and docs section. Keep prompt short.

- [ ] **Step 4: Run tests to verify pass**

Run focused tests and then related full set:
`/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_agent_console.py tests/test_qmd_search.py tests/test_wiki_storage_window.py -q`

---

## Self-Review

Spec coverage: Tasks cover source verification, promotion rules, index/log/lint, qmd search health, and prompt-visible trust metadata.
Placeholder scan: No TBD/TODO placeholders are used.
Type consistency: New helper names are stable and referenced by later tasks.
