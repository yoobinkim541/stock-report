"""test_kiwoom_mock_close_generation.py — 세대 마감 CLI 스모크 테스트."""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import kiwoom_mock  # noqa: E402
import store  # noqa: E402
from crons import kiwoom_mock_close_generation as cli  # noqa: E402


class _FakeStore:
    """store.append/all 을 인메모리로 대체 — 실제 세션 공유 DB 오염 방지."""

    def __init__(self):
        self._data: dict[str, list[dict]] = {}

    def all(self, name):
        return list(self._data.get(name, []))

    def append(self, name, item):
        self._data.setdefault(name, []).append(dict(item))
        return len(self._data[name])


def _isolate_store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(store, "all", fake.all)
    monkeypatch.setattr(store, "append", fake.append)
    return fake


def test_main_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(kiwoom_mock, "is_enabled", lambda: False)
    assert cli.main([]) == 1


def test_main_closes_generation_and_notifies(monkeypatch):
    _isolate_store(monkeypatch)
    monkeypatch.setattr(kiwoom_mock, "is_enabled", lambda: True)
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {
        "ok": True, "nav": 11_000_000, "positions": {
            "005930": {"name": "삼성전자", "shares": 5, "value": 11_000_000},
        },
    })

    sent = []
    import notify
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, **kwargs: sent.append(text) or True)

    code = cli.main(["--reason", "테스트 갱신"])

    assert code == 0
    assert len(sent) == 1
    assert "세대 1 마감" in sent[0]


def test_main_fails_when_balance_dead(monkeypatch):
    _isolate_store(monkeypatch)
    monkeypatch.setattr(kiwoom_mock, "is_enabled", lambda: True)
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {"ok": False})

    code = cli.main([])

    assert code == 1
