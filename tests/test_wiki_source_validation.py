import json

import pytest

from reports import wiki_distillation as wd


def _article(**changes):
    return {"evidence_id": "e1", "url": "https://example.com/article",
            "text": "A complete source body.", "text_complete": True,
            "content_kind": "article", "source_text_scope": "article_fulltext",
            "provenance": "official", **changes}


@pytest.mark.parametrize("invalid", [
    {"content_kind": "unknown", "source_text_scope": "unclassified_source_text"},
    {"provenance": "arca", "content_kind": "news", "url": "https://example.com/article"},
    {"provenance": "telegram:channel", "content_kind": "analysis", "url": "https://t.me/other/42"},
    {"provenance": "fred", "content_kind": "article", "url": "https://example.com/series/DFF"},
    {"url": "javascript:alert(1)"},
    {"url": "https://user:password@example.com/article"},
    {"url": "https://example.com/" + "a" * 5000},
    {"text": ""},
])
def test_valid_source_cannot_mask_invalid_source_in_same_batch(invalid):
    called = []
    page = {"id": "mixed-source", "_require_articles": True,
            "_source_articles": [_article(), _article(evidence_id="e2", **invalid)]}
    plan = {"action": "create", "kind": "risk", "title": "Invalid synthesis",
            "body": "The second source claims a risk [S2]."}
    result, status, reason = wd._distill_one_with_status(
        page, lambda _: called.append(True) or json.dumps(plan))
    assert called == []
    assert result is None and status == "failed"
    assert "source" in reason


@pytest.mark.parametrize(("provider", "kind", "url"), [
    ("arca", "info", "https://arca.live/b/stock/123"),
    ("telegram:channel", "breaking", "https://t.me/channel/42"),
    ("telegram:channel", "premarket", "https://t.me/channel/42"),
])
def test_supported_full_community_categories_retain_unverified_source_policy(provider, kind, url):
    page = {"id": "community", "_require_articles": True,
            "_source_articles": [_article(provenance=provider, content_kind=kind, url=url)]}
    seen = []
    plan = {"action": "create", "kind": "concept", "title": "Community observation",
            "body": "The community source reports an observation [S1].", "confidence": 0.99}
    result, status, reason = wd._distill_one_with_status(
        page, lambda prompt: seen.append(prompt) or json.dumps(plan))
    assert status == "created", reason
    assert result["confidence"] <= 0.5
    assert result["status"] == "draft"
    assert "미확인 주장" in seen[0]


def test_valid_multi_source_batch_preserves_source_citation_numbering():
    page = {"id": "valid-mixed", "_require_articles": True,
            "_source_articles": [_article(), _article(evidence_id="e2", url="https://example.com/other")]}
    plan = {"action": "create", "kind": "concept", "title": "Multiple observations",
            "body": "The sources provide related observations [S1] [S2]."}
    result, status, reason = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert status == "created", reason
    assert "[S1](https://example.com/article)" in result["body"]
    assert "[S2](https://example.com/other)" in result["body"]


def test_source_with_url_and_no_evidence_id_uses_url_citation_without_crashing():
    article = _article()
    article.pop("evidence_id")
    page = {"id": "url-only", "_require_articles": True, "_source_articles": [article]}
    plan = {"action": "create", "kind": "concept", "title": "URL observation",
            "body": "The source documents an observation [S1]."}
    result, status, reason = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert status == "created", reason
    assert "[S1](https://example.com/article)" in result["body"]
    assert result["evidence_ids"] == []
