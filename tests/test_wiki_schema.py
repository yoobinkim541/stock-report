from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def test_wiki_judgment_schema_round_trips_citation_and_parent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "AI 수요 검증",
        "summary": "수요와 가격 반응을 함께 확인한다.",
        "body": "AI 수요는 가격에 선반영될 수 있다.\n\n> **리포트 인용 요약**: 수요 뉴스만으로 추격하지 않는다.",
        "surface": "ticker",
        "kind": "playbook",
        "status": "draft",
        "parent_page_id": "parent-001",
        "report_citation": "수요 뉴스만으로 추격하지 않는다.",
    })

    loaded = wiki.get_page(page["id"])

    assert loaded["report_citation"] == "수요 뉴스만으로 추격하지 않는다."
    assert loaded["wiki_schema_version"] == 2
    assert loaded["parent_page_id"] == "parent-001"
    assert loaded["body"].count("리포트 인용 요약") == 1


def test_legacy_body_exposes_citation_without_rewriting_storage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "기존 판단",
        "summary": "기존 요약",
        "body": "기존 본문\n\n> **리포트 인용 요약**: 기존 문서도 리포트에서 재사용 가능하다.",
        "surface": "market",
        "kind": "risk",
        "status": "draft",
    })

    loaded = wiki.get_page(page["id"])

    assert loaded["report_citation"] == "기존 문서도 리포트에서 재사용 가능하다."
    assert loaded["wiki_schema_version"] == 2


def test_plan_payload_adds_one_report_citation_line(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    payload = wiki._plan_to_page_payload(
        {
            "action": "create",
            "title": "금리 리스크",
            "summary": "금리 상승 시 성장주 변동성이 커질 수 있다.",
            "body": "금리 상승은 할인율을 높여 성장주 밸류에이션을 압박한다.",
            "kind": "risk",
            "status": "draft",
            "report_citation": "금리 상승 국면에서는 성장주 밸류에이션과 크레딧을 함께 확인한다.",
        },
        question="금리 리스크를 정리해줘",
        answer="금리 리스크를 정리한다.",
        surface="market",
    )

    assert payload["report_citation"].startswith("금리 상승 국면")
    assert payload["body"].count("리포트 인용 요약") == 1
    assert payload["body"].endswith("금리 상승 국면에서는 성장주 밸류에이션과 크레딧을 함께 확인한다.")
