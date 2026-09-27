import json
from reports import wiki_distillation as wd


def test_failed_refresh_keeps_prior_document_and_bounded_retry(monkeypatch):
    page = {"id": "s", "kind": "source_digest", "evidence_ids": ["new"], "links": ["k"],
            "distillation_state": {"status": "created", "last_result_id": "k", "attempts": 9,
                                   "evidence_fingerprint": "old"}}
    monkeypatch.setattr(wd.wiki, "upsert_page", lambda p: p)
    failed = wd._mark_distillation_attempt(page, status="failed", reason="transient")
    assert failed["distillation_state"]["last_result_id"] == "k"
    assert failed["distillation_state"]["attempts"] == 1
    knowledge = {"id": "k", "kind": "concept"}
    assert wd.select_distillation_candidates([failed, knowledge]) == [failed]
    failed = wd._mark_distillation_attempt(failed, status="failed")
    failed = wd._mark_distillation_attempt(failed, status="failed")
    assert wd.select_distillation_candidates([failed, knowledge]) == []


def test_fulltext_provenance_contains_only_used_articles(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path))
    page = {"id": "source-example", "surface": "market", "evidence_ids": ["unused", "used"],
            "source_refs": [f"https://example.org/old/{i}" for i in range(12)],
            "_source_articles": [{
                "evidence_id": "used", "title": "Actual article",
                "url": "https://example.org/actual", "source": "publisher",
                "published_at": "2026-09-09", "text": "body",
            }]}
    plan = {"action": "create", "kind": "concept", "title": "Capacity", "summary": "Capacity limits",
            "body": "Capacity constrains execution [S1]", "references": ["S1"],
            "report_citation": "Capacity limits execution.", "status": "draft", "confidence": .5}
    result, status, _ = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert status == "created"
    assert result["source_refs"][0] == "https://example.org/actual"
    assert result["evidence_ids"] == ["used"]


def test_archived_knowledge_is_not_revived(monkeypatch):
    source = {"id": "source-s", "kind": "source_digest", "evidence_ids": ["new"],
              "links": ["old"], "distillation_state": {"status": "created", "last_result_id": "old"}}
    archived = {"id": "old", "kind": "concept", "status": "archived"}
    monkeypatch.setattr(wd.wiki, "_all_wiki_pages", lambda: [source, archived])
    monkeypatch.setattr(wd, "_attach_article_context", lambda *_args, **_kwargs: None)
    saved = []
    monkeypatch.setattr(wd.wiki, "upsert_page", lambda p: saved.append(p) or p)
    def forbidden(_):
        raise AssertionError("must not regenerate archived knowledge")
    result = wd.run(llm_fn=forbidden, fulltext=True, page_ids=["source-s"])
    assert result["created"] == []
    assert [p["id"] for p in saved] == ["source-s"]
    assert saved[0]["distillation_state"]["status"] == "skipped"


def test_oldest_attempt_is_not_starved_by_larger_topic():
    old = {"id": "old", "kind": "source_digest", "evidence_ids": ["one"],
           "distillation_state": {"status": "failed", "last_attempt_at": "2026-09-01"}}
    recent = {"id": "recent", "kind": "source_digest", "evidence_ids": list(map(str, range(100))),
              "distillation_state": {"status": "failed", "last_attempt_at": "2026-09-09"}}
    assert wd.select_distillation_candidates([recent, old], limit=1) == [old]


def test_manual_link_does_not_replace_first_synthesis():
    source = {"id": "s", "kind": "source_digest", "evidence_ids": ["e"], "links": ["k"],
              "distillation_state": {"status": ""}}
    assert wd.select_distillation_candidates([source, {"id": "k", "kind": "concept"}]) == [source]


def test_generated_page_roundtrip_has_single_citation_without_fake_chat(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    marker = wd.wiki.REPORT_CITATION_MARKER
    plan = {"action": "create", "kind": "concept", "title": "Definition", "summary": "A definition",
            "body": f"Definition with evidence.> **{marker}**: summary", "report_citation": f"> **{marker}**: summary"}
    payload, status, _ = wd._distill_one_with_status({"id": "source-test"}, lambda _: json.dumps(plan))
    assert status == "created"
    saved = wd.wiki.upsert_page(payload)
    reread = wd.wiki.get_page(saved["id"])
    assert reread["body"].count(marker) == 1
    assert "대화 발췌" not in reread["body"]
    assert reread["report_citation"] == "summary"
