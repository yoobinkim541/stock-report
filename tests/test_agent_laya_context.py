import pytest


@pytest.mark.parametrize("fail", [False, True])
def test_answer_reviews_bounded_wiki_context_and_survives_bridge_failure(monkeypatch, fail):
    from agent_console import agent, laya_bridge

    pages = [{"id": f"p{i}", "title": f"page {i}", "body": "source-backed context"} for i in range(8)]
    history = [{"role": "user", "content": "previous question"}]
    seen = {}

    def search(**kwargs):
        seen["search"] = kwargs
        return pages

    def review(question, candidates, history=None, limit=4):
        seen["review"] = (question, candidates, history, limit)
        if fail:
            raise RuntimeError("secret worker path")
        return [pages[7], *pages[:3]], {"mode": "shadow", "reason": "profile_unvalidated"}

    def compose(question, pack, history=None):
        seen["pack"] = pack
        return "answer"

    monkeypatch.setattr(agent.wiki, "list_pages", search)
    monkeypatch.setattr(laya_bridge, "review_wiki_candidates", review)
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda **kw: history)
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_track_wiki_page_usage", lambda *a: None)
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *a, **kw: {})
    monkeypatch.setattr(agent, "_compose_answer", compose)
    monkeypatch.setattr(agent.evidence_usage, "record_answer_use", lambda *a, **kw: {})
    pack = {"sources": {"events": [], "source_counts": []}, "portfolio": {"holdings": []},
            "paper": {}, "models": {}, "memory": [], "reports": [], "ml_activity": []}
    result = agent.answer("wiki:p7 explain", "market", context_pack_data=pack)
    assert result["ok"]
    assert seen["search"]["limit"] == 8
    assert seen["review"] == ("wiki:p7 explain", pages, history, 4)
    assert len(seen["pack"]["_wiki_context_pages"]) == 4
    assert seen["pack"]["_wiki_context_pages"][0]["id"] == ("p0" if fail else "p7")
    assert result["context"]["laya_review"]["reason"] == ("bridge_failure" if fail else "profile_unvalidated")
    assert "secret" not in str(result["context"]["laya_review"])


def test_explicit_wiki_id_is_resolved_even_if_not_in_lexical_results(monkeypatch):
    from agent_console import agent, laya_bridge

    wanted = {"id": "old-page", "title": "Archived market reasoning", "status": "draft"}
    lexical = [{"id": f"p{i}"} for i in range(8)]
    seen = {}

    def review(question, candidates, **kw):
        seen["candidates"] = candidates
        return candidates[:4], {"reason": "exact_citation"}

    monkeypatch.setattr(agent.wiki, "list_pages", lambda **kw: lexical)
    monkeypatch.setattr(agent.wiki, "get_page", lambda page_id: wanted if page_id == "old-page" else None)
    monkeypatch.setattr(laya_bridge, "review_wiki_candidates", review)
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda **kw: [])
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_track_wiki_page_usage", lambda *a: None)
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *a, **kw: {})
    monkeypatch.setattr(agent, "_compose_answer", lambda *a, **kw: "answer")
    monkeypatch.setattr(agent.evidence_usage, "record_answer_use", lambda *a, **kw: {})
    pack = {"sources": {"events": []}, "portfolio": {"holdings": []}, "paper": {}, "models": {},
            "memory": [], "reports": [], "ml_activity": []}
    agent.answer("[[old-page]] explain", context_pack_data=pack)
    assert seen["candidates"][0] is wanted
    assert len(seen["candidates"]) == 8
