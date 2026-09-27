from __future__ import annotations

from reports import wiki_relation_cleanup


def _page(page_id: str, *, status: str = "draft", links: list[str] | None = None, tags: list[str] | None = None, **extra) -> dict:
    return {
        "id": page_id,
        "title": page_id,
        "summary": page_id,
        "body": page_id,
        "surface": "wiki",
        "kind": "note",
        "status": status,
        "tags": tags or ["wiki"],
        "source_refs": [],
        "links": links or [],
        **extra,
    }


def test_plan_removes_only_safe_graph_noise():
    pages = [
        _page("source", kind="source_digest", links=["source", "missing", "stale", "old", "peer", "merged", "merged", "live"]),
        _page("stale", status="archived", tags=["wiki", "archived_reason:stale"]),
        _page("old", status="archived"),
        _page("peer", kind="source_digest"),
        _page(
            "merged",
            status="archived",
            tags=["wiki", "archived_reason:merged", "merged_into:live"],
            merged_into="live",
            merge_event_id="merge-1",
        ),
        _page("live"),
    ]

    result = wiki_relation_cleanup.plan(pages)

    assert result["candidate_count"] == 6
    assert result["by_reason"] == {
        "source_provenance_edge": 1,
        "self_link": 1,
        "dangling_link": 1,
        "duplicate_link": 1,
        "archived_target": 1,
        "stale_archived_target": 1,
    }
    assert result["updates"][0]["id"] == "source"
    assert result["updates"][0]["links"] == ["merged", "live"]


def test_apply_uses_one_batch_and_dry_run_does_not_write(monkeypatch):
    pages = [
        _page("source", links=["stale"]),
        _page("stale", status="archived", tags=["wiki", "archived_reason:stale"]),
    ]
    calls: list[list[dict]] = []
    monkeypatch.setattr(
        wiki_relation_cleanup.wiki,
        "batch_upsert_pages",
        lambda updates: calls.append(list(updates)) or list(updates),
    )

    dry_run = wiki_relation_cleanup.apply(pages, dry_run=True)
    assert dry_run["updated_count"] == 1
    assert calls == []

    applied = wiki_relation_cleanup.apply(pages, dry_run=False)
    assert applied["updated_count"] == 1
    assert len(calls) == 1
    assert calls[0][0]["links"] == []
