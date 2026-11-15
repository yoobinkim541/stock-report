from __future__ import annotations

from agent_console import wiki_ontology as wo


def _lexicon() -> wo.Lexicon:
    return wo.Lexicon([
        {"id": "region:iran", "type": "region", "name": "이란", "aliases": ["Iran"]},
        {"id": "region:india", "type": "region", "name": "인도"},
        {"id": "indicator:inflation", "type": "indicator", "name": "인플레이션"},
        {"id": "asset:crude-oil", "type": "asset", "name": "원유", "aliases": ["유가"]},
        {"id": "institution:fed", "type": "institution", "name": "연준", "aliases": ["Fed", "연방준비제도"]},
        {"id": "institution:bok", "type": "institution", "name": "한국은행"},
        {"id": "sector:banks", "type": "sector", "name": "은행"},
        {"id": "theme:ai", "type": "theme", "name": "AI"},
    ], use_tickers=False)


def _page(page_id: str, claims: list[tuple[str, str, str]], **extra) -> dict:
    """claims: (claim_id, source_revision_id, text)."""
    return {"id": page_id, "title": f"문서 {page_id}", "kind": "note", "status": "draft",
            "updated_at": "2026-11-10T00:00:00+00:00",
            "source_knowledge": {"claims": [
                {"claim_id": claim_id, "source_revision_id": revision, "text": text, "as_of": "2026-11-09",
                 "competing_claim_ids": []} for claim_id, revision, text in claims]}, **extra}


def test_two_letter_hangul_alias_needs_a_word_boundary():
    lexicon = _lexicon()

    assert lexicon.match("인플레이션이란 무엇인가") == ["indicator:inflation"]
    assert lexicon.match("차량 인도량이 늘었다") == []
    assert lexicon.match("이란산 원유 수출과 인도의 수입") == ["region:iran", "asset:crude-oil", "region:india"]


def test_longest_alias_wins_and_latin_alias_respects_word_boundary():
    lexicon = _lexicon()

    assert lexicon.match("한국은행 총재 발언") == ["institution:bok"]
    assert lexicon.match("The Fed held rates; AI demand stays") == ["institution:fed", "theme:ai"]
    assert lexicon.match("federal budget, said the chair") == []


def test_extraction_keeps_only_relations_whose_cited_claims_name_both_ends():
    lexicon = _lexicon()
    pages = [_page("p1", [("a", "r1", "이란의 호르무즈 봉쇄 위협으로 유가가 급등했다."),
                          ("b", "r1", "연준은 금리를 동결했다.")])]
    _, keymap = wo.build_extraction_request(pages, lexicon)
    payload = {
        "entities": [{"name": "호르무즈", "type": "region", "aliases": ["없는별칭"]},
                     {"name": "삼성전자", "type": "company"}],
        "relations": [
            {"type": "positive_for", "from": "region:iran", "to": "asset:crude-oil", "claims": ["c1"], "note": "봉쇄 위협"},
            {"type": "negative_for", "from": "institution:fed", "to": "asset:crude-oil", "claims": ["c2"]},
            {"type": "regulates", "from": "region:iran", "to": "호르무즈", "claims": ["c1"]},
            {"type": "invented", "from": "region:iran", "to": "asset:crude-oil", "claims": ["c1"]},
            {"type": "leads", "from": "institution:fed", "to": "region:iran", "claims": ["c1", "c2"]},
        ],
    }

    result = wo.validate_extraction(payload, keymap, lexicon)

    assert [(r["type"], r["from"], r["to"]) for r in result["relations"]] == [
        ("positive_for", "region:iran", "asset:crude-oil"), ("regulates", "region:iran", "region:호르무즈")]
    assert result["relations"][0]["evidence"] == [{"page_id": "p1", "claim_id": "a"}]
    assert result["entities"] == [{"id": "region:호르무즈", "type": "region", "name": "호르무즈", "aliases": []}]
    assert result["rejected"] == {"entity_not_in_claims": 1, "relation_not_grounded": 1, "relation_type_invalid": 1,
                                  "relation_domain_invalid": 1}


def _extraction(page: dict, relations: list[dict]) -> dict:
    return {"input_hash": wo.page_input_hash(page), "entities": [], "relations": relations}


def test_compile_marks_relations_asserted_corroborated_and_contested():
    lexicon = _lexicon()
    p1 = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다."), ("b", "r1", "연준 긴축은 은행 수익에 호재다.")])
    p2 = _page("p2", [("c", "r2", "이란 리스크가 원유 가격을 밀어 올렸다."), ("d", "r2", "연준 긴축은 은행 건전성에 악재다.")])
    relation = lambda kind, source, target, page, claim: {
        "type": kind, "from": source, "to": target, "evidence": [{"page_id": page, "claim_id": claim}], "note": ""}
    extractions = {"pages": {
        "p1": _extraction(p1, [relation("positive_for", "region:iran", "asset:crude-oil", "p1", "a"),
                               relation("positive_for", "institution:fed", "sector:banks", "p1", "b")]),
        "p2": _extraction(p2, [relation("positive_for", "region:iran", "asset:crude-oil", "p2", "c"),
                               relation("negative_for", "institution:fed", "sector:banks", "p2", "d")]),
    }}

    ontology = wo.compile_ontology([p1, p2], extractions, generated_at="2026-11-15T00:00:00+00:00", lexicon=lexicon)

    states = {(r["type"], r["from"], r["to"]): (r["state"], r["source_count"]) for r in ontology["relations"]}
    assert states == {
        ("positive_for", "region:iran", "asset:crude-oil"): ("corroborated", 2),
        ("positive_for", "institution:fed", "sector:banks"): ("contested", 1),
        ("negative_for", "institution:fed", "sector:banks"): ("contested", 1),
    }
    oil = ontology["entities"]["asset:crude-oil"]
    assert (oil["claim_count"], oil["source_count"], oil["relation_count"]) == (2, 2, 1)
    assert ["asset:crude-oil", "region:iran", 2] in ontology["co_mentions"]
    assert ontology["stats"]["extracted_pages"] == 2


def test_compile_drops_relations_whose_evidence_claim_is_gone():
    lexicon = _lexicon()
    page = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다.")])
    extractions = {"pages": {"p1": _extraction(page, [
        {"type": "positive_for", "from": "region:iran", "to": "asset:crude-oil",
         "evidence": [{"page_id": "p1", "claim_id": "a"}], "note": ""}])}}
    rewritten = _page("p1", [("z", "r9", "연준은 금리를 동결했다.")])
    archived = {**page, "status": "archived"}

    for pages in ([rewritten], [archived]):
        ontology = wo.compile_ontology(pages, extractions, generated_at="2026-11-15T00:00:00+00:00", lexicon=lexicon)
        assert ontology["relations"] == []
        assert ontology["stats"]["extracted_pages"] == 0
        assert "asset:crude-oil" not in ontology["entities"]


def test_saved_ontology_answers_entity_lookup_and_context(tmp_path):
    lexicon = _lexicon()
    page = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다."), ("b", "r2", "이란 제재 완화 논의.")])
    extractions = {"pages": {"p1": _extraction(page, [
        {"type": "positive_for", "from": "region:iran", "to": "asset:crude-oil",
         "evidence": [{"page_id": "p1", "claim_id": "a"}], "note": "긴장 고조"}])}}
    ontology = wo.compile_ontology([page], extractions, generated_at="2026-11-15T00:00:00+00:00", lexicon=lexicon)
    wo.save_ontology(ontology, tmp_path)
    insight = {"id": "insight-x", "status": "draft", "tags": ["insight", "entity:asset:crude-oil"],
               "summary": "공급 차질 우려가 유가를 지지한다는 가설."}
    assert wo.insight_entity_id({"tags": ["entity:company:nvda"],
                                 "source_knowledge": {"insight": {"entity_id": "company:NVDA"}}}) == "company:NVDA"

    assert [entity["id"] for entity in wo.find_entities("유가 전망 알려줘", root=tmp_path)] == ["asset:crude-oil"]
    section = wo.context_section("유가 전망 알려줘", insights=[insight], root=tmp_path)
    assert "이란 → 원유 [호재·상승 요인] — 단일 출처, 출처 1개" in section
    assert "인사이트(모델 추론, 미검증): 공급 차질 우려가 유가를 지지한다는 가설. [[insight-x]]" in section
    assert wo.context_section("오늘 날씨", insights=[insight], root=tmp_path) == ""
    markdown = (tmp_path / "ontology.md").read_text(encoding="utf-8")
    assert "- 이란 → 원유 [호재·상승 요인] · asserted · 출처 1개 · 근거 claim 1건 — 긴장 고조" in markdown


def test_seed_config_is_a_valid_lexicon_without_alias_collisions():
    config = wo._seed_config()
    lexicon = wo.seed_lexicon()
    seeded = {entity["id"] for entity in config["entities"]}

    assert seeded <= set(lexicon.entities)
    for entity in config["entities"]:
        assert entity["type"] in wo.ENTITY_TYPES
        assert entity["id"].split(":", 1)[0] == entity["type"]
        # 이름이 다른 개체의 별칭으로 먼저 등록돼 사라지면 그 개체는 영영 매칭되지 않는다.
        # 한 글자 이름(금·은)은 오탐이 커서 별칭으로만 잡는다.
        names = [name for name in [entity["name"], *entity.get("aliases", [])] if len(name) >= 2]
        assert names and all(lexicon.resolve(name) == entity["id"] for name in names), entity["id"]
