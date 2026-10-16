from reports import wiki_distillation as wd
from reports import raw_archive, source_collector
from reports.evidence_cards import event_to_evidence_card
from datetime import datetime, timezone
import json


def _article(**changes):
    return {
        "url": "https://t.me/channel/42",
        "evidence_id": "event-evidence",
        "text": "Complete message body.",
        "raw_sha256": "a" * 64,
        "content_kind": "community_signal",
        "provenance": "telegram:channel",
        "source_text_scope": "article_fulltext",
        "text_complete": True,
        **changes,
    }


def test_event_and_matching_archive_body_are_attached_only_once():
    page = {"_source_articles": []}
    wd._append_source_article(page, _article())
    wd._append_source_article(page, _article(evidence_id="", content_kind="unknown"))

    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["evidence_id"] == "event-evidence"
    assert page["_source_articles"][0]["content_kind"] == "community_signal"


def test_duplicate_bodies_do_not_use_up_source_count_budget(monkeypatch):
    monkeypatch.setattr(wd, "MAX_SOURCE_ARTICLES_PER_PAGE", 1)
    page = {"_source_articles": []}
    wd._append_source_article(page, _article())
    wd._append_source_article(page, _article(evidence_id=""))

    assert len(page["_source_articles"]) == 1
    assert not page.get("_source_text_error")


def test_same_url_different_body_remains_separate_evidence():
    page = {"_source_articles": []}
    wd._append_source_article(page, _article())
    wd._append_source_article(page, _article(text="Revised complete body.", raw_sha256="b" * 64))
    assert len(page["_source_articles"]) == 2


def test_identical_body_from_different_source_is_not_deduplicated():
    page = {"_source_articles": []}
    wd._append_source_article(page, _article())
    wd._append_source_article(page, _article(url="https://t.me/other_channel/10", evidence_id="other"))
    assert len(page["_source_articles"]) == 2


def _stored_telegram(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    record = raw_archive.save_raw_artifact(
        "telegram", "html", now, "Complete message", "https://t.me/channel/42",
        b"<article>Complete message body.</article>", ".html",
    )
    record.update(body_coverage="full", text_complete=True, extraction_scope="public_message")
    raw_archive.save_extracted_text(record, "Complete message body.")
    return now, record


def test_real_archive_does_not_replace_event_provider_with_folder_name(monkeypatch, tmp_path):
    now, record = _stored_telegram(monkeypatch, tmp_path)
    event = {**record, "id": "telegram-event", "source": "telegram:channel",
             "collected_at": now.isoformat(), "fulltext_status": "full",
             "classification": {"kind": "community_signal"}}
    path = source_collector._event_file(tmp_path / "cache", now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(event) + "\n")
    page = {"evidence_ids": [event_to_evidence_card(event).id], "source_refs": [record["text_path"]]}

    wd._attach_article_context([page], cache_dir=tmp_path / "cache", now=now)

    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["provenance"] == "telegram:channel"
    assert wd._full_body_source_class(page["_source_articles"][0]) == "community"


def test_complete_archive_remains_eligible_after_recent_event_expiry(monkeypatch, tmp_path):
    now, record = _stored_telegram(monkeypatch, tmp_path)
    page = {"evidence_ids": ["expired-event"], "source_refs": [record["text_path"]]}

    wd._attach_article_context([page], cache_dir=tmp_path / "empty-cache", now=now)

    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["text"] == "Complete message body."
    assert page["_source_articles"][0]["provenance"] == "telegram:channel"
    assert wd._full_body_source_class(page["_source_articles"][0]) == "community"


def test_archive_provider_cannot_be_inferred_from_mismatched_url(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    record = raw_archive.save_raw_artifact(
        "telegram", "html", datetime(2026, 10, 8, tzinfo=timezone.utc), "Wrong source",
        "https://example.com/channel/42", b"<article>Some content</article>", ".html",
    )
    record.update(body_coverage="full", text_complete=True)
    raw_archive.save_extracted_text(record, "Some content")
    article = wd._extracted_text_record(record["text_path"])
    article["provenance"] = article["source"]
    assert wd._full_body_source_class(article) == ""
