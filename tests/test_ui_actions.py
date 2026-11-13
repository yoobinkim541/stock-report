"""화면 이동 계약 — 이미 선택된 티커여도 종목 분석으로 간다."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import streamlit as st

from dashboard import ui_actions


def test_open_ticker_switches_even_when_already_selected(monkeypatch):
    switched = []
    monkeypatch.setattr(st, "switch_page", lambda page: switched.append(page))
    monkeypatch.setattr(st, "toast", lambda *args, **kwargs: None)
    page = object()
    st.session_state["ticker"] = "NVDA"
    st.session_state["_ticker_page"] = page
    assert ui_actions.open_ticker("NVDA", toast="종목 분석 → NVDA") is True
    assert switched == [page]
    assert st.session_state["ticker"] == "NVDA"


def test_open_ticker_ignores_blank_and_placeholder(monkeypatch):
    monkeypatch.setattr(st, "switch_page", lambda page: (_ for _ in ()).throw(AssertionError(page)))
    st.session_state["ticker"] = "MSFT"
    assert ui_actions.open_ticker("  ") is False
    assert ui_actions.open_ticker("—") is False
    assert st.session_state["ticker"] == "MSFT"


def test_open_ticker_normalizes_alphanumeric_kr_code(monkeypatch):
    switched = []
    monkeypatch.setattr(st, "switch_page", lambda page: switched.append(page))
    monkeypatch.setattr(st, "toast", lambda *args, **kwargs: None)
    page = object()
    st.session_state["ticker"] = "MSFT"
    st.session_state["_ticker_page"] = page
    assert ui_actions.open_ticker("0167A0") is True
    assert st.session_state["ticker"] == "0167A0.KS"
    assert switched == [page]


def test_group_news_uses_tickers_field_not_only_dollar_tags():
    from dashboard import views
    events = [{
        "id": "k",
        "source": "saveticker",
        "title": "삼성전자 수주",
        "published_at": "2026-07-06T10:00:00+09:00",
        "tags": ["실적"],
        "tickers": ["005930.KS"],
    }]
    grouped = views.group_news(events, score_fn=lambda event: (5, ""))
    assert grouped["saveticker"][0]["tickers"] == ["005930"]


def test_news_line_escapes_markdown_link_breakers():
    from dashboard.pages.market import _news_line
    line = _news_line({
        "title": "실적 *상회* [속보]",
        "url": "https://example.com/a)b",
        "score": 8,
        "tickers": ["NVDA"],
        "time_str": "07-06 10:00",
    })
    assert "*상회*" not in line
    assert "a)b" not in line
    assert "%29" in line
    assert "`$NVDA`" in line


def test_mentioned_tickers_uses_parser_not_prefix():
    got = ui_actions.mentioned_tickers([
        {"title": "see $NVDA and $BRK-B $SOL-USD", "tickers": ["AAPL", "AAPL"]},
        {"title": "again $NVDA", "tickers": ["  "]},
        "not-a-row",
    ])
    assert got == ["AAPL", "NVDA", "BRK-B", "SOL-USD"]
    assert "SOL" not in got
    assert "BRK" not in got


def test_mentioned_tickers_caps_and_normalizes_fields():
    items = [{"title": "삼성전자 수주", "tickers": []}]
    items += [{"title": f"item {i}", "tickers": [f"{i:06d}"]} for i in range(1, 11)]
    got = ui_actions.mentioned_tickers(items)
    assert got[0] == "005930.KS"
    assert len(got) == 8
    assert "000008.KS" not in got


def test_mentioned_tickers_alphanumeric_kr_and_nonpositive_limit():
    got = ui_actions.mentioned_tickers([
        {"title": "신규 $0167A0", "tickers": ["0167A0"]},
    ])
    assert got == ["0167A0.KS"]
    assert ui_actions.mentioned_tickers([{"title": "$NVDA", "tickers": ["AAPL"]}], limit=0) == []
    assert ui_actions.mentioned_tickers([{"title": "$NVDA", "tickers": ["AAPL"]}], limit=-3) == []
