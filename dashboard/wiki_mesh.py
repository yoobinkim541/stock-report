from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import random
from typing import Any

import numpy as np
import plotly.graph_objects as go

from dashboard import wiki_browser


STATUS_COLORS = {
    "draft": "#60a5fa",
    "reviewed": "#f59e0b",
    "stable": "#22c55e",
    "archived": "#94a3b8",
}

SURFACE_COLORS = {
    "market": "#22d3ee",
    "portfolio": "#a78bfa",
    "ticker": "#f472b6",
    "paper": "#34d399",
    "lab": "#f59e0b",
    "wiki": "#67e8f9",
}


@dataclass(frozen=True)
class WikiGraphNode:
    id: str
    title: str
    surface: str
    kind: str
    status: str
    summary: str
    tags: tuple[str, ...]
    source_refs: tuple[str, ...]
    degree: int = 0
    level: int = 0
    selected: bool = False


@dataclass(frozen=True)
class WikiGraphEdge:
    source: str
    target: str
    weight: int
    tags: int = 0
    refs: int = 0


def _clean(value: object, limit: int = 2000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _surface_color(surface: str) -> str:
    return SURFACE_COLORS.get(str(surface or "wiki").lower(), "#67e8f9")


def _status_color(status: str) -> str:
    return STATUS_COLORS.get(str(status or "draft").lower(), "#60a5fa")


def _stable_seed(text: str) -> int:
    value = 0
    for idx, ch in enumerate(str(text or "")[:64]):
        value = (value * 131 + ord(ch) + idx) % (2**32)
    return value


def _page_tags(page: dict[str, Any]) -> set[str]:
    return {str(tag).strip().lower() for tag in (page.get("tags") or []) if str(tag).strip()}


def _page_refs(page: dict[str, Any]) -> set[str]:
    return {str(ref).strip().lower() for ref in (page.get("source_refs") or []) if str(ref).strip()}


def _edge_similarity(left: dict[str, Any], right: dict[str, Any]) -> tuple[int, int, int]:
    left_tags = _page_tags(left)
    right_tags = _page_tags(right)
    left_refs = _page_refs(left)
    right_refs = _page_refs(right)
    tag_hits = len(left_tags & right_tags)
    ref_hits = len(left_refs & right_refs)
    same_surface = int((left.get("surface") or "").lower() == (right.get("surface") or "").lower())
    same_kind = int((left.get("kind") or "").lower() == (right.get("kind") or "").lower())
    score = tag_hits * 4 + ref_hits * 6 + same_surface + same_kind
    return score, tag_hits, ref_hits


def _build_adjacency(pages: list[dict[str, Any]]) -> tuple[dict[str, dict[str, WikiGraphEdge]], Counter[str]]:
    adjacency: dict[str, dict[str, WikiGraphEdge]] = defaultdict(dict)
    ref_counter: Counter[str] = Counter()
    for page in pages:
        for ref in page.get("source_refs") or []:
            ref_counter[str(ref)] += 1
    for idx, left in enumerate(pages):
        left_id = str(left.get("id") or "")
        if not left_id:
            continue
        for right in pages[idx + 1 :]:
            right_id = str(right.get("id") or "")
            if not right_id or right_id == left_id:
                continue
            score, tag_hits, ref_hits = _edge_similarity(left, right)
            if score <= 0:
                continue
            edge = WikiGraphEdge(source=left_id, target=right_id, weight=score, tags=tag_hits, refs=ref_hits)
            adjacency[left_id][right_id] = edge
            adjacency[right_id][left_id] = WikiGraphEdge(source=right_id, target=left_id, weight=score, tags=tag_hits, refs=ref_hits)
    return adjacency, ref_counter


def _rank_seed_pages(pages: list[dict[str, Any]], adjacency: dict[str, dict[str, WikiGraphEdge]], *, selected_id: str = "") -> list[str]:
    if selected_id and any(page.get("id") == selected_id for page in pages):
        return [selected_id]
    ranked = []
    for page in pages:
        pid = str(page.get("id") or "")
        if not pid:
            continue
        degree = len(adjacency.get(pid) or {})
        recency = str(page.get("updated_at") or page.get("created_at") or "")
        ranked.append((degree, recency, pid))
    ranked.sort(reverse=True)
    return [pid for _degree, _recency, pid in ranked[:1]]


def _expand_nodes(seed_ids: list[str], pages_by_id: dict[str, dict[str, Any]], adjacency: dict[str, dict[str, WikiGraphEdge]], depth: int, *, max_nodes: int) -> tuple[list[str], dict[str, int]]:
    depth = max(1, min(int(depth or 2), 4))
    visited: set[str] = set(seed_ids)
    level_map: dict[str, int] = {pid: 0 for pid in seed_ids}
    frontier = list(seed_ids)
    for level in range(1, depth + 1):
        next_frontier: list[str] = []
        for pid in frontier:
            neighbors = sorted((adjacency.get(pid) or {}).values(), key=lambda edge: edge.weight, reverse=True)
            for edge in neighbors[:6]:
                other = edge.target
                if other in visited or other not in pages_by_id:
                    continue
                visited.add(other)
                level_map[other] = level
                next_frontier.append(other)
                if len(visited) >= max_nodes:
                    break
            if len(visited) >= max_nodes:
                break
        frontier = next_frontier
        if len(visited) >= max_nodes or not frontier:
            break
    return list(visited), level_map


def _fill_by_degree(pages: list[dict[str, Any]], adjacency: dict[str, dict[str, WikiGraphEdge]], visited: set[str], *, max_nodes: int) -> list[str]:
    if len(visited) >= max_nodes:
        return list(visited)
    ranked = []
    for page in pages:
        pid = str(page.get("id") or "")
        if not pid or pid in visited:
            continue
        degree = len(adjacency.get(pid) or {})
        recency = str(page.get("updated_at") or page.get("created_at") or "")
        ranked.append((degree, recency, pid))
    ranked.sort(reverse=True)
    for _degree, _recency, pid in ranked:
        visited.add(pid)
        if len(visited) >= max_nodes:
            break
    return list(visited)


def _cluster_centers(surfaces: list[str], focus_surface: str) -> dict[str, np.ndarray]:
    uniq = [surface for surface in surfaces if surface]
    if not uniq:
        return {"wiki": np.array([0.0, 0.0], dtype=float)}
    uniq = list(dict.fromkeys(uniq))
    if focus_surface in uniq:
        uniq = [focus_surface, *[surface for surface in uniq if surface != focus_surface]]
    if len(uniq) == 1:
        return {uniq[0]: np.array([0.0, 0.0], dtype=float)}
    centers: dict[str, np.ndarray] = {uniq[0]: np.array([0.0, 0.0], dtype=float)}
    ring = max(1.8, 2.4 + 0.12 * len(uniq))
    for idx, surface in enumerate(uniq[1:], start=1):
        angle = (2.0 * math.pi * idx) / max(2, len(uniq) - 1)
        centers[surface] = np.array([math.cos(angle) * ring, math.sin(angle) * ring], dtype=float)
    return centers


def _layout_nodes(nodes: list[dict[str, Any]], edges: list[WikiGraphEdge], *, focus_surface: str, selected_id: str) -> dict[str, tuple[float, float]]:
    if not nodes:
        return {}
    surfaces = [str(node.get("surface") or "wiki").lower() for node in nodes]
    centers = _cluster_centers(surfaces, focus_surface)
    seed = _stable_seed(focus_surface + "|" + selected_id + "|" + str(len(nodes)))
    rng = np.random.default_rng(seed)
    positions = np.zeros((len(nodes), 2), dtype=float)
    node_index = {str(node["id"]): idx for idx, node in enumerate(nodes)}
    for idx, node in enumerate(nodes):
        surface = str(node.get("surface") or "wiki").lower()
        center = centers.get(surface, np.array([0.0, 0.0], dtype=float))
        positions[idx] = center + rng.normal(scale=0.45, size=2)
        if str(node.get("id") or "") == selected_id:
            positions[idx] = np.array([0.0, 0.0], dtype=float)
    edge_pairs: list[tuple[int, int, int]] = []
    for edge in edges:
        i = node_index.get(edge.source)
        j = node_index.get(edge.target)
        if i is None or j is None:
            continue
        edge_pairs.append((i, j, max(1, int(edge.weight))))
    if not edge_pairs:
        return {str(node["id"]): (float(positions[idx, 0]), float(positions[idx, 1])) for idx, node in enumerate(nodes)}
    k = math.sqrt(max(1.0, 12.0 / max(1, len(nodes))))
    for _ in range(70):
        disp = np.zeros_like(positions)
        delta = positions[:, None, :] - positions[None, :, :]
        dist = np.linalg.norm(delta, axis=2) + 1e-6
        rep = (k * k) / (dist * dist)
        np.fill_diagonal(rep, 0.0)
        disp += np.sum((delta / dist[:, :, None]) * rep[:, :, None], axis=1)

        for i, j, weight in edge_pairs:
            diff = positions[i] - positions[j]
            d = float(np.linalg.norm(diff) + 1e-6)
            force = (d * d / k) * (1.0 / (1.0 + weight * 0.18))
            step = diff / d * force
            disp[i] -= step
            disp[j] += step

        for idx, node in enumerate(nodes):
            surface = str(node.get("surface") or "wiki").lower()
            center = centers.get(surface, np.array([0.0, 0.0], dtype=float))
            disp[idx] += (center - positions[idx]) * 0.02
            if str(node.get("id") or "") == selected_id:
                disp[idx] += -positions[idx] * 0.08

        positions += np.clip(disp, -1.4, 1.4) * 0.03

    max_abs = float(np.max(np.abs(positions))) or 1.0
    positions = positions / max(1.2, max_abs)
    return {str(node["id"]): (float(positions[idx, 0]), float(positions[idx, 1])) for idx, node in enumerate(nodes)}


def build_wiki_graph_model(
    pages: Iterable[dict[str, Any]],
    *,
    selected_page_id: str = "",
    query: str = "",
    surface: str = "all",
    status: str = "all",
    depth: int = 2,
    max_nodes: int = 96,
) -> dict[str, Any]:
    normalized = [wiki_browser._normalize_page(page) for page in pages or []]
    if not normalized:
        return {
            "nodes": [],
            "edges": [],
            "selected": None,
            "selected_id": "",
            "visible": [],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    visible = wiki_browser._visible_pages(normalized, query=query, surface=surface, status=status)
    corpus = visible or normalized
    adjacency, ref_counter = _build_adjacency(corpus)
    pages_by_id = {str(page.get("id") or ""): page for page in corpus if str(page.get("id") or "")}
    selected_id = str(selected_page_id or "").strip()
    if selected_id and selected_id not in pages_by_id:
        selected_id = ""
    if not selected_id:
        seed_ids = _rank_seed_pages(corpus, adjacency, selected_id=selected_id)
    else:
        seed_ids = [selected_id]
    if not seed_ids and corpus:
        seed_ids = [str(corpus[0].get("id") or "")]
    node_ids, level_map = _expand_nodes(seed_ids, pages_by_id, adjacency, depth, max_nodes=max_nodes)
    visited = set(node_ids)
    node_ids = _fill_by_degree(corpus, adjacency, visited, max_nodes=max_nodes)
    nodes: list[dict[str, Any]] = []
    edges: dict[tuple[str, str], WikiGraphEdge] = {}
    for pid in node_ids:
        page = pages_by_id.get(pid)
        if not page:
            continue
        node_edges = adjacency.get(pid) or {}
        degree = len(node_edges)
        if pid in seed_ids:
            level = 0
        else:
            level = level_map.get(pid, min(4, 1 + degree // 4))
        nodes.append(
            {
                "id": pid,
                "title": page.get("title") or pid,
                "surface": page.get("surface") or "wiki",
                "kind": page.get("kind") or "note",
                "status": page.get("status") or "draft",
                "summary": page.get("summary") or "",
                "tags": tuple(page.get("tags") or ()),
                "source_refs": tuple(page.get("source_refs") or ()),
                "degree": degree,
                "level": level,
                "selected": pid == selected_id,
            }
        )
    for node in nodes:
        for edge in adjacency.get(node["id"], {}).values():
            a, b = sorted((edge.source, edge.target))
            if a == b:
                continue
            edges[(a, b)] = WikiGraphEdge(source=a, target=b, weight=edge.weight, tags=edge.tags, refs=edge.refs)
    ordered_edges = list(edges.values())
    positions = _layout_nodes(nodes, ordered_edges, focus_surface=(surface if surface != "all" else (nodes[0]["surface"] if nodes else "wiki")), selected_id=selected_id)
    return {
        "nodes": nodes,
        "edges": ordered_edges,
        "positions": positions,
        "selected": next((node for node in nodes if node["id"] == selected_id), None),
        "selected_id": selected_id,
        "visible": visible,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ref_counter": ref_counter,
    }


def _node_hover(node: dict[str, Any]) -> str:
    tags = " · ".join(list(node.get("tags") or [])[:5]) or "-"
    refs = len(node.get("source_refs") or [])
    summary = _clean(node.get("summary") or "", 180)
    return (
        f"<b>{_clean(node.get('title') or '', 80)}</b><br>"
        f"{node.get('surface', 'wiki')} · {node.get('kind', 'note')} · {node.get('status', 'draft')}<br>"
        f"degree {node.get('degree', 0)} · refs {refs}<br>"
        f"{summary}<br>"
        f"tags: {tags}"
    )


def _extract_selected_page_id(event: Any) -> str:
    if not event:
        return ""
    selection = None
    if isinstance(event, dict):
        selection = event.get("selection") or event.get("points") or event
    else:
        selection = getattr(event, "selection", None) or getattr(event, "points", None) or event
    points = []
    if isinstance(selection, dict):
        points = selection.get("points") or []
    elif isinstance(selection, Iterable):
        points = list(selection)
    for point in points:
        try:
            customdata = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
            if customdata:
                return str(customdata[0] if isinstance(customdata, (list, tuple)) else customdata)
        except Exception:
            continue
    return ""


def _build_figure(model: dict[str, Any]) -> go.Figure:
    nodes = model.get("nodes") or []
    positions = model.get("positions") or {}
    edges = model.get("edges") or []
    fig = go.Figure()
    if edges:
        xs: list[float | None] = []
        ys: list[float | None] = []
        for edge in edges:
            left = positions.get(edge.source)
            right = positions.get(edge.target)
            if not left or not right:
                continue
            xs.extend([left[0], right[0], None])
            ys.extend([left[1], right[1], None])
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line={"color": "rgba(148,163,184,0.22)", "width": 1},
                hoverinfo="skip",
                showlegend=False,
            )
        )

    x_sel: list[float] = []
    y_sel: list[float] = []
    text_sel: list[str] = []
    custom_sel: list[list[str]] = []
    size_sel: list[float] = []
    color_sel: list[str] = []
    x_rest: list[float] = []
    y_rest: list[float] = []
    text_rest: list[str] = []
    custom_rest: list[list[str]] = []
    size_rest: list[float] = []
    color_rest: list[str] = []
    hover_sel: list[str] = []
    hover_rest: list[str] = []

    for node in nodes:
        pos = positions.get(node["id"], (0.0, 0.0))
        size = 11 + min(12, node.get("degree", 0) * 1.8)
        if node.get("selected"):
            size += 8
        color = _status_color(node.get("status"))
        hover = _node_hover(node)
        label = _clean(node.get("title") or "", 18)
        if node.get("selected") or node.get("degree", 0) >= 3:
            x_sel.append(pos[0])
            y_sel.append(pos[1])
            text_sel.append(label)
            custom_sel.append([node["id"]])
            size_sel.append(size)
            color_sel.append(color)
            hover_sel.append(hover)
        else:
            x_rest.append(pos[0])
            y_rest.append(pos[1])
            text_rest.append("")
            custom_rest.append([node["id"]])
            size_rest.append(size)
            color_rest.append(color)
            hover_rest.append(hover)

    if x_rest:
        fig.add_trace(
            go.Scatter(
                x=x_rest,
                y=y_rest,
                mode="markers",
                marker={
                    "size": size_rest,
                    "color": color_rest,
                    "line": {"width": 1, "color": "rgba(15,23,42,0.95)"},
                    "opacity": 0.72,
                },
                customdata=custom_rest,
                hovertemplate=[f"{text}<extra></extra>" for text in hover_rest],
                hoverinfo="text",
                showlegend=False,
            )
        )
    if x_sel:
        fig.add_trace(
            go.Scatter(
                x=x_sel,
                y=y_sel,
                mode="markers+text",
                text=text_sel,
                textposition="top center",
                textfont={"size": 12, "color": "#e2e8f0"},
                marker={
                    "size": size_sel,
                    "color": color_sel,
                    "line": {"width": 2, "color": "rgba(255,255,255,0.9)"},
                    "opacity": 0.98,
                },
                customdata=custom_sel,
                hovertemplate=[f"{text}<extra></extra>" for text in hover_sel],
                hoverinfo="text",
                showlegend=False,
            )
        )

    fig.update_layout(
        height=680,
        margin={"l": 8, "r": 8, "t": 10, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"visible": False, "fixedrange": True},
        yaxis={"visible": False, "fixedrange": True, "scaleanchor": "x", "scaleratio": 1},
        dragmode="lasso",
        hovermode="closest",
        showlegend=False,
    )
    return fig


def render_wiki_mesh(
    pages: Iterable[dict[str, Any]],
    *,
    selected_page_id: str = "",
    query: str = "",
    surface: str = "all",
    status: str = "all",
    depth: int = 2,
    max_nodes: int = 96,
    key: str = "agent_wiki_mesh",
) -> str:
    import streamlit as st

    model = build_wiki_graph_model(
        pages,
        selected_page_id=selected_page_id,
        query=query,
        surface=surface,
        status=status,
        depth=depth,
        max_nodes=max_nodes,
    )
    nodes = model.get("nodes") or []
    if not nodes:
        st.info("조건에 맞는 관계 그래프가 없습니다.")
        return ""

    st.markdown(
        f"""
        <div class="widget-card-head">
          <span>Linked Memory</span>
          <b>Knowledge Mesh</b>
        </div>
        """,
        unsafe_allow_html=True,
    )
    depth_col, fit_col, stat_col = st.columns([0.75, 0.4, 1.1], gap="small")
    graph_depth = depth_col.slider("깊이", min_value=1, max_value=4, value=max(1, min(int(depth or 2), 4)), key=f"{key}_depth")
    fit_pressed = fit_col.button("Fit", width="stretch", key=f"{key}_fit")
    stat_col.caption(
        f"{len(nodes)} nodes · {len(model.get('edges') or [])} links · surface {surface} · status {status}"
    )
    if fit_pressed:
        st.session_state[f"{key}_fit_token"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    fig = _build_figure({**model, "selected_id": selected_page_id})
    event = st.plotly_chart(
        fig,
        key=key,
        on_select="rerun",
        selection_mode="points",
        width="stretch",
        config={"displayModeBar": False, "scrollZoom": True},
    )
    chosen = _extract_selected_page_id(event)
    if chosen:
        st.session_state["agent_wiki_selected_page_id"] = chosen
        return chosen
    if selected_page_id:
        return selected_page_id
    return model.get("selected_id") or ""
