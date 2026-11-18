"""lib/cron_common.send_cron_telegram — notify 의 실패 결과가 성공으로 보고되지 않아야 한다."""
import notify
from lib import cron_common


def test_send_cron_telegram_propagates_notify_failure(monkeypatch):
    monkeypatch.setenv("STOCK_BOT_TOKEN", "t")
    monkeypatch.setenv("STOCK_BOT_CHAT_ID", "c")
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: False)
    assert cron_common.send_cron_telegram("x") is False


def test_send_cron_telegram_reports_success(monkeypatch):
    monkeypatch.setenv("STOCK_BOT_TOKEN", "t")
    monkeypatch.setenv("STOCK_BOT_CHAT_ID", "c")
    calls = []

    def _ok(text, *, token, chat_id, timeout):
        calls.append((text, token, chat_id))
        return True
    monkeypatch.setattr(notify, "send_telegram", _ok)
    assert cron_common.send_cron_telegram("hello") is True
    assert calls == [("hello", "t", "c")]


def test_send_cron_telegram_without_keys(monkeypatch):
    monkeypatch.delenv("STOCK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("STOCK_BOT_CHAT_ID", raising=False)
    assert cron_common.send_cron_telegram("x") is False
