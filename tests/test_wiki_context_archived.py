"""Archived pages are history: default LLM context must not present them as knowledge (B-06)."""
import pytest

from agent_console import shared_memory, wiki


@pytest.fixture(autouse=True)
def local_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    wiki.upsert_page({"id": "old", "title": "Rejected guarantee", "body": "ARCHIVED_FALSE_GUARANTEE",
                      "surface": "market", "kind": "concept", "status": "archived"})
    wiki.upsert_page({"id": "live", "title": "Current capacity view", "body": "LIVE_KNOWLEDGE guarantee",
                      "surface": "market", "kind": "concept", "status": "draft"})
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


QUERY = "ARCHIVED_FALSE_GUARANTEE guarantee"


def test_fallback_retrieval_excludes_archived_by_default():
    context = wiki.build_context_section(query=QUERY, surface="market", limit=1)

    assert "ARCHIVED_FALSE_GUARANTEE" not in context
    assert "LIVE_KNOWLEDGE" in context


def test_qmd_retrieval_excludes_archived_by_default(monkeypatch):
    class FakeQmd:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def status():
            return {"installed": True}

        @staticmethod
        def search(query, *, limit=10, surface="all", status="all"):
            return [{"page_id": "old", "score": 0.99}, {"page_id": "live", "score": 0.5}]

    monkeypatch.setattr(wiki, "qmd_search", FakeQmd, raising=False)

    context = wiki.build_context_section(query=QUERY, surface="market", limit=1)

    assert "ARCHIVED_FALSE_GUARANTEE" not in context
    assert "LIVE_KNOWLEDGE" in context


def test_injected_pages_drop_archived_and_empty_result_is_empty_context():
    old, live = wiki.get_page("old"), wiki.get_page("live")

    context = wiki.build_context_section(query=QUERY, surface="market", pages=[old, live])

    assert "ARCHIVED_FALSE_GUARANTEE" not in context
    assert "LIVE_KNOWLEDGE" in context
    assert wiki.build_context_section(query=QUERY, surface="market", pages=[old]) == ""


def test_history_lookup_and_browser_listing_still_include_archived():
    history = wiki.build_context_section(query=QUERY, surface="market", limit=2, include_archived=True)

    assert "ARCHIVED_FALSE_GUARANTEE" in history
    assert "old" in {page["id"] for page in wiki.list_pages(surface="all", status="all", limit=100)}
    assert "old" in {page["id"] for page in wiki.list_pages(query=QUERY, surface="market", limit=5)}


def test_agent_answer_keeps_archived_pages_out_of_review_usage_and_evidence(monkeypatch):
    """agent.answer() 의 위키 검색 결과에 archived 가 섞이면 laya 검토 후보·사용량 추적·
    evidence 슬롯(허용 근거 ID)을 차지한다 — 현재 지식만 들어가야 한다."""
    from agent_console import agent, laya_bridge

    seen = {"review": [], "usage": [], "provided": None, "retrieved": None}

    def review(question, candidates, history=None, limit=4):
        seen["review"] = [page.get("id") for page in candidates]
        return candidates[:limit], {"mode": "baseline"}

    def compose(question, pack, history=None):
        seen["provided"] = agent._provided_context_evidence_ids(pack, pack.get("_wiki_context_pages"))
        return "답변"

    def record(query_id, cited, provided, **kwargs):
        seen["retrieved"] = list(kwargs.get("retrieved_page_ids") or [])
        return {}

    monkeypatch.setattr(laya_bridge, "review_wiki_candidates", review)
    monkeypatch.setattr(agent, "_track_wiki_page_usage", lambda page_id, question: seen["usage"].append(page_id))
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda **kw: [])
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *a, **kw: {})
    monkeypatch.setattr(agent, "_compose_answer", compose)
    monkeypatch.setattr(agent.evidence_usage, "record_answer_use", record)
    pack = {"sources": {"events": [], "source_counts": []}, "portfolio": {"holdings": []},
            "paper": {}, "models": {}, "memory": [], "reports": [], "ml_activity": []}

    result = agent.answer(QUERY, "market", context_pack_data=pack)

    assert result["ok"]
    assert "live" in seen["review"]                          # 현재 지식은 그대로 검색됨
    assert "old" not in seen["review"]
    assert "old" not in seen["usage"]
    assert "old" not in seen["provided"]
    assert "old" not in seen["retrieved"]
