import json
from datetime import datetime, timezone

from agent_console import shared_memory, wiki
from reports import raw_archive, source_wiki_curator, wiki_distillation as wd
from reports.evidence_cards import cards_to_source_refs, event_to_evidence_card


URL = "https://example.com/article/" + "a" * 700
PATH = "/home/ubuntu/reports/text/saveticker_report_pdf/2026/10/08/" + "p" * 200 + ".txt"


def test_shared_record_retains_source_identifiers_instead_of_clipping_them():
    record = shared_memory.normalize_record({"artifacts": [URL, PATH]})
    assert record["artifacts"] == [URL, PATH]


def test_oversized_reference_is_rejected_instead_of_stored_as_a_different_identifier():
    oversized = "https://example.com/article/" + "a" * 5000
    record = shared_memory.normalize_record({"artifacts": [oversized, URL]})
    assert record["artifacts"] == [URL]


def test_reference_normalization_keeps_existing_sensitive_value_redaction():
    record = shared_memory.normalize_record({"artifacts": ["https://example.com?token=private-value"]})
    assert "private-value" not in json.dumps(record)


def test_reference_normalization_does_not_expose_unrelated_home_paths():
    record = shared_memory.normalize_record({"artifacts": ["/home/ubuntu/.ssh/private-key",
                                                            "/home/ubuntu/reports/text/../../../.ssh/private-key"]})
    assert "private-key" not in json.dumps(record)
    assert record["artifacts"] == ["<local-path>"]


def test_wiki_storage_reload_and_split_preserve_full_source_references(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    page = wiki.upsert_page({"id": "long-reference", "title": "Source reference identity",
                             "body": "A source-backed paragraph. " * 50, "source_refs": [URL, PATH]})
    wiki._CACHE.clear()
    loaded = wiki.get_page(page["id"])
    assert loaded["source_refs"] == [URL, PATH]
    assert loaded["raw"]["artifacts"] == [URL, PATH]
    split = wiki.split_for_readability(page["id"], max_chars=400)
    children = wiki.split_children_for_page(split)
    assert children
    assert all(child["source_refs"] == [URL, PATH] for child in children)


def test_complete_archive_can_be_reopened_from_long_stored_wiki_reference(monkeypatch, tmp_path):
    root = tmp_path / ("archive-" + "p" * 100)
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(root))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    artifact = raw_archive.save_raw_artifact("saveticker_article", "html", now, "A" * 80,
                                             "https://saveticker.com/news/42", "Original full body", ".html")
    artifact.update(body_coverage="full", text_complete=True)
    raw_archive.save_extracted_text(artifact, "Original full body")
    assert len(artifact["text_path"]) > 180
    stored = wiki.upsert_page({"id": "archived-reference", "title": "Stored body reference",
                              "kind": "source_digest", "body": "Source digest",
                              "source_refs": [artifact["url"], artifact["text_path"]]})
    wd._attach_article_context([stored], cache_dir=tmp_path / "empty-cache", now=now)
    assert len(stored["_source_articles"]) == 1
    assert stored["_source_articles"][0]["text"] == "Original full body"


def test_event_evidence_and_curator_preserve_full_source_identifiers():
    event = {"source": "saveticker", "title": "Full article", "url": URL,
             "body": "A substantive full article. " * 40,
             "text_path": PATH, "raw_path": PATH.removesuffix(".txt") + ".html",
             "classification": {"wiki_eligible": True, "kind": "article", "topic": "policy"}}
    card = event_to_evidence_card(event)
    assert card.source_url == URL
    refs = cards_to_source_refs([card])
    assert URL in refs and PATH in refs
    pages = source_wiki_curator.build_wiki_pages_from_events([event])
    assert pages and all(URL in page["source_refs"] and PATH in page["source_refs"] for page in pages)


def test_curator_body_keeps_a_working_full_source_url():
    event = {"source": "saveticker", "title": "Long source URL", "url": URL,
             "body": "The complete source body.",
             "classification": {"wiki_eligible": True, "kind": "article", "topic": "policy"}}
    pages = source_wiki_curator.build_wiki_pages_from_events([event])
    assert pages and all(URL in page["body"] for page in pages)


def test_different_long_urls_do_not_create_false_event_links(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    events = [{"source": "saveticker", "title": title, "url": URL + suffix,
               "body": "The complete source body.",
               "classification": {"wiki_eligible": True, "kind": "article", "topic": topic}}
              for title, suffix, topic in [("Article A", "A", "alpha"), ("Article B", "B", "beta")]]
    pages = {p["id"]: p for p in source_wiki_curator.build_wiki_pages_from_events(events)}
    assert "source-topic-beta" not in pages["source-topic-alpha"]["links"]
    assert "source-topic-alpha" not in pages["source-topic-beta"]["links"]


def test_different_long_urls_do_not_create_false_shared_source_lint():
    pages = [{"id": identity, "title": identity, "status": "draft", "source_refs": [URL + suffix]}
             for identity, suffix in [("page-a", "A"), ("page-b", "B")]]
    issues = wiki._lint_relational_issues(pages)
    assert not any(i["code"] == "missing_cross_ref" for i in issues)


def test_different_long_urls_do_not_link_an_unrelated_judgment(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    wiki.upsert_page({"id": "unrelated-judgment", "title": "Alpha definition", "kind": "concept",
                      "body": "An unrelated source defines alpha.", "source_refs": [URL + "A"]})
    event = {"source": "saveticker", "title": "Beta observation", "url": URL + "B",
             "body": "The source describes beta.",
             "classification": {"wiki_eligible": True, "kind": "article", "topic": "beta"}}
    pages = source_wiki_curator.build_wiki_pages_from_events([event])
    assert pages and all("unrelated-judgment" not in p["links"] for p in pages)


def test_distilled_citation_and_reference_point_to_the_same_full_url():
    page = {"id": "long-url-source", "_require_articles": True,
            "_source_articles": [{"url": URL, "text": "The source describes a definition.",
                                  "text_complete": True, "content_kind": "article",
                                  "source_text_scope": "article_fulltext", "evidence_id": "e1"}]}
    plan = {"action": "create", "kind": "concept", "title": "Source definition",
            "body": "A definition from the source [S1]."}
    result, status, reason = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert status == "created", reason
    assert f"[S1]({URL})" in result["body"]
    assert URL in result["source_refs"]


def test_source_context_retains_a_url_longer_than_a_display_limit(tmp_path):
    long_url = URL + "b" * 700
    cache = tmp_path / "cache"
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    event = {"source": "official", "title": "Full source", "url": long_url,
             "body": "The complete official source body.", "body_raw": "The complete official source body.",
             "fulltext_status": "full", "type": "article"}
    from reports import source_collector

    source_collector.append_events([event], cache_dir=cache, now=now)
    stored = source_collector.load_recent_events(cache_dir=cache, now=now)[0]
    page = {"evidence_ids": [event_to_evidence_card(stored, now=now).id]}
    wd._attach_article_context([page], cache_dir=cache, now=now)
    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["url"] == long_url
