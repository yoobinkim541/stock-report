from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_build_health_report_dry_run_does_not_modify(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki
    from reports import wiki_health_check

    stale = wiki.upsert_page({
        "title": "오래된 규칙",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(20),
    })

    report = wiki_health_check.build_health_report(dry_run=True)

    assert report["dry_run"] is True
    assert report["stale_count"] == 1
    fetched = wiki.get_page(stale["id"])
    assert fetched["status"] != "archived"


def test_build_health_report_archives_stale_pages_when_not_dry_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki
    from reports import wiki_health_check

    stale = wiki.upsert_page({
        "title": "오래된 규칙",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(20),
    })

    report = wiki_health_check.build_health_report(dry_run=False)

    assert any("archive" in rec.lower() for rec in report["recommendations"])
    fetched = wiki.get_page(stale["id"])
    assert fetched["status"] == "archived"


def test_build_health_report_flags_very_unused_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki
    from reports import wiki_health_check

    wiki.upsert_page({
        "title": "70일 미사용 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "created_at": _iso(70),
        "updated_at": _iso(1),
    })

    report = wiki_health_check.build_health_report(dry_run=True)

    assert report["very_unused_count"] == 1
    assert any("60일" in rec for rec in report["recommendations"])


def test_build_health_report_does_not_flag_very_unused_source_digest(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_health_check

    wiki.upsert_page({
        "title": "오래된 원문 다이제스트",
        "summary": "원문 보존",
        "body": "원문",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "source_refs": ["https://example.com/source"],
        "created_at": _iso(70),
        "updated_at": _iso(70),
    })

    report = wiki_health_check.build_health_report(dry_run=True)

    assert report["very_unused_count"] == 0
    assert not any("삭제 검토" in rec for rec in report["recommendations"])


def test_build_health_report_wraps_pipeline_report(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from reports import wiki_health_check

    monkeypatch.setattr(
        wiki_health_check,
        "build_pipeline_health_report",
        lambda dry_run=False: {
            "dry_run": dry_run,
            "generated_at": "2026-07-31T00:00:00+00:00",
            "source_health": {"overall": {"tracked_sources": 1, "expected_sources": 1, "healthy_sources": 1, "stale_sources": 0, "missing_success_sources": 0}},
            "wiki_health": {
                "stats": {"total": 2, "status_counts": {"archived": 0, "reviewed": 1, "stable": 1}},
                "lint": {"issues": []},
                "stale_count": 0,
                "unused_count": 0,
                "source_backed_count": 1,
                "unverified_count": 1,
                "source_missing_for_promoted_count": 0,
            },
            "curation_health": {"source_digest_count": 1, "source_digest_linked_count": 1, "source_digest_unlinked_count": 0},
            "recommendations": [{"category": "healthy", "title": "파이프라인 안정", "detail": "ok"}],
        },
    )
    monkeypatch.setattr(wiki_health_check.wiki, "list_stale_pages", lambda max_age_days=14: [])
    monkeypatch.setattr(wiki_health_check.wiki, "list_unused_pages", lambda days=30: [])

    report = wiki_health_check.build_health_report(dry_run=True)
    text = wiki_health_check.format_report(report)

    assert report["pipeline_report"]["source_health"]["overall"]["tracked_sources"] == 1
    assert report["source_health"]["overall"]["healthy_sources"] == 1
    assert "소스 파이프라인" in text
    assert "큐레이션" in text


def test_format_report_contains_key_sections(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki
    from reports import wiki_health_check

    wiki.upsert_page({
        "title": "정상 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    report = wiki_health_check.build_health_report(dry_run=True)
    text = wiki_health_check.format_report(report)

    assert "[위키 헬스 체크]" in text
    assert "전체" in text
    assert "스테일" in text
    assert "미사용" in text


def test_main_dry_run_prints_report_and_returns_zero(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)

    import sys

    from reports import wiki_health_check

    monkeypatch.setattr(sys, "argv", ["wiki_health_check", "--dry-run"])
    exit_code = wiki_health_check.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[위키 헬스 체크]" in captured.out


def _sample_report():
    return {
        "dry_run": False,
        "stats": {
            "total": 10,
            "trust_counts": {"unverified": 3},
            "status_counts": {"archived": 2},
        },
        "lint_issues": [],
        "stale_count": 1,
        "unused_count": 1,
        "very_unused_count": 0,
        "recommendations": [],
    }


def test_run_llm_health_review_returns_list(monkeypatch):
    import agent_console.agent as agent_module
    from reports import wiki_health_check

    monkeypatch.setattr(
        agent_module,
        "_try_llm_prompt",
        lambda prompt, **kwargs: (
            '{"actions": [{"page_id": "p1", "action": "archive", "reason": "stale"}], '
            '"health_score": 7, "summary": "ok"}'
        ),
    )

    actions = wiki_health_check.run_llm_health_review(_sample_report())

    assert actions == [{"page_id": "p1", "action": "archive", "reason": "stale"}]


def test_run_llm_health_review_failure_graceful(monkeypatch):
    import agent_console.agent as agent_module
    from reports import wiki_health_check

    def _raise(prompt, **kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(agent_module, "_try_llm_prompt", _raise)

    actions = wiki_health_check.run_llm_health_review(_sample_report())

    assert actions == []


def test_main_archive_action_targets_only_recommended_page(monkeypatch, tmp_path):
    """LLM 이 특정 page_id 만 archive 하라고 했는데, 실제로는 wiki.archive_stale_pages(
    max_age_days=0) 를 호출해 신선한 페이지까지 전부 archive 되던 버그(2026-08-26 감사) —
    live 위키에서 헬스체크 크론(2시간 주기) 발동 시각마다 십수~수십 개 페이지가 한꺼번에
    archived 되는 패턴으로 실제 발생 확인됨."""
    _isolate(monkeypatch, tmp_path)

    import sys

    from agent_console import wiki
    from reports import wiki_health_check

    target = wiki.upsert_page({
        "title": "LLM 이 archive 하라고 지목한 페이지", "summary": "요약", "body": "본문",
        "surface": "market", "kind": "note", "status": "reviewed", "source_refs": [],
    })
    untouched = wiki.upsert_page({
        "title": "방금 갱신된 무관한 신선 페이지", "summary": "요약", "body": "본문",
        "surface": "market", "kind": "note", "status": "reviewed", "source_refs": [],
    })

    monkeypatch.setattr(
        wiki_health_check, "run_llm_health_review",
        lambda report: [{"page_id": target["id"], "action": "archive", "reason": "stale"}],
    )
    monkeypatch.setattr(sys, "argv", ["wiki_health_check"])

    exit_code = wiki_health_check.main()

    assert exit_code == 0
    assert wiki.get_page(target["id"])["status"] == "archived"
    assert wiki.get_page(untouched["id"])["status"] != "archived"


def test_main_reactivate_action_sets_valid_status_not_active(monkeypatch, tmp_path):
    """"active" 는 VALID_STATUSES 에 없어 normalize_trust_status() 가 조용히
    "draft" 로 깎아내리던 버그(2026-08-26 감사) — reactivate 는 reviewed 로 복귀해야 한다."""
    _isolate(monkeypatch, tmp_path)

    import sys

    from agent_console import wiki
    from reports import wiki_health_check

    archived = wiki.upsert_page({
        "title": "재활성화 대상 페이지", "summary": "요약", "body": "본문",
        "surface": "market", "kind": "note", "status": "archived",
        "source_refs": ["https://example.com/evidence"],
    })

    monkeypatch.setattr(
        wiki_health_check, "run_llm_health_review",
        lambda report: [{"page_id": archived["id"], "action": "reactivate", "reason": "복귀"}],
    )
    monkeypatch.setattr(sys, "argv", ["wiki_health_check"])

    exit_code = wiki_health_check.main()

    assert exit_code == 0
    assert wiki.get_page(archived["id"])["status"] == "reviewed"


def test_main_with_llm_gate_skips_llm_on_dry_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    import sys

    from reports import wiki_health_check

    called = []
    monkeypatch.setattr(
        wiki_health_check,
        "run_llm_health_review",
        lambda report: called.append(report) or [],
    )
    monkeypatch.setattr(sys, "argv", ["wiki_health_check", "--dry-run"])

    exit_code = wiki_health_check.main()

    assert exit_code == 0
    assert called == []
