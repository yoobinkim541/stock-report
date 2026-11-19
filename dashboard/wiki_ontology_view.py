"""AI 위키의 온톨로지·인사이트 화면.

`agent_console.wiki_ontology` 가 만든 `ontology.json` 과 `kind=insight` 페이지를 읽기만 한다.
모델 계산(`build_view_model`, `build_relation_network_html`)은 Streamlit 없이 동작한다.
"""
from __future__ import annotations

import json
from html import escape

from agent_console import wiki_ontology
from agent_console.wiki_ontology import ENTITY_TYPES, INSIGHT_KIND, PERSPECTIVES, RELATION_TYPES

TYPE_COLORS = {
    "company": "#38bdf8", "asset": "#f59e0b", "indicator": "#a78bfa", "institution": "#34d399",
    "person": "#f472b6", "region": "#94a3b8", "sector": "#fb923c", "theme": "#facc15", "event": "#f87171",
}
STATE_COLORS = {"asserted": "#64748b", "corroborated": "#22c55e", "contested": "#ef4444"}


def build_view_model(ontology: dict | None, pages: list[dict]) -> dict:
    """화면에 필요한 수치·목록. 온톨로지가 아직 없으면 ``ready=False``."""
    insights = [page for page in pages if page.get("kind") == INSIGHT_KIND and page.get("status") != "archived"]
    insights.sort(key=lambda page: str(page.get("updated_at") or page.get("created_at") or ""), reverse=True)
    if not ontology:
        return {"ready": False, "insights": insights, "entities": [], "relations": [], "stats": {}}
    insight_by_entity = {wiki_ontology.insight_entity_id(page).casefold(): page for page in insights}
    entities = []
    for entity in (ontology.get("entities") or {}).values():
        insight = insight_by_entity.get(entity["id"].casefold())
        entities.append({**{key: entity.get(key) for key in (
            "id", "type", "name", "ticker", "claim_count", "page_count", "source_count", "publisher_count",
            "relation_count", "last_seen")},
            "insight_id": insight.get("id") if insight else ""})
    stats = dict(ontology.get("stats") or {})
    pages_total = stats.get("pages") or 0
    stats["coverage"] = (stats.get("extracted_pages", 0) / pages_total) if pages_total else 0.0
    return {"ready": True, "generated_at": ontology.get("generated_at", ""), "stats": stats, "insights": insights,
            "entities": entities, "relations": list(ontology.get("relations") or [])}


def neutrality_caption(ontology: dict) -> str:
    """claim 이 어느 발행자·관점에서 왔는지와 반대 검토 결과 한 줄."""
    stats = ontology.get("stats") or {}
    publishers = ontology.get("publishers") or {}
    total = sum(row.get("claims", 0) for row in publishers.values()) or 1
    voices = ", ".join(f"{row.get('name', key)}({PERSPECTIVES.get(row.get('role'), row.get('role'))}) "
                       f"{row.get('claims', 0) / total:.0%}" for key, row in list(publishers.items())[:6])
    review = stats.get("review") or {}
    views = (stats.get("relation_modalities") or {}).get("view", 0)
    return (f"발행자 {len(publishers)}곳: {voices or '정보 없음'} · 견해·전망으로 적힌 관계 {views}개 · "
            f"반대 검토 완료 {review.get('pages_reviewed', 0)}쪽 / 대기 {review.get('pages_pending', 0)}쪽 "
            f"(기각 {review.get('reject', 0)} · 견해로 낮춤 {review.get('view', 0)} · 검토자 추가 {review.get('added', 0)}"
            f", 그중 되검토 기각 {review.get('added_rejected', 0)} · 되검토 대기 {review.get('added_unchecked', 0)})"
            + (f" · 맡은 모델: {wiki_ontology.models_label(stats)}" if stats.get("models") else ""))


def entity_detail(ontology: dict, entity_id: str, pages_by_id: dict[str, dict], *, claim_limit: int = 12) -> dict:
    """한 개체의 관계와 근거 claim (최근 것부터)."""
    entity = (ontology.get("entities") or {}).get(entity_id) or {}
    claims = []
    for page_id, claim_id in entity.get("claims") or []:
        page = pages_by_id.get(page_id)
        claim = next((row for row in wiki_ontology.page_claims(page or {}) if row["claim_id"] == claim_id), None)
        if claim:
            claims.append({"page_id": page_id, "title": str(page.get("title") or page_id),
                           "text": " ".join(str(claim["text"]).split()),
                           "day": wiki_ontology._iso_day(claim.get("as_of"), page.get("updated_at"))})
    claims.sort(key=lambda row: row["day"], reverse=True)
    relations = [{**relation, "phrase": wiki_ontology.relation_phrase(ontology, relation)}
                 for relation in wiki_ontology.entity_relations(ontology, entity_id)]
    partners = [(right if left == entity_id else left, count) for left, right, count in ontology.get("co_mentions") or []
                if entity_id in (left, right)][:8]
    names = ontology.get("entities") or {}
    return {"entity": entity, "relations": relations, "claims": claims[:claim_limit],
            "co_mentions": [((names.get(other) or {}).get("name") or other, count) for other, count in partners]}


def build_relation_network_html(ontology: dict, *, focus: str = "", max_edges: int = 120) -> str:
    """타입이 붙은 관계 그래프 (vis-network). ``focus`` 가 있으면 그 개체의 1촌만."""
    entities = ontology.get("entities") or {}
    relations = [relation for relation in ontology.get("relations") or []
                 if not focus or focus in (relation["from"], relation["to"])][:max_edges]
    node_ids = list(dict.fromkeys(node for relation in relations for node in (relation["from"], relation["to"])))
    nodes = []
    for node_id in node_ids:
        entity = entities.get(node_id) or {"name": node_id, "type": "theme"}
        nodes.append({
            "id": node_id, "label": str(entity["name"])[:22],
            "title": f"{entity['name']} · {ENTITY_TYPES.get(entity['type'], entity['type'])} · "
                     f"claim {entity.get('claim_count', 0)}건 · 출처 {entity.get('source_count', 0)}개",
            "color": {"background": TYPE_COLORS.get(entity["type"], "#38bdf8"),
                      "border": "#ffffff" if node_id == focus else "rgba(255,255,255,.4)"},
            "size": 12 + min(20, int(entity.get("relation_count", 0)) * 2) + (8 if node_id == focus else 0),
            "font": {"color": "#f8fafc", "size": 13},
        })
    edges = []
    for relation in relations:
        label, symmetric = RELATION_TYPES.get(relation["type"], (relation["type"], False))
        edges.append({
            "from": relation["from"], "to": relation["to"], "label": label,
            "title": wiki_ontology.relation_label(relation) + (f" · {relation['notes'][0]}" if relation.get("notes") else ""),
            "arrows": "" if symmetric else "to", "width": 1 + min(4, relation["source_count"]),
            "color": {"color": STATE_COLORS.get(relation["state"], "#64748b")},
            "font": {"color": "#cbd5e1", "size": 10, "strokeWidth": 0},
            "dashes": relation["state"] == "contested" or ([2, 6] if relation.get("modality") == "view" else False),
        })
    legend = " ".join(
        f"<span style='display:inline-flex;align-items:center;gap:4px;margin-right:10px;'>"
        f"<i style='width:9px;height:9px;border-radius:50%;background:{color};display:inline-block;'></i>{escape(ENTITY_TYPES[name])}</span>"
        for name, color in TYPE_COLORS.items())
    # </script> 가 데이터 안에서 스크립트를 끊지 못하게 '<' 를 이스케이프한다.
    data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>html,body{{margin:0;background:#0b1220;color:#cbd5e1;font-family:sans-serif;}}
#net{{height:520px;}} #legend{{padding:6px 10px;font-size:12px;}}</style></head>
<body><div id="legend">{legend}</div><div id="net"></div>
<script>
const data = {data};
const box = document.getElementById('net');
const network = new vis.Network(box, {{nodes: new vis.DataSet(data.nodes), edges: new vis.DataSet(data.edges)}}, {{
  nodes: {{shape: 'dot', borderWidth: 1.5}},
  edges: {{smooth: {{type: 'continuous'}}}},
  physics: {{solver: 'forceAtlas2Based', forceAtlas2Based: {{gravitationalConstant: -60, springLength: 110}}, stabilization: {{iterations: 160}}}},
  interaction: {{hover: true, tooltipDelay: 120}}
}});
// 숨겨진 탭 안에서 처음 그려지면 폭이 0 이라 한쪽으로 쏠린다. 크기가 잡힐 때마다 다시 맞춘다.
const fit = () => {{ if (box.clientWidth) {{ network.redraw(); network.fit({{animation: false}}); }} }};
network.once('stabilizationIterationsDone', fit);
new ResizeObserver(fit).observe(box);
</script></body></html>"""


def render_section(pages_all: list[dict], *, on_open) -> None:
    """AI 위키 페이지의 '온톨로지·인사이트' 구역."""
    import pandas as pd
    import streamlit as st

    ontology = wiki_ontology.load_ontology()
    model = build_view_model(ontology, pages_all)
    st.markdown("##### 온톨로지·인사이트")
    if not model["ready"]:
        st.info("온톨로지가 아직 만들어지지 않았습니다. `python -m reports.wiki_ontology_insights --apply` 가 "
                "6시간마다 claim 에서 개체·관계를 추출해 채웁니다.")
        return
    stats = model["stats"]
    states = stats.get("relation_states") or {}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("개체", f"{stats.get('entities', 0):,}")
    c2.metric("관계", f"{stats.get('relations', 0):,}", help="claim 이 직접 진술한 타입 관계만 셉니다.")
    c3.metric("교차 확인된 관계", f"{states.get('corroborated', 0):,}",
              help="서로 다른 발행자 2곳 이상이 같은 관계를 진술. 한 채널의 글 여러 건은 한 목소리로 셉니다.")
    c4.metric("인사이트", f"{len(model['insights']):,}")
    c5.metric("관계 추출 진행", f"{stats['coverage']:.0%}",
              help=f"claim 이 있는 활성 페이지 {stats.get('pages', 0)}개 중 {stats.get('extracted_pages', 0)}개 추출 완료")
    st.caption(f"마지막 컴파일 {str(model['generated_at'])[:16].replace('T', ' ')} UTC · claim {stats.get('claims', 0):,}건 기준 · "
               "관계와 인사이트는 공개 출처를 모델이 정리한 것이며 원문 검증 전입니다.")
    st.caption(neutrality_caption(ontology))

    tab_insight, tab_entity, tab_graph = st.tabs(["인사이트", "개체·관계", "관계 그래프"])
    with tab_insight:
        if not model["insights"]:
            st.info("아직 인사이트가 없습니다. 출처 3개 이상에서 claim 이 모인 개체부터 생성됩니다.")
        for page in model["insights"][:30]:
            with st.container(border=True):
                st.markdown(f"**{escape(str(page.get('title') or ''))}**")
                st.write(str(page.get("summary") or ""))
                meta = ((page.get("source_knowledge") or {}).get("insight") or {})
                st.caption(f"근거 claim {len(meta.get('cited') or [])}건 · 신뢰도 {float(page.get('confidence') or 0):.2f} · "
                           f"갱신 {str(page.get('updated_at') or '')[:10]}")
                if st.button("문서 열기", key=f"onto_open_{page.get('id')}"):
                    on_open(str(page.get("id") or ""))
                    st.rerun()
    with tab_entity:
        f1, f2 = st.columns([1, 2])
        type_options = ["all", *[name for name in ENTITY_TYPES if (stats.get("entity_types") or {}).get(name)]]
        entity_type = f1.selectbox("타입", type_options, key="onto_entity_type",
                                   format_func=lambda value: "전체" if value == "all" else ENTITY_TYPES[value])
        rows = [row for row in model["entities"] if entity_type == "all" or row["type"] == entity_type]
        if rows:
            st.dataframe(pd.DataFrame([{
                "개체": row["name"], "타입": ENTITY_TYPES.get(row["type"], row["type"]), "claim": row["claim_count"],
                "출처": row["source_count"], "발행자": row["publisher_count"] or 0, "관계": row["relation_count"],
                "최근": row["last_seen"],
                "인사이트": "있음" if row["insight_id"] else "",
            } for row in rows[:300]]), hide_index=True, width="stretch", height=260)
            picked = f2.selectbox("개체 상세", [row["id"] for row in rows[:300]], key="onto_entity_pick",
                                  format_func=lambda value: f"{ontology['entities'][value]['name']} ({value})")
            detail = entity_detail(ontology, picked, {str(page.get("id")): page for page in pages_all})
            left, right = st.columns(2)
            with left:
                st.markdown("**관계**")
                if not detail["relations"]:
                    st.caption("아직 추출된 관계가 없습니다.")
                for relation in detail["relations"][:20]:
                    note = f" — {relation['notes'][0]}" if relation.get("notes") else ""
                    st.markdown(f"- {escape(relation['phrase'])} · {wiki_ontology.relation_label(relation)}{escape(note)}")
                if detail["co_mentions"]:
                    st.caption("함께 언급: " + ", ".join(f"{name} {count}회" for name, count in detail["co_mentions"]))
            with right:
                st.markdown("**근거 claim**")
                for claim in detail["claims"]:
                    st.markdown(f"- {escape(claim['text'][:260])}  \n  <small>{escape(claim['day'])} · {escape(claim['title'][:50])}</small>",
                                unsafe_allow_html=True)
    with tab_graph:
        if not model["relations"]:
            st.info("관계가 추출되면 여기에 타입 그래프가 그려집니다.")
        else:
            import streamlit.components.v1 as components

            connected = [row for row in model["entities"] if row["relation_count"]]
            focus = st.selectbox("중심 개체", ["", *[row["id"] for row in connected]], key="onto_graph_focus",
                                 format_func=lambda value: "전체 (출처 많은 관계부터)" if not value
                                 else f"{ontology['entities'][value]['name']} · 관계 {ontology['entities'][value]['relation_count']}개")
            components.html(build_relation_network_html(ontology, focus=focus), height=570, scrolling=False)
            st.caption("선 색: 회색 단일 발행자 · 초록 복수 발행자 일치 · 빨강 점선 엇갈림 · 성긴 점선은 견해·전망")
