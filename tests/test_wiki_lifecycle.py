from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_is_page_stale_detects_old_updated_at():
    from agent_console import wiki

    page = {"updated_at": _iso(45)}
    assert wiki._is_page_stale(page, max_age_days=30) is True


def test_is_page_stale_false_for_recent_page():
    from agent_console import wiki

    page = {"updated_at": _iso(2)}
    assert wiki._is_page_stale(page, max_age_days=30) is False


def test_is_page_stale_true_when_no_timestamp():
    from agent_console import wiki

    assert wiki._is_page_stale({}, max_age_days=30) is True


def test_list_stale_pages_returns_only_stale(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    fresh = wiki.upsert_page({
        "title": "최근 갱신 페이지",
        "summary": "최근 요약",
        "body": "최근 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(1),
    })
    stale = wiki.upsert_page({
        "title": "오래된 페이지",
        "summary": "오래된 요약",
        "body": "오래된 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(45),
    })

    stale_pages = wiki.list_stale_pages(max_age_days=30)
    stale_ids = {p["id"] for p in stale_pages}

    assert stale["id"] in stale_ids
    assert fresh["id"] not in stale_ids


def test_archive_stale_pages_marks_status_archived(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    stale = wiki.upsert_page({
        "title": "오래된 페이지",
        "summary": "오래된 요약",
        "body": "오래된 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(45),
    })

    result = wiki.archive_stale_pages(max_age_days=30)

    assert result["archived"] == 1
    reloaded = wiki.get_page(stale["id"])
    assert reloaded["status"] == "archived"


def test_archive_stale_pages_dry_run_does_not_modify(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    stale = wiki.upsert_page({
        "title": "오래된 페이지",
        "summary": "오래된 요약",
        "body": "오래된 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
        "updated_at": _iso(45),
    })

    result = wiki.archive_stale_pages(max_age_days=30, dry_run=True)

    assert result["archived"] == 1
    reloaded = wiki.get_page(stale["id"])
    assert reloaded["status"] == "draft"


def test_archive_stale_pages_deletes_pages_archived_over_90_days(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    expired = wiki.upsert_page({
        "title": "만료된 아카이브 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "archived",
        "source_refs": [],
        "updated_at": _iso(120),
    })

    result = wiki.archive_stale_pages(max_age_days=30)

    assert result["deleted"] == 1
    assert wiki.get_page(expired["id"]) is None


def test_upsert_page_can_reactivate_archived_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    archived = wiki.upsert_page({
        "title": "재활성화 대상",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "archived",
        "source_refs": [],
        "updated_at": _iso(10),
    })
    assert archived["status"] == "archived"

    reactivated = wiki.upsert_page({
        "id": archived["id"],
        "title": "재활성화 대상",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })

    assert reactivated["status"] == "draft"
    assert wiki.get_page(archived["id"])["status"] == "draft"


def test_rebuild_artifacts_index_shows_archived_section(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    wiki.upsert_page({
        "title": "활성 페이지",
        "summary": "활성 요약",
        "body": "활성 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "draft",
        "source_refs": [],
    })
    wiki.upsert_page({
        "title": "아카이브된 페이지",
        "summary": "아카이브 요약",
        "body": "아카이브 본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "archived",
        "source_refs": [],
    })

    wiki.rebuild_artifacts()
    index_text = (wiki.wiki_artifacts_dir() / "index.md").read_text(encoding="utf-8")

    assert "## Archived" in index_text
    assert "아카이브된 페이지" in index_text.split("## Archived", 1)[1]
    assert "아카이브된 페이지" not in index_text.split("## Archived", 1)[0]


def test_stats_status_counts_include_archived(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    wiki.upsert_page({
        "title": "아카이브된 페이지",
        "summary": "요약",
        "body": "본문",
        "surface": "portfolio",
        "kind": "note",
        "status": "archived",
        "source_refs": [],
    })

    result = wiki.stats()

    assert result["status_counts"].get("archived") == 1
