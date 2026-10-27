import json
from datetime import datetime, timedelta, timezone

from agent_console import wiki
from reports import article_crawler, article_queue
from reports import wiki_distillation as wd
from reports.evidence_cards import event_to_evidence_card


UTC = timezone.utc


def test_changed_evidence_reopens_linked_completed_digest():
    page = {"id": "s", "kind": "source_digest", "evidence_ids": ["old"],
            "links": ["k"], "distillation_state": {"status": "created", "last_result_id": "k"}}
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    knowledge = {"id": "k", "kind": "concept"}
    assert wd.select_distillation_candidates([page, knowledge]) == []
    page["evidence_ids"] = ["old", "new"]
    assert wd.select_distillation_candidates([page, knowledge]) == [page]


def test_skipped_evidence_retries_only_after_change():
    page = {"evidence_ids": ["e"], "distillation_state": {"status": "skipped"}}
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    assert not wd._distillation_is_eligible(page)
    page["evidence_ids"].append("new")
    assert wd._distillation_is_eligible(page)


def test_archived_body_reaches_prompt_without_whole_file_read(tmp_path, monkeypatch):
    from reports import source_collector as collector
    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    event = {"id": "event", "source": "official", "title": "Capacity",
             "url": "https://example.org/report", "collected_at": now.isoformat(),
             "body_raw": "a" * 800 + " capacity constraint evidence beyond preview", "tickers": ["ABC"]}
    path = collector._event_file(tmp_path, now)
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"evidence_ids": [event_to_evidence_card(event, now=now).id]}
    from pathlib import Path
    original_read_text = Path.read_text
    def reject_event_file_read(path, *args, **kwargs):
        if path == path_to_event_file:
            raise AssertionError("whole event file read")
        return original_read_text(path, *args, **kwargs)
    path_to_event_file = path
    monkeypatch.setattr(Path, "read_text", reject_event_file_read)
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)
    page["_previous_knowledge"] = {"kind": "concept", "body": "existing causal explanation"}
    prompt = wd._build_distillation_prompt(page)
    assert "capacity constraint evidence beyond preview" in prompt
    assert "existing causal explanation" in prompt
    assert "https://example.org/report" in prompt


def test_archived_judgment_link_reselects_completed_digest():
    page = {
        "id": "source-archived",
        "kind": "source_digest",
        "evidence_ids": ["e1"],
        "links": ["old-judgment"],
        "distillation_state": {"status": "created", "last_result_id": "old-judgment"},
    }
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    archived = {"id": "old-judgment", "kind": "concept", "status": "archived"}

    assert wd.select_distillation_candidates([page, archived]) == [page]


def test_article_context_loads_only_selected_ready_body(monkeypatch):
    selected_url = "https://example.org/selected"
    other_url = "https://example.org/other"
    index = {
        selected_url: {
            "status": "ready", "content_hash": "a" * 64, "evidence_ids": ["selected"],
            "title": "Selected", "source": "official", "published_at": "2026-09-09",
        },
        other_url: {
            "status": "ready", "content_hash": "b" * 64, "evidence_ids": ["other"],
            "title": "Other", "source": "official", "published_at": "2026-09-09",
        },
    }
    page = {"evidence_ids": ["selected"], "source_refs": [selected_url], "tags": ["source:official"]}
    wd._attach_article_metadata([page], index)
    calls = []

    def get_article(url, **kwargs):
        calls.append(url)
        return {
            "url": url, "title": "Selected", "source": "official",
            "published_at": "2026-09-09", "text": "selected body", "content_hash": "a" * 64,
        }

    monkeypatch.setattr(wd.article_queue, "get_article", get_article)
    wd._attach_article_context([page], index=index)

    assert calls == [selected_url]
    assert page["_source_articles"][0]["text"] == "selected body"


def test_invalid_article_citation_rejected():
    page = {"_source_articles": [{"url": "https://example.org", "text": "evidence"}]}
    plan = {"action": "create", "kind": "concept", "title": "T", "body": "unsupported [S9]"}
    payload, status, reason = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert payload is None and status == "failed"
    assert "citation" in reason


def test_missing_article_does_not_generate_from_links_alone():
    def unexpected_model_call(_prompt):
        raise AssertionError("missing article must not invoke model")
    payload, status, reason = wd._distill_one_with_status(
        {"_require_articles": True, "_source_articles": []}, unexpected_model_call)
    assert payload is None and status == "failed"
    assert "unavailable" in reason


# ── run() over article-queue bodies (moved from tests/test_wiki_narrative.py) ──

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    wiki._CACHE.clear()


def _event(number=1, **overrides):
    event = {
        "id": f"event-{number}",
        "source": "saveticker",
        "title": f"설비투자 기사 {number}",
        "url": f"https://saveticker.com/news/{number}",
        "published_at": "2026-09-09T00:00:00+00:00",
        "classification": {"kind": "article", "wiki_eligible": True},
    }
    event.update(overrides)
    return event


def _ready(cache, event, text, now):
    article_queue.enqueue_events([event], root=cache, now=now)
    result = article_crawler.crawl_pending(
        root=cache,
        fetcher=lambda _url: f"<article><p>{text}</p></article>",
        now=now,
    )
    assert result["ready"] == 1


def _source_page(event, **overrides):
    page = {
        "id": "source-ticker-abc",
        "title": "수집 소스 위키: ABC",
        "summary": "설비투자 근거",
        "body": "자동 수집 다이제스트",
        "surface": "ticker",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": ["wiki", "source_digest", "source:saveticker", "ticker:ABC"],
        "source_refs": [event["url"]],
        "evidence_ids": [event_to_evidence_card(event).id],
    }
    page.update(overrides)
    return page


def _plan(body="가동 가능한 증설이 매출 인식의 선행 조건이다.[S1]", references=None):
    return json.dumps({
        "action": "create",
        "kind": "concept",
        "title": "설비투자와 매출 인식",
        "summary": "증설의 현금흐름 전달 경로",
        "body": body,
        "references": references or ["S1"],
        "report_citation": "가동 가능한 증설 여부가 매출 전환의 핵심 조건이다.",
        "status": "draft",
    }, ensure_ascii=False)


def test_run_uses_ready_body_beyond_300_and_skips_pending_metadata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache = tmp_path / "article-cache"
    now = datetime(2026, 9, 9, tzinfo=UTC)
    ready = _event(1)
    pending = _event(2, title="PENDING TITLE MUST NOT REACH MODEL")
    deep_marker = "본문 300자 이후의 매출 전환 근거"
    _ready(cache, ready, ("가" * 420) + deep_marker + ("나" * 180), now)
    article_queue.enqueue_events([pending], root=cache, now=now)
    page = _source_page(
        ready,
        evidence_ids=[event_to_evidence_card(ready).id, event_to_evidence_card(pending).id],
        source_refs=[ready["url"], pending["url"]],
    )
    wiki.upsert_page(page)
    prompts = []
    metadata_loads = []
    real_load_index = article_queue.load_index

    def load_index_once(*, root=None):
        metadata_loads.append(root)
        return real_load_index(root=root)

    monkeypatch.setattr(article_queue, "load_index", load_index_once)
    result = wd.run(
        llm_fn=lambda prompt: prompts.append(prompt) or _plan(),
        fulltext=True,
        article_cache_dir=cache,
    )

    assert len(result["created"]) == 1
    assert len(prompts) == 1
    assert metadata_loads == [cache]
    assert deep_marker in prompts[0]
    assert "PENDING TITLE MUST NOT REACH MODEL" not in prompts[0]
    saved = wiki.get_page(result["created"][0]["id"])
    assert saved["body"].count(wiki.REPORT_CITATION_MARKER) == 1
    assert saved["messages"] == []
    # Inline [Sn](URL) citations are the single citation format (ours, live wiki).
    assert "[S1](https://saveticker.com/news/1)" in saved["body"]
    assert "https://saveticker.com/news/1" in saved["source_refs"]

    second_calls = []
    second = wd.run(
        llm_fn=lambda prompt: second_calls.append(prompt) or _plan(),
        fulltext=True,
        article_cache_dir=cache,
    )
    assert second["created"] == []
    assert second_calls == []


def test_changed_article_hash_reopens_same_evidence_ids(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache = tmp_path / "article-cache"
    start = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    _ready(cache, event, "첫 본문 " * 80, start)
    wiki.upsert_page(_source_page(event))
    first = wd.run(llm_fn=lambda _prompt: _plan(), fulltext=True, article_cache_dir=cache)
    first_id = first["created"][0]["id"]

    later = start + timedelta(hours=25)
    article_queue.enqueue_events([event], root=cache, now=later)
    refreshed = article_crawler.crawl_pending(
        root=cache,
        fetcher=lambda _url: "<article><p>" + ("변경된 본문 " * 80) + "</p></article>",
        now=later,
    )
    assert refreshed["ready"] == 1 and refreshed["unchanged"] == 0
    calls = []
    second = wd.run(
        llm_fn=lambda prompt: calls.append(prompt) or _plan(),
        fulltext=True,
        article_cache_dir=cache,
    )

    assert len(calls) == 1
    assert second["created"][0]["id"] == first_id


def test_pending_only_group_never_calls_model_and_stays_retryable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache = tmp_path / "article-cache"
    event = _event()
    article_queue.enqueue_events([event], root=cache, now=datetime(2026, 9, 9, tzinfo=UTC))
    source = wiki.upsert_page(_source_page(event))

    result = wd.run(
        llm_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("model must not run")),
        fulltext=True,
        article_cache_dir=cache,
    )

    assert result["created"] == []
    state = wiki.get_page(source["id"])["distillation_state"]
    assert state["status"] == "failed"
    assert state["attempts"] == 1
    assert wd.select_distillation_candidates([wiki.get_page(source["id"])]) != []


def test_archived_result_uses_rich_digest_fallback_without_resurrecting(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache = tmp_path / "article-cache"
    archived = wiki.upsert_page({
        "id": "distill-archived",
        "title": "보관된 지식",
        "summary": "과거 요약",
        "body": "과거 본문",
        "surface": "ticker",
        "kind": "concept",
        "status": "archived",
        "source_refs": ["https://example.com/old"],
    })
    event = _event()
    evidence_id = event_to_evidence_card(event).id
    rich_marker = "저장된 다이제스트의 검증 가능한 설비 가동률 근거"
    source = _source_page(
        event,
        body=(rich_marker + " ") * 20,
        links=[archived["id"]],
        distillation_state={"status": "created", "attempts": 1,
                            "last_result_id": archived["id"]},
    )
    source["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(source)
    wiki.upsert_page(source)
    prompts = []

    result = wd.run(
        llm_fn=lambda prompt: prompts.append(prompt) or json.dumps({
            "action": "create", "kind": "concept", "title": "새 지식",
            "summary": "저장 근거만 사용한 요약", "body": "저장 근거에서 확인되는 조건부 판단",
        }, ensure_ascii=False),
        fulltext=True,
        article_cache_dir=cache,
    )

    assert len(result["created"]) == 1
    replacement = result["created"][0]
    assert replacement["id"] != archived["id"]
    assert wiki.get_page(archived["id"])["status"] == "archived"
    assert rich_marker in prompts[0]
    assert event["url"] in prompts[0]
    assert evidence_id in prompts[0]
    assert "확인할 수 없는 세부 사실이나 인과관계는 만들지 않는다" in prompts[0]
    assert replacement["source_refs"][:2] == [event["url"], f"wiki:{source['id']}"]
    assert replacement["evidence_ids"] == [evidence_id]


def test_invalid_reference_preserves_previous_document_byte_for_byte(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache = tmp_path / "article-cache"
    now = datetime(2026, 9, 9, tzinfo=UTC)
    event = _event()
    _ready(cache, event, "검증 가능한 기사 본문 " * 50, now)
    previous = wiki.upsert_page({
        "id": "distill-existing",
        "title": "기존 지식",
        "summary": "기존 요약",
        "body": "기존 문서는 한 바이트도 바뀌면 안 된다.",
        "surface": "ticker",
        "kind": "concept",
        "status": "draft",
        "source_refs": ["https://example.com/old"],
    })
    source = _source_page(event, links=[previous["id"]], distillation_state={
        "status": "created", "attempts": 4, "last_result_id": previous["id"],
        "evidence_fingerprint": "outdated",
    })
    wiki.upsert_page(source)
    before = wiki.get_page(previous["id"])
    calls = []

    result = wd.run(
        llm_fn=lambda prompt: calls.append(prompt) or _plan(body="근거 오류.[S9]", references=["S9"]),
        fulltext=True,
        article_cache_dir=cache,
    )

    assert result["created"] == []
    assert calls and len(calls) == 1
    assert wiki.get_page(previous["id"]) == before
    retry = wiki.get_page(source["id"])["distillation_state"]
    assert retry["status"] == "failed" and retry["attempts"] == 1


def test_institution_digest_retains_legacy_non_article_path(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    wiki.upsert_page({
        "id": "institution-watch-fund",
        "title": "기관 공통 패턴",
        "summary": "13F 변화",
        "body": "분기 보유 비중 변화",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": ["wiki", "source_digest", "institution_watch", "source:13f"],
        "source_refs": ["https://www.sec.gov/example"],
    })
    calls = []

    result = wd.run(
        llm_fn=lambda prompt: calls.append(prompt) or json.dumps({
            "action": "create", "kind": "concept", "title": "13F 해석",
            "summary": "공시 지연을 고려한다.", "body": "분기 지연을 반영한다.",
        }, ensure_ascii=False),
        fulltext=True,
        article_cache_dir=tmp_path / "empty-cache",
    )

    assert len(calls) == 1
    assert len(result["created"]) == 1
