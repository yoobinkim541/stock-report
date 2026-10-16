import json
from pathlib import Path

import pytest
import requests

from reports import source_collector as collector, wiki_distillation


def _card(body, url="https://t.me/insidertracking/60001"):
    return (f'<div class="tgme_widget_message">'
            f'<div class="tgme_widget_message_text">{body}</div>'
            f'<a class="tgme_widget_message_date" href="{url}"></a></div>')


def _fetch(monkeypatch, tmp_path, html):
    class Response:
        def __init__(self, text):
            self.text = text

    def get(url, timeout=0):
        if url == "https://r.jina.ai/http://t.me/s/insidertracking":
            return Response("No bold preview")
        assert url == "https://t.me/s/insidertracking"
        return Response(html)

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(collector, "_bounded_get", get)
    return collector.fetch_telegram_channel_events(["insidertracking"])[0]


def _archived_page(event, tmp_path):
    page = {"id": "source-message-test", "source_refs": [event["text_path"]]}
    wiki_distillation._attach_article_context([page], cache_dir=tmp_path / "empty-cache")
    return page


def test_direct_telegram_parser_retains_message_beyond_display_limit():
    body = "Earnings discussion " + "source detail " * 350 + "END-OF-MESSAGE"
    message = collector._telegram_messages_from_html(_card(body), "insidertracking")[0]
    assert message["body_raw"] == body


def test_collected_message_is_rereadable_from_archive_without_event_queue(monkeypatch, tmp_path):
    body = "Earnings discussion " + "source detail " * 350 + "END-OF-MESSAGE"
    event = _fetch(monkeypatch, tmp_path, _card(body))
    assert event["body_raw"] == body
    assert event["fulltext_status"] == "full"
    assert event["body_coverage"] == "full"
    assert event["extraction_scope"] == "public_message"
    assert Path(event["text_path"]).read_text() == body
    manifest = json.loads(Path(event["manifest_path"]).read_text())
    assert manifest["text_complete"] is True
    original = Path(event["raw_path"]).read_bytes()
    page = _archived_page(event, tmp_path)
    assert len(page["_source_articles"]) == 1
    article = page["_source_articles"][0]
    assert article["text"] == body
    assert article["text_complete"] is True
    assert wiki_distillation._article_is_eligible(article)
    assert Path(event["raw_path"]).read_bytes() == original


def test_native_full_message_upgrades_preview_once_without_erasing_it(monkeypatch, tmp_path):
    body = "Earnings discussion " + "source detail " * 350 + "END-OF-MESSAGE"
    event = _fetch(monkeypatch, tmp_path, _card(body))
    cache = tmp_path / "cache"
    preview = {"source": event["source"], "title": event["title"], "url": event["url"],
               "body_raw": "Only a preview", "fulltext_status": "preview_only"}
    assert collector.append_events([preview], cache_dir=cache) == 1
    assert collector.append_events([event], cache_dir=cache) == 1
    assert collector.append_events([event], cache_dir=cache) == 0
    path = next(cache.glob("events-*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["body_raw"] == "Only a preview"
    assert rows[1]["body_raw"] == body
    recent = collector.load_recent_events(cache_dir=cache)
    assert len(recent) == 1 and recent[0]["body_raw"] == body


def test_jina_title_fallback_remains_explicitly_incomplete(monkeypatch, tmp_path):
    class Response:
        text = "**Only a headline** https://t.me/insidertracking/60001"

    def get(url, timeout=0):
        if url.startswith("https://r.jina.ai/"):
            return Response()
        raise requests.ConnectionError("public page unavailable")

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(collector, "_bounded_get", get)
    event = collector.fetch_telegram_channel_events(["insidertracking"])[0]
    assert event["fulltext_status"] == "preview_only"
    assert event["body_coverage"] == "partial"
    manifest = json.loads(Path(event["manifest_path"]).read_text())
    assert manifest["text_complete"] is False
    page = _archived_page(event, tmp_path)
    assert not any(wiki_distillation._article_is_eligible(article)
                   for article in page["_source_articles"])


@pytest.mark.parametrize("url", ["https://t.me/other_channel/60001",
                                "https://t.me/insidertracking/60001?token=secret",
                                "https://example.com/60001"])
def test_unbound_message_url_cannot_claim_complete_source(monkeypatch, tmp_path, url):
    event = _fetch(monkeypatch, tmp_path, _card("Public message text", url))
    assert event["fulltext_status"] != "full"
    assert event["text_complete"] is False


def test_long_complete_message_is_preserved_but_requires_chunk_pipeline(monkeypatch, tmp_path):
    body = "Public message " + "x" * 20001
    event = _fetch(monkeypatch, tmp_path, _card(body))
    assert event["body_raw"] == body
    assert Path(event["text_path"]).read_text() == body
    page = _archived_page(event, tmp_path)
    assert "chunk pipeline required" in page["_source_text_error"]
