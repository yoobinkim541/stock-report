"""Unit tests: thematic taxonomy, graph HTML (drag & drop), status filter, relay URL."""
import json

from agent_console import wiki
from dashboard import wiki_browser, wiki_mesh
from lib import llm_direct


def _page(pid, title, summary="", kind="note", tags=()):
    return {"id": pid, "title": title, "summary": summary, "body": summary, "kind": kind,
            "surface": "market", "tags": list(tags), "source_refs": ["https://example.com/" + pid]}


def test_taxonomy_routes_pages_to_thematic_categories():
    assert wiki._index_category(_page("a", "FOMC 금리 인하 전망")).startswith("금리·통화정책")
    assert wiki._index_category(_page("b", "우크라이나 휴전 협상")).startswith("정치·지정학")
    assert wiki._index_category(_page("c", "HBM 반도체 소재")).startswith("기술·반도체·AI")
    assert wiki._index_category(_page("d", "아무 관련 없는 문서")).startswith("종목전략")
    assert wiki._index_category(_page("overview-x", "포털", kind="overview")).startswith("종합 위키 포털")
    assert wiki._index_category(_page("e", "요약", tags=["compiled_source"])).startswith("원문 지식 요약")


def test_index_headings_are_thematic_not_surface_names():
    index = wiki._render_index_md([_page("a", "FOMC 금리 전망"), _page("b", "우크라이나 휴전")])
    assert "## 금리·통화정책" in index and "## 정치·지정학" in index
    assert "## market" not in index and "## ticker" not in index


def test_obsidian_html_enables_node_dragging_with_all_nodes_and_edges():
    pages = [_page("a", "금리 인하", "연준 금리"), _page("b", "국채 금리", "채권 금리")]
    model = wiki_mesh.build_wiki_graph_model(pages, depth=1, max_nodes=10)
    html = wiki_mesh.build_obsidian_network_html(model)
    assert "dragNodes: true" in html and "forceAtlas2Based" in html
    assert html.count('"id": "a"') == 1 and html.count('"id": "b"') == 1
    nodes = json.loads(html.split("new vis.DataSet(", 1)[1].split(");", 1)[0])
    assert {n["id"] for n in nodes} == {"a", "b"}


def test_active_status_filter_hides_only_archived_pages():
    for matcher in (wiki_browser._matches_status, wiki_mesh._matches_status):
        assert matcher({"status": "draft"}, "active")
        assert matcher({"status": "reviewed"}, "active")
        assert not matcher({"status": "archived"}, "active")
        assert matcher({"status": "archived"}, "archived")
        assert not matcher({"status": "draft"}, "archived")


def test_openrouter_url_is_resolved_at_call_time(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    def post(url, **kwargs):
        seen["url"] = url
        return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_URL", "http://127.0.0.1:8999/v1/chat/completions")
    llm_direct.direct_chat("p", "m", post=post)
    assert seen["url"] == "http://127.0.0.1:8999/v1/chat/completions"
    monkeypatch.delenv("OPENROUTER_URL")
    llm_direct.direct_chat("p", "m", post=post)
    assert seen["url"].startswith("https://openrouter.ai/")
