"""agent_console 테스트 공통 격리 헬퍼 — test_agent_console_*.py 가 공유."""
from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(tmp_path / "reports" / "source-cache"))
    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "reports" / "ml-data"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    # 단일 월드 메모리(lib.world_memory)도 테스트별 격리 — DB_PATH 는 호출 시점 참조
    from lib import world_memory as _wm
    monkeypatch.setattr(_wm, "DB_PATH", tmp_path / "world_issue_log.sqlite3")
