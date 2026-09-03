from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_dashboard_watchdog_tracks_agent_console_sources():
    body = (ROOT / "scripts" / "dashboard_watchdog.sh").read_text(encoding="utf-8")

    assert '"$PROJECT_DIR"/agent_console' in body


def test_bot_watchdog_tracks_shared_agent_sources():
    body = (ROOT / "scripts" / "bot_watchdog.sh").read_text(encoding="utf-8")

    assert '"$PROJECT_DIR"/agent_console' in body
