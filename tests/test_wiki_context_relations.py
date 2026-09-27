from __future__ import annotations


def test_archived_pages_do_not_expand_active_backlinks_or_context(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "현재 판단",
        "summary": "현재 판단 요약",
        "body": "현재 판단 본문",
        "surface": "market",
        "kind": "risk",
        "status": "draft",
    })
    active = wiki.upsert_page({
        "title": "활성 근거",
        "summary": "활성 근거 요약",
        "body": "활성 근거 본문",
        "surface": "market",
        "kind": "source_digest",
        "status": "draft",
        "links": [target["id"]],
    })
    archived = wiki.upsert_page({
        "title": "오래된 근거",
        "summary": "오래된 근거 요약",
        "body": "오래된 근거 본문",
        "surface": "market",
        "kind": "source_digest",
        "status": "archived",
        "links": [target["id"]],
    })

    fetched = wiki.get_page(target["id"])
    assert fetched["backlinks"] == [active["id"]]

    section = wiki.build_context_section(query="현재 판단", surface="market", pages=[fetched])
    assert "활성 근거" in section
    assert "오래된 근거" not in section
    assert archived["id"] not in fetched["backlinks"]
