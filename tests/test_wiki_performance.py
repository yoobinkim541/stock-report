from __future__ import annotations


def _wiki_row(page_id: str, *, surface: str = "market") -> dict:
    return {
        "id": page_id,
        "title": page_id,
        "summary": "요약",
        "body": "본문",
        "tags": ["wiki"],
        "source": {"surface": surface, "screen": surface},
        "createdAt": "2026-08-31T00:00:00+00:00",
        "updatedAt": "2026-08-31T00:00:00+00:00",
    }


def test_all_wiki_pages_reuses_cached_storage_snapshot(monkeypatch):
    from agent_console import wiki

    rows = [_wiki_row("page-1"), _wiki_row("page-2", surface="portfolio")]
    calls = 0

    def all_records():
        nonlocal calls
        calls += 1
        return rows

    wiki._CACHE.clear()
    monkeypatch.setattr(wiki.shared_memory, "all_records", all_records)

    first = wiki._all_wiki_pages()
    second = wiki._all_wiki_pages()

    assert calls == 1
    assert [page["id"] for page in first] == ["page-1", "page-2"]
    assert [page["id"] for page in second] == ["page-1", "page-2"]


def test_list_pages_empty_query_avoids_full_fallback_ranking(monkeypatch):
    from agent_console import wiki

    rows = [_wiki_row("market-page"), _wiki_row("portfolio-page", surface="portfolio")]
    wiki._CACHE.clear()
    monkeypatch.setattr(wiki, "_wiki_records", lambda: rows)
    monkeypatch.setattr(
        wiki,
        "_fallback_ranked_pages",
        lambda **_: (_ for _ in ()).throw(AssertionError("empty query must use the page snapshot")),
    )
    monkeypatch.setattr(
        wiki,
        "_qmd_ranked_pages",
        lambda **_: (_ for _ in ()).throw(AssertionError("empty query must not invoke qmd")),
    )

    pages = wiki.list_pages(query="", surface="portfolio", status="all", limit=10)

    assert [page["id"] for page in pages] == ["portfolio-page"]


def test_stats_reuses_normalized_page_snapshot(monkeypatch):
    from agent_console import wiki

    wiki._CACHE.clear()
    monkeypatch.setattr(
        wiki,
        "_all_wiki_pages",
        lambda: [{
            "id": "page-1",
            "title": "페이지 1",
            "status": "stable",
            "kind": "risk",
            "surface": "market",
            "updated_at": "2026-08-31T00:00:00+00:00",
        }],
    )
    monkeypatch.setattr(wiki, "_wiki_records", lambda: (_ for _ in ()).throw(AssertionError("stats should use page snapshot")))

    result = wiki.stats()

    assert result["total"] == 1
    assert result["status_counts"] == {"stable": 1}
    assert result["kind_counts"] == {"risk": 1}
