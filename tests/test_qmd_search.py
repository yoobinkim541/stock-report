from __future__ import annotations

import json
from pathlib import Path


class _Result:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_qmd_bin_finds_hermes_install_when_cron_path_omits_it(monkeypatch, tmp_path):
    hermes_qmd = tmp_path / ".hermes" / "node" / "bin" / "qmd"
    hermes_qmd.parent.mkdir(parents=True)
    hermes_qmd.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_qmd.chmod(0o755)

    monkeypatch.delenv("AGENT_CONSOLE_QMD_BIN", raising=False)
    monkeypatch.setattr("agent_console.qmd_search.Path.home", lambda: tmp_path)
    monkeypatch.setattr("agent_console.qmd_search.shutil.which", lambda _binary: None)

    from agent_console import qmd_search

    assert qmd_search.qmd_bin() == str(hermes_qmd)


def test_qmd_search_parses_json_results_and_builds_search_command(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", "qmd")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_COLLECTIONS", "wiki")
    calls = []

    def fake_runner(cmd, capture_output, text, timeout):
        calls.append(cmd)
        payload = [
            {
                "title": "손실한도와 레버리지",
                "file": "qmd://wiki/wiki-page-001.md",
                "snippet": "손실한도 1%에서는 QQQ가 기본입니다.",
                "score": 0.91,
            }
        ]
        return _Result(json.dumps(payload, ensure_ascii=False))

    from agent_console import qmd_search

    results = qmd_search.search("손실한도", limit=3, runner=fake_runner)

    assert calls
    assert calls[0][:2] == ["qmd", "search"]
    assert calls[0][2] == "손실한도"
    assert "--format" in calls[0]
    assert "json" in calls[0]
    assert "-n" in calls[0]
    assert results[0]["title"] == "손실한도와 레버리지"
    assert results[0]["page_id"] == "wiki-page-001"
    assert results[0]["provider"] == "qmd"


def test_qmd_search_returns_empty_when_disabled_or_cli_fails(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")

    from agent_console import qmd_search

    assert qmd_search.search("손실한도", runner=lambda *a, **k: _Result("[]")) == []

    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")

    def failing_runner(*args, **kwargs):
        return _Result("not-json", returncode=2)

    assert qmd_search.search("손실한도", runner=failing_runner) == []


def test_qmd_export_pages_writes_markdown_mirror(monkeypatch, tmp_path):
    wiki_dir = tmp_path / "wiki-md"
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(wiki_dir))

    from agent_console import qmd_search

    result = qmd_search.export_pages(
        [
            {
                "id": "abc123",
                "title": "손실한도와 레버리지",
                "surface": "portfolio",
                "kind": "playbook",
                "status": "reviewed",
                "summary": "QQQ는 기본, TQQQ는 변동성 예산을 더 씁니다.",
                "body": "손실한도 1% 기준에서는 현금 완충이 필요합니다.",
                "tags": ["risk", "portfolio"],
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ]
    )

    path = Path(result["files"][0])
    assert result["ok"] is True
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 손실한도와 레버리지" in text
    assert "surface: portfolio" in text
    assert "손실한도 1% 기준" in text


def test_qmd_search_can_use_query_command_when_configured(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_COMMAND", "query")
    calls = []

    def fake_runner(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return _Result("[]")

    from agent_console import qmd_search

    assert qmd_search.search("손실한도", runner=fake_runner) == []
    assert Path(calls[0][0]).name == "qmd"
    assert calls[0][1] == "query"


def test_qmd_health_reports_installation_and_wiki_file_count(monkeypatch, tmp_path):
    wiki_dir = tmp_path / "wiki-md"
    wiki_dir.mkdir()
    (wiki_dir / "a.md").write_text("# A", encoding="utf-8")
    (wiki_dir / "b.md").write_text("# B", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", "qmd")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(wiki_dir))

    from agent_console import qmd_search

    monkeypatch.setattr(qmd_search.shutil, "which", lambda binary: "/usr/bin/qmd" if binary == "qmd" else None)

    health = qmd_search.health()

    assert health["provider"] == "qmd"
    assert health["enabled"] is True
    assert health["installed"] is True
    assert health["file_count"] == 2
    assert health["fallback_available"] is True


def test_qmd_sync_exports_pages_and_updates_index_once(monkeypatch, tmp_path):
    wiki_dir = tmp_path / "wiki-md"
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", "qmd")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(wiki_dir))
    calls = []

    def fake_runner(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return _Result("updated")

    from agent_console import qmd_search

    wiki_dir.mkdir(parents=True)
    (wiki_dir / "deleted.md").write_text("old", encoding="utf-8")

    result = qmd_search.sync_pages([
        {"id": "one", "title": "One", "body": "first"},
        {"id": "two", "title": "Two", "body": "second"},
    ], runner=fake_runner)

    assert result["ok"] is True
    assert result["exported_count"] == 2
    assert result["removed_count"] == 1
    assert not (wiki_dir / "deleted.md").exists()
    assert calls == [["qmd", "update"]]


def test_qmd_sync_rejects_empty_snapshot_without_deleting_existing_docs(monkeypatch, tmp_path):
    wiki_dir = tmp_path / "wiki-md"
    wiki_dir.mkdir()
    existing = wiki_dir / "existing.md"
    existing.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(wiki_dir))
    from agent_console import qmd_search

    result = qmd_search.sync_pages([], runner=lambda *args, **kwargs: _Result("updated"))

    assert result["ok"] is False
    assert result["skipped"] == "empty_snapshot"
    assert existing.exists()


def test_qmd_health_executes_query_probe_and_detects_stale_export(monkeypatch, tmp_path):
    wiki_dir = tmp_path / "wiki-md"
    wiki_dir.mkdir()
    doc = wiki_dir / "old.md"
    doc.write_text("# Old", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", "qmd")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(wiki_dir))

    from agent_console import qmd_search

    monkeypatch.setattr(qmd_search.shutil, "which", lambda _binary: "/usr/bin/qmd")
    monkeypatch.setattr(
        qmd_search.shared_memory,
        "inspect_records",
        lambda: [{"tags": ["wiki"], "updatedAt": "2099-01-01T00:00:00+00:00"}],
        raising=False,
    )

    health = qmd_search.health(runner=lambda *args, **kwargs: _Result("[]"))

    assert health["query_ok"] is True
    assert health["index_fresh"] is False
    assert health["latest_page_at"] == "2099-01-01T00:00:00+00:00"


def test_qmd_health_marks_failed_query_even_when_binary_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(tmp_path / "wiki-md"))
    from agent_console import qmd_search

    monkeypatch.setattr(qmd_search.shutil, "which", lambda _binary: "/usr/bin/qmd")
    health = qmd_search.health(runner=lambda *args, **kwargs: _Result("", returncode=2))

    assert health["query_ok"] is False
    assert health["error"]
