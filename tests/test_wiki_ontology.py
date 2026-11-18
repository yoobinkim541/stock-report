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

    ontology = wo.compile_ontology([p1, p2], extractions, generated_at="2026-11-15T00:00:00+00:00", lexicon=lexicon,
                                   source_providers={"r1": "telegram:insidertracking", "r2": "telegram:kwusa"})

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


def _relation(kind: str, source: str, target: str, page: str, claim: str, **extra) -> dict:
    return {"type": kind, "from": source, "to": target, "evidence": [{"page_id": page, "claim_id": claim}],
            "note": "", **extra}


def test_posts_from_one_publisher_never_corroborate_each_other():
    lexicon = _lexicon()
    p1 = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다.")])
    p2 = _page("p2", [("c", "r2", "이란 리스크가 원유 가격을 밀어 올렸다.")])
    p3 = _page("p3", [("e", "r3", "이란 변수로 유가가 뛰었다.")])
    extractions = {"pages": {page["id"]: _extraction(page, [
        _relation("positive_for", "region:iran", "asset:crude-oil", page["id"], claim)])
        for page, claim in ((p1, "a"), (p2, "c"), (p3, "e"))}}

    def compiled(providers):
        ontology = wo.compile_ontology([p1, p2, p3], extractions, generated_at="2026-11-15T00:00:00+00:00",
                                       lexicon=lexicon, source_providers=providers)
        relation = ontology["relations"][0]
        return relation["state"], relation["source_count"], relation["publishers"], relation["perspectives"], ontology

    same = compiled({"r1": "telegram:insidertracking", "r2": "telegram:insidertracking", "r3": "telegram:insidertracking"})
    assert same[:4] == ("asserted", 3, ["미국 주식 인사이더"], ["community"])
    # 같은 증권사의 채널 글과 리포트 PDF 는 한 목소리다.
    house = compiled({"r1": "telegram:kwusa", "r2": "broker_report:kwusa", "r3": "telegram:KWUSA"})
    assert house[0] == "asserted" and len(house[2]) == 1 and house[3] == ["sell_side"]
    mixed = compiled({"r1": "telegram:insidertracking", "r2": "telegram:kwusa", "r3": "telegram:kwusa"})
    assert mixed[0] == "corroborated" and mixed[3] == ["community", "sell_side"]
    assert mixed[4]["stats"]["perspectives"] == {"community": 1, "sell_side": 2}
    assert mixed[4]["entities"]["asset:crude-oil"]["publisher_count"] == 2
    # 발행자를 모르는 글끼리는 독립이라고 세지 않는다.
    assert compiled({})[:3] == ("asserted", 3, [wo.UNKNOWN_PUBLISHER])


def test_review_accepts_only_grounded_objections():
    lexicon = _lexicon()
    pages = [_page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다."), ("b", "r1", "연준 긴축은 은행 수익에 호재다."),
                          ("c", "r1", "이란 제재 완화 논의로 유가가 내렸다."), ("d", "r1", "연준과 이란 소식이 함께 전해졌다.")])]
    proposed = [_relation("positive_for", "region:iran", "asset:crude-oil", "p1", "a", note="추출 모델의 설명"),
                _relation("positive_for", "institution:fed", "sector:banks", "p1", "b"),
                _relation("conflicts_with", "institution:fed", "region:iran", "p1", "d"),
                _relation("exposed_to", "sector:banks", "institution:fed", "p1", "b"),
                _relation("regulates", "institution:fed", "sector:banks", "p1", "b")]

    prompt, keymap, relmap = wo.build_review_request(pages, proposed, lexicon)

    assert "- r1: positive_for | region:iran → asset:crude-oil | cites c1" in prompt
    assert "추출 모델의 설명" not in prompt  # 검토자는 추출 모델의 설명을 보지 않는다
    payload = {
        "reviews": [
            {"id": "r1", "verdict": "keep", "reasons": ["contradicted"], "counter": ["c3", "c1", "c99"]},
            {"id": "r2", "verdict": "reject", "reasons": ["opinion"]},        # 평가라는 이유만으로는 지우지 않는다
            {"id": "r3", "verdict": "reject", "reasons": ["listed_only"]},
            {"id": "r4", "verdict": "reject", "reasons": ["마음에 안 듦"]},    # 근거 없는 기각
            {"id": "r5", "verdict": "keep", "reasons": ["contradicted"], "counter": ["c2"]},  # 자기 근거는 반대 근거가 아니다
            {"id": "r9", "verdict": "keep"}, {"id": "r1", "verdict": "reject", "reasons": ["trivial"]},
        ],
        "missed": [
            {"type": "negative_for", "from": "region:iran", "to": "asset:crude-oil", "claims": ["c3"], "modality": "view"},
            {"type": "positive_for", "from": "region:iran", "to": "asset:crude-oil", "claims": ["c1"]},  # 이미 제안된 관계
            {"type": "negative_for", "from": "institution:fed", "to": "asset:crude-oil", "claims": ["c2"]},  # 근거 없음
        ],
    }

    result = wo.validate_review(payload, relmap, keymap, lexicon)

    assert result["reviews"] == {
        "r1": {"verdict": "keep", "reasons": ["contradicted"], "counter": [{"page_id": "p1", "claim_id": "c"}]},
        "r2": {"verdict": "view", "reasons": ["opinion"], "counter": []},
        "r3": {"verdict": "reject", "reasons": ["listed_only"], "counter": []},
        "r5": {"verdict": "keep", "reasons": [], "counter": []},
    }
    assert result["rejected"] == {"reject_downgraded": 1, "reject_unfounded": 1, "counter_unsupported": 1,
                                  "review_invalid": 2}
    assert [(r["type"], r["from"], r["to"], r["modality"]) for r in result["missed"]["relations"]] == [
        ("negative_for", "region:iran", "asset:crude-oil", "view")]
    assert result["missed"]["rejected"] == {"relation_not_grounded": 1}
    assert wo.validate_review({"relations": []}, relmap, keymap, lexicon)["rejected"] == {"payload_invalid": 1}


def test_compile_applies_review_verdicts_by_rule():
    lexicon = _lexicon()
    page = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다."), ("b", "r1", "연준 긴축은 은행 수익에 호재다."),
                        ("c", "r1", "이란 제재 완화 논의로 유가가 내렸다."), ("d", "r1", "연준과 이란 소식이 함께 전해졌다."),
                        ("e", "r1", "AI 투자가 은행 수익을 끌어올릴 것으로 전망된다.")])
    record = _extraction(page, [
        _relation("positive_for", "region:iran", "asset:crude-oil", "p1", "a",
                  review={"verdict": "keep", "reasons": ["contradicted"], "counter": [{"page_id": "p1", "claim_id": "c"}]}),
        _relation("positive_for", "institution:fed", "sector:banks", "p1", "b",
                  review={"verdict": "view", "reasons": ["opinion"], "counter": []}),
        _relation("conflicts_with", "institution:fed", "region:iran", "p1", "d",
                  review={"verdict": "reject", "reasons": ["listed_only"], "counter": []}),
        _relation("positive_for", "theme:ai", "sector:banks", "p1", "e",
                  review={"verdict": "keep", "reasons": [], "counter": []}),
        _relation("regulates", "institution:fed", "sector:banks", "p1", "b"),
        _relation("negative_for", "region:iran", "institution:fed", "p1", "d", origin="reviewer"),
    ])
    record["review"] = {"input_hash": record["input_hash"]}

    ontology = wo.compile_ontology([page], {"pages": {"p1": record}}, generated_at="2026-11-15T00:00:00+00:00",
                                   lexicon=lexicon, source_providers={"r1": "telegram:kwusa"})

    rows = {(r["type"], r["from"], r["to"]): r for r in ontology["relations"]}
    assert ("conflicts_with", "institution:fed", "region:iran") not in rows  # 기각된 관계는 그래프에 없다
    contested = rows[("positive_for", "region:iran", "asset:crude-oil")]
    assert (contested["state"], contested["modality"], contested["counter"]) == ("contested", "reported", [["p1", "c"]])
    assert rows[("positive_for", "institution:fed", "sector:banks")]["modality"] == "view"
    # 검토자가 유지했어도 인용 claim 이 전망 어투면 견해로 적는다.
    assert rows[("positive_for", "theme:ai", "sector:banks")]["modality"] == "view"
    assert rows[("regulates", "institution:fed", "sector:banks")]["review"] == {"unreviewed": 1}
    assert ontology["stats"]["review"] == {"pages_reviewed": 1, "pages_cross_pending": 1, "keep": 2, "view": 1,
                                           "reject": 1, "unreviewed": 1, "added": 1, "added_unchecked": 1}
    assert ontology["stats"]["relation_modalities"] == {"reported": 3, "view": 2}
    assert "견해·전망" in wo.relation_label(rows[("positive_for", "institution:fed", "sector:banks")])
    markdown = wo.render_markdown(ontology)
    assert "- 발행자: 키움증권(증권사 리서치) 5건 100%" in markdown
    assert "유지 2 · 견해로 낮춤 1 · 기각 1 · 검토자가 추가 1(되검토에서 기각 0 · 되검토 대기 1) · 미검토 1" in markdown
    # 기각된 관계는 사유와 함께 따로 남아 사람이 되짚어 볼 수 있다.
    assert ontology["rejected"] == [{"type": "conflicts_with", "from": "institution:fed", "to": "region:iran",
                                     "reasons": ["listed_only"], "note": "", "evidence": [["p1", "d"]]}]
    assert "## 검토에서 기각된 관계" in markdown
    assert "· 함께 나열됐을 뿐" in markdown


def test_reviewer_additions_are_cross_checked_like_any_other_proposal():
    lexicon = _lexicon()
    page = _page("p1", [("a", "r1", "이란 긴장으로 유가가 올랐다."), ("b", "r1", "연준과 이란 소식이 함께 전해졌다."),
                        ("c", "r1", "연준 긴축은 은행 수익에 호재가 될 전망이다.")])
    record = _extraction(page, [
        _relation("positive_for", "region:iran", "asset:crude-oil", "p1", "a",
                  review={"verdict": "keep", "reasons": [], "counter": []}),
        _relation("conflicts_with", "institution:fed", "region:iran", "p1", "b", origin="reviewer",
                  review={"verdict": "reject", "reasons": ["listed_only"], "counter": []}),
        _relation("positive_for", "institution:fed", "sector:banks", "p1", "c", origin="reviewer",
                  review={"verdict": "view", "reasons": ["forecast"], "counter": []}),
    ])
    record["review"] = {"input_hash": record["input_hash"], "reviewer": "hermes", "cross": {"reviewer": "codex"}}

    ontology = wo.compile_ontology([page], {"pages": {"p1": record}}, generated_at="2026-11-15T00:00:00+00:00",
                                   lexicon=lexicon)

    rows = {(r["type"], r["from"], r["to"]): r for r in ontology["relations"]}
    assert set(rows) == {("positive_for", "region:iran", "asset:crude-oil"),
                         ("positive_for", "institution:fed", "sector:banks")}
    assert rows[("positive_for", "institution:fed", "sector:banks")]["modality"] == "view"
    assert ontology["stats"]["review"] == {"pages_reviewed": 1, "keep": 1, "added": 2, "added_rejected": 1}
    assert [(r["type"], r.get("origin")) for r in ontology["rejected"]] == [("conflicts_with", "reviewer")]
    # 누가 추출·검토·되검토했는지 남긴다. 추출 제공자 기록이 없는 페이지는 그렇게 적는다.
    assert ontology["stats"]["models"] == {"extractor": {"unknown": 1}, "reviewer": {"hermes": 1}, "cross": {"codex": 1}}
    assert "추출 기록 없음 1 → 검토 hermes 1 → 되검토 codex 1" in wo.render_markdown(ontology)
    assert "— 검토자 제안" in wo.render_markdown(ontology)
    assert not wo.needs_cross_review(record, page)
    del record["review"]["cross"]
    assert wo.needs_cross_review(record, page)


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
    assert "이란 → 원유 [호재·상승 요인] — 단일 발행자 · 발행자 1곳 · 출처 1개" in section
    assert "인사이트(모델 추론, 미검증): 공급 차질 우려가 유가를 지지한다는 가설. [[insight-x]]" in section
    assert wo.context_section("오늘 날씨", insights=[insight], root=tmp_path) == ""
    markdown = (tmp_path / "ontology.md").read_text(encoding="utf-8")
    assert "- 이란 → 원유 [호재·상승 요인] · 단일 발행자 · 발행자 1곳 · 출처 1개 · 근거 claim 1건 — 긴장 고조" in markdown


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


def test_context_puts_backed_relations_first_and_caps_single_source_ones():
    def relation(name, state="asserted", publishers=1, sources=1, modality="reported"):
        return {"id": name, "from": "asset:crude-oil", "to": name, "type": "positive_for", "state": state,
                "publisher_count": publishers, "source_count": sources, "modality": modality}
    ontology = {"relations": [relation("view", modality="view"), relation("single-a"), relation("multi-source", sources=3),
                              relation("corroborated", "corroborated", 2, 2), relation("single-b"), relation("single-c")]}

    shown, omitted = wo.context_relations(ontology, "asset:crude-oil")

    assert [item["id"] for item in shown] == ["corroborated", "multi-source", "single-a", "single-b"]
    assert omitted == 2
