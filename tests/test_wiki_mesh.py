from __future__ import annotations

from dashboard import wiki_mesh


def test_build_wiki_graph_model_links_related_pages():
    pages = [
        {
            "id": "page-a",
            "title": "AI 반도체 점검",
            "summary": "AI CAPEX와 반도체 수요를 점검한다.",
            "tags": ["ai", "semiconductor", "stable"],
            "surface": "portfolio",
            "kind": "note",
            "source_refs": ["news:tsmc-q2", "report:daily-001"],
        },
        {
            "id": "page-b",
            "title": "AI 밸류에이션 리스크",
            "summary": "밸류에이션과 마진 압박을 본다.",
            "tags": ["ai", "risk"],
            "surface": "portfolio",
            "kind": "note",
            "source_refs": ["news:tsmc-q2"],
        },
        {
            "id": "page-c",
            "title": "중동 지정학 체크",
            "summary": "유가와 지정학을 본다.",
            "tags": ["geopolitics", "oil"],
            "surface": "market",
            "kind": "note",
            "source_refs": ["news:oil-spike"],
        },
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="page-a", depth=2, max_nodes=10)

    assert model["selected_id"] == "page-a"
    assert any(node["id"] == "page-a" and node["selected"] for node in model["nodes"])
    assert any(edge.source == "page-a" and edge.target == "page-b" for edge in model["edges"])
    assert model["positions"]["page-a"]


def test_build_wiki_graph_model_respects_max_nodes():
    pages = [
        {"id": "page-a", "title": "A", "summary": "A", "tags": ["alpha"], "source_refs": ["shared"]},
        {"id": "page-b", "title": "B", "summary": "B", "tags": ["alpha"], "source_refs": ["shared"]},
        {"id": "page-c", "title": "C", "summary": "C", "tags": ["beta"], "source_refs": ["other"]},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="page-a", depth=4, max_nodes=2)

    assert len(model["nodes"]) == 2
    assert {node["id"] for node in model["nodes"]} <= {"page-a", "page-b"}


def test_extract_selected_page_id_reads_plotly_customdata():
    event = {
        "selection": {
            "points": [
                {"customdata": ["page-b"]},
            ]
        }
    }

    assert wiki_mesh._extract_selected_page_id(event) == "page-b"