from __future__ import annotations

from dashboard import wiki_mesh


def _page(page_id: str, *, links: list[str] | None = None) -> dict:
    return {
        "id": page_id,
        "title": page_id,
        "summary": "shared investment evidence",
        "surface": "wiki",
        "kind": "concept",
        "tags": ["shared-investment-theme"],
        "source_refs": ["https://example.test/research/shared"],
        "links": links or [],
    }


def _inferred_neighbors(adjacency: dict, page_id: str) -> set[str]:
    return {
        target
        for target, edge in adjacency.get(page_id, {}).items()
        if not edge.explicit
    }


def test_inferred_only_degree_is_bounded_and_order_independent():
    pages = [_page(f"node-{idx:02}") for idx in range(20)]

    first, _ = wiki_mesh._build_adjacency(pages)
    reversed_adjacency, _ = wiki_mesh._build_adjacency(list(reversed(pages)))

    for page in pages:
        page_id = page["id"]
        assert len(_inferred_neighbors(first, page_id)) <= wiki_mesh.MAX_INFERRED_NEIGHBORS
    first_pairs = {
        tuple(sorted((source, target)))
        for source, targets in first.items()
        for target, edge in targets.items()
        if source < target and not edge.explicit
    }
    reversed_pairs = {
        tuple(sorted((source, target)))
        for source, targets in reversed_adjacency.items()
        for target, edge in targets.items()
        if source < target and not edge.explicit
    }
    assert first_pairs == reversed_pairs


def test_explicit_hub_edges_are_never_trimmed_by_inferred_degree_cap():
    explicit_targets = [f"node-{idx:02}" for idx in range(1, 13)]
    pages = [_page("node-00", links=explicit_targets)]
    pages.extend(_page(page_id) for page_id in [*explicit_targets, "node-13", "node-14"])

    adjacency, _ = wiki_mesh._build_adjacency(pages)

    for target in explicit_targets:
        assert adjacency["node-00"][target].explicit
        assert adjacency[target]["node-00"].explicit
    for page in pages:
        assert len(_inferred_neighbors(adjacency, page["id"])) <= wiki_mesh.MAX_INFERRED_NEIGHBORS
