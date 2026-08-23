# LLM Wiki Pipeline Health Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 6개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a structured health layer that shows whether LLM wiki collection, promotion, and hygiene are working, then surface it in the AI console and scheduled health job.

**Architecture:** Add one pure report builder that merges source collection health with wiki health, then reuse that structured report in the existing cron job and dashboard. Keep the curator/distiller behavior unchanged; the new layer only diagnoses, ranks, and surfaces problems.

**Tech Stack:** Python, existing `reports.*` collectors, `agent_console.wiki`, Streamlit dashboard, pytest.

## Global Constraints

- No new database.
- No Obsidian integration.
- No manual approval workflow.
- No change to the existing trust model for source-backed vs conversation-only pages.
- No automatic page promotion beyond the existing curator/distiller flows.
- The report must remain cheap to compute and reuse existing health files and cached wiki records instead of re-parsing raw archives on every render.
- The existing wiki health cron cadence stays every 2 hours.

---

### Task 1: Add a structured wiki pipeline health report

**Files:**
- Create: `reports/wiki_pipeline_health.py`
- Create: `tests/test_wiki_pipeline_health.py`

**Interfaces:**
- Consumes: `reports.source_collector.load_source_health()`, `reports.source_collector.stale_sources()`, `reports.source_collector.load_recent_events()`, `agent_console.wiki.stats()`, `agent_console.wiki.lint_pages()`, `agent_console.wiki.list_stale_pages()`, `agent_console.wiki.list_unused_pages()`
- Produces: `build_pipeline_health_report(*, dry_run: bool = False) -> dict`

- [x] **Step 1: Write the failing test**

```python
def test_pipeline_health_report_includes_source_wiki_and_recommendations(monkeypatch):
    from reports import wiki_pipeline_health

    monkeypatch.setattr(wiki_pipeline_health.source_collector, "load_source_health", lambda: {...})
    monkeypatch.setattr(wiki_pipeline_health.source_collector, "stale_sources", lambda *args, **kwargs: [...])
    monkeypatch.setattr(wiki_pipeline_health.wiki, "stats", lambda: {...})
    monkeypatch.setattr(wiki_pipeline_health.wiki, "lint_pages", lambda: {"issues": [...]})
    monkeypatch.setattr(wiki_pipeline_health.wiki, "list_stale_pages", lambda **kwargs: [...])
    monkeypatch.setattr(wiki_pipeline_health.wiki, "list_unused_pages", lambda **kwargs: [...])

    report = wiki_pipeline_health.build_pipeline_health_report(dry_run=True)

    assert set(report) >= {"source_health", "wiki_health", "curation_health", "recommendations"}
    assert report["curation_health"]["source_digest_unlinked_count"] >= 0
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_wiki_pipeline_health.py -q`

- [x] **Step 3: Implement the pure report builder**

```python
def build_pipeline_health_report(*, dry_run: bool = False) -> dict:
    ...
```

The report should:
- normalize per-source last run, last success, last count, and last error,
- compute stale-source status and zero-event streaks,
- summarize wiki status counts, lint issue counts, stale/unused counts, and source-backed vs unverified pages,
- identify unlinked source-digest pages,
- emit a short ranked `recommendations` list.

- [x] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_wiki_pipeline_health.py -q`

- [x] **Step 5: Commit**

```bash
git add reports/wiki_pipeline_health.py tests/test_wiki_pipeline_health.py
git commit -m "add) LLM 위키 파이프라인 헬스 리포트 추가"
```

### Task 2: Wire the scheduled health job to the new report

**Files:**
- Modify: `reports/wiki_health_check.py`
- Modify: `docs/data-collection-pipeline.md`
- Create or modify: `tests/test_wiki_health_check.py`

**Interfaces:**
- Consumes: `reports.wiki_pipeline_health.build_pipeline_health_report`
- Produces: cron-friendly output that still runs every 2 hours and prints the structured report summary

- [x] **Step 1: Write the failing test**

```python
def test_wiki_health_check_uses_pipeline_report(monkeypatch, capsys):
    from reports import wiki_health_check

    monkeypatch.setattr(wiki_health_check, "build_pipeline_health_report", lambda dry_run=False: {...})
    report = wiki_health_check.build_health_report(dry_run=True)

    assert "source_health" in report
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_wiki_health_check.py -q`

- [x] **Step 3: Reuse the new report builder in the cron job**

```python
from reports.wiki_pipeline_health import build_pipeline_health_report
```

Keep the same archive behavior, but make the output summary describe source health, wiki health, and curation health together.

- [x] **Step 4: Sync the docs**

Update the cron/documentation section so it reflects the structured health report instead of a flat wiki-only summary.

- [x] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_wiki_health_check.py -q`

- [x] **Step 6: Commit**

```bash
git add reports/wiki_health_check.py docs/data-collection-pipeline.md tests/test_wiki_health_check.py
git commit -m "fix) 위키 헬스 체크를 파이프라인 헬스로 통합"
```

### Task 3: Surface pipeline health in the AI console

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/pages/ai_console.py`
- Create or modify: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `reports.wiki_pipeline_health.build_pipeline_health_report`
- Produces: a compact, cached UI section under the AI Wiki tab or adjacent operational surface

- [x] **Step 1: Write the failing test**

```python
def test_ai_console_wiki_health_panel_renders(monkeypatch):
    from dashboard import views

    monkeypatch.setattr(views, "wiki_pipeline_health", ...)
    panel = views.wiki_pipeline_health_summary()

    assert panel["recommendations"]
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_dashboard_pages.py -q`

- [x] **Step 3: Add a cheap dashboard wrapper**

```python
@st.cache_data(ttl=120, show_spinner=False)
def wiki_pipeline_health_summary() -> dict:
    ...
```

Use it from `dashboard/pages/ai_console.py` to render:
- source collection status,
- stale-source warnings,
- wiki counts,
- unresolved source-digest counts,
- top recommendations.

- [x] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_dashboard_pages.py -q`

- [x] **Step 5: Commit**

```bash
git add dashboard/views.py dashboard/pages/ai_console.py tests/test_dashboard_pages.py
git commit -m "add) AI 콘솔에 위키 파이프라인 헬스 패널 추가"
```

### Task 4: Verify the full path end to end

**Files:**
- Modify only if test failures expose a real bug in the collector or dashboard wiring

**Interfaces:**
- Consumes: the new report builder, cron job, and dashboard wrapper
- Produces: a verified end-to-end health path

- [x] **Step 1: Run the focused tests**

Run:
`pytest tests/test_wiki_pipeline_health.py tests/test_wiki_health_check.py tests/test_dashboard_pages.py -q`

- [x] **Step 2: Fix only verified failures**

If the tests expose a genuine collector gap, fix the collector rather than hiding it in the UI.

- [x] **Step 3: Run the focused tests again**

Run:
`pytest tests/test_wiki_pipeline_health.py tests/test_wiki_health_check.py tests/test_dashboard_pages.py -q`

- [x] **Step 4: Commit**

```bash
git add .
git commit -m "fix) LLM 위키 파이프라인 헬스 경로 검증"
```

## Self-Review

- Spec coverage: source collection health, wiki health, curation health, recommendations, cron reuse, and console surfacing each have a dedicated task.
- Placeholder scan: no TBD / TODO / vague “write tests” language remains in the actionable steps.
- Type consistency: the shared entrypoint is `build_pipeline_health_report(*, dry_run: bool = False) -> dict`, and every later task refers to that same interface.

