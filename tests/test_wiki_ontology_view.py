from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from agent_console import wiki_ontology as wo
from dashboard import wiki_ontology_view as view


def _ontology() -> tuple[dict, list[dict]]:
    lexicon = wo.Lexicon([
        {"id": "region:iran", "type": "region", "name": "이란"},
        {"id": "asset:crude-oil", "type": "asset", "name": "원유", "aliases": ["유가"]},
        {"id": "institution:opec", "type": "institution", "name": "OPEC"},
    ], use_tickers=False)
    pages = [{"id": f"p{n}", "title": f"문서 {n}", "kind": "note", "status": "draft", "updated_at": f"2026-11-1{n}T00:00:00+00:00",
              "source_knowledge": {"claims": [{"claim_id": "a", "source_revision_id": f"r{n}", "text": text}]}}
             for n, text in enumerate(["이란 긴장으로 유가가 올랐다.", "이란 리스크가 원유를 밀어 올렸다.",
                                       "OPEC 증산은 유가에 </script> 부담이다."], 1)]
    relation = lambda kind, source, page: {"type": kind, "from": source, "to": "asset:crude-oil",
                                           "evidence": [{"page_id": page, "claim_id": "a"}], "note": "</script>"}
    extractions = {"pages": {
        "p1": {"input_hash": wo.page_input_hash(pages[0]), "relations": [relation("positive_for", "region:iran", "p1")]},
        "p2": {"input_hash": wo.page_input_hash(pages[1]), "relations": [relation("positive_for", "region:iran", "p2")]},
        "p3": {"input_hash": "stale", "relations": [relation("negative_for", "institution:opec", "p3")]},
    }}
    providers = {"r1": "telegram:insidertracking", "r2": "telegram:kwusa", "r3": "telegram:insidertracking"}
    return wo.compile_ontology(pages, extractions, generated_at="2026-11-15T00:00:00+00:00", lexicon=lexicon,
                               source_providers=providers), pages


def test_view_model_reports_coverage_and_links_insights_to_entities():
    ontology, pages = _ontology()
    insight = {"id": "insight-1", "kind": "insight", "status": "draft", "tags": ["entity:asset:crude-oil"],
               "updated_at": "2026-11-15T00:00:00+00:00"}
    archived = {**insight, "id": "insight-old", "status": "archived"}

    model = view.build_view_model(ontology, [*pages, insight, archived])

    assert model["ready"] and [page["id"] for page in model["insights"]] == ["insight-1"]
    assert round(model["stats"]["coverage"], 2) == 0.67
    by_id = {row["id"]: row for row in model["entities"]}
    assert by_id["asset:crude-oil"]["insight_id"] == "insight-1" and by_id["region:iran"]["insight_id"] == ""
    assert view.build_view_model(None, [insight])["ready"] is False


def test_entity_detail_lists_relations_newest_claims_and_co_mentions():
    ontology, pages = _ontology()

    detail = view.entity_detail(ontology, "asset:crude-oil", {page["id"]: page for page in pages})

    assert [row["phrase"] for row in detail["relations"]] == [
        "이란 → 원유 [호재·상승 요인]", "OPEC → 원유 [악재·하락 요인]"]
    assert [claim["page_id"] for claim in detail["claims"]] == ["p3", "p2", "p1"]
    assert detail["co_mentions"] == [("이란", 2)]


def test_relation_network_html_embeds_typed_edges_without_breaking_out_of_script():
    ontology, _ = _ontology()

    html = view.build_relation_network_html(ontology)
    focused = view.build_relation_network_html(ontology, focus="institution:opec")

    assert html.count("</script>") == 2  # 라이브러리 태그와 본문 스크립트만 — 데이터 안의 것은 이스케이프
    data = json.loads(html.split("const data = ", 1)[1].split(";\n", 1)[0])
    assert {(edge["from"], edge["to"], edge["label"]) for edge in data["edges"]} == {
        ("region:iran", "asset:crude-oil", "호재·상승 요인"), ("institution:opec", "asset:crude-oil", "악재·하락 요인")}
    corroborated = next(edge for edge in data["edges"] if edge["from"] == "region:iran")
    assert corroborated["color"]["color"] == view.STATE_COLORS["corroborated"] and corroborated["arrows"] == "to"
    focused_data = json.loads(focused.split("const data = ", 1)[1].split(";\n", 1)[0])
    assert {node["id"] for node in focused_data["nodes"]} == {"institution:opec", "asset:crude-oil"}


def test_health_check_flags_lagging_index_and_stale_ontology(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path))
    from reports import wiki_health_check as health

    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    stats = {"latest": {"updated_at": "2026-11-15T11:00:00+00:00"}, "kind_counts": {"insight": 2}}

    summary, notes = health._derived_health(stats, now)
    assert summary == {"insights": 2}
    assert len(notes) == 2 and "index.md" in notes[0] and "온톨로지가 아직 없습니다" in notes[1]

    index = tmp_path / "index.md"
    index.write_text("# index", encoding="utf-8")
    old = (now - timedelta(days=3)).timestamp()
    os.utime(index, (old, old))
    ontology, _ = _ontology()
    wo.save_ontology({**ontology, "generated_at": "2026-11-13T00:00:00+00:00"}, tmp_path / "ontology")
    summary, notes = health._derived_health(stats, now)
    assert summary["relations"] == 2 and summary["insights"] == 2
    assert len(notes) == 2 and "index.md" in notes[0] and "24시간 넘게 멈췄습니다" in notes[1]

    fresh = (now - timedelta(hours=1)).timestamp()
    os.utime(index, (fresh, fresh))
    wo.save_ontology({**ontology, "generated_at": "2026-11-15T07:00:00+00:00"}, tmp_path / "ontology")
    assert health._derived_health(stats, now)[1] == []
    report = {"ontology": health._derived_health(stats, now)[0], "stats": {}}
    assert "온톨로지: 개체 3 · 관계 2 (교차 확인 1 · 상충 0) · 인사이트 2 · 관계 추출 2/3 페이지" in health.format_report(report)
