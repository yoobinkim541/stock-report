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
        ok = True
        status_code = 200
        text = ""

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append((url, json))
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    ok = notify.send_telegram("a\n" * 2300, token="T", chat_id="C")  # 2부 분할
    assert ok is True
    assert len(calls) == 2
    assert all("/botT/sendMessage" in u for u, _ in calls)
    assert all(j["chat_id"] == "C" for _, j in calls)


def _resp(status, body=None):
    class _R:
        ok = 200 <= status < 300
        status_code = status
        text = ""

        def json(self):
            return body or {}
    return _R()


def test_send_telegram_retries_on_429_with_retry_after(monkeypatch):
    """429 는 retry_after 만큼 쉬고 재시도, 4xx(400) 는 즉시 실패·재시도 없음."""
    seq = [_resp(429, {"parameters": {"retry_after": 2}}), _resp(200)]
    sleeps = []
    monkeypatch.setattr(notify.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: seq.pop(0))
    assert notify.send_telegram("x", token="T", chat_id="C") is True
    assert sleeps == [2.0]

    calls = []
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: calls.append(1) or _resp(400))
    assert notify.send_telegram("x", token="T", chat_id="C") is False
    assert len(calls) == 1


def test_send_telegram_gives_up_after_attempts_on_5xx(monkeypatch):
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: calls.append(1) or _resp(502))
    assert notify.send_telegram("x", token="T", chat_id="C") is False
    assert len(calls) == 3
