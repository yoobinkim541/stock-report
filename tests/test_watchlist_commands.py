"""tests/test_watchlist_commands.py — bot/watchlist_commands.py 텔레그램 /watch 명령."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import watchlist_commands as wc  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "store.db"))


def test_watch_no_args_shows_usage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", [], lambda chat_id, text: sent.append(text))

    assert len(sent) == 1
    assert "/watch add" in sent[0]


def test_watch_add_creates_entry_and_confirms(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", ["add", "nvda", "실적", "기대"], lambda chat_id, text: sent.append(text))

    from lib import watchlist
    entries = watchlist.list_watchlist()
    assert len(entries) == 1
    assert entries[0]["ticker"] == "NVDA"
    assert entries[0]["note"] == "실적 기대"
    assert entries[0]["source"] == "manual"
    assert "추가" in sent[0]


def test_watch_add_without_note(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", ["add", "TSLA"], lambda chat_id, text: sent.append(text))

    from lib import watchlist
    entries = watchlist.list_watchlist()
    assert entries[0]["ticker"] == "TSLA"
    assert entries[0]["note"] is None


def test_watch_add_missing_ticker_shows_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", ["add"], lambda chat_id, text: sent.append(text))

    assert "❌" in sent[0]
    from lib import watchlist
    assert watchlist.list_watchlist() == []


def test_watch_list_shows_entries(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from lib import watchlist
    watchlist.add_ticker("AAPL", reason="버핏 신규 편입", source="notable_investor:berkshire")

    sent = []
    wc.cmd_watch("chat1", ["list"], lambda chat_id, text: sent.append(text))

    assert "AAPL" in sent[0]
    assert "버핏 신규 편입" in sent[0]


def test_watch_list_empty_shows_message(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", ["list"], lambda chat_id, text: sent.append(text))

    assert "없습니다" in sent[0]


def test_watch_remove_deletes_and_confirms(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from lib import watchlist
    watchlist.add_ticker("MSFT", reason="테스트", source="manual")

    sent = []
    wc.cmd_watch("chat1", ["remove", "msft"], lambda chat_id, text: sent.append(text))

    assert watchlist.list_watchlist() == []
    assert "삭제" in sent[0]


def test_watch_remove_missing_shows_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    sent = []
    wc.cmd_watch("chat1", ["remove", "ZZZZ"], lambda chat_id, text: sent.append(text))

    assert "❌" in sent[0]
