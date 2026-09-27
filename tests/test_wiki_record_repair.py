from __future__ import annotations

from reports import wiki_record_repair


def _page(page_id: str, title: str, *, kind: str = "playbook", links: list[str] | None = None, refs: list[str] | None = None) -> dict:
    return {
        "id": page_id,
        "title": title,
        "summary": title,
        "body": f"본문 {title}",
        "surface": "ticker",
        "kind": kind,
        "status": "draft",
        "tags": ["wiki", "ticker", "playbook"],
        "source_refs": refs or [],
        "links": links or [],
    }


def test_plan_preserves_colliding_documents_and_relinks_source_pages():
    pages = [
        _page(
            "source-ticker-alpha",
            "수집 소스 위키: 종목:ALPHA",
            kind="source_digest",
            links=["distill-collision"],
        ),
        _page(
            "source-ticker-beta",
            "수집 소스 위키: 종목:BETA",
            kind="source_digest",
            links=["distill-collision"],
        ),
        _page(
            "distill-collision",
            "수집 소스 위키: 종목:ALPHA · 근거 정리",
            links=["source-ticker-alpha", "source-ticker-beta"],
            refs=["wiki:source-ticker-alpha"],
        ),
        _page(
            "distill-collision",
            "수집 소스 위키: 종목:BETA · 근거 정리",
            links=["source-ticker-beta", "source-ticker-alpha"],
            refs=["wiki:source-ticker-beta"],
        ),
    ]

    result = wiki_record_repair.plan(pages)

    assert result["collision_group_count"] == 1
    assert result["renamed_count"] == 1
    assert len({page["id"] for page in result["upserts"]}) == 3
    alpha = next(page for page in result["upserts"] if page["title"].endswith("ALPHA · 근거 정리"))
    beta = next(page for page in result["upserts"] if page["title"].endswith("BETA · 근거 정리"))
    assert alpha["id"] == "distill-collision"
    assert beta["id"] != "distill-collision"
    assert beta["id"] in next(page for page in result["upserts"] if page["id"] == "source-ticker-beta")["links"]


def test_plan_is_deterministic_and_keeps_exact_duplicates_as_separate_records():
    duplicate = _page(
        "distill-same",
        "같은 제목",
        refs=["wiki:source-ticker-alpha"],
    )
    pages = [duplicate, dict(duplicate)]

    first = wiki_record_repair.plan(pages)
    second = wiki_record_repair.plan(pages)

    assert [page["id"] for page in first["upserts"]] == [page["id"] for page in second["upserts"]]
    assert len({page["id"] for page in first["upserts"]}) == 2
    assert first["renamed_count"] == 1


def test_apply_passes_old_ids_to_atomic_replacement(monkeypatch):
    pages = [
        _page("distill-same", "첫 카드"),
        _page("distill-same", "둘째 카드"),
    ]
    calls = []
    monkeypatch.setattr(
        wiki_record_repair.wiki,
        "batch_replace_pages",
        lambda upserts, *, deletes: calls.append((list(upserts), list(deletes))) or list(upserts),
    )

    result = wiki_record_repair.apply(pages, dry_run=False)

    assert result["saved_count"] == 2
    assert calls[0][1] == ["distill-same"]
    assert len({page["id"] for page in calls[0][0]}) == 2
