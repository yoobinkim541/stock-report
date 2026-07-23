# LLM Wiki Cross-Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give stock-report's LLM wiki authored, persisted cross-references between pages (`links` + computed `backlinks`) and lint checks that catch isolated or under-linked pages, closing the biggest gap versus Karpathy's LLM wiki idea.

**Architecture:** Add a `links: list[str]` field to the wiki page record (written once, on the linking page only). `backlinks` are never stored — they're computed at read time by scanning all wiki records. Two ingest paths author links: the LLM/heuristic conversation path (`agent_console/wiki.py::auto_curate_from_chat`) and the deterministic source-event path (`reports/source_wiki_curator.py`). `lint_pages()` gains two relational checks. Rendering (`index.md`, `build_context_section`) surfaces the links. `dashboard/wiki_mesh.py` is untouched.

**Tech Stack:** Python 3.11, pytest, existing `agent_console/wiki.py`, `agent_console/shared_memory.py`, `reports/source_wiki_curator.py`.

## Global Constraints

- No new dependency.
- `links` is a plain, untyped array of target `page_id` strings — no relationship types (no `supersedes`/`contradicts`).
- Cap `links` at 12 entries (`MAX_LINKS`), deduped, self-references dropped.
- Non-existent target ids in `links` are allowed and silently produce no backlink — do not validate existence.
- `backlinks` are always computed at read time (`get_page`, `list_pages`) from a full scan of wiki records; they are never written to a record.
- `dashboard/wiki_mesh.py` is out of scope for this plan — do not modify it.
- `missing_cross_ref` lint match on `source_refs` requires an exact string match (post `_clean` normalization) — no fuzzy matching.
- `orphan_page` severity is `info`; `missing_cross_ref` severity is `warning`; `missing_cross_ref` fires once per unordered page pair, not once per page.
- Existing trust/verification contract (`normalize_trust_status`, `trust_warnings_for`, `VALID_STATUSES`, `VALID_KINDS`) is unaffected by this plan.
- Run tests with `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest <path> -q` from `/home/ubuntu/projects/stock-report`.

---

## File Structure

- Modify `agent_console/wiki.py`: add `links`/`backlinks` data model, backlink computation, lint relational checks, rendering changes, conversation-path link authoring.
- Modify `reports/source_wiki_curator.py`: add deterministic link authoring between pages sharing source events in the same curation batch.
- Modify `tests/test_agent_console.py`: new tests for links/backlinks, lint, rendering.
- Modify `tests/test_source_wiki_curator.py`: new test for deterministic cross-links.

---

### Task 1: Data Model — `links` Field and Computed `backlinks`

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `wiki.MAX_LINKS: int` (constant, value 12)
- Produces: `wiki._clean_links(values, *, self_id: str = "", limit: int = MAX_LINKS) -> list[str]`
- Produces: `wiki._wiki_records() -> list[dict]`
- Produces: `wiki._backlink_index(records: list[dict]) -> dict[str, list[str]]`
- Produces: `wiki._apply_backlinks(pages: list[dict], records: list[dict]) -> list[dict]`
- Produces: pages returned by `get_page`/`list_pages` include `links: list[str]` and `backlinks: list[str]`
- Consumes: `upsert_page(page: dict)` now reads `page.get("links")`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_console.py` (after `test_wiki_capture_and_context_section`, before `test_wiki_conversation_only_pages_stay_unverified_draft`):

```python
def test_wiki_upsert_page_persists_links(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "링크 출발 페이지",
        "summary": "출발 요약",
        "body": "출발 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [target["id"], target["id"], ""],
    })

    assert source["links"] == [target["id"]]


def test_wiki_get_page_and_list_pages_compute_backlinks(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "백링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "백링크 출발 페이지",
        "summary": "출발 요약",
        "body": "출발 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [target["id"]],
    })

    fetched_target = wiki.get_page(target["id"])
    assert fetched_target["backlinks"] == [source["id"]]

    listed = {page["id"]: page for page in wiki.list_pages(surface="market", limit=10)}
    assert listed[target["id"]]["backlinks"] == [source["id"]]
    assert listed[source["id"]]["links"] == [target["id"]]
```

Note: `links: [target["id"], target["id"], ""]` in the first test intentionally includes a duplicate and a blank entry — the assertion `source["links"] == [target["id"]]` confirms dedupe and blank-dropping both work.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_upsert_page_persists_links or wiki_get_page_and_list_pages_compute_backlinks"`
Expected: FAIL — `KeyError: 'links'` or `AssertionError` because pages have no `links`/`backlinks` field yet.

- [ ] **Step 3: Implement the data model**

In `agent_console/wiki.py`:

3a. Change the import line near the top:

```python
from collections import Counter
```
to:
```python
from collections import Counter, defaultdict
```

3b. Add `MAX_LINKS` to the constants block:

```python
WIKI_TAG = "wiki"
WIKI_SURFACE = "wiki"
VALID_STATUSES = ("draft", "reviewed", "stable", "archived")
VALID_KINDS = ("note", "playbook", "decision", "risk", "concept", "source_digest")
```
becomes:
```python
WIKI_TAG = "wiki"
WIKI_SURFACE = "wiki"
VALID_STATUSES = ("draft", "reviewed", "stable", "archived")
VALID_KINDS = ("note", "playbook", "decision", "risk", "concept", "source_digest")
MAX_LINKS = 12
```

3c. Add `_clean_links` immediately after the `_dedupe_texts` function definition:

```python
def _clean_links(values: Iterable[object], *, self_id: str = "", limit: int = MAX_LINKS) -> list[str]:
    filtered = [v for v in (values or []) if _clean(v, 80) != self_id]
    return _dedupe_texts(filtered, limit=limit, item_limit=80)
```

3d. Add `_wiki_records`, `_backlink_index`, `_apply_backlinks` immediately after the `_is_wiki_record` function definition:

```python
def _wiki_records() -> list[dict]:
    try:
        rows = shared_memory.all_records()
    except Exception:
        rows = []
    return [row for row in rows if _is_wiki_record(row)]


def _backlink_index(records: list[dict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for row in records:
        row_id = _clean(row.get("id"), 80)
        if not row_id:
            continue
        for target_id in _clean_links(row.get("links") or [], self_id=row_id):
            index[target_id].append(row_id)
    return index


def _apply_backlinks(pages: list[dict], records: list[dict]) -> list[dict]:
    index = _backlink_index(records)
    for page in pages:
        page_id = _clean(page.get("id"), 80)
        page["backlinks"] = _dedupe_texts(index.get(page_id, []), limit=MAX_LINKS, item_limit=80)
    return pages
```

3e. In `_record_to_page`, add a `links` field to the returned dict. Find:

```python
        "source_refs": source_refs,
        "decisions": decisions,
        "openQuestions": open_questions,
```
and change to:
```python
        "source_refs": source_refs,
        "links": _clean_links(record.get("links") or [], self_id=_clean(record.get("id"), 80)),
        "backlinks": [],
        "decisions": decisions,
        "openQuestions": open_questions,
```

(`backlinks` defaults to `[]` here because `_record_to_page` only sees one record; callers with access to the full record set fill it in via `_apply_backlinks`.)

3f. Replace `list_pages`'s record-fetching and return logic. Find:

```python
    try:
        rows = shared_memory.all_records()
    except Exception:
        rows = []
    records = [row for row in rows if _is_wiki_record(row)]
    if not records:
        return []
    fallback = _fallback_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    qmd_pages = _qmd_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    if not qmd_pages:
        return fallback
    merged: list[dict] = []
    seen: set[str] = set()
    for page in [*qmd_pages, *fallback]:
        page_id = _clean(page.get("id"), 120)
        if page_id and page_id in seen:
            continue
        if page_id:
            seen.add(page_id)
        merged.append(page)
        if len(merged) >= limit:
            break
    return merged
```
with:
```python
    records = _wiki_records()
    if not records:
        return []
    fallback = _fallback_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    qmd_pages = _qmd_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    if not qmd_pages:
        return _apply_backlinks(fallback, records)
    merged: list[dict] = []
    seen: set[str] = set()
    for page in [*qmd_pages, *fallback]:
        page_id = _clean(page.get("id"), 120)
        if page_id and page_id in seen:
            continue
        if page_id:
            seen.add(page_id)
        merged.append(page)
        if len(merged) >= limit:
            break
    return _apply_backlinks(merged, records)
```

3g. Replace `get_page`. Find:

```python
def get_page(page_id: str) -> dict | None:
    page_id = _clean(page_id, 80)
    if not page_id:
        return None
    for row in shared_memory.all_records():
        if row.get("id") == page_id and _is_wiki_record(row):
            return _record_to_page(row)
    return None
```
with:
```python
def get_page(page_id: str) -> dict | None:
    page_id = _clean(page_id, 80)
    if not page_id:
        return None
    records = _wiki_records()
    for row in records:
        if row.get("id") == page_id:
            page = _record_to_page(row)
            return _apply_backlinks([page], records)[0]
    return None
```

3h. In `stats()`, find:

```python
def stats() -> dict:
    rows = [row for row in shared_memory.all_records() if _is_wiki_record(row)]
```
and replace with:
```python
def stats() -> dict:
    rows = _wiki_records()
```

3i. In `upsert_page`, find:

```python
    source_refs = _dedupe_texts(page.get("source_refs") or [], limit=12, item_limit=120)
    status = normalize_trust_status(page.get("status") or "draft", source_refs)
    page_id = _clean(page.get("id") or _page_id(title, surface, kind), 80)

    existing = get_page(page_id) or {}
```
and replace with:
```python
    source_refs = _dedupe_texts(page.get("source_refs") or [], limit=12, item_limit=120)
    status = normalize_trust_status(page.get("status") or "draft", source_refs)
    page_id = _clean(page.get("id") or _page_id(title, surface, kind), 80)
    links = _clean_links(page.get("links") or [], self_id=page_id)

    existing = get_page(page_id) or {}
```

Then find the `record = {` block and add `"links": links,` right after `"artifacts": source_refs,`:

```python
    record = {
        "id": page_id,
        "title": title,
        "summary": _clean(page.get("summary") or "", 2400),
        "body": _clean(page.get("body") or "", 6000),
        "tags": tags,
        "artifacts": source_refs,
        "links": links,
        "messages": page.get("messages") or [],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_upsert_page_persists_links or wiki_get_page_and_list_pages_compute_backlinks"`
Expected: PASS

- [ ] **Step 5: Run the full existing wiki test subset to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k wiki`
Expected: PASS (all existing wiki tests still pass — they don't assert on the new `links`/`backlinks` keys, so adding them is additive)

- [ ] **Step 6: Commit**

```bash
git add agent_console/wiki.py tests/test_agent_console.py
git commit -m "feat) 위키 페이지 links 필드와 읽기시 backlinks 계산 추가"
```

---

### Task 2: Conversation-Path Link Authoring

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `wiki._clean_links` (Task 1), `wiki._candidate_score(record, query, surface, status) -> int` (existing)
- Produces: `wiki._auto_link_candidates(question: str, surface: str, candidates: list[dict], *, exclude_id: str = "", limit: int = 3) -> list[str]`
- Modifies: `wiki._build_auto_curation_prompt` (adds `links` to schema/instructions), `wiki._heuristic_curation_plan` (adds `candidates` param, populates `links`), `wiki._plan_to_page_payload` (persists/merges `links`), `wiki.auto_curate_from_chat` (passes `candidates` through)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_console.py` (after `test_wiki_auto_curate_from_chat_updates_existing_page`, before `test_wiki_api_routes`):

```python
def test_wiki_auto_curate_llm_plan_links_are_persisted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    related = wiki.upsert_page({
        "title": "레버리지 손실한도 기준",
        "summary": "레버리지 상품의 손실한도 원칙",
        "body": "TQQQ 같은 레버리지 상품은 손실한도를 더 좁게 잡는다.",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed"],
    })

    def fake_llm(prompt: str) -> str:
        assert "links" in prompt
        return (
            '{"action":"create","title":"현금 비중과 변동성 예산","summary":"현금 비중은 변동성 예산과 함께 본다.",'
            '"body":"변동성이 커지면 현금 비중을 늘린다.\\n- 변동성 지표 확인\\n- 현금 20% 하한",'
            '"kind":"playbook","status":"reviewed","tags":["risk","portfolio"],'
            '"source_refs":["conversation:003"],"links":["' + related["id"] + '"],'
            '"target_id":"","confidence":0.8,"reason":"related to leverage loss limit"}'
        )

    saved = wiki.auto_curate_from_chat(
        "현금 비중 기준은 변동성 예산과 어떻게 맞춰?",
        "변동성이 커지면 현금 비중을 늘린다.\n- 변동성 지표 확인\n- 현금 20% 하한\n- 손실 한도 검증 필요",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": ["포트폴리오"]},
        history=[],
    )

    assert saved is not None
    assert related["id"] in saved["page"]["links"]

    backfilled = wiki.get_page(related["id"])
    assert saved["page"]["id"] in backfilled["backlinks"]


def test_wiki_auto_curate_heuristic_auto_links_related_candidate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    first = wiki.upsert_page({
        "title": "변동성 예산 원칙 A",
        "summary": "변동성 예산 원칙 A 요약",
        "body": "변동성 예산 원칙 A 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed-a"],
    })
    second = wiki.upsert_page({
        "title": "변동성 예산 원칙 B",
        "summary": "변동성 예산 원칙 B 요약",
        "body": "변동성 예산 원칙 B 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed-b"],
    })

    saved = wiki.auto_curate_from_chat(
        "변동성 예산 원칙을 다시 정리해줘",
        "변동성이 커지면 현금 비중을 늘린다.\n- 변동성 지표 확인\n- 현금 20% 하한\n- 손실 한도 검증 필요",
        surface="portfolio",
        llm=None,
        pack={"focus": []},
        history=[],
    )

    assert saved is not None
    target_id = saved["page"]["id"]
    assert target_id in {first["id"], second["id"]}
    other_id = second["id"] if target_id == first["id"] else first["id"]
    assert other_id in saved["page"]["links"]
    assert target_id not in saved["page"]["links"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_auto_curate_llm_plan_links_are_persisted or wiki_auto_curate_heuristic_auto_links_related_candidate"`
Expected: FAIL — `saved["page"]["links"]` is empty/missing because plans don't produce or persist `links` yet.

- [ ] **Step 3: Implement link authoring**

3a. In `_build_auto_curation_prompt`, find:

```python
        "필드: action, title, summary, body, kind, status, tags, source_refs, target_id, confidence, reason.",
```
and replace with:
```python
        "필드: action, title, summary, body, kind, status, tags, source_refs, links, target_id, confidence, reason.",
        "관련 있는 기존 위키 후보가 있으면 해당 id 를 links 배열에 넣는다. 관련 없으면 links 는 빈 배열이다.",
```

Then find the JSON example line:

```python
        '{"action":"create","title":"손실한도와 레버리지","summary":"...","body":"...","kind":"playbook","status":"reviewed","tags":["risk","portfolio"],"source_refs":["conversation:123"],"target_id":"","confidence":0.86,"reason":"..."}',
```
and replace with:
```python
        '{"action":"create","title":"손실한도와 레버리지","summary":"...","body":"...","kind":"playbook","status":"reviewed","tags":["risk","portfolio"],"source_refs":["conversation:123"],"links":[],"target_id":"","confidence":0.86,"reason":"..."}',
```

3b. Add `_auto_link_candidates` immediately after `_best_candidate_page`:

```python
def _auto_link_candidates(
    question: str,
    surface: str,
    candidates: list[dict],
    *,
    exclude_id: str = "",
    limit: int = 3,
) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for page in candidates:
        page_id = _clean(page.get("id"), 80)
        if not page_id or page_id == exclude_id or page_id in seen:
            continue
        seen.add(page_id)
        score = _candidate_score(page.get("raw") or {}, question, surface, "all")
        if score >= AUTO_CURATE_MIN_SCORE:
            scored.append((score, page_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [page_id for _score, page_id in scored[:limit]]
```

3c. In `_heuristic_curation_plan`, find the signature and body:

```python
def _heuristic_curation_plan(
    question: str,
    answer: str,
    *,
    surface: str,
    target: dict | None = None,
) -> dict | None:
    text = f"{question}\n{answer}".lower()
    if not _should_auto_curate(question, answer):
        return None
    kind = _infer_kind_from_text(text)
    if kind == "note" and not any(k in text for k in ("규칙", "기준", "조건", "검증", "원문", "본문", "저장", "수집")):
        return None
    status = "draft"
    if any(token in text for token in ("규칙", "기준", "조건", "손실한도", "레버리지", "검증", "원문", "본문", "편향", "수집")):
        status = "reviewed"
    title = _derive_title(question, answer)
    plan = {
        "action": "update" if target else "create",
        "title": title,
        "summary": _clean(answer[:900] or question[:900], 900),
        "body": _clean(answer, 6000),
        "kind": kind,
        "status": status,
        "tags": _auto_tags(text, surface, kind),
        "source_refs": [],
        "target_id": target.get("id") if target else "",
        "confidence": 0.72 if status == "reviewed" else 0.58,
        "reason": "heuristic curation",
        "source": "heuristic",
    }
    return plan
```
and replace with:
```python
def _heuristic_curation_plan(
    question: str,
    answer: str,
    *,
    surface: str,
    target: dict | None = None,
    candidates: list[dict] | None = None,
) -> dict | None:
    text = f"{question}\n{answer}".lower()
    if not _should_auto_curate(question, answer):
        return None
    kind = _infer_kind_from_text(text)
    if kind == "note" and not any(k in text for k in ("규칙", "기준", "조건", "검증", "원문", "본문", "저장", "수집")):
        return None
    status = "draft"
    if any(token in text for token in ("규칙", "기준", "조건", "손실한도", "레버리지", "검증", "원문", "본문", "편향", "수집")):
        status = "reviewed"
    title = _derive_title(question, answer)
    target_id = target.get("id") if target else ""
    plan = {
        "action": "update" if target else "create",
        "title": title,
        "summary": _clean(answer[:900] or question[:900], 900),
        "body": _clean(answer, 6000),
        "kind": kind,
        "status": status,
        "tags": _auto_tags(text, surface, kind),
        "source_refs": [],
        "links": _auto_link_candidates(question, surface, candidates or [], exclude_id=target_id),
        "target_id": target_id,
        "confidence": 0.72 if status == "reviewed" else 0.58,
        "reason": "heuristic curation",
        "source": "heuristic",
    }
    return plan
```

3d. In `auto_curate_from_chat`, find:

```python
    if not plan:
        plan = _heuristic_curation_plan(question, answer, surface=surface, target=target)
```
and replace with:
```python
    if not plan:
        plan = _heuristic_curation_plan(question, answer, surface=surface, target=target, candidates=candidates)
```

3e. In `_plan_to_page_payload`, find:

```python
    target_id = _clean(plan.get("target_id") or (target.get("id") if target else ""), 80)
    title = _clean(plan.get("title") or _derive_title(question, answer), 160)
    summary = _clean(plan.get("summary") or answer[:2400] or question[:2400], 2400)
    body = _clean(plan.get("body") or answer or summary, 6000)
    kind = _clean(plan.get("kind") or "playbook", 40).lower() or "playbook"
    if kind not in VALID_KINDS:
        kind = "note"
    status = _clean(plan.get("status") or "draft", 40).lower() or "draft"
    if status not in VALID_STATUSES:
        status = "draft"
    confidence = _num_or_default(plan.get("confidence"), 0.5)
    tags = _dedupe_texts([
        WIKI_TAG,
        surface,
        kind,
        status,
        *(plan.get("tags") or []),
    ], limit=20, item_limit=60)
```
and replace with:
```python
    target_id = _clean(plan.get("target_id") or (target.get("id") if target else ""), 80)
    title = _clean(plan.get("title") or _derive_title(question, answer), 160)
    summary = _clean(plan.get("summary") or answer[:2400] or question[:2400], 2400)
    body = _clean(plan.get("body") or answer or summary, 6000)
    kind = _clean(plan.get("kind") or "playbook", 40).lower() or "playbook"
    if kind not in VALID_KINDS:
        kind = "note"
    status = _clean(plan.get("status") or "draft", 40).lower() or "draft"
    if status not in VALID_STATUSES:
        status = "draft"
    confidence = _num_or_default(plan.get("confidence"), 0.5)
    final_id = target_id or _page_id(title, surface, kind)
    links = _clean_links(plan.get("links") or [], self_id=final_id)
    if target:
        links = _clean_links([*(target.get("links") or []), *links], self_id=final_id)
    tags = _dedupe_texts([
        WIKI_TAG,
        surface,
        kind,
        status,
        *(plan.get("tags") or []),
    ], limit=20, item_limit=60)
```

Then find the final return block:

```python
    if not title or not body:
        return None
    return {
        "id": target_id or _page_id(title, surface, kind),
        "title": title,
        "summary": summary,
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": status,
        "tags": tags,
        "source_refs": source_refs,
        "messages": messages,
        "confidence": confidence,
    }
```
and replace with:
```python
    if not title or not body:
        return None
    return {
        "id": final_id,
        "title": title,
        "summary": summary,
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": status,
        "tags": tags,
        "source_refs": source_refs,
        "links": links,
        "messages": messages,
        "confidence": confidence,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_auto_curate_llm_plan_links_are_persisted or wiki_auto_curate_heuristic_auto_links_related_candidate"`
Expected: PASS

- [ ] **Step 5: Run the full existing wiki/auto-curate test subset to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent_console/wiki.py tests/test_agent_console.py
git commit -m "feat) 대화 기반 위키 큐레이션에 links 자동 연결 추가"
```

---

### Task 3: Deterministic Source-Curator Link Authoring

**Files:**
- Modify: `reports/source_wiki_curator.py`
- Test: `tests/test_source_wiki_curator.py`

**Interfaces:**
- Produces: `source_wiki_curator._event_key(event: dict) -> str`
- Produces: `source_wiki_curator._link_pages_sharing_events(pages: list[dict], page_event_keys: dict[str, set[str]]) -> None` (mutates `pages` in place, adding a `"links"` key to each)
- Modifies: `build_wiki_pages_from_events` return value — each page dict now includes `"links": list[str]`

- [ ] **Step 1: Write failing test**

Add to `tests/test_source_wiki_curator.py` (after the existing `test_build_wiki_pages_from_events_groups_source_backed_topic` test):

```python
def test_build_wiki_pages_from_events_links_pages_sharing_events():
    events = [
        {
            "source": "saveticker",
            "title": "엔비디아 AI 서버 수요 확대",
            "url": "https://saveticker.com/nvda",
            "body_raw": "AI 서버와 반도체 수요가 확대됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "text_path": "/tmp/nvda.txt",
            "raw_path": "/tmp/nvda.json",
            "classification": {"kind": "article", "topic": "기술/AI", "trust": "B"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "AI 데이터센터 전력 수요 증가",
            "url": "https://t.me/insidertracking/1",
            "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "text_path": "/tmp/tg.txt",
            "raw_path": "/tmp/tg.html",
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 23, 10, 0, tzinfo=KST))
    by_id = {page["id"]: page for page in pages}

    assert by_id["source-topic-기술-ai"]["links"] == ["source-ticker-nvda"]
    assert by_id["source-ticker-nvda"]["links"] == ["source-topic-기술-ai"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_wiki_curator.py -q -k links_pages_sharing_events`
Expected: FAIL — `KeyError: 'links'`

- [ ] **Step 3: Implement deterministic link authoring**

In `reports/source_wiki_curator.py`:

3a. Add a constant and `_event_key` helper right after `MAX_SOURCE_REFS = 12`:

```python
MAX_SOURCE_REFS = 12
MAX_CURATOR_LINKS = 12
GENERIC_TOPICS = {"기타", "saveticker", "텔레그램", "시장데이터"}
```

(Only add `MAX_CURATOR_LINKS = 12` — `GENERIC_TOPICS` already exists below it, leave that line as-is; this step just inserts the new constant above the existing `GENERIC_TOPICS` line.)

Add `_event_key` immediately after the `_source_refs` function:

```python
def _event_key(event: dict) -> str:
    return _clean(event.get("url"), 300) or _clean(event.get("title"), 220)
```

3b. Add `_link_pages_sharing_events` immediately after `_group_label`:

```python
def _link_pages_sharing_events(pages: list[dict], page_event_keys: dict[str, set[str]]) -> None:
    for left in pages:
        left_id = left.get("id")
        left_keys = page_event_keys.get(left_id) or set()
        if not left_keys:
            left["links"] = []
            continue
        linked: list[str] = []
        for right in pages:
            right_id = right.get("id")
            if right_id == left_id:
                continue
            right_keys = page_event_keys.get(right_id) or set()
            if left_keys & right_keys:
                linked.append(right_id)
            if len(linked) >= MAX_CURATOR_LINKS:
                break
        left["links"] = linked
```

3c. In `build_wiki_pages_from_events`, find:

```python
    pages: list[dict] = []
    for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        group_type, label, display = _group_label(key)
        rows = sorted(rows, key=lambda event: str(event.get("published_at") or event.get("collected_at") or ""), reverse=True)
        if not _is_strong_group(rows):
            continue
        refs = _source_refs(rows)
        source_roots = sorted({_root_source(row.get("source")) for row in rows})
        ticker_counts = Counter(t for row in rows for t in (row.get("tickers") or []) if isinstance(t, str) and t.strip())
        tags = _dedupe([
            "wiki",
            "market",
            "source_digest",
            f"{group_type}:{label}",
            *(f"source:{src}" for src in source_roots),
            *(f"ticker:{ticker}" for ticker, _count in ticker_counts.most_common(8)),
        ], limit=20)
        pages.append({
            "id": f"source-{group_type}-{_slug(label)}",
            "title": f"수집 소스 위키: {display}",
            "surface": "market",
            "kind": "source_digest",
            "status": _status_for(rows, refs),
            "tags": tags,
            "summary": _summary_for(display, rows),
            "body": _body_for(display, rows, now),
            "source_refs": refs,
            "openQuestions": [
                f"{display} 신호가 가격·크레딧·환율 데이터에서도 확인되는가?",
                "공식 자료와 충돌하는 커뮤니티성 단서가 있는가?",
            ],
            "confidence": 0.78 if _status_for(rows, refs) == "reviewed" else 0.55,
        })
    return pages
```
and replace with:
```python
    pages: list[dict] = []
    page_event_keys: dict[str, set[str]] = {}
    for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        group_type, label, display = _group_label(key)
        rows = sorted(rows, key=lambda event: str(event.get("published_at") or event.get("collected_at") or ""), reverse=True)
        if not _is_strong_group(rows):
            continue
        refs = _source_refs(rows)
        source_roots = sorted({_root_source(row.get("source")) for row in rows})
        ticker_counts = Counter(t for row in rows for t in (row.get("tickers") or []) if isinstance(t, str) and t.strip())
        tags = _dedupe([
            "wiki",
            "market",
            "source_digest",
            f"{group_type}:{label}",
            *(f"source:{src}" for src in source_roots),
            *(f"ticker:{ticker}" for ticker, _count in ticker_counts.most_common(8)),
        ], limit=20)
        page_id = f"source-{group_type}-{_slug(label)}"
        pages.append({
            "id": page_id,
            "title": f"수집 소스 위키: {display}",
            "surface": "market",
            "kind": "source_digest",
            "status": _status_for(rows, refs),
            "tags": tags,
            "summary": _summary_for(display, rows),
            "body": _body_for(display, rows, now),
            "source_refs": refs,
            "openQuestions": [
                f"{display} 신호가 가격·크레딧·환율 데이터에서도 확인되는가?",
                "공식 자료와 충돌하는 커뮤니티성 단서가 있는가?",
            ],
            "confidence": 0.78 if _status_for(rows, refs) == "reviewed" else 0.55,
        })
        page_event_keys[page_id] = {key for key in (_event_key(row) for row in rows) if key}
    _link_pages_sharing_events(pages, page_event_keys)
    return pages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_wiki_curator.py -q`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add reports/source_wiki_curator.py tests/test_source_wiki_curator.py
git commit -m "feat) 소스 큐레이터가 이벤트 공유 페이지끼리 결정적 links 생성"
```

---

### Task 4: Lint Relational Checks

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `wiki._clean_links` (Task 1)
- Produces: `wiki._lint_relational_issues(pages: list[dict]) -> list[dict]`
- Modifies: `wiki.lint_pages` now also emits `orphan_page` and `missing_cross_ref` issues

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_console.py` (after `test_wiki_lint_flags_source_less_promoted_pages_and_open_questions`):

```python
def test_wiki_lint_flags_orphan_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "solo1",
            "title": "고립된 페이지",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "links": [],
            "backlinks": [],
        }
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "orphan_page" in codes


def test_wiki_lint_flags_missing_cross_ref(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "tickerA",
            "title": "NVDA 메모 A",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
        {
            "id": "tickerB",
            "title": "NVDA 메모 B",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_cross_ref" in codes
    cross_ref_issues = [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]
    assert len(cross_ref_issues) == 1


def test_wiki_lint_skips_missing_cross_ref_when_linked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "tickerC",
            "title": "NVDA 메모 C",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": ["tickerD"],
            "backlinks": [],
        },
        {
            "id": "tickerD",
            "title": "NVDA 메모 D",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": ["tickerC"],
        },
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_cross_ref" not in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_lint_flags_orphan_page or wiki_lint_flags_missing_cross_ref or wiki_lint_skips_missing_cross_ref_when_linked"`
Expected: FAIL — `wiki_lint_flags_orphan_page` and `wiki_lint_flags_missing_cross_ref` fail because the codes are never emitted (`wiki_lint_skips_missing_cross_ref_when_linked` trivially passes already since nothing is emitted yet — that's expected and will keep passing after the real implementation too).

- [ ] **Step 3: Implement relational lint checks**

In `agent_console/wiki.py`, add `_lint_relational_issues` immediately before `lint_pages`:

```python
def _lint_relational_issues(pages: list[dict]) -> list[dict]:
    issues: list[dict] = []
    valid_pages = [page for page in pages or [] if isinstance(page, dict) and _clean(page.get("id") or "", 80)]

    for page in valid_pages:
        page_id = _clean(page.get("id"), 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        links = set(_clean_links(page.get("links") or [], self_id=page_id))
        backlinks = set(_clean_links(page.get("backlinks") or [], self_id=page_id))
        if not links and not backlinks:
            issues.append({
                "code": "orphan_page",
                "severity": "info",
                "page_id": page_id,
                "title": title,
                "message": "다른 페이지와 연결이 없습니다.",
            })

    ticker_index: dict[str, list[dict]] = defaultdict(list)
    ref_index: dict[str, list[dict]] = defaultdict(list)
    for page in valid_pages:
        for tag in page.get("tags") or []:
            clean_tag = _clean(tag, 60).lower()
            if clean_tag.startswith("ticker:"):
                ticker_index[clean_tag].append(page)
        for ref in page.get("source_refs") or page.get("artifacts") or []:
            clean_ref = _clean(ref, 200)
            if clean_ref:
                ref_index[clean_ref].append(page)

    seen_pairs: set[tuple[str, str]] = set()
    for group in [*ticker_index.values(), *ref_index.values()]:
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left, right = group[i], group[j]
                left_id = _clean(left.get("id"), 80)
                right_id = _clean(right.get("id"), 80)
                if not left_id or not right_id or left_id == right_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs:
                    continue
                left_links = set(_clean_links(left.get("links") or [], self_id=left_id))
                right_links = set(_clean_links(right.get("links") or [], self_id=right_id))
                if right_id in left_links or left_id in right_links:
                    continue
                seen_pairs.add(pair)
                issues.append({
                    "code": "missing_cross_ref",
                    "severity": "warning",
                    "page_id": left_id,
                    "title": f"{left.get('title')} / {right.get('title')}",
                    "message": f"'{left.get('title')}'와(과) '{right.get('title')}'가 태그·출처를 공유하지만 서로 연결되어 있지 않습니다.",
                })
    return issues
```

Then modify `lint_pages`. Find:

```python
def lint_pages(pages: list[dict] | None = None) -> dict:
    if pages is None:
        pages = list_pages(status="all", surface="all", limit=400)
    issues: list[dict] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        page_id = _clean(page.get("id") or "", 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        status = _clean(page.get("status") or "draft", 40).lower()
        refs = page.get("source_refs") or page.get("artifacts") or []
        if status in {"reviewed", "stable"} and not has_non_conversation_source_refs(refs):
            issues.append({
                "code": "source_missing_for_promoted",
                "severity": "error",
                "page_id": page_id,
                "title": title,
                "message": "reviewed/stable 페이지에는 conversation 이외의 원문 출처가 필요합니다.",
            })
        open_questions = page.get("openQuestions") or page.get("open_questions") or []
        if open_questions:
            issues.append({
                "code": "open_questions_present",
                "severity": "info",
                "page_id": page_id,
                "title": title,
                "message": f"열린 질문 {len(open_questions)}건이 남아 있습니다.",
            })
        if not page.get("summary") and not page.get("body"):
            issues.append({
                "code": "empty_page",
                "severity": "warning",
                "page_id": page_id,
                "title": title,
                "message": "요약과 본문이 모두 비어 있습니다.",
            })
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}
```
and replace with:
```python
def lint_pages(pages: list[dict] | None = None) -> dict:
    if pages is None:
        pages = list_pages(status="all", surface="all", limit=400)
    issues: list[dict] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        page_id = _clean(page.get("id") or "", 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        status = _clean(page.get("status") or "draft", 40).lower()
        refs = page.get("source_refs") or page.get("artifacts") or []
        if status in {"reviewed", "stable"} and not has_non_conversation_source_refs(refs):
            issues.append({
                "code": "source_missing_for_promoted",
                "severity": "error",
                "page_id": page_id,
                "title": title,
                "message": "reviewed/stable 페이지에는 conversation 이외의 원문 출처가 필요합니다.",
            })
        open_questions = page.get("openQuestions") or page.get("open_questions") or []
        if open_questions:
            issues.append({
                "code": "open_questions_present",
                "severity": "info",
                "page_id": page_id,
                "title": title,
                "message": f"열린 질문 {len(open_questions)}건이 남아 있습니다.",
            })
        if not page.get("summary") and not page.get("body"):
            issues.append({
                "code": "empty_page",
                "severity": "warning",
                "page_id": page_id,
                "title": title,
                "message": "요약과 본문이 모두 비어 있습니다.",
            })
    issues.extend(_lint_relational_issues(pages or []))
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_lint_flags_orphan_page or wiki_lint_flags_missing_cross_ref or wiki_lint_skips_missing_cross_ref_when_linked"`
Expected: PASS

- [ ] **Step 5: Run the full existing lint/wiki test subset to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent_console/wiki.py tests/test_agent_console.py
git commit -m "feat) 위키 lint 에 orphan_page/missing_cross_ref 관계형 검사 추가"
```

---

### Task 5: Rendering and Prompt Exposure

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `wiki._wiki_records` (Task 1)
- Produces: `wiki._title_lookup_for(page_ids: set[str]) -> dict[str, str]`
- Modifies: `wiki._render_index_md` (adds link-count marker), `wiki.build_context_section` (adds "관련" line)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_console.py` (after `test_wiki_context_section_includes_search_and_trust_metadata`):

```python
def test_wiki_index_md_shows_link_marker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    first = wiki.upsert_page({
        "title": "링크 원본 페이지",
        "summary": "원본 요약",
        "body": "원본 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    wiki.upsert_page({
        "title": "링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [first["id"]],
    })

    wiki.rebuild_artifacts()
    index_text = (wiki.wiki_artifacts_dir() / "index.md").read_text(encoding="utf-8")

    assert "[[링크 대상 페이지]]" in index_text
    assert "🔗1" in index_text


def test_wiki_context_section_includes_related_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    related = wiki.upsert_page({
        "title": "연관 위키 페이지",
        "summary": "연관 요약",
        "body": "연관 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    wiki.upsert_page({
        "title": "중심 위키 페이지",
        "summary": "중심 요약 텍스트",
        "body": "중심 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [related["id"]],
    })

    section = wiki.build_context_section(query="중심 위키 페이지", surface="market", limit=4)

    assert "관련: [[연관 위키 페이지]]" in section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_index_md_shows_link_marker or wiki_context_section_includes_related_pages"`
Expected: FAIL — no `🔗` marker in `index.md`, no `관련:` line in the context section.

- [ ] **Step 3: Implement rendering changes**

3a. In `_render_index_md`, find:

```python
            summary = _clean(page.get("summary") or page.get("body") or "", 180)
            lines.append(f"- [[{title}]] ({meta}) — {summary}")
```
and replace with:
```python
            summary = _clean(page.get("summary") or page.get("body") or "", 180)
            link_count = len({*(page.get("links") or []), *(page.get("backlinks") or [])})
            marker = f" [\U0001f517{link_count}]" if link_count else ""
            lines.append(f"- [[{title}]] ({meta}) — {summary}{marker}")
```

3b. Add `_title_lookup_for` immediately before `build_context_section`:

```python
def _title_lookup_for(page_ids: set[str]) -> dict[str, str]:
    if not page_ids:
        return {}
    lookup: dict[str, str] = {}
    for row in _wiki_records():
        row_id = _clean(row.get("id"), 80)
        if row_id in page_ids:
            lookup[row_id] = _clean(row.get("title") or "위키 페이지", 160)
    return lookup
```

3c. In `build_context_section`, find:

```python
def build_context_section(*, query: str = "", surface: str = WIKI_SURFACE, limit: int = 4,
                          status: str = "all") -> str:
    pages = list_pages(query=query, surface=surface, status=status, limit=limit)
    if not pages:
        return ""
    lines = ["[위키 지식]"]
    for idx, page in enumerate(pages, start=1):
```
and replace with:
```python
def build_context_section(*, query: str = "", surface: str = WIKI_SURFACE, limit: int = 4,
                          status: str = "all") -> str:
    pages = list_pages(query=query, surface=surface, status=status, limit=limit)
    if not pages:
        return ""
    related_ids = {
        rid
        for page in pages
        for rid in [*(page.get("links") or []), *(page.get("backlinks") or [])]
    }
    title_lookup = _title_lookup_for(related_ids)
    lines = ["[위키 지식]"]
    for idx, page in enumerate(pages, start=1):
```

Then find:

```python
        lines.append(f"- 검증: {page.get('verification_status', 'unverified')}")
        for warning in page.get("trust_warnings") or []:
            lines.append(f"- 주의: {warning}")
        if page.get("tags"):
            lines.append(f"- 태그: {', '.join(page['tags'][:8])}")
    return "\n".join(lines).strip()
```
and replace with:
```python
        lines.append(f"- 검증: {page.get('verification_status', 'unverified')}")
        for warning in page.get("trust_warnings") or []:
            lines.append(f"- 주의: {warning}")
        related_ids_for_page = _dedupe_texts(
            [*(page.get("links") or []), *(page.get("backlinks") or [])], limit=6, item_limit=80
        )
        related_titles = [title_lookup[rid] for rid in related_ids_for_page if rid in title_lookup]
        if related_titles:
            lines.append(f"- 관련: {', '.join(f'[[{t}]]' for t in related_titles)}")
        if page.get("tags"):
            lines.append(f"- 태그: {', '.join(page['tags'][:8])}")
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py -q -k "wiki_index_md_shows_link_marker or wiki_context_section_includes_related_pages"`
Expected: PASS

- [ ] **Step 5: Run the full test suite for the touched modules**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py tests/test_source_wiki_curator.py tests/test_wiki_storage_window.py tests/test_qmd_search.py -q`
Expected: PASS (no regressions across the wiki module and its neighbors)

- [ ] **Step 6: Commit**

```bash
git add agent_console/wiki.py tests/test_agent_console.py
git commit -m "feat) 위키 인덱스/컨텍스트 섹션에 링크·관련 페이지 노출"
```

---

## Self-Review

**Spec coverage:**
- Data model (`links` field, computed `backlinks`, cap/dedupe/self-drop, nonexistent-id tolerance) → Task 1.
- Conversation-path link authoring (LLM plan `links` field, heuristic top-candidate auto-link) → Task 2.
- Source-curator deterministic link authoring (shared-event pages) → Task 3.
- Lint relational checks (`orphan_page`, `missing_cross_ref`, once-per-pair, severities) → Task 4.
- Rendering (`index.md` marker) and prompt exposure (`build_context_section` "관련" line) → Task 5.
- `dashboard/wiki_mesh.py` untouched → confirmed, no task modifies it.

**Placeholder scan:** No TBD/TODO/draft-artifact text remains.

**Type consistency:** `links: list[str]` and `backlinks: list[str]` are used consistently across `_record_to_page`, `_apply_backlinks`, `upsert_page`, `_plan_to_page_payload`, `_lint_relational_issues`, `_render_index_md`, and `build_context_section`. `_auto_link_candidates(question, surface, candidates, *, exclude_id, limit) -> list[str]` signature matches its Task 2 usage in `_heuristic_curation_plan`. `_clean_links(values, *, self_id, limit) -> list[str]` signature matches all call sites (Tasks 1, 2, 4).

**Scope check:** Single cohesive subsystem (wiki cross-links), no decomposition needed. Five tasks, each independently testable and committable.
