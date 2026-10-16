import json
import pytest
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
            "_source_articles": [{"evidence_id": "used", "url": "https://example.org/actual", "text": "body"}]}
    plan = {"action": "create", "kind": "concept", "title": "Capacity", "summary": "Capacity limits",
            "body": "Capacity constrains execution [S1]", "status": "draft", "confidence": .5}
    result, status, _ = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert status == "created"
    assert result["source_refs"][0] == "https://example.org/actual"
    assert result["evidence_ids"] == ["used"]


def test_archived_knowledge_without_evidence_is_not_revived(monkeypatch):
    source = {"id": "source-s", "kind": "source_digest", "evidence_ids": ["new"],
              "links": ["old"], "distillation_state": {"status": "created", "last_result_id": "old"}}
    archived = {"id": "old", "kind": "concept", "status": "archived"}
    monkeypatch.setattr(wd.wiki, "_all_wiki_pages", lambda: [source, archived])
    monkeypatch.setattr(wd, "_attach_article_context", lambda _: None)
    saved = []
    monkeypatch.setattr(wd.wiki, "upsert_page", lambda p: saved.append(p) or p)
    def forbidden(_):
        raise AssertionError("must not regenerate archived knowledge")
    result = wd.run(llm_fn=forbidden, fulltext=True, page_ids=["source-s"])
    assert result["created"] == []
    assert [p["id"] for p in saved] == ["source-s"]
    assert saved[0]["distillation_state"]["status"] == "failed"
    assert "unavailable" in saved[0]["distillation_state"]["reason"]


@pytest.mark.parametrize("fulltext", [False, True])
def test_archived_knowledge_with_digest_evidence_gets_new_version(monkeypatch, tmp_path, fulltext):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    archived_id = wd._distillation_id("source-s", "concept")
    archived = wd.wiki.upsert_page({"id": archived_id, "title": "Old knowledge", "kind": "concept",
                                    "status": "archived", "body": "Historical explanation"})
    wd.wiki.upsert_page({"id": "source-s", "title": "New evidence", "kind": "source_digest",
                        "body": "Stored evidence explains changed capacity constraints.",
                        "evidence_ids": ["new"], "links": [archived["id"]],
                        "distillation_state": {"status": "created", "last_result_id": archived["id"]}})
    def attach_articles(pages):
        if fulltext:
            for page in pages:
                page["_source_articles"] = [{
                    "evidence_id": "new",
                    "url": "https://example.org/updated-capacity",
                    "text": "Updated evidence describes the changed capacity constraints.",
                    "content_kind": "article",
                    "source_text_scope": "extracted_text",
                    "provenance": "test fixture",
                }]
    monkeypatch.setattr(wd, "_attach_article_context", attach_articles)
    plan = {"action": "create", "kind": "concept", "title": "Updated capacity",
            "summary": "Updated constraints", "body": "Capacity depends on the updated constraints [S1]."}
    result = wd.run(llm_fn=lambda _: json.dumps(plan), fulltext=fulltext, page_ids=["source-s"])
    assert len(result["created"]) == 1
    assert result["created"][0]["id"] != archived["id"]
    old = wd.wiki.get_page(archived["id"])
    assert old["status"] == "archived"
    assert old["body"] == archived["body"]
    source = wd.wiki.get_page("source-s")
    assert source["distillation_state"]["last_result_id"] == result["created"][0]["id"]


@pytest.mark.parametrize("status,attempts", [("skipped", 1), ("failed", 3)])
def test_orphan_replacement_honors_recorded_outcome(status, attempts):
    page = {"id": "source-s", "kind": "source_digest", "evidence_ids": ["new"],
            "distillation_state": {"status": status, "attempts": attempts, "last_result_id": "old"}}
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    archived = {"id": "old", "kind": "concept", "status": "archived"}
    assert wd.select_distillation_candidates([page, archived]) == []
    from reports.wiki_pipeline_health import _summarize_curation_health
    health = _summarize_curation_health([page, archived])
    assert health["actionable_unlinked_count"] == 0
    assert health["distillation_pending_count"] == 0


def test_legacy_skip_without_fingerprint_is_preserved():
    page = {"id": "source-s", "kind": "source_digest", "evidence_ids": ["old"],
            "distillation_state": {"status": "skipped", "attempts": 1}}
    assert wd.select_distillation_candidates([page]) == []
    from reports.wiki_pipeline_health import _summarize_curation_health
    health = _summarize_curation_health([page])
    assert health["intentional_skip_count"] == 1
    assert health["actionable_unlinked_count"] == 0


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
