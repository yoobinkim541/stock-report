from __future__ import annotations

import json
from pathlib import Path


class _Result:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


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
    assert calls[0][:2] == ["qmd", "query"]
