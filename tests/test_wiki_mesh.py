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


def test_build_wiki_graph_model_groups_nodes_by_surface():
    pages = [
        {"id": "m1", "title": "시장 1", "summary": "시장", "surface": "market", "kind": "note", "tags": ["market"], "source_refs": []},
        {"id": "m2", "title": "시장 2", "summary": "시장", "surface": "market", "kind": "note", "tags": ["market"], "source_refs": []},
        {"id": "p1", "title": "포트폴리오 1", "summary": "포트폴리오", "surface": "portfolio", "kind": "note", "tags": ["portfolio"], "source_refs": []},
        {"id": "t1", "title": "티커 1", "summary": "티커", "surface": "ticker", "kind": "note", "tags": ["ticker"], "source_refs": []},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="", depth=1, max_nodes=10)
    by_id = {node["id"]: node for node in model["nodes"]}
    groups = {group["surface"]: group for group in model["groups"]}

    assert [group["surface"] for group in model["groups"]] == ["market", "portfolio", "ticker"]
    assert groups["market"]["count"] == 2
    assert groups["portfolio"]["count"] == 1
    assert by_id["m1"]["surface_color"] == wiki_mesh.SURFACE_COLORS["market"]
    assert by_id["p1"]["group"] == "portfolio"
    assert model["positions"]["m1"] != model["positions"]["p1"]


def test_build_wiki_graph_model_respects_max_nodes():
    pages = [
        {"id": "page-a", "title": "A", "summary": "A", "tags": ["alpha"], "source_refs": ["shared"]},
        {"id": "page-b", "title": "B", "summary": "B", "tags": ["alpha"], "source_refs": ["shared"]},
        {"id": "page-c", "title": "C", "summary": "C", "tags": ["beta"], "source_refs": ["other"]},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="page-a", depth=4, max_nodes=2)

    assert len(model["nodes"]) == 2
    assert {node["id"] for node in model["nodes"]} <= {"page-a", "page-b"}


def test_build_wiki_graph_model_includes_all_pages_when_no_limit_is_requested():
    pages = [
        {"id": f"page-{idx}", "title": f"문서 {idx}", "summary": "지식", "surface": "wiki", "kind": "note"}
        for idx in range(120)
    ]

    model = wiki_mesh.build_wiki_graph_model(pages)

    assert len(model["nodes"]) == len(pages)


def test_build_wiki_graph_model_uses_explicit_links_and_keeps_edges_visible():
    pages = [
        {"id": "page-a", "title": "A", "summary": "A", "surface": "wiki", "kind": "note", "links": ["page-b"]},
        {"id": "page-b", "title": "B", "summary": "B", "surface": "wiki", "kind": "note"},
        {"id": "page-c", "title": "C", "summary": "C", "surface": "wiki", "kind": "note"},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, max_nodes=2)

    node_ids = {node["id"] for node in model["nodes"]}
    direct_edge = next(edge for edge in model["edges"] if {edge.source, edge.target} == {"page-a", "page-b"})
    assert direct_edge.explicit is True
    assert all(edge.source in node_ids and edge.target in node_ids for edge in model["edges"])

    figure = wiki_mesh._build_figure(model)
    assert any(trace.line.color == "rgba(34,211,238,0.48)" for trace in figure.data if trace.mode == "lines")


def test_build_wiki_graph_model_shows_parent_child_hierarchy():
    pages = [
        {"id": "parent", "title": "부모 문서", "summary": "개요", "surface": "wiki", "kind": "playbook"},
        {"id": "child", "title": "세부 문서", "summary": "세부", "surface": "wiki", "kind": "playbook", "parent_page_id": "parent"},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="parent", depth=1, max_nodes=10)

    assert any({edge.source, edge.target} == {"parent", "child"} and edge.explicit for edge in model["edges"])


def test_build_figure_allows_drag_pan_and_zoom():
    """dragmode="pan" 은 이미 설정돼 있었는데 xaxis/yaxis 의 fixedrange=True 가 팬·줌을
    전부 무력화하고 있었다(감사 2026-09-03, 유빈님 리포트: "옵시디언처럼 드래그로 옮기고
    싶은데 안 됨"). 캔버스를 드래그해 옮기려면(Obsidian 식) 두 축 다 fixedrange 가
    아니어야 한다."""
    pages = [
        {"id": "page-a", "title": "A", "summary": "A", "surface": "wiki", "kind": "note"},
        {"id": "page-b", "title": "B", "summary": "B", "surface": "wiki", "kind": "note"},
    ]

    figure = wiki_mesh._build_figure(wiki_mesh.build_wiki_graph_model(pages))

    assert figure.layout.dragmode == "pan"
    assert figure.layout.xaxis.fixedrange is not True
    assert figure.layout.yaxis.fixedrange is not True


def test_build_figure_uses_webgl_for_large_graphs():
    pages = [
        {"id": f"page-{idx}", "title": f"문서 {idx}", "summary": "지식", "surface": "wiki", "kind": "note"}
        for idx in range(500)
    ]

    figure = wiki_mesh._build_figure(wiki_mesh.build_wiki_graph_model(pages))

    assert figure.data
    assert all(trace.type == "scattergl" for trace in figure.data)


def test_extract_selected_page_id_reads_plotly_customdata():
    event = {
        "selection": {
            "points": [
                {"customdata": ["page-b"]},
            ]
        }
    }

    assert wiki_mesh._extract_selected_page_id(event) == "page-b"

def test_wiki_graph_nodes_carry_trust_metadata_and_color():
    pages = [
        {"id": "good", "title": "Good", "summary": "S", "verification_status": "source-backed", "source_refs": ["source:a"]},
        {"id": "warn", "title": "Warn", "summary": "S", "verification_status": "unverified", "source_refs": ["conversation:1"]},
        {"id": "bad", "title": "Bad", "summary": "S", "verification_status": "source-backed", "source_refs": ["source:b"], "lint_issue_count": 1},
    ]

    model = wiki_mesh.build_wiki_graph_model(pages, max_nodes=10)
    by_id = {node["id"]: node for node in model["nodes"]}

    assert by_id["good"]["verification_status"] == "source-backed"
    assert by_id["good"]["color"] == wiki_mesh.TRUST_COLORS["source-backed"]
    assert by_id["warn"]["color"] == wiki_mesh.TRUST_COLORS["unverified"]
    assert by_id["bad"]["color"] == wiki_mesh.TRUST_COLORS["lint"]


def test_build_figure_caps_text_labels_on_dense_large_graphs():
    """실측(2026-09-05): 위키 페이지 1740개·링크 4947개짜리 실제 그래프를 열면 라벨이
    전부 겹쳐서 화면이 텍스트 뭉텅이로 보였다 — degree>=3 인 노드는 전부 상시 라벨을
    붙이는데, 노드가 조밀하게 연결된 큰 그래프에선 대부분이 그 기준을 넘는다. 라벨은
    허브(연결이 가장 많은) 노드 상위 일부에만 붙여야 큰 그래프에서도 읽힌다."""
    pages = []
    for idx in range(200):
        links = [f"page-{(idx + offset) % 200}" for offset in (1, 2, 3, 4)]
        pages.append({
            "id": f"page-{idx}", "title": f"문서 {idx}", "summary": "지식",
            "surface": "wiki", "kind": "note", "links": links,
        })

    model = wiki_mesh.build_wiki_graph_model(pages, max_nodes=300)
    figure = wiki_mesh._build_figure(model)

    labeled_trace = next(trace for trace in figure.data if trace.mode == "markers+text")
    assert len(labeled_trace.text) <= 16


def test_build_figure_always_labels_the_selected_node_even_in_dense_graphs():
    pages = []
    for idx in range(200):
        links = [f"page-{(idx + offset) % 200}" for offset in (1, 2, 3, 4)]
        pages.append({
            "id": f"page-{idx}", "title": f"문서 {idx}", "summary": "지식",
            "surface": "wiki", "kind": "note", "links": links,
        })

    model = wiki_mesh.build_wiki_graph_model(pages, selected_page_id="page-100", max_nodes=300)
    figure = wiki_mesh._build_figure(model)

    labeled_trace = next(trace for trace in figure.data if trace.mode == "markers+text")
    assert "문서 100" in labeled_trace.text


def test_trust_color_for_node_prioritizes_lint_then_verification():
    assert wiki_mesh.trust_color_for_node({"lint_issue_count": 1, "verification_status": "source-backed"}) == wiki_mesh.TRUST_COLORS["lint"]
    assert wiki_mesh.trust_color_for_node({"verification_status": "source-backed"}) == wiki_mesh.TRUST_COLORS["source-backed"]
    assert wiki_mesh.trust_color_for_node({"verification_status": "unverified"}) == wiki_mesh.TRUST_COLORS["unverified"]


def test_corpus_fingerprint_changes_only_when_count_or_latest_update_changes():
    pages_a = [
        {"id": "p1", "updated_at": "2026-09-01T00:00:00+00:00"},
        {"id": "p2", "updated_at": "2026-09-02T00:00:00+00:00"},
    ]
    pages_same_data = [
        {"id": "p1", "updated_at": "2026-09-01T00:00:00+00:00"},
        {"id": "p2", "updated_at": "2026-09-02T00:00:00+00:00"},
    ]
    pages_edited = [
        {"id": "p1", "updated_at": "2026-09-01T00:00:00+00:00"},
        {"id": "p2", "updated_at": "2026-09-05T00:00:00+00:00"},
    ]
    pages_more = [*pages_a, {"id": "p3", "updated_at": "2026-09-03T00:00:00+00:00"}]

    fp_a = wiki_mesh._corpus_fingerprint(pages_a)
    assert fp_a == wiki_mesh._corpus_fingerprint(pages_same_data)
    assert fp_a != wiki_mesh._corpus_fingerprint(pages_edited)
    assert fp_a != wiki_mesh._corpus_fingerprint(pages_more)


def test_cached_graph_model_skips_recompute_for_unrelated_interactions(monkeypatch):
    """실측(2026-09-06): 1810노드 그래프가 편집 토글처럼 그래프와 무관한
    상호작용(rerun)마다 매번 처음부터 다시 계산돼(force-directed 레이아웃 등)
    상호작용할 때마다 몇 초씩 걸렸다. 같은 데이터·같은 필터로 다시 부르면
    build_wiki_graph_model 을 다시 돌리지 않고 캐시를 재사용해야 한다."""
    wiki_mesh._cached_graph_model.clear()
    calls = {"n": 0}
    real_build = wiki_mesh.build_wiki_graph_model

    def spy_build(*args, **kwargs):
        calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(wiki_mesh, "build_wiki_graph_model", spy_build)

    pages = [
        {"id": "p1", "title": "A", "summary": "A", "surface": "wiki", "kind": "note", "updated_at": "2026-09-01T00:00:00+00:00"},
        {"id": "p2", "title": "B", "summary": "B", "surface": "wiki", "kind": "note", "updated_at": "2026-09-02T00:00:00+00:00"},
    ]

    def get_model():
        fingerprint = wiki_mesh._corpus_fingerprint(pages)
        return wiki_mesh._cached_graph_model(
            pages, fingerprint, selected_page_id="", query="", surface="all", status="all", depth=2, max_nodes=None,
        )

    get_model()
    get_model()  # 편집 토글처럼 무관한 rerun — 데이터·필터 그대로
    assert calls["n"] == 1

    pages[1]["updated_at"] = "2026-09-05T00:00:00+00:00"  # 실제 변경(병합 등)
    get_model()
    assert calls["n"] == 2


def test_rest_node_opacity_grows_with_degree_but_stays_bounded():
    """유빈님 요청(2026-09-06): 위키 그래프를 더 예쁘게 — 라벨 없는(리프) 노드가
    전부 고정 불투명도라 대량 노드 덩어리가 평평한 블록으로 보였다. 연결 수가
    많을수록 점점 진해지되 범위 안에 머물러야 한다."""
    assert wiki_mesh._rest_node_opacity(0) < wiki_mesh._rest_node_opacity(2) < wiki_mesh._rest_node_opacity(10)
    assert 0.2 <= wiki_mesh._rest_node_opacity(0) <= 0.3
    assert wiki_mesh._rest_node_opacity(100) <= 0.8


def test_build_figure_adds_soft_halo_layer_behind_labeled_hub_nodes():
    """허브 노드 뒤에 크고 옅은 후광 레이어를 깔아 은은하게 빛나는 느낌을 준다 —
    실제 노드(선명한 markers+text)보다 먼저 그려지는 별도 marker-only 트레이스."""
    pages = [
        {"id": "page-a", "title": "A", "summary": "A", "surface": "wiki", "kind": "note", "links": ["page-b", "page-c", "page-d"]},
        {"id": "page-b", "title": "B", "summary": "B", "surface": "wiki", "kind": "note"},
        {"id": "page-c", "title": "C", "summary": "C", "surface": "wiki", "kind": "note"},
        {"id": "page-d", "title": "D", "summary": "D", "surface": "wiki", "kind": "note"},
    ]
    figure = wiki_mesh._build_figure(wiki_mesh.build_wiki_graph_model(pages, selected_page_id="page-a", max_nodes=10))

    labeled_idx = next(i for i, trace in enumerate(figure.data) if trace.mode == "markers+text")
    halo = figure.data[labeled_idx - 1]
    assert halo.mode == "markers"
    assert halo.marker.opacity < 0.3
    labeled = figure.data[labeled_idx]
    assert all(h > s for h, s in zip(halo.marker.size, labeled.marker.size))
