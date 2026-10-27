"""Independent source scheduling must never turn missing provenance into success."""
import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from reports import raw_archive
from reports.evidence_cards import event_to_evidence_card

NOW = datetime(2026, 10, 9, 3, tzinfo=timezone.utc)


def build_archived_event(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))

    def make(*, body="Demand improved, but orders remain uncertain.\n", raw=None,
             source="saveticker", archive_source="saveticker_article",
             url="https://saveticker.com/news/test-article", scope="authorized_article",
             kind="article"):
        record = raw_archive.save_raw_artifact(
            archive_source, "text", NOW, "Source test", url,
            raw if raw is not None else body, ".txt")
        record.update(body_coverage="full", text_complete=True, extraction_scope=scope)
        raw_archive.save_extracted_text(record, body)
        return {"source": source, "title": "Source test", "url": url,
                "kind": kind, "collected_at": NOW.isoformat(),
                "body_raw": body[:4000], "raw_sha256": record["sha256"],
                "extraction_scope": scope, "body_coverage": "full",
                **{key: record[key] for key in ("raw_path", "text_path", "manifest_path")}}

    return make


def module():
    return importlib.import_module("reports.wiki_source_revision")


def test_complete_source_progresses_independently(archived_event):
    readable = archived_event()
    missing = archived_event(url="https://saveticker.com/news/missing", body="Missing original.")
    Path(missing["raw_path"]).unlink()
    before = Path(readable["raw_path"]).read_bytes()
    result = module().discover_revisions([readable, missing], now=NOW)
    assert result["eligible"][0]["body"] == "Demand improved, but orders remain uncertain.\n"
    assert result["unavailable"][0]["reason"] == "raw_unavailable"
    assert Path(readable["raw_path"]).read_bytes() == before


def test_redelivery_reuses_revision_and_actual_ids(archived_event):
    first = archived_event()
    again = {**first, "collected_at": "2026-10-09T03:01:00+00:00", "title": "Retitled"}
    result = module().discover_revisions([first, again], now=NOW)
    assert len(result["eligible"]) == 1
    assert set(result["eligible"][0]["observed_evidence_ids"]) == {
        event_to_evidence_card(first, now=NOW).id, event_to_evidence_card(again, now=NOW).id}
    assert len(result["eligible"][0]["revision_id"]) == 64


def test_role_change_reuses_compilation_identity(archived_event):
    first = archived_event()
    again = {**first, "collected_at": "2026-10-09T03:01:00+00:00"}
    roles = {event_to_evidence_card(again, now=NOW).id: "conflict"}
    result = module().discover_revisions([first, again], now=NOW, evidence_roles=roles)
    assert {s["role"] for s in result["eligible"]} == {"support", "conflict"}
    assert len({s["revision_id"] for s in result["eligible"]}) == 1
    assert result["eligible"][1]["observed_evidence_ids"] == [event_to_evidence_card(again, now=NOW).id]


@pytest.mark.parametrize("change", ["text", "raw", "scope"])
def test_changed_source_creates_new_revision(archived_event, change):
    original = module().build_revision(archived_event(), now=NOW)
    kwargs = {"text": {"body": "A revised source body.\n"},
              "raw": {"raw": "Different full raw original."},
              "scope": {"scope": "rendered_article"}}[change]
    revised = module().build_revision(archived_event(**kwargs), now=NOW)
    assert original["revision_id"] != revised["revision_id"]


@pytest.mark.parametrize("change,reason", [
    ("provider", "provider_mismatch"), ("url", "url_mismatch"),
    ("raw_hash", "raw_hash_mismatch"), ("body_hash", "text_hash_mismatch"),
    ("scope", "scope_mismatch"), ("manifest", "manifest_unavailable"),
    ("outside", "archive_path_invalid"), ("preview", "body_incomplete"),
])
def test_invalid_provenance_is_not_compiled(archived_event, tmp_path, change, reason):
    event = archived_event()
    if change == "provider":
        event["source"] = "arca"
    elif change == "url":
        event["url"] = "https://saveticker.com/news/other"
    elif change == "raw_hash":
        Path(event["raw_path"]).write_text("tampered raw")
    elif change == "body_hash":
        event["text_sha256"] = "0" * 64
    elif change == "scope":
        event["extraction_scope"] = "rendered_article"
    elif change == "manifest":
        Path(event["manifest_path"]).unlink()
    elif change == "outside":
        event["text_path"] = str(tmp_path / "private.txt")
    else:
        manifest = json.loads(Path(event["manifest_path"]).read_text())
        manifest.update(body_coverage="preview", text_complete=False)
        Path(event["manifest_path"]).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=reason):
        module().build_revision(event, now=NOW)


def test_oversized_source_keeps_full_pending_identity(archived_event):
    event = archived_event(body="x" * 20001)
    result = module().discover_revisions([event], now=NOW)
    assert result["eligible"] == []
    assert result["pending_chunking"][0]["reason"] == "source_budget_exceeded"
    assert result["pending_chunking"][0]["text_sha256"] == hashlib.sha256(b"x" * 20001).hexdigest()


def test_chunking_opt_in_keeps_verified_complete_body_and_identity(archived_event):
    body = 'x' * 20000 + 'ORIGINAL TAIL'
    event = archived_event(body=body)
    pending = module().discover_revisions([event], now=NOW)['pending_chunking'][0]
    result = module().discover_revisions([event], now=NOW, allow_chunking=True)
    assert result['eligible'][0]['body'] == body
    assert result['eligible'][0]['revision_id'] == pending['revision_id']
    assert result['pending_chunking'] == []


@pytest.mark.parametrize("body", ["Just a moment... Cloudflare challenge", "Sign in to read this article"])
def test_challenge_or_login_is_not_article(archived_event, body):
    with pytest.raises(ValueError, match="reauth_required"):
        module().build_revision(archived_event(body=body), now=NOW)


@pytest.mark.parametrize("source,archive,url,scope,kind,want", [
    ("telegram:public_channel", "telegram", "https://t.me/public_channel/42", "public_message", "community_signal", "community"),
    ("arca", "arca", "https://arca.live/b/stock/42", "authorized_article", "news", "community"),
    ("fred", "fred", "https://fred.stlouisfed.org/series/DFF", "series_notes", "series_notes", "fred_series_notes"),
    ("saveticker_report_pdf", "saveticker_report_pdf", "https://saveticker.com/report/daily.pdf", "ocr_extracted_text", "report", "ocr_report"),
])
def test_source_classes_keep_attribution_and_scope(archived_event, source, archive, url, scope, kind, want):
    event = archived_event(source=source, archive_source=archive, url=url, scope=scope, kind=kind)
    result = module().build_revision(event, now=NOW)
    assert result["provenance_class"] == want
    assert result["scope"] == scope


@pytest.mark.parametrize("classified", [True, False])
def test_native_public_telegram_without_top_level_kind_is_eligible(archived_event, classified):
    event = archived_event(source="telegram:public_channel", archive_source="telegram",
        url="https://t.me/public_channel/42", scope="public_message", kind="community_signal")
    event.pop("kind")
    event["record_kind"] = "content"
    if classified:
        event["classification"] = {"kind": "community_signal"}
    revision = module().build_revision(event, now=NOW)
    assert revision["provenance_class"] == "community"
    assert revision["provider"] == "telegram:public_channel"


def test_expired_original_is_not_verified_from_text(archived_event):
    event = archived_event()
    manifest = json.loads(Path(event["manifest_path"]).read_text())
    manifest["expires_at"] = "2026-10-08T00:00:00+00:00"
    Path(event["manifest_path"]).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="raw_expired"):
        module().build_revision(event, now=NOW)


@pytest.mark.parametrize("source,archive,url,scope,kind", [
    ("saveticker", "saveticker_article", "https://example.com/news/test", "authorized_article", "article"),
    ("telegram:public_channel", "telegram", "https://t.me/other_channel/42", "public_message", "community_signal"),
    ("fred", "fred", "https://example.com/series/DFF", "series_notes", "series_notes"),
])
def test_matching_manifest_cannot_launder_wrong_provider_host(archived_event, source, archive, url, scope, kind):
    event = archived_event(source=source, archive_source=archive, url=url, scope=scope, kind=kind)
    with pytest.raises(ValueError, match="source_class_ineligible"):
        module().build_revision(event, now=NOW)


def test_invalid_text_encoding_does_not_become_replacement_character_knowledge(archived_event):
    event = archived_event()
    Path(event["text_path"]).write_bytes(b"Complete body\xff")
    with pytest.raises(ValueError, match="text_encoding_invalid"):
        module().build_revision(event, now=NOW)


def test_io_failure_does_not_block_an_independent_source(archived_event, monkeypatch):
    first = archived_event()
    second = archived_event(url="https://saveticker.com/news/second", body="Another valid article.")
    read = raw_archive.read_raw_artifact

    def guarded(path, **kwargs):
        if str(path) == first["raw_path"]:
            raise PermissionError("private path must not leak")
        return read(path, **kwargs)

    monkeypatch.setattr(raw_archive, "read_raw_artifact", guarded)
    result = module().discover_revisions([first, second], now=NOW)
    assert len(result["eligible"]) == 1
    assert result["eligible"][0]["canonical_url"] == "https://saveticker.com/news/second"
    assert result["unavailable"][0]["reason"] == "archive_read_failed"


def test_private_archive_requires_explicit_authorization_and_keeps_hash_gate(archived_event, monkeypatch):
    root = raw_archive.reports_root()
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(root / "private-sources"))
    event = archived_event()
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(root))
    monkeypatch.delenv("WIKI_SOURCE_ALLOW_PRIVATE", raising=False)
    with pytest.raises(module().SourceGateError, match="archive_path_invalid"):
        module().build_revision(event, now=NOW)
    monkeypatch.setenv("WIKI_SOURCE_ALLOW_PRIVATE", "1")
    assert module().build_revision(event, now=NOW)["body"] == "Demand improved, but orders remain uncertain.\n"
    Path(event["raw_path"]).write_text("tampered private raw")
    with pytest.raises(module().SourceGateError, match="raw_hash_mismatch"):
        module().build_revision(event, now=NOW)
