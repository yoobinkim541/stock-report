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
