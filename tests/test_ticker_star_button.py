"""tests/test_ticker_star_button.py — 종목 분석페이지 ⭐ 관심종목 토글 (감사 후속).

기존 관심종목 추가는 텔레그램 봇 `/watch add` 로만 가능했다 — 종목 분석페이지에서
바로 별표시로 추가/제거할 수 있게 하는 버튼. 클릭 시 '미분류' 폴더로 추가되고,
이미 있으면 제거(토글). monkeypatch 는 바깥쪽에서(AppTest 스크립트가 같은 프로세스
모듈 객체를 공유하므로 자동 복원되도록).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import ticker_names  # noqa: E402
from dashboard import data  # noqa: E402
from dashboard.pages import ticker  # noqa: E402

_RUN_SCRIPT = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import ticker
ticker._star_button("AAPL")
"""


def test_star_button_shows_add_label_when_not_starred(monkeypatch):
    monkeypatch.setattr(data, "is_in_watchlist", lambda t: False)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    assert at.button(key="_ticker_star_btn").label == "☆ 관심종목 추가"


def test_star_button_shows_remove_label_when_starred(monkeypatch):
    monkeypatch.setattr(data, "is_in_watchlist", lambda t: True)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    assert at.button(key="_ticker_star_btn").label == "⭐ 관심종목에서 제거"


def test_star_button_click_adds_ticker(monkeypatch):
    monkeypatch.setattr(data, "is_in_watchlist", lambda t: False)
    monkeypatch.setattr(ticker_names, "display_name", lambda t, allow_net=False: "Apple Inc")
    calls = []
    monkeypatch.setattr(data, "toggle_watchlist_star",
                        lambda ticker, name=None: calls.append((ticker, name)) or True)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.button(key="_ticker_star_btn").click().run()

    assert not at.exception, str(at.exception)
    assert calls == [("AAPL", "Apple Inc")]
    toast_body = " ".join(str(t.value) for t in at.toast)
    assert "추가됨" in toast_body


def test_star_button_click_removes_ticker(monkeypatch):
    monkeypatch.setattr(data, "is_in_watchlist", lambda t: True)
    monkeypatch.setattr(ticker_names, "display_name", lambda t, allow_net=False: "Apple Inc")
    calls = []
    monkeypatch.setattr(data, "toggle_watchlist_star",
                        lambda ticker, name=None: calls.append((ticker, name)) or False)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.button(key="_ticker_star_btn").click().run()

    assert not at.exception, str(at.exception)
    assert calls == [("AAPL", "Apple Inc")]
    toast_body = " ".join(str(t.value) for t in at.toast)
    assert "제거됨" in toast_body
