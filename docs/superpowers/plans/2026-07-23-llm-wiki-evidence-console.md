# LLM Wiki Evidence Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an evidence-console layer to the existing AI Wiki UI.

**Architecture:** Keep `agent_console/wiki.py` as the data/trust provider. Add pure model helpers in `dashboard/wiki_browser.py` and `dashboard/wiki_mesh.py`, then render those helpers in Streamlit.

**Tech Stack:** Python 3.11, Streamlit, Plotly, pytest.

## Global Constraints

- Use TDD: add failing tests before implementation.
- Do not add Obsidian or new dependencies.
- Do not make human review a required workflow.
- Keep canonical wiki storage in shared-memory; UI models are derived views.
- qmd must remain optional with fallback search.

---

### Task 1: Browser Health and Evidence Models

**Files:**
- Modify: `dashboard/wiki_browser.py`
- Test: `tests/test_wiki_browser.py`

**Interfaces:**
- `build_wiki_health_model(pages, search_health=None, lint=None) -> dict`
- `build_selected_evidence_model(page, context_section="") -> dict`
- `promotion_guardrail(status, source_refs) -> dict`

Steps:
- Add failing tests for health counts, selected evidence sections, promotion guardrail, and alias matching.
- Implement pure helpers.
- Run `pytest tests/test_wiki_browser.py -q`.

### Task 2: Graph Trust Styling

**Files:**
- Modify: `dashboard/wiki_mesh.py`
- Test: `tests/test_wiki_mesh.py`

**Interfaces:**
- `trust_color_for_node(node) -> str`
- graph nodes include `verification_status`, `trust_warnings`, `lint_issue_count`, `color`

Steps:
- Add failing tests for source-backed, unverified, and lint issue node colors.
- Implement node metadata and rendering color use.
- Run `pytest tests/test_wiki_mesh.py -q`.

### Task 3: Streamlit Rendering Integration

**Files:**
- Modify: `dashboard/wiki_browser.py`
- Test: `tests/test_dashboard_pages.py`

Interfaces:
- Render health metrics above filters.
- Render selected evidence sections before body.
- Render editor guardrail under source refs/status.

Steps:
- Add/adjust smoke tests if needed.
- Implement rendering using the pure helpers from Task 1.
- Run AI console page smoke tests.

### Task 4: Verification and Deployment

Steps:
- Run related tests: `tests/test_wiki_browser.py tests/test_wiki_mesh.py tests/test_dashboard_pages.py tests/test_agent_console.py tests/test_qmd_search.py tests/test_wiki_storage_window.py`.
- Run `py_compile` and `git diff --check`.
- Commit, push master, restart Streamlit, verify `/agent`.
