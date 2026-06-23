"""tests/test_notify.py — 텔레그램 발송 단일 진실원 단위 테스트 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify


def test_split_message_short():
    assert notify.split_message("짧은 메시지") == ["짧은 메시지"]


def test_split_message_boundary_no_truncation():
    msg = "\n".join(f"라인 {i:04d} " + "x" * 50 for i in range(200))
    parts = notify.split_message(msg)
    assert len(parts) > 1
    assert all(len(p) <= notify.TG_MAX_CHARS for p in parts)
    assert "\n".join(parts) == msg          # 줄바꿈 경계 → 내용 무손실


def test_split_message_hard_cut_when_no_newline():
    msg = "x" * 9000                          # 줄바꿈 없는 초장문 → 강제 분할
    parts = notify.split_message(msg)
    assert all(len(p) <= notify.TG_MAX_CHARS for p in parts)
    assert "".join(parts) == msg


def test_mask_hides_token_in_url_and_plain():
    masked = notify._mask("fail /bot12345:ABC-de_f/sendMessage", "12345:ABC-de_f")
    assert "12345:ABC-de_f" not in masked
    assert "/bot***" in masked


def test_send_telegram_no_token_returns_false(monkeypatch):
    monkeypatch.delenv("STOCK_BOT_TOKEN", raising=False)
    assert notify.send_telegram("hi", token=None) is False


def test_send_telegram_posts_each_part(monkeypatch):
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append((url, json))
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    ok = notify.send_telegram("a\n" * 2300, token="T", chat_id="C")  # 2부 분할
    assert ok is True
    assert len(calls) == 2
    assert all("/botT/sendMessage" in u for u, _ in calls)
    assert all(j["chat_id"] == "C" for _, j in calls)
