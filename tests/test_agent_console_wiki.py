"""agent_console — 위키(upsert·검색·병합/분할·자동 큐레이션·린트) 테스트."""
from __future__ import annotations

import json


from agent_console_support import _isolate

def test_wiki_capture_and_context_section(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.capture_from_chat(
        "손실한도 1% 안에서 QQQ와 TQQQ를 비교해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
        surface="portfolio",
        title="손실한도와 레버리지",
        status="reviewed",
        kind="playbook",
        tags=["risk", "portfolio"],
        source_refs=["conversation:001"],
    )
    pages = wiki.list_pages(query="손실한도", surface="portfolio")
    section = wiki.build_context_section(query="손실한도", surface="portfolio", limit=4)

    assert page["title"] == "손실한도와 레버리지"
    assert pages and pages[0]["title"] == "손실한도와 레버리지"
    assert "[위키 지식]" in section
    assert "손실한도와 레버리지" in section


def test_wiki_upsert_page_persists_links(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "링크 출발 페이지",
        "summary": "출발 요약",
        "body": "출발 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [target["id"], target["id"], ""],
    })

    assert source["links"] == [target["id"]]


def test_wiki_upsert_page_round_trips_evidence_metadata_and_renders_context(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    saved = wiki.upsert_page({
        "title": "AI CAPEX 근거 카드",
        "summary": "AI CAPEX 수요와 비용을 함께 추적합니다.",
        "body": "공식 자료와 가격 반응을 교차확인합니다.",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "source_refs": ["https://example.com/ai-capex"],
        "evidence_ids": ["evidence-demand", "evidence-cost"],
        "conflicting_evidence_ids": ["evidence-margin-risk"],
        "staleness_policy": "refresh_after_12h",
        "answer_hints": [
            "커뮤니티 단독 신호는 공식 자료와 교차확인합니다.",
            "최신 evidence freshness를 먼저 확인합니다.",
        ],
    })

    fetched = wiki.get_page(saved["id"])
    section = wiki.build_context_section(
        query="AI CAPEX",
        surface="market",
        pages=[fetched],
    )

    assert fetched["evidence_ids"] == ["evidence-demand", "evidence-cost"]
    assert fetched["conflicting_evidence_ids"] == ["evidence-margin-risk"]
    assert fetched["staleness_policy"] == "refresh_after_12h"
    assert fetched["answer_hints"] == [
        "커뮤니티 단독 신호는 공식 자료와 교차확인합니다.",
        "최신 evidence freshness를 먼저 확인합니다.",
    ]
    assert "근거: 2개 (상충 1개)" in section
    assert "갱신 정책: refresh_after_12h" in section
    assert "답변 힌트: 커뮤니티 단독 신호는 공식 자료와 교차확인합니다." in section


def test_wiki_get_page_and_list_pages_compute_backlinks(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "백링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "백링크 출발 페이지",
        "summary": "출발 요약",
        "body": "출발 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [target["id"]],
    })

    fetched_target = wiki.get_page(target["id"])
    assert fetched_target["backlinks"] == [source["id"]]

    listed = {page["id"]: page for page in wiki.list_pages(surface="market", limit=10)}
    assert listed[target["id"]]["backlinks"] == [source["id"]]
    assert listed[source["id"]]["links"] == [target["id"]]


def test_wiki_conversation_only_pages_stay_unverified_draft(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.capture_from_chat(
        "손실한도 규칙을 reviewed 로 저장해줘",
        "검증이라는 단어가 있어도 이 답변은 대화에서 나온 규칙일 뿐입니다.",
        surface="portfolio",
        title="대화 기반 손실한도 규칙",
        status="reviewed",
        kind="playbook",
        tags=["risk", "portfolio"],
        source_refs=["conversation:abc123"],
    )
    section = wiki.build_context_section(query="손실한도", surface="portfolio", limit=4)

    assert page["status"] == "draft"
    assert page["verification_status"] == "unverified"
    assert any("원문 출처 없음" in warning for warning in page["trust_warnings"])
    assert "검증: unverified" in section
    assert "원문 출처 없음" in section


def test_wiki_rebuild_artifacts_writes_index_log_open_questions_and_lint(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    wiki.upsert_page({
        "title": "AI CAPEX 검증 규칙",
        "summary": "AI CAPEX 뉴스는 마진과 현금흐름을 함께 본다.",
        "body": "CAPEX 확대는 수요 신호이면서 비용 압박일 수 있습니다.",
        "surface": "market",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["ai", "capex"],
        "source_refs": ["source:saveticker:ai-capex"],
        "openQuestions": ["전력비 상승이 마진을 얼마나 압박하는가?"],
    })

    result = wiki.rebuild_artifacts()
    files = result["files"]

    assert result["ok"] is True
    assert result["page_count"] == 1
    assert set(files) == {"index.md", "log.md", "open-questions.md", "lint.md", "schema.md"}
    assert "AI CAPEX 검증 규칙" in (wiki.wiki_artifacts_dir() / "index.md").read_text(encoding="utf-8")
    assert "## [" in (wiki.wiki_artifacts_dir() / "log.md").read_text(encoding="utf-8")
    assert (wiki.wiki_artifacts_dir() / "schema.md").read_text(encoding="utf-8")
    assert "전력비 상승" in (wiki.wiki_artifacts_dir() / "open-questions.md").read_text(encoding="utf-8")
    assert "source_missing_for_promoted" not in (wiki.wiki_artifacts_dir() / "lint.md").read_text(encoding="utf-8")


def test_wiki_lint_flags_source_less_promoted_pages_and_open_questions(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "bad1",
            "title": "출처 없는 stable 규칙",
            "status": "stable",
            "verification_status": "unverified",
            "source_refs": [],
            "openQuestions": ["어떤 원문으로 검증했나?"],
            "surface": "portfolio",
            "kind": "playbook",
        }
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "source_missing_for_promoted" in codes
    assert "open_questions_present" in codes
    assert result["ok"] is False


def test_wiki_lint_flags_orphan_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "solo1",
            "title": "고립된 페이지",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "links": [],
            "backlinks": [],
        }
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "orphan_page" in codes


def test_wiki_lint_flags_missing_cross_ref(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "tickerA",
            "title": "NVDA 메모 A",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
        {
            "id": "tickerB",
            "title": "NVDA 메모 B",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_cross_ref" in codes
    cross_ref_issues = [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]
    assert len(cross_ref_issues) == 1


def test_wiki_lint_skips_missing_cross_ref_when_linked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "tickerC",
            "title": "NVDA 메모 C",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": ["tickerD"],
            "backlinks": [],
        },
        {
            "id": "tickerD",
            "title": "NVDA 메모 D",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": ["tickerC"],
        },
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_cross_ref" not in codes


def test_wiki_lint_does_not_suggest_source_digest_cross_links(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "source-topic",
            "title": "소스 주제",
            "status": "draft",
            "source_refs": ["https://example.com/article"],
            "surface": "market",
            "kind": "source_digest",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
        {
            "id": "source-ticker",
            "title": "소스 티커",
            "status": "draft",
            "source_refs": ["https://example.com/article"],
            "surface": "ticker",
            "kind": "source_digest",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
    ])

    assert not [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]


def test_wiki_lint_does_not_explode_on_shared_placeholder_ref(monkeypatch, tmp_path):
    """shared_memory 의 로컬 경로 리댁션 자리표시자("<local-path>")를 진짜 공유 출처로
    취급해 그룹핑하면 그걸 공유하는 모든 페이지 쌍마다 missing_cross_ref 를 만든다.
    실측(2026-08-30): 라이브 위키에서 367개 페이지가 이 자리표시자를 공유해 린트
    이슈가 67,432개(거의 다 이 조합)로 폭증 — 헬스체크 LLM 프롬프트·lint.md 무의미해짐."""
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    pages = [
        {
            "id": f"placeholder-{i}", "title": f"플레이스홀더 {i}", "status": "draft",
            "verification_status": "unverified", "source_refs": ["<local-path>"],
            "surface": "market", "kind": "note", "tags": ["wiki"],
            "links": [], "backlinks": [],
        }
        for i in range(50)
    ]

    result = wiki.lint_pages(pages)

    cross_ref_issues = [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]
    assert not cross_ref_issues, f"자리표시자 공유로 {len(cross_ref_issues)}건의 무의미한 교차링크 제안 생성됨"


def test_wiki_lint_treats_shared_source_ref_as_provenance_not_relation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    pages = [
        {
            "id": f"same-source-{i}", "title": f"같은 원문 {i}", "status": "draft",
            "source_refs": ["https://example.com/shared-article"],
            "surface": "market", "kind": "note", "tags": ["wiki"],
            "links": [], "backlinks": [],
        }
        for i in range(2)
    ]

    result = wiki.lint_pages(pages)

    assert not [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]


def test_wiki_lint_caps_cross_ref_group_size(monkeypatch, tmp_path):
    """진짜 출처라도 그룹이 지나치게 크면(실질적 병합 제안 가치 없음·O(n²) 폭증 위험)
    pairwise 제안을 통째로 스킵해야 한다 — 자리표시자 필터와 별개의 일반 안전장치."""
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    pages = [
        {
            "id": f"big-group-{i}", "title": f"큰 그룹 페이지 {i}", "status": "draft",
            "verification_status": "unverified",
            "source_refs": ["https://example.com/shared-article"],
            "surface": "market", "kind": "note", "tags": ["wiki"],
            "links": [], "backlinks": [],
        }
        for i in range(40)
    ]

    result = wiki.lint_pages(pages)

    cross_ref_issues = [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]
    assert not cross_ref_issues, f"40개짜리 그룹인데 캡 없이 {len(cross_ref_issues)}건 생성됨"


def test_wiki_search_health_reports_qmd_or_fallback(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    monkeypatch.setattr(
        wiki.qmd_search,
        "health",
        lambda: {"enabled": True, "installed": False, "provider": "qmd", "file_count": 0},
        raising=False,
    )

    health = wiki.search_health()

    assert health["provider"] == "fallback"
    assert health["qmd"]["installed"] is False
    assert health["fallback_available"] is True


def test_wiki_context_section_includes_search_and_trust_metadata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "qmd 검색 메타 규칙",
        "summary": "qmd 검색 결과도 검증 상태와 출처를 함께 보여준다.",
        "body": "검색 점수만으로 신뢰하지 않는다.",
        "surface": "market",
        "kind": "playbook",
        "status": "reviewed",
        "source_refs": ["source:saveticker:qmd-meta"],
    })

    class FakeQmd:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def status():
            return {"installed": True}

        @staticmethod
        def export_pages(pages):
            return {"ok": True, "files": []}

        @staticmethod
        def search(query, *, limit=10, surface="all", status="all"):
            return [{"page_id": page["id"], "summary": "qmd snippet", "score": 0.93}]

    monkeypatch.setattr(wiki, "qmd_search", FakeQmd, raising=False)

    section = wiki.build_context_section(query="검색 메타", surface="market", limit=2)

    assert "검색: qmd" in section
    assert "score=0.93" in section
    assert "검증: source-backed" in section
    assert "출처: source:saveticker:qmd-meta" in section


def test_wiki_internal_refs_are_not_treated_as_verified_source(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    assert wiki.verification_status_for(["conversation:abc", "wiki:source-ticker-nvda"]) == "unverified"
    assert wiki.verification_status_for(["<local-path>"]) == "unverified"
    assert wiki.verification_status_for(["source:saveticker:abc"]) == "source-backed"


def test_wiki_normalizes_untrusted_shapes_and_redacts_secret_like_values(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "입력 경계 점검",
        "summary": "token=summary-secret",
        "body": '본문 api_key=body-secret {"password": "json-secret"}',
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": "risk",
        "links": "related-page",
        "source_refs": "https://example.com/report?token=url-secret",
        "messages": "잘못된 배열",
        "confidence": "nan",
        "useCount": "not-a-number",
    })

    assert "summary-secret" not in page["summary"]
    assert "body-secret" not in page["body"]
    assert "json-secret" not in page["body"]
    assert "url-secret" not in page["source_refs"][0]
    assert "risk" in page["tags"]
    assert page["links"] == ["related-page"]
    assert page["messages"] == []
    assert page["confidence"] == 0.5
    assert page["useCount"] == 0


def test_merge_pages_archives_sources_and_preserves_metadata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "NVDA 판단",
        "summary": "target",
        "body": "target body",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["source:saveticker:target"],
        "evidence_ids": ["e-target"],
        "conflicting_evidence_ids": ["e-conflict-target"],
        "staleness_policy": "refresh_after_12h",
        "answer_hints": ["target hint"],
    })
    source = wiki.upsert_page({
        "title": "NVDA 판단 중복",
        "summary": "source",
        "body": "source body",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["https://example.com/source"],
        "evidence_ids": ["e-source"],
        "conflicting_evidence_ids": ["e-conflict-source"],
        "staleness_policy": "refresh_after_24h",
        "answer_hints": ["source hint"],
    })

    result = wiki._merge_pages(
        [source["id"]], target["id"], "합성 근거", reason="중복 판단 병합"
    )

    assert result["action"] == "merge"
    assert result["archived"] == [source["id"]]
    assert result["deleted"] == []
    archived = wiki.get_page(source["id"])
    merged = wiki.get_page(target["id"])
    assert archived["status"] == "archived"
    assert target["id"] in archived["links"]
    assert f"merged_into:{target['id']}" in archived["tags"]
    assert "source body" in archived["body"]
    assert {"e-target", "e-source"} <= set(merged["evidence_ids"])
    assert {"e-conflict-target", "e-conflict-source"} <= set(merged["conflicting_evidence_ids"])
    assert merged["staleness_policy"] == "refresh_after_12h"
    assert {"target hint", "source hint"} <= set(merged["answer_hints"])
    assert merged["merge_history"]
    assert merged["merge_history"][-1]["source_ids"] == [source["id"]]
    wiki.rebuild_artifacts()
    log_text = (wiki.wiki_artifacts_dir() / "log.md").read_text(encoding="utf-8")
    assert result["merge_event_id"] in log_text
    assert source["id"] in log_text


def test_merge_pages_rejects_surface_mismatch(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "시장 메모", "summary": "t", "body": "t", "surface": "market",
        "kind": "note", "status": "draft", "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "포트폴리오 메모", "summary": "s", "body": "s", "surface": "portfolio",
        "kind": "note", "status": "draft", "source_refs": [],
    })

    assert wiki._merge_pages([source["id"]], target["id"], "cross surface") is None
    assert wiki.get_page(source["id"])["status"] == "draft"


def test_auto_curate_merge_rejects_cross_surface_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "시장 판단", "summary": "t", "body": "t", "surface": "market",
        "kind": "note", "status": "draft", "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "포트폴리오 판단", "summary": "s", "body": "s", "surface": "portfolio",
        "kind": "note", "status": "draft", "source_refs": [],
    })

    result = wiki.auto_curate_from_chat(
        "두 판단을 같은 기준으로 합쳐줘",
        "중복 내용을 정리했습니다. 규칙과 기준을 합칩니다.\n- surface를 검증합니다.\n- 출처와 예외를 확인합니다.\n- 병합 뒤 원문과 메타데이터를 보존합니다.\n이 판단은 재사용 가능한 위키 기준으로 저장합니다.",
        surface="market",
        pack={"focus": []},
        history=[],
        llm=lambda prompt: (
            '{"action":"merge","target_page_id":"' + target["id"] + '",'
            '"source_page_ids":["' + source["id"] + '"],"body":"합성"}'
        ),
    )

    assert result["ok"] is False
    assert result["action"] == "skipped_injection_guard"
    assert wiki.get_page(source["id"])["status"] == "draft"


def test_parse_curation_plan_rejects_string_array_fields():
    from agent_console import wiki

    plan = wiki._parse_curation_plan(
        '{"action":"merge","source_page_ids":"source-1","tags":"risk"}'
    )

    assert plan["source_page_ids"] == []
    assert plan["tags"] == []


def test_parse_curation_plan_extracts_one_valid_json_object_without_greedy_brace_match():
    from agent_console import wiki

    plan = wiki._parse_curation_plan(
        '로그 {"not_action":"ignore"} 결과 '
        '{"action":"create","title":"유효한 카드","body":"본문","kind":"note"}'
    )

    assert plan["action"] == "create"
    assert plan["title"] == "유효한 카드"


def test_wiki_index_md_shows_link_marker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    first = wiki.upsert_page({
        "title": "링크 원본 페이지",
        "summary": "원본 요약",
        "body": "원본 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    target = wiki.upsert_page({
        "title": "링크 대상 페이지",
        "summary": "대상 요약",
        "body": "대상 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [first["id"]],
    })

    wiki.rebuild_artifacts()
    index_text = (wiki.wiki_artifacts_dir() / "index.md").read_text(encoding="utf-8")

    assert f"[[{target['id']}|링크 대상 페이지]]" in index_text
    assert "🔗1" in index_text


def test_wiki_context_section_includes_related_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    related = wiki.upsert_page({
        "title": "연관 위키 페이지",
        "summary": "연관 요약",
        "body": "연관 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
    })
    wiki.upsert_page({
        "title": "중심 위키 페이지",
        "summary": "중심 요약 텍스트",
        "body": "중심 본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "tags": ["wiki"],
        "source_refs": [],
        "links": [related["id"]],
    })

    section = wiki.build_context_section(query="중심 위키 페이지", surface="market", limit=4)

    assert "관련: [[연관 위키 페이지]]" in section


def test_wiki_list_pages_prefers_qmd_search_when_available(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    fallback = wiki.capture_from_chat(
        "레버리지 원칙",
        "로컬 점수 검색으로 잡히는 기존 페이지입니다.",
        surface="portfolio",
        title="기존 로컬 후보",
        status="reviewed",
        kind="playbook",
    )
    qmd_target = wiki.capture_from_chat(
        "손실한도 1%",
        "qmd 의미 검색으로 먼저 잡혀야 하는 페이지입니다.",
        surface="portfolio",
        title="qmd 우선 후보",
        status="reviewed",
        kind="playbook",
    )
    calls = []

    class FakeQmd:
        @staticmethod
        def search(query, *, limit=10, surface="all", status="all"):
            calls.append(("search", query, limit, surface, status))
            return [
                {
                    "provider": "qmd",
                    "page_id": qmd_target["id"],
                    "title": "qmd 우선 후보",
                    "summary": "qmd hit",
                    "score": 0.98,
                }
            ]

    monkeypatch.setattr(wiki, "qmd_search", FakeQmd, raising=False)

    pages = wiki.list_pages(query="레버리지", surface="portfolio", limit=3)
    section = wiki.build_context_section(query="레버리지", surface="portfolio", limit=3)

    assert pages[0]["id"] == qmd_target["id"]
    assert pages[0]["search_provider"] == "qmd"
    # QMD hit가 있으면 전체 원장을 다시 스캔하지 않고 의미 검색 결과를
    # authoritative 결과로 사용한다. fallback 페이지는 QMD 장애/무결과 때만 쓴다.
    assert fallback["id"] not in {page["id"] for page in pages}
    assert {page["id"] for page in pages} == {qmd_target["id"]}
    assert not any(call[0] == "export" for call in calls)
    assert "[위키 지식]" in section
    assert "qmd 우선 후보" in section


def test_wiki_capture_from_chat_retains_long_body(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    answer = "규칙 설명 " + ("세부 내용 " * 1400) + "[LONG_BODY_TAIL]"

    page = wiki.capture_from_chat(
        "긴 위키 문서를 남겨줘",
        answer,
        surface="portfolio",
        title="긴 본문 보존",
        status="reviewed",
        kind="playbook",
    )

    assert "[LONG_BODY_TAIL]" in page["body"]
    assert len(page["body"]) > 6000


def test_wiki_auto_curate_llm_prompt_and_saved_body_keep_long_content(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    question = "LLM 위키 문서를 길게 만들어줘"
    answer = "규칙과 예외를 자세히 정리합니다. " + ("본문 확장 " * 1200) + "[AUTO_CURATE_TAIL]"
    observed: dict[str, str] = {}

    def fake_llm(prompt: str) -> str:
        observed["prompt"] = prompt
        assert "[AUTO_CURATE_TAIL]" in prompt
        assert "body는 요약문이 아니라 재사용 가능한 위키 문서여야 한다." in prompt
        assert "체크리스트" in prompt
        return json.dumps(
            {
                "action": "create",
                "title": "긴 자동 위키",
                "summary": "긴 자동 위키 요약",
                "body": "섹션 1\n- 규칙\n- 예외\n\n섹션 2\n- 체크리스트\n- 복구 절차\n\n"
                + ("본문 확장 " * 1200)
                + "[AUTO_PLAN_TAIL]",
                "kind": "playbook",
                "status": "reviewed",
                "tags": ["wiki", "long"],
                "source_refs": [],
                "links": [],
                "target_id": "",
                "confidence": 0.91,
                "reason": "long body capture",
                "page_feedback": {},
            },
            ensure_ascii=False,
        )

    result = wiki.auto_curate_from_chat(
        question,
        answer,
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert observed["prompt"]
    assert result and result["ok"] is True
    assert "[AUTO_PLAN_TAIL]" in result["page"]["body"]
    assert len(result["page"]["body"]) > 6000


def test_auto_curation_prompt_lists_all_chat_kind_options():
    """LLM이 예시 하나(playbook)만 보고 kind를 고정하지 않도록, 선택 가능한 kind를 전부 명시해야 한다."""
    from agent_console import wiki

    prompt = wiki._build_auto_curation_prompt(
        question="질문", answer="답변", surface="market",
        candidates=[], pack={}, history=[],
    )

    for kind in ("playbook", "risk", "decision", "concept", "note"):
        assert kind in prompt
    assert "source_digest" in prompt  # "이 경로에서 쓰지 않는다" 로 명시적으로 배제됨


def test_wiki_track_page_usage_increments_use_count_and_last_used(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "사용량 테스트",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    wiki.track_page_usage(page["id"], "손실한도 질문")
    wiki.track_page_usage(page["id"], "레버리지 질문")

    fetched = wiki.get_page(page["id"])
    assert fetched["useCount"] == 2
    assert fetched["lastQuery"] == "레버리지 질문"
    assert fetched["lastUsedAt"]


def test_wiki_track_page_usage_is_atomic_under_concurrency_and_keeps_metadata(monkeypatch, tmp_path):
    """동시 track_page_usage — 증분이 소실되지 않고(종전 get→upsert lost update), evidence_ids
    등 페이지 메타는 그대로 유지된다."""
    import threading
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "동시 사용량", "summary": "요약", "body": "본문", "surface": "portfolio",
        "kind": "note", "status": "draft", "source_refs": [],
        "evidence_ids": ["ev-1", "ev-2"], "answer_hints": ["힌트"],
    })
    barrier = threading.Barrier(8)

    def w(i):
        barrier.wait()
        for _ in range(5):
            wiki.track_page_usage(page["id"], f"q{i}")
    ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    fetched = wiki.get_page(page["id"])
    assert fetched["useCount"] == 40
    assert fetched["evidence_ids"] == ["ev-1", "ev-2"] and fetched["answer_hints"] == ["힌트"]
    assert wiki.track_page_usage("no-such-page", "q") is None     # 없는 페이지는 무시


def test_wiki_list_unused_pages_flags_old_and_never_used_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from datetime import datetime, timedelta, timezone

    from agent_console import wiki

    def _iso(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")

    recent = wiki.upsert_page({
        "title": "최근 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "created_at": _iso(1),
    })
    old_unused = wiki.upsert_page({
        "title": "오래되고 미사용",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "created_at": _iso(45),
    })

    unused_ids = {p["id"] for p in wiki.list_unused_pages(days=30)}

    assert old_unused["id"] in unused_ids
    assert recent["id"] not in unused_ids


def test_wiki_lint_flags_zero_usage_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "unused1",
            "title": "미사용 페이지",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "createdAt": "2000-01-01T00:00:00+00:00",
        }
    ])

    codes = {issue["code"] for issue in result["issues"]}
    assert "zero_usage" in codes


def test_wiki_context_section_includes_unused_page_count(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    wiki.upsert_page({
        "title": "장기 방치 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "created_at": "2000-01-01T00:00:00+00:00",
    })

    section = wiki._build_wiki_context_section()
    assert "미사용(30일+): 1" in section


def test_wiki_auto_curate_skips_transient_acknowledgements(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    assert wiki.auto_curate_from_chat(
        "진행해줘",
        "ㅇㅇ 진행해",
        surface="portfolio",
        llm=None,
        pack={"focus": []},
        history=[],
    ) is None


def test_wiki_auto_curate_from_chat_updates_existing_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    existing = wiki.capture_from_chat(
        "손실한도 1% 기준으로 QQQ와 TQQQ를 비교해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
        surface="portfolio",
        title="손실한도와 레버리지",
        status="draft",
        kind="playbook",
        tags=["risk", "portfolio"],
        source_refs=["conversation:001"],
    )

    def fake_llm(prompt: str) -> str:
        assert "JSON object" in prompt
        return (
            '{"action":"update","title":"손실한도와 레버리지","summary":"손실한도 1%에서는 QQQ를 기본으로 두고 TQQQ는 예산을 더 크게 봅니다.",'
            '"body":"손실한도 1%에서는 QQQ를 기본으로 두고 TQQQ는 변동성 예산을 더 크게 잡습니다.\\n- QQQ 기본\\n- TQQQ는 보수적\\n- 현금 완충 필요",'
            '"kind":"playbook","status":"reviewed","tags":["risk","portfolio","leverage"],'
            '"source_refs":["conversation:002"],"target_id":"'
            + existing["id"]
            + '","confidence":0.91,"reason":"merge with existing"}'
        )

    saved = wiki.auto_curate_from_chat(
        "손실한도 1% 안에서 QQQ와 TQQQ를 다시 정리해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.\n- QQQ 기본\n- TQQQ는 보수적\n- 현금 완충 필요",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": ["포트폴리오"]},
        history=[{"role": "user", "message": "손실한도 1%"}],
    )

    assert saved is not None
    assert saved["page"]["id"] == existing["id"]
    assert "현금 완충 필요" in saved["page"]["body"]
    assert "leverage" in saved["page"]["tags"]


def test_wiki_auto_curate_llm_plan_links_are_persisted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    related = wiki.upsert_page({
        "title": "레버리지 손실한도 기준",
        "summary": "레버리지 상품의 손실한도 원칙",
        "body": "TQQQ 같은 레버리지 상품은 손실한도를 더 좁게 잡는다.",
        "surface": "market",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed"],
    })

    def fake_llm(prompt: str) -> str:
        assert "links" in prompt
        return (
            '{"action":"create","title":"현금 비중과 변동성 예산","summary":"현금 비중은 변동성 예산과 함께 본다.",'
            '"body":"변동성이 커지면 현금 비중을 늘린다.\\n- 변동성 지표 확인\\n- 현금 20% 하한",'
            '"kind":"playbook","status":"reviewed","tags":["risk","portfolio"],'
            '"source_refs":["conversation:003"],"links":["' + related["id"] + '"],'
            '"target_id":"","confidence":0.8,"reason":"related to leverage loss limit"}'
        )

    saved = wiki.auto_curate_from_chat(
        "현금 비중 기준은 변동성 예산과 어떻게 맞춰?",
        "변동성이 커지면 현금 비중을 늘린다.\n- 변동성 지표 확인\n- 현금 20% 하한\n- 손실 한도 검증 필요",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": ["포트폴리오"]},
        history=[],
    )

    assert saved is not None
    assert related["id"] in saved["page"]["links"]

    backfilled = wiki.get_page(related["id"])
    assert saved["page"]["id"] in backfilled["backlinks"]


def test_wiki_auto_curate_llm_update_with_blank_target_id_falls_back_to_matched_candidate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    existing = wiki.upsert_page({
        "title": "변동성 예산 원칙 A",
        "summary": "변동성 예산 원칙 A 요약",
        "body": "변동성 예산 원칙 A 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed-a"],
    })

    def fake_llm(prompt: str) -> str:
        return (
            '{"action":"update","title":"변동성 예산 원칙 A","summary":"업데이트된 요약",'
            '"body":"변동성이 커지면 현금 비중을 늘린다.\\n- 변동성 지표 확인\\n- 현금 20% 하한\\n- 손실 한도 검증 필요",'
            '"kind":"playbook","status":"reviewed","tags":["risk","portfolio"],'
            '"source_refs":["conversation:updated"],"links":[],"target_id":"","confidence":0.8,'
            '"reason":"blank target_id should still fall back to matched candidate"}'
        )

    saved = wiki.auto_curate_from_chat(
        "변동성 예산 원칙을 다시 정리해줘",
        "변동성이 커지면 현금 비중을 늘린다.\n- 변동성 지표 확인\n- 현금 20% 하한\n- 손실 한도 검증 필요",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert saved is not None
    assert saved["page"]["id"] == existing["id"]


def test_wiki_auto_curate_heuristic_auto_links_related_candidate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    first = wiki.upsert_page({
        "title": "변동성 예산 원칙 A",
        "summary": "변동성 예산 원칙 A 요약",
        "body": "변동성 예산 원칙 A 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed-a"],
    })
    second = wiki.upsert_page({
        "title": "변동성 예산 원칙 B",
        "summary": "변동성 예산 원칙 B 요약",
        "body": "변동성 예산 원칙 B 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["risk", "portfolio"],
        "source_refs": ["conversation:seed-b"],
    })

    saved = wiki.auto_curate_from_chat(
        "변동성 예산 원칙을 다시 정리해줘",
        "변동성이 커지면 현금 비중을 늘린다.\n- 변동성 지표 확인\n- 현금 20% 하한\n- 손실 한도 검증 필요",
        surface="portfolio",
        llm=None,
        pack={"focus": []},
        history=[],
    )

    assert saved is not None
    target_id = saved["page"]["id"]
    assert target_id in {first["id"], second["id"]}
    other_id = second["id"] if target_id == first["id"] else first["id"]
    assert other_id in saved["page"]["links"]
    assert target_id not in saved["page"]["links"]


def test_wiki_api_routes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()

    capture = client.post(
        "/api/wiki/capture",
        json={
            "question": "손실한도 1% 기준으로 QQQ와 TQQQ를 비교해줘",
            "answer": "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
            "surface": "portfolio",
            "title": "손실한도와 레버리지",
            "status": "reviewed",
            "kind": "playbook",
            "tags": ["risk", "portfolio"],
        },
    )
    assert capture.status_code == 200
    page_id = capture.get_json()["page"]["id"]

    got = client.get(f"/api/wiki/pages/{page_id}")
    listed = client.get("/api/wiki/pages?query=손실한도&surface=portfolio&status=all&limit=10")

    assert got.status_code == 200
    assert got.get_json()["page"]["title"] == "손실한도와 레버리지"
    assert listed.status_code == 200
    assert listed.get_json()["pages"][0]["title"] == "손실한도와 레버리지"


def test_wiki_parse_curation_plan_handles_delete_action():
    from agent_console import wiki

    plan = wiki._parse_curation_plan(
        '{"action":"delete","target_id":"abc123","reason":"stale and superseded"}'
    )

    assert plan is not None
    assert plan["action"] == "delete"
    assert plan["target_id"] == "abc123"


def test_wiki_auto_curate_llm_delete_action_removes_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WIKI_CURATION_AUTO_APPLY", "1")   # 즉시 적용 모드(기본은 승인 대기)

    from agent_console import wiki

    existing = wiki.upsert_page({
        "title": "오래된 임시 메모",
        "summary": "더 이상 유효하지 않음",
        "body": "이 페이지는 삭제 대상 후보입니다.",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "tags": ["misc"],
        "source_refs": [],
    })

    def fake_llm(prompt: str) -> str:
        assert "delete" in prompt
        return '{"action":"delete","target_id":"' + existing["id"] + '","reason":"stale and superseded"}'

    result = wiki.auto_curate_from_chat(
        "위키 페이지 삭제 기준을 정리해줘. 오래된 메모는 지워도 될까?",
        "네, 아래 기준에 맞으면 삭제합니다.\n- 30일 이상 갱신 안 됨\n- 최신 리포트와 모순\n- 중복 내용\n검증 결과 이 페이지는 삭제 대상입니다.",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert result is not None
    assert result["ok"] is True
    assert result["action"] == "delete"
    assert result["page_id"] == existing["id"]
    assert wiki.get_page(existing["id"]) is None


def test_wiki_auto_curate_llm_delete_without_target_id_is_noop(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WIKI_CURATION_AUTO_APPLY", "1")   # 즉시 적용 모드(기본은 승인 대기)

    from agent_console import wiki

    def fake_llm(prompt: str) -> str:
        return '{"action":"delete","target_id":"","reason":"no target"}'

    result = wiki.auto_curate_from_chat(
        "위키 페이지 삭제 기준을 정리해줘. 오래된 메모는 지워도 될까?",
        "네, 아래 기준에 맞으면 삭제합니다.\n- 30일 이상 갱신 안 됨\n- 최신 리포트와 모순\n- 중복 내용\n검증 결과 이 페이지는 삭제 대상입니다.",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert result is None


def test_wiki_auto_curate_cannot_delete_source_backed_candidate_without_explicit_target(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    existing = wiki.upsert_page({
        "title": "검증된 시장 메모",
        "summary": "공식 출처 기반 메모",
        "body": "원문을 보존해야 하는 문서입니다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": ["source:saveticker:verified"],
    })

    result = wiki.auto_curate_from_chat(
        "위키 페이지 삭제 기준을 정리해줘. 오래된 메모는 지워도 될까?",
        "네, 아래 기준에 맞으면 삭제합니다.\n- 30일 이상 갱신 안 됨\n- 최신 리포트와 모순\n- 중복 내용\n검증 결과 이 페이지는 삭제 대상입니다.",
        surface="market",
        llm=lambda _prompt: '{"action":"delete","target_id":"","reason":"stale"}',
        pack={"focus": []},
        history=[],
    )

    assert result["action"] == "skipped_injection_guard"
    assert wiki.get_page(existing["id"])["status"] == "draft"


def test_merge_pages_combines_sources_and_archives_them(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "NVDA 실적 메모",
        "summary": "NVDA Q2 실적 요약",
        "body": "NVDA Q2 매출은 예상치를 상회했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "NVDA 실적 메모 (중복)",
        "summary": "NVDA Q2 실적 요약 중복본",
        "body": "NVDA 데이터센터 매출이 급증했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    result = wiki._merge_pages([source["id"]], target["id"], "두 메모는 같은 NVDA Q2 실적을 다룬다.")

    assert result["action"] == "merge"
    assert result["target"] == target["id"]
    assert result["archived"] == [source["id"]]
    assert result["deleted"] == []

    assert wiki.get_page(source["id"])["status"] == "archived"
    merged = wiki.get_page(target["id"])
    assert "데이터센터 매출" in merged["body"]
    assert "두 메모는 같은 NVDA Q2 실적을 다룬다" in merged["body"]
    assert f"merged_from:{source['id']}" in merged["tags"]


def test_split_page_creates_new_pages_and_archives_source(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    source = wiki.upsert_page({
        "title": "NVDA 사업 전반",
        "summary": "NVDA AI와 데이터센터를 함께 다룬 메모",
        "body": "AI 매출과 데이터센터 매출이 모두 성장했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    result = wiki._split_page(
        source["id"],
        ["NVDA AI 매출", "NVDA 데이터센터 매출"],
        ["AI 매출 관련 내용입니다.", "데이터센터 매출 관련 내용입니다."],
    )

    assert result["action"] == "split"
    assert result["source"] == source["id"]
    assert len(result["created"]) == 2

    reloaded_source = wiki.get_page(source["id"])
    assert reloaded_source["status"] == "archived"
    for new_id in result["created"]:
        assert f"split_into:{new_id}" in reloaded_source["tags"]

    first_id, second_id = result["created"]
    first_page = wiki.get_page(first_id)
    second_page = wiki.get_page(second_id)
    assert first_page["title"] == "NVDA AI 매출"
    assert "AI 매출 관련 내용입니다." in first_page["body"]
    assert second_id in first_page["links"]
    assert first_id in second_page["links"]


def test_wiki_upsert_page_auto_splits_overlong_body(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    body = "\n\n".join([
        "## 개요\n" + ("개요 문장 " * 900),
        "## 리스크\n" + ("리스크 문장 " * 900),
        "## 결론\n" + ("결론 문장 " * 900) + " [END_MARKER]",
    ])

    saved = wiki.upsert_page({
        "title": "긴 위키 문서",
        "summary": "길이가 긴 문서는 세부 문서로 분리합니다.",
        "body": body,
        "surface": "market",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["wiki", "split"],
        "source_refs": ["https://example.com/long"],
        "evidence_ids": ["e-long"],
        "conflicting_evidence_ids": ["e-conflict"],
        "staleness_policy": "refresh_after_12h",
        "answer_hints": ["본문 근거를 유지합니다."],
    })

    split_into = [tag.split(":", 1)[1] for tag in saved["tags"] if str(tag).startswith("split_into:")]
    assert split_into, "긴 문서는 자식 문서가 생성되어야 합니다."
    assert saved["links"] == split_into
    assert "[END_MARKER]" not in saved["body"]
    assert "세부 문서" in saved["body"] or "분리" in saved["body"]
    assert saved["evidence_ids"] == ["e-long"]
    assert saved["conflicting_evidence_ids"] == ["e-conflict"]

    child_bodies = []
    for child_id in split_into:
        child = wiki.get_page(child_id)
        assert child is not None
        assert f"split_from:{saved['id']}" in child["tags"]
        assert saved["id"] in child["links"]
        assert child["evidence_ids"] == ["e-long"]
        assert child["conflicting_evidence_ids"] == ["e-conflict"]
        child_bodies.append(child["body"])

    assert any("[END_MARKER]" in body_text for body_text in child_bodies)
    assert sum(len(body_text) for body_text in child_bodies) >= len(body) - 200


def test_wiki_auto_curate_llm_merge_action_merges_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WIKI_CURATION_AUTO_APPLY", "1")   # 즉시 적용 모드(기본은 승인 대기)

    from agent_console import wiki

    target = wiki.upsert_page({
        "title": "NVDA 실적 메모",
        "summary": "NVDA Q2 실적 요약",
        "body": "NVDA Q2 매출은 예상치를 상회했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })
    source = wiki.upsert_page({
        "title": "NVDA 실적 메모 (중복)",
        "summary": "NVDA Q2 실적 요약 중복본",
        "body": "NVDA 데이터센터 매출이 급증했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    def fake_llm(prompt: str) -> str:
        assert "merge" in prompt
        return (
            '{"action":"merge","target_page_id":"' + target["id"] + '",'
            '"source_page_ids":["' + source["id"] + '"],'
            '"body":"두 메모는 같은 NVDA Q2 실적을 다룬다.",'
            '"reason":"두 페이지가 NVDA Q2 실적으로 완전히 중복됨"}'
        )

    result = wiki.auto_curate_from_chat(
        "NVDA 실적 메모 두 개가 중복되는데 병합 기준을 정리해줘",
        "네, 두 메모 모두 NVDA Q2 실적을 다루고 있어 병합이 맞습니다.\n- 중복 내용 확인\n- 병합 후 삭제\n검증 결과 병합 대상입니다.",
        surface="market",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert result is not None
    assert result["ok"] is True
    assert result["action"] == "merge"
    assert result["target"] == target["id"]
    archived_source = wiki.get_page(source["id"])
    assert archived_source is not None
    assert archived_source["status"] == "archived"
    assert archived_source["merged_into"] == target["id"]


def test_wiki_auto_curate_llm_split_action_splits_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WIKI_CURATION_AUTO_APPLY", "1")   # 즉시 적용 모드(기본은 승인 대기)

    from agent_console import wiki

    source = wiki.upsert_page({
        "title": "NVDA 사업 전반",
        "summary": "NVDA AI와 데이터센터를 함께 다룬 메모",
        "body": "AI 매출과 데이터센터 매출이 모두 성장했다.",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    def fake_llm(prompt: str) -> str:
        assert "split" in prompt
        return (
            '{"action":"split","source_page_id":"' + source["id"] + '",'
            '"new_titles":["NVDA AI 매출","NVDA 데이터센터 매출"],'
            '"new_bodies":["AI 매출 관련 내용입니다.","데이터센터 매출 관련 내용입니다."],'
            '"reason":"두 개의 서로 다른 주제를 다루고 있음"}'
        )

    result = wiki.auto_curate_from_chat(
        "NVDA 사업 전반 메모가 두 주제를 다루는데 분할 기준을 정리해줘",
        "네, AI 매출과 데이터센터 매출은 서로 다른 주제라 분할하는 게 맞습니다.\n- 주제 분리 확인\n- 분할 후 교차 링크\n검증 결과 분할 대상입니다.",
        surface="market",
        llm=fake_llm,
        pack={"focus": []},
        history=[],
    )

    assert result is not None
    assert result["ok"] is True
    assert result["action"] == "split"
    assert result["source"] == source["id"]
    assert len(result["created"]) == 2
    assert wiki.get_page(source["id"])["status"] == "archived"


def test_wiki_lint_missing_cross_ref_suggests_link_review(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    result = wiki.lint_pages([
        {
            "id": "tickerA",
            "title": "NVDA 메모 A",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
        {
            "id": "tickerB",
            "title": "NVDA 메모 B",
            "status": "draft",
            "verification_status": "unverified",
            "source_refs": [],
            "surface": "market",
            "kind": "note",
            "tags": ["wiki", "ticker:nvda"],
            "links": [],
            "backlinks": [],
        },
    ])

    cross_ref_issues = [issue for issue in result["issues"] if issue["code"] == "missing_cross_ref"]
    assert len(cross_ref_issues) == 1
    assert cross_ref_issues[0]["suggested"] == "review_link"
    assert {cross_ref_issues[0]["page_id"], cross_ref_issues[0]["related_page_id"]} == {"tickerA", "tickerB"}


def test_wiki_health_check_reactivate_does_not_bloat_body(monkeypatch, tmp_path):
    """wiki_health_check.py 의 reactivate 경로 자체(실제 운영 코드)를 반복 호출해도 안전해야 한다."""
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    wiki.upsert_page({
        "id": "health-reactivate-test",
        "title": "헬스체크 재활성 테스트",
        "body": "원본 본문",
        "surface": "market",
        "kind": "source_digest",
        "status": "archived",
        "openQuestions": ["신호가 유효한가?"],
    })

    for _ in range(2):        # 2시간마다 도는 크론이 같은 페이지를 두 번 reactivate 대상으로 판단한 상황
        page = wiki.get_page("health-reactivate-test")
        if page:
            page["status"] = "active"
            wiki.upsert_page(page)

    final = wiki.get_page("health-reactivate-test")
    assert final["body"].count("열린 질문") == 1     # 핵심 — status 값 자체(tags 유도)는 별개 사안


def test_wiki_destructive_curation_is_proposed_by_default_and_applied_on_approval(monkeypatch, tmp_path):
    """기본 모드 — LLM 의 delete/merge/split 계획은 실행되지 않고 승인 대기열에 쌓인다.
    apply_pending_curation 이 실제 실행, reject 는 폐기."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("WIKI_CURATION_AUTO_APPLY", raising=False)
    from agent_console import wiki

    existing = wiki.upsert_page({
        "title": "오래된 임시 메모", "summary": "더 이상 유효하지 않음", "body": "삭제 후보",
        "surface": "portfolio", "kind": "note", "status": "draft", "tags": ["misc"], "source_refs": [],
    })
    result = wiki.auto_curate_from_chat(
        "위키 페이지 삭제 기준을 정리해줘. 오래된 메모는 지워도 될까?",
        "네, 아래 기준에 맞으면 삭제합니다.\n- 30일 이상 갱신 안 됨\n- 최신 리포트와 모순\n- 중복 내용\n검증 결과 이 페이지는 삭제 대상입니다.",
        surface="portfolio",
        llm=lambda _p: '{"action":"delete","target_id":"' + existing["id"] + '","reason":"stale"}',
        pack={"focus": []}, history=[],
    )
    assert result["action"] == "proposed" and result["proposed_action"] == "delete"
    assert wiki.get_page(existing["id"]) is not None                  # 아직 삭제 안 됨
    pending = wiki.list_pending_curation()
    assert [p["id"] for p in pending] == [result["proposal_id"]]
    assert pending[0]["plan"]["target_id"] == existing["id"]

    assert wiki.reject_pending_curation("no-such") is False
    applied = wiki.apply_pending_curation(result["proposal_id"])
    assert applied["ok"] is True and applied["action"] == "delete"
    assert wiki.get_page(existing["id"]) is None
    assert wiki.list_pending_curation() == []
    assert wiki.apply_pending_curation(result["proposal_id"]) is None   # 이미 소진


def test_wiki_curation_api_lists_applies_and_rejects_proposals(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("WIKI_CURATION_AUTO_APPLY", raising=False)
    from agent_console import wiki
    from agent_console.server import create_app

    page = wiki.upsert_page({"title": "제안 대상", "summary": "s", "body": "b", "surface": "portfolio",
                             "kind": "note", "status": "draft", "source_refs": []})
    prop = wiki._store_pending_curation("delete", {"target_id": page["id"], "reason": "stale"},
                                        surface="portfolio", question="q", target_id=page["id"], source="llm")
    client = create_app().test_client()
    assert [p["id"] for p in client.get("/api/wiki/curation/pending").json["proposals"]] == [prop["id"]]
    assert client.post(f"/api/wiki/curation/{prop['id']}/reject").json["ok"] is True
    assert client.get("/api/wiki/curation/pending").json["proposals"] == []
    assert client.post(f"/api/wiki/curation/{prop['id']}/apply").status_code == 404

    prop2 = wiki._store_pending_curation("delete", {"target_id": page["id"], "reason": "stale"},
                                         surface="portfolio", question="q", target_id=page["id"], source="llm")
    assert client.post(f"/api/wiki/curation/{prop2['id']}/apply").json["result"]["action"] == "delete"
    assert wiki.get_page(page["id"]) is None
