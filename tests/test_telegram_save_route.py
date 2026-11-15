import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_OWNER_ONLY = "🔒 이 명령은 소유자 전용입니다.\n읽기전용 계정 사용법: /help"
_INCIDENT = "0123456789abcdef01234567"


def _callback(data, *, chat_id=4242, user_id=9001, callback_id="cb"):
    return {
        "update_id": 1,
        "callback_query": {
            "id": callback_id,
            "data": data,
            "from": {"id": user_id},
            "message": {"chat": {"id": chat_id}},
        },
    }


def test_dispatch_save_reaches_handle_and_rejects_guest(monkeypatch):
    import telegram_bot as tb
    from bot import saveticker_commands

    seen = []

    def fake_handle(chat_id, args, *, send_fn):
        seen.append((chat_id, list(args), send_fn))

    monkeypatch.setattr(saveticker_commands, "handle", fake_handle)
    sent = []
    monkeypatch.setattr(tb, "send", lambda chat_id, text: sent.append((chat_id, text)))

    tb.dispatch("/save status", "owner-chat", "owner")

    assert seen == [("owner-chat", ["status"], tb.send)]
    assert "/save" not in tb._SLOW_COMMANDS

    tb.dispatch("/save status", "guest-chat", "guest")

    assert seen == [("owner-chat", ["status"], tb.send)]
    assert sent == [("guest-chat", _OWNER_ONLY)]


def test_save_callback_owner_answered_and_enqueued(monkeypatch):
    import queue
    import telegram_bot as tb

    api = []

    def fake_api(method, **kw):
        api.append((method, kw))
        return {"ok": True, "result": []}

    monkeypatch.setattr(tb, "_api", fake_api)
    assert tb.get_updates(7) == []
    assert api[0] == (
        "getUpdates",
        {"timeout": tb.POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"],
         "offset": 7, "_request_timeout": tb.POLL_TIMEOUT + 10},
    )

    monkeypatch.setattr(tb, "OWNER_CHAT_ID", "4242")
    monkeypatch.setenv("STOCK_BOT_OWNER_USER_ID", "9001")
    fast, slow = queue.Queue(), queue.Queue()
    monkeypatch.setattr(tb, "_DISPATCH_Q", fast)
    monkeypatch.setattr(tb, "_SLOW_Q", slow)
    api.clear()

    assert tb._ingest_update(_callback(f"savecheck:{_INCIDENT}", callback_id="c1")) is None
    assert api == [("answerCallbackQuery", {
        "callback_query_id": "c1",
        "text": "SaveTicker 뉴스 수집은 종료되었습니다.",
    })]
    assert fast.get_nowait() == ("/save", "4242", "owner")
    assert slow.empty() and fast.empty()

    tb._ingest_update(_callback(f"saveopen:{_INCIDENT}", callback_id="c2"))
    assert api[-1] == ("answerCallbackQuery", {
        "callback_query_id": "c2",
        "text": "SaveTicker 뉴스 수집은 종료되었습니다.",
    })
    assert fast.get_nowait() == ("/save", "4242", "owner")
    assert slow.empty()


def test_save_callback_refuses_non_owner_or_malformed_and_ignores_other_prefix(monkeypatch):
    import queue
    import telegram_bot as tb

    api = []
    monkeypatch.setattr(tb, "_api", lambda method, **kw: api.append((method, kw)))
    monkeypatch.setattr(tb, "OWNER_CHAT_ID", "4242")
    monkeypatch.setenv("STOCK_BOT_OWNER_USER_ID", "9001")
    fast = queue.Queue()
    monkeypatch.setattr(tb, "_DISPATCH_Q", fast)

    refused = []
    for upd in (
        _callback(f"savecheck:{_INCIDENT}", user_id=1, callback_id="user"),
        _callback(f"saveopen:{_INCIDENT}", chat_id=1, callback_id="chat"),
        _callback("savecheck:" + "a" * 23, callback_id="short"),
        _callback("savecheck:" + "A" * 24, callback_id="upper"),
        _callback("saveopen:not-hex-but-long-enough", callback_id="junk"),
    ):
        assert tb._ingest_update(upd) is None
        refused.append(api[-1])
        assert fast.empty()

    assert refused == [
        ("answerCallbackQuery", {"callback_query_id": cid, "text": "소유자만 확인할 수 있습니다."})
        for cid in ("user", "chat", "short", "upper", "junk")
    ]

    api.clear()
    other = {"callback_query": {"id": "other", "data": "alert:1", "from": {"id": 9001},
                                "message": {"chat": {"id": 4242}}}}
    assert tb._handle_save_recovery_callback(other) is False
    assert api == [] and fast.empty()
    # 다른 prefix 는 이 핸들러가 소비하지 않으므로 일반 메시지 처리는 그대로 진행된다.
    monkeypatch.setattr(tb, "_role_for", lambda cid: "owner")
    monkeypatch.setattr(tb, "_enqueue_command", lambda text, chat_id, role: fast.put((text, chat_id, role)))
    mixed = dict(other, message={"chat": {"id": 4242}, "text": "/status"})
    tb._ingest_update(mixed)
    assert api == []
    assert fast.get_nowait() == ("/status", "4242", "owner")
