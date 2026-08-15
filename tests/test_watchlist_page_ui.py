"""tests/test_watchlist_page_ui.py — 관심종목 페이지 재구성 (감사 후속).

기존 페이지는 (1) 기관투자자 허브의 LLM 요약(최대 20초)이 매 렌더마다 동기 호출돼
"느리다", (2) 내 관심종목 표에 티커/사유/추가일뿐 가격이 없어 "정보가 없다",
(3) 무관한 두 기능(기관 허브 vs 내 관심종목)이 섞여 "쓰는 법을 모르겠다"는
불만으로 이어졌다. 이 테스트는 재구성된 페이지가 실제로 그 문제를 없앴는지 검증한다.

AppTest.from_string 스크립트는 이 프로세스와 같은 모듈 객체를 공유하므로(별도 서브
프로세스가 아님), 스크립트 안에서 직접 속성을 대입하면 pytest monkeypatch 의 자동
복원 없이 다른 테스트 파일로 오염이 새어나간다 — 반드시 바깥쪽 monkeypatch 로 패치.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from dashboard import cached, data  # noqa: E402

_RUN_SCRIPT = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import watchlist
watchlist.render()
"""

_ROWS = [
    {"ticker": "PLTR", "name": "Palantir", "reason": "AI 테마", "source": "manual",
     "note": None, "added_at": "2026-08-01T00:00:00"},
    {"ticker": "NVDA", "name": "엔비디아", "reason": "GPU 수요", "source": "manual",
     "note": None, "added_at": "2026-07-15T00:00:00"},
]


def test_first_render_never_touches_institution_hub(monkeypatch):
    """토글 기본값 False — 첫 렌더에서 institution_watch 가 호출되면 안 된다(감사 후속 성능수정)."""
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {"PLTR": {"price": 30.0, "chg_pct": 1.5}})

    def _boom(*a, **k):
        raise AssertionError("institution_watch 호출됨 — 토글 꺼진 상태에서 호출되면 안 됨")
    monkeypatch.setattr(cached, "institution_watch", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_watchlist_table_shows_price_and_change_columns(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {
        "PLTR": {"price": 30.0, "chg_pct": 1.5},
        "NVDA": {"price": 176.0, "chg_pct": -2.2},
    })
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    df = at.dataframe[0].value
    assert list(df["현재가"]) == ["$30.00", "$176.00"]
    assert list(df["등락률"]) == ["+1.50%", "-2.20%"]


def test_watchlist_search_filters_rows(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert len(at.dataframe[0].value) == 2

    at.text_input(key="_watchlist_search").set_value("nvda").run()
    assert not at.exception, str(at.exception)
    df = at.dataframe[0].value
    assert list(df["티커"]) == ["NVDA"]


def test_empty_watchlist_shows_info_without_calling_quotes(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: [])

    def _boom(*a, **k):
        raise AssertionError("관심종목이 비었는데 시세 조회가 호출됨")
    monkeypatch.setattr(cached, "watchlist_quotes", _boom)
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("비어 있습니다" in str(m.value) for m in at.info)


def test_toggling_hub_on_loads_institutions_without_llm_call(monkeypatch):
    """허브 토글을 켜면 기관 카드는 뜨지만, LLM 버튼을 안 눌렀으면 with_llm_summary=True 호출은 없다."""
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    calls = []

    def _institution_watch(keys=None, with_llm_summary=False):
        calls.append(with_llm_summary)
        return {
            "institutions": [{"key": "buffett", "display_name": "버크셔", "category": "asset_manager",
                              "source_kind": "13f", "freshness": "fresh", "holdings_count": 5,
                              "availability_flags": {}, "primary_sources": []}],
            "comparison": {"rows": []},
            "analysis": {"summary": "", "shared_moves": [], "divergences": [], "confidence": 0.0,
                        "mode": "heuristic"},
        }
    monkeypatch.setattr(cached, "institution_watch", _institution_watch)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    assert "버크셔" in body
    caption_body = " ".join(str(c.value) for c in at.caption)
    assert "버튼을 누르면" in caption_body, "LLM 버튼 안 눌렀는데 요약이 이미 뜸 — 지연 로딩 위반"
    assert True not in calls, "with_llm_summary=True 로 호출됨 — 버튼 안 눌렀는데 LLM 요청됨"


def test_congress_section_hidden_until_hub_toggle_on(monkeypatch):
    """정치인 거래 섹션도 기관허브와 같은 토글 뒤에 있어 첫 렌더에 실행되면 안 된다."""
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    def _boom(*a, **k):
        raise AssertionError("congress_trading 호출됨 — 허브 토글 꺼진 상태에서 호출되면 안 됨")
    monkeypatch.setattr(_cached, "congress_trading", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_congress_section_shows_transactions_for_searched_member(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})

    def _fake_congress_trading(name):
        assert name == "Pelosi"
        return [{"date": "01/16/2026", "ticker": "GOOGL", "asset": "Alphabet Inc.",
                 "type": "Purchase", "amount": "$500,001 - $1,000,000", "owner": "Spouse"}]
    monkeypatch.setattr(_cached, "congress_trading", _fake_congress_trading)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()
    at.text_input(key="_congress_member_search").set_value("Pelosi").run()

    assert not at.exception, str(at.exception)
    dataframes = [df.value for df in at.dataframe]
    assert any("GOOGL" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)


def test_congress_section_no_op_caption_when_search_blank(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})

    def _boom(*a, **k):
        raise AssertionError("검색어 없는데 congress_trading 호출됨")
    monkeypatch.setattr(_cached, "congress_trading", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert not at.exception, str(at.exception)
    caption_body = " ".join(str(c.value) for c in at.caption)
    assert "의원 이름을 입력하면" in caption_body
