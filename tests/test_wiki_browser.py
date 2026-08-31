from __future__ import annotations

from dashboard import wiki_browser


PAGES = [
    {
        "id": "p1",
        "title": "손실한도와 레버리지",
        "summary": "QQQ와 TQQQ를 손실한도 1% 안에서 비교한다.",
        "body": "QQQ는 기본, TQQQ는 예산을 더 크게 써야 한다.",
        "tags": ["risk", "portfolio", "leverage"],
        "status": "stable",
        "surface": "portfolio",
        "kind": "playbook",
        "confidence": 0.9,
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T01:00:00+00:00",
        "source_refs": ["conversation:001", "news:alpha"],
    },
    {
        "id": "p2",
        "title": "AI 콘솔 위키 브라우저",
        "summary": "문서 브라우저와 관련 문서를 보여준다.",
        "body": "문서 브라우저는 대화와 메모를 다시 읽게 한다.",
        "tags": ["wiki", "browser"],
        "status": "reviewed",
        "surface": "portfolio",
        "kind": "concept",
        "confidence": 0.7,
        "created_at": "2026-07-13T00:05:00+00:00",
        "updated_at": "2026-07-13T02:00:00+00:00",
        "source_refs": ["conversation:002"],
    },
    {
        "id": "p3",
        "title": "중동 재교전 시나리오",
        "summary": "유가와 달러가 같이 흔들리는 국면이다.",
        "body": "지정학 꼬리위험을 먼저 본다.",
        "tags": ["geo", "risk"],
        "status": "draft",
        "surface": "market",
        "kind": "risk",
        "confidence": 0.6,
        "created_at": "2026-07-13T00:10:00+00:00",
        "updated_at": "2026-07-13T02:30:00+00:00",
        "source_refs": ["news:middleeast", "market_event:1"],
    },
    {
        "id": "p4",
        "title": "손실한도와 레버리지 보강",
        "summary": "TQQQ는 변동성 예산을 크게 잡아야 한다.",
        "body": "현금 완충이 중요하다.",
        "tags": ["risk", "portfolio"],
        "status": "draft",
        "surface": "market",
        "kind": "playbook",
        "confidence": 0.8,
        "created_at": "2026-07-13T00:20:00+00:00",
        "updated_at": "2026-07-13T03:00:00+00:00",
        "source_refs": ["conversation:001"],
    },
]


def test_build_browser_model_prefers_selected_page_and_filters_surface():
    model = wiki_browser.build_browser_model(
        PAGES,
        selected_page_id="p1",
        query="손실",
        surface="portfolio",
        status="all",
    )

    assert model["selected_id"] == "p1"
    assert model["selected"]["id"] == "p1"
    assert model["visible_count"] == 2
    assert [page["id"] for page in model["visible"]] == ["p1", "p4"]


def test_related_pages_uses_tags_and_source_refs():
    related = wiki_browser.related_pages(PAGES[0], PAGES, limit=3)

    assert related[0]["id"] == "p4"
    assert any(page["id"] == "p2" for page in related)


def test_select_page_id_falls_back_to_first_visible_page():
    selected = wiki_browser.select_page_id(
        PAGES,
        selected_page_id="missing",
        query="AI",
        surface="portfolio",
        status="all",
    )

    assert selected == "p2"


def test_build_browser_model_related_pages_use_full_corpus_not_visible_slice():
    model = wiki_browser.build_browser_model(
        PAGES,
        selected_page_id="p1",
        query="손실",
        surface="portfolio",
        status="stable",
    )

    assert model["visible_count"] == 1
    assert model["visible"][0]["id"] == "p1"
    assert model["related"][0]["id"] == "p4"


def test_build_browser_model_groups_visible_pages_by_surface():
    model = wiki_browser.build_browser_model(PAGES, selected_page_id="p1", query="", surface="all", status="all")

    assert [group["surface"] for group in model["groups"]] == ["market", "portfolio"]
    assert [group["count"] for group in model["groups"]] == [2, 2]


def test_limit_browser_groups_keeps_counts_but_caps_rendered_pages():
    groups = wiki_browser._group_visible_pages(
        [
            {"id": f"market-{idx}", "title": f"시장 {idx}", "surface": "market"}
            for idx in range(5)
        ]
        + [
            {"id": f"portfolio-{idx}", "title": f"포트폴리오 {idx}", "surface": "portfolio"}
            for idx in range(5)
        ]
    )

    limited = wiki_browser.limit_browser_groups(groups, limit=6)

    assert [group["count"] for group in limited] == [5, 5]
    assert sum(len(group["pages"]) for group in limited) == 6
    assert [page["id"] for page in limited[0]["pages"]] == [f"market-{idx}" for idx in range(5)]
    assert [page["id"] for page in limited[1]["pages"]] == ["portfolio-0"]


def test_aliases_are_available():
    filtered = wiki_browser.filter_pages(PAGES, query="레버리지", surface="portfolio", status="all")
    picked = wiki_browser.pick_selected_page(PAGES, selected_page_id="p4", query="", surface="all", status="all")

    assert [page["id"] for page in filtered] == ["p1", "p4"]
    assert picked == "p4"


def test_build_wiki_health_model_counts_trust_and_search_state():
    health = wiki_browser.build_wiki_health_model(
        [
            {"id": "a", "verification_status": "source-backed", "source_refs": ["source:saveticker:health"], "openQuestions": ["확인할 것"]},
            {"id": "b", "verification_status": "unverified", "trust_warnings": ["원문 출처 없음"]},
        ],
        search_health={"provider": "qmd", "qmd": {"file_count": 7, "installed": True}, "fallback_available": True},
        lint={"issue_count": 1, "issues": [{"page_id": "b", "code": "source_missing_for_promoted"}]},
    )

    assert health["provider"] == "qmd"
    assert health["qmd_file_count"] == 7
    assert health["source_backed_count"] == 1
    assert health["unverified_count"] == 1
    assert health["open_question_count"] == 1
    assert health["lint_issue_count"] == 1


def test_build_wiki_health_model_reports_surface_counts_and_recommendations():
    health = wiki_browser.build_wiki_health_model(
        [
            {"id": "a", "surface": "market", "kind": "source_digest", "verification_status": "unverified", "source_refs": []},
            {"id": "b", "surface": "market", "kind": "source_digest", "verification_status": "unverified", "source_refs": []},
            {"id": "c", "surface": "market", "kind": "source_digest", "verification_status": "unverified", "source_refs": []},
            {"id": "d", "surface": "market", "kind": "source_digest", "verification_status": "unverified", "source_refs": []},
            {"id": "e", "surface": "portfolio", "kind": "note", "verification_status": "source-backed", "source_refs": ["source:1"]},
        ],
        search_health={"provider": "qmd", "qmd": {"file_count": 1, "installed": True}, "fallback_available": True},
        lint={"issue_count": 2, "issues": [{"code": "orphan_page"}, {"code": "missing_cross_ref"}]},
    )

    assert health["surface_counts"]["market"] == 4
    assert health["kind_counts"]["source_digest"] == 4
    assert any("편중" in rec["title"] for rec in health["recommendations"])
    assert any("고립" in rec["title"] or "교차 연결" in rec["title"] for rec in health["recommendations"])


def test_build_selected_evidence_model_orders_judgment_evidence_and_prompt_preview():
    model = wiki_browser.build_selected_evidence_model(
        {
            "title": "AI CAPEX 검증 규칙",
            "summary": "CAPEX는 수요와 비용을 같이 봅니다.",
            "body": "긴 본문",
            "verification_status": "source-backed",
            "source_refs": ["source:saveticker:ai-capex"],
            "openQuestions": ["전력비 영향은?"],
            "trust_warnings": [],
        },
        context_section="[위키 지식]\n- preview",
    )

    assert model["summary"] == "CAPEX는 수요와 비용을 같이 봅니다."
    assert model["judgment"] == "CAPEX는 수요와 비용을 같이 봅니다."
    assert model["evidence"] == ["source:saveticker:ai-capex"]
    assert model["verification_status"] == "source-backed"
    assert model["open_questions"] == ["전력비 영향은?"]
    assert "[위키 지식]" in model["prompt_preview"]


def test_build_selected_evidence_model_keeps_full_body_text():
    long_body = "본문 세부 " * 1400 + "[FULL_BODY_TAIL]"
    model = wiki_browser.build_selected_evidence_model(
        {
            "title": "전체 본문 보존",
            "summary": "요약",
            "body": long_body,
            "verification_status": "source-backed",
            "source_refs": ["source:saveticker:full-body"],
            "openQuestions": [],
            "trust_warnings": [],
        },
        context_section="[위키 지식]\n- preview",
    )

    assert "[FULL_BODY_TAIL]" in model["body"]
    assert len(model["body"]) > 6000


def test_promotion_guardrail_blocks_promoted_conversation_only_pages():
    blocked = wiki_browser.promotion_guardrail("stable", ["conversation:123"])
    allowed = wiki_browser.promotion_guardrail("reviewed", ["source:saveticker:abc"])

    assert blocked["allowed"] is False
    assert blocked["downgraded_to"] == "draft"
    assert "source ref" in blocked["message"]
    assert allowed["allowed"] is True


def test_internal_wiki_refs_are_not_treated_as_verified_source():
    page = {
        "title": "병합된 판단 카드",
        "summary": "내부 문서 링크만 가진 카드",
        "body": "본문",
        "source_refs": ["wiki:source-topic-ai", "<local-path>"],
        "status": "stable",
        "verification_status": "source-backed",
    }

    normalized = wiki_browser.build_selected_evidence_model(page)

    assert normalized["verification_status"] == "unverified"
    assert wiki_browser.promotion_guardrail("stable", page["source_refs"])["allowed"] is False


def test_selected_evidence_keeps_merge_archive_history():
    page = {
        "id": "merged-card",
        "title": "통합된 카드",
        "summary": "두 문서를 통합한 결과",
        "body": "통합 본문",
        "source_refs": ["https://example.com/source"],
        "merge_history": [{
            "event_id": "merge-001",
            "target_id": "merged-card",
            "source_ids": ["old-card"],
            "source_titles": ["이전 카드"],
            "reason": "중복 판단 통합",
            "synthesis": "공통 규칙으로 정리",
            "occurred_at": "2026-08-30T00:00:00+00:00",
        }],
        "merged_into": "",
        "merge_event_id": "merge-001",
    }

    model = wiki_browser.build_selected_evidence_model(page)

    assert model["merge_event_id"] == "merge-001"
    assert model["merge_history"][0]["source_ids"] == ["old-card"]
