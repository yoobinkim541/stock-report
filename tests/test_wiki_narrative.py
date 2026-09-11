import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_console import wiki
from reports import article_crawler, article_queue
from reports import wiki_distillation as wd
from reports import wiki_narrative as narrative
from reports.evidence_cards import event_to_evidence_card


UTC = timezone.utc


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


def test_renderer_numbers_sources_by_first_use_and_omits_unknown_date():
    articles = [
        {"evidence_id": "e1", "title": "첫 기사", "url": "https://example.com/1",
         "source": "매체1", "published_at": "2026-09-09T02:00:00Z", "text": "본문"},
        {"evidence_id": "e2", "title": "둘째 기사", "url": "https://example.com/2",
         "source": "매체2", "published_at": "", "text": "본문"},
    ]
    rendered = narrative.render_plan({
        "body": "비용 조건은 먼저 확인한다.[S2] 이후 수요를 본다.[S1] 다시 조건을 점검한다.[S2]",
        "references": ["S2", "S1"],
        "report_citation": "비용과 수요를 함께 본다.",
    }, articles=articles)

    assert "[1]" in rendered["body"] and "[2]" in rendered["body"]
    assert "[S" not in rendered["body"]
    assert rendered["body"].count("https://example.com/2") == 1
    assert "1. [둘째 기사](https://example.com/2) — 매체2\n" in rendered["body"]
    assert "2. [첫 기사](https://example.com/1) — 매체1 · 2026-09-09" in rendered["body"]
    assert rendered["body"].count(wiki.REPORT_CITATION_MARKER) == 1


@pytest.mark.parametrize("references", [["S1", "S1"], ["S9"]])
def test_renderer_rejects_duplicate_or_unknown_reference_ids(references):
    with pytest.raises(narrative.NarrativeValidationError):
        narrative.render_plan(
            {"body": "주장.[S1]" if references[0] == "S1" else "주장.[S9]",
             "references": references, "report_citation": "요약"},
            articles=[{"evidence_id": "e1", "title": "기사", "url": "https://example.com/1",
                       "source": "매체", "published_at": "", "text": "본문"}],
        )


def test_renderer_preserves_used_previous_source_and_deduplicates_url():
    previous = {
        "body": (
            "기존 주장은 조건부였다.[1]\n\n"
            f"> **{wiki.REPORT_CITATION_MARKER}**: 기존 요약\n\n"
            "## 출처\n"
            "1. [기존 기사](https://example.com/shared?utm_source=x) — 기존매체 · 2026-09-01"
        )
    }
    rendered = narrative.render_plan(
        {"body": "기존 조건은 유지된다.[P1] 새 근거도 같은 원문이다.[S1]",
         "references": ["P1", "S1"], "report_citation": "조건은 유지된다."},
        articles=[{"evidence_id": "e1", "title": "새 제목", "url": "https://example.com/shared",
                   "source": "새매체", "published_at": "2026-09-09", "text": "본문"}],
        previous=previous,
    )

    assert rendered["body"].count("https://example.com/shared") == 1
    assert "1. [기존 기사](https://example.com/shared)" in rendered["body"]
    assert "2. [" not in rendered["body"]


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
    assert saved["body"].count("## 출처") == 1
    assert saved["body"].count(wiki.REPORT_CITATION_MARKER) == 1
    assert saved["messages"] == []
    assert "[1]" in saved["body"] and "[S1]" not in saved["body"]

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

