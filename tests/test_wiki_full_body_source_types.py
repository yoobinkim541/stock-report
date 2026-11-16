import json

import pytest


@pytest.mark.parametrize(
    ("provider", "url", "scope", "content_kind"),
    [
        ("telegram:public_channel", "https://t.me/public_channel/42", "article_fulltext", "community_signal"),
        ("arca", "https://arca.live/b/stock/12345", "article_fulltext", "community_signal"),
        ("telegram:public_channel", "https://t.me/public_channel/43", "article_fulltext", "analysis"),
        ("arca", "https://arca.live/b/stock/12346", "authorized_article", "news"),
        ("arca", "https://arca.live/b/stock/12347", "authorized_article", "earnings"),
    ],
)
def test_full_public_community_bodies_are_citable_low_confidence_drafts(provider, url, scope, content_kind):
    from reports import wiki_distillation as wd

    article = {
        "evidence_id": "community-evidence",
        "url": url,
        "text": "A complete public community message with a concrete observation.",
        "text_complete": True,
        "content_kind": content_kind,
        "source_text_scope": scope,
        "provenance": provider,
    }
    plan = {
        "action": "create",
        "kind": "concept",
        "title": "Community observation",
        "summary": "A community observation, not a verified fact.",
        "body": "The message reports an observation [S1].",
        "status": "reviewed",
        "confidence": 0.95,
    }

    payload, outcome, reason = wd._distill_one_with_status(
        {"id": "community-source", "_require_articles": True, "_source_articles": [article]},
        lambda prompt: json.dumps(plan),
    )

    assert outcome == "created", reason
    assert payload["status"] == "draft"
    assert payload["confidence"] <= 0.5
    assert f"[S1]({url})" in payload["body"]
    assert url in payload["source_refs"]
    assert "community-evidence" in payload["evidence_ids"]


@pytest.mark.parametrize("scope", ["series_notes", "extracted_text"])
def test_official_fred_series_notes_are_full_body_eligible(scope):
    from reports import wiki_distillation as wd

    url = "https://fred.stlouisfed.org/series/DFF"
    article = {
        "evidence_id": "fred-notes-evidence",
        "url": url,
        "text": "Series notes define the measure and explain a historical methodology change.",
        "text_complete": True,
        "content_kind": "unknown",
        "source_text_scope": scope,
        "provenance": "fred",
    }
    plan = {"action": "create", "kind": "concept", "title": "FRED series definition",
            "summary": "Series definition", "body": "The notes describe the series [S1]."}

    payload, outcome, reason = wd._distill_one_with_status(
        {"id": "fred-source", "_require_articles": True, "_source_articles": [article]},
        lambda _: json.dumps(plan),
    )

    assert outcome == "created", reason
    assert payload["status"] == "draft"
    assert f"[S1]({url})" in payload["body"]
    assert url in payload["source_refs"]
    assert "fred-notes-evidence" in payload["evidence_ids"]


@pytest.mark.parametrize(
    ("provider", "url", "scope", "content_kind", "complete"),
    [
        ("telegram:public_channel", "https://example.com/42", "article_fulltext", "community_signal", True),
        ("arca", "https://t.me/channel/42", "article_fulltext", "community_signal", True),
        ("fred", "https://example.com/series/DFF", "series_notes", "unknown", True),
        ("telegram:public_channel", "https://t.me/public_channel/42", "article_fulltext", "community_signal", False),
        ("fred", "https://fred.stlouisfed.org/series/DFF", "series_notes", "unknown", False),
    ],
)
def test_specialized_sources_require_matching_provider_url_and_complete_body(
    provider, url, scope, content_kind, complete
):
    from reports import wiki_distillation as wd

    page = {
        "id": "untrusted-special-source",
        "_require_articles": True,
        "_source_articles": [{
            "evidence_id": "e1", "url": url, "text": "Some body text",
            "text_complete": complete, "content_kind": content_kind,
            "source_text_scope": scope, "provenance": provider,
        }],
    }

    payload, outcome, reason = wd._distill_one_with_status(
        page,
        lambda _: pytest.fail("mismatched or incomplete source must not reach the model"),
    )

    assert payload is None
    assert outcome == "failed"
    assert "article" in reason or "incomplete" in reason


def test_macro_snapshot_is_not_eligible_as_fred_series_notes():
    from reports import wiki_distillation as wd

    page = {
        "id": "fred-snapshot",
        "_require_articles": True,
        "_source_articles": [{
            "evidence_id": "fred-snapshot-evidence",
            "url": "https://fred.stlouisfed.org/series/DFF",
            "text": "Latest value: 3.2",
            "text_complete": True,
            "content_kind": "macro_snapshot",
            "source_text_scope": "structured_snapshot",
            "provenance": "fred",
        }],
    }

    payload, outcome, reason = wd._distill_one_with_status(
        page,
        lambda _: pytest.fail("numeric macro snapshot must not enter full-text synthesis"),
    )

    assert payload is None
    assert outcome == "failed"
    assert "article" in reason


def test_sell_side_channel_post_is_its_own_class_and_prompt_attributes_house_views():
    from reports import wiki_distillation as wd

    def article(channel: str) -> dict:
        return {"evidence_id": f"{channel}-evidence", "url": f"https://t.me/{channel}/42",
                "text": "키움증권은 엔비디아 목표주가를 올렸다. 3분기 매출은 570억 달러였다.", "text_complete": True,
                "content_kind": "broker_comment", "source_text_scope": "public_message",
                "provenance": f"telegram:{channel}"}

    assert wd._full_body_source_class(article("kwusa")) == "sell_side"
    assert wd._full_body_source_class(article("insidertracking")) == "community"
    assert wd._full_body_source_class({**article("kwusa"), "url": "https://t.me/other/42"}) == ""

    prompts: list[str] = []
    wd._distill_one_with_status(
        {"id": "broker-source", "_require_articles": True, "_source_articles": [article("kwusa")]},
        lambda prompt: prompts.append(prompt) or "{}")
    assert "증권사에 귀속" in prompts[0] and "커뮤니티 신호" not in prompts[0]

