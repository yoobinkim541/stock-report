from __future__ import annotations

from agent_console import wiki


def _row(page_id: str) -> dict:
    return {
        "id": page_id,
        "title": f"Knowledge {page_id}",
        "summary": "investment evidence",
        "body": "body",
        "tags": ["risk"],
        "source": {"surface": "portfolio", "screen": "portfolio"},
        "createdAt": "2026-09-01T00:00:00+00:00",
        "updatedAt": "2026-09-01T00:00:00+00:00",
    }


def test_qmd_hydrates_only_hit_records(monkeypatch):
    records = [_row(f"page-{idx}") for idx in range(1000)]
    converted = []
    original_convert = wiki._record_to_page

    monkeypatch.setattr(wiki.qmd_search, "enabled", lambda: True)
    monkeypatch.setattr(wiki.qmd_search, "status", lambda: {"installed": True})
    monkeypatch.setattr(
        wiki.qmd_search,
        "search",
        lambda *_args, **_kwargs: [{"page_id": "page-777", "summary": "matched", "score": 0.9}],
    )

    def count_conversion(record):
        converted.append(record["id"])
        return original_convert(record)

    monkeypatch.setattr(wiki, "_record_to_page", count_conversion)

    pages = wiki._qmd_ranked_pages(
        records,
        query="investment evidence",
        surface="portfolio",
        status="all",
        limit=10,
    )

    assert [page["id"] for page in pages] == ["page-777"]
    assert converted == ["page-777"]


def test_nonempty_qmd_results_skip_full_fallback_scan(monkeypatch):
    rows = [_row(f"page-{idx}") for idx in range(1000)]
    wiki._CACHE.clear()
    monkeypatch.setattr(wiki, "_wiki_records", lambda: rows)
    monkeypatch.setattr(wiki, "_qmd_ranked_pages", lambda *_args, **_kwargs: [{
        "id": "qmd-hit",
        "title": "QMD hit",
    }])
    monkeypatch.setattr(
        wiki,
        "_fallback_ranked_pages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("QMD hits should not trigger fallback scan")),
    )
    monkeypatch.setattr(wiki, "_apply_backlinks", lambda pages, _records: pages)
    monkeypatch.setattr(wiki, "_record_retrieval_usage", lambda *_args, **_kwargs: None)

    pages = wiki.list_pages(query="investment", surface="all", status="all", limit=10)

    assert [page["id"] for page in pages] == ["qmd-hit"]


def test_fallback_remains_available_when_qmd_returns_no_hits(monkeypatch):
    rows = [_row("page-1")]
    fallback = [{"id": "fallback-hit", "title": "Fallback hit"}]
    providers = []
    wiki._CACHE.clear()
    monkeypatch.setattr(wiki, "_wiki_records", lambda: rows)
    monkeypatch.setattr(wiki, "_qmd_ranked_pages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(wiki, "_fallback_ranked_pages", lambda *_args, **_kwargs: fallback)
    monkeypatch.setattr(wiki, "_apply_backlinks", lambda pages, _records: pages)
    monkeypatch.setattr(
        wiki,
        "_record_retrieval_usage",
        lambda *_args, **kwargs: providers.append(kwargs.get("provider")),
    )

    pages = wiki.list_pages(query="investment", surface="all", status="all", limit=10)

    assert pages == fallback
    assert providers == ["fallback"]
