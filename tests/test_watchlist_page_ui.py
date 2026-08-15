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
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})

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
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})

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
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})

    def _boom(*a, **k):
        raise AssertionError("검색어 없는데 congress_trading 호출됨")
    monkeypatch.setattr(_cached, "congress_trading", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert not at.exception, str(at.exception)
    caption_body = " ".join(str(c.value) for c in at.caption)
    assert "의원 이름을 입력하면" in caption_body


def test_screening_sections_hidden_until_hub_toggle_on(monkeypatch):
    """스크리닝/정치인 섹션도 기관허브 토글 뒤 — 첫 렌더에 호출되면 안 된다."""
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    def _boom(*a, **k):
        raise AssertionError("토글 꺼진 상태에서 스크리닝이 호출됨")
    monkeypatch.setattr(_cached, "institution_screener", _boom)
    monkeypatch.setattr(_cached, "congress_top_traded", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_screening_sections_render_when_hub_toggle_on(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [
                            {"key": "berkshire", "display_name": "버크셔", "category": "holding_company",
                             "source_kind": "13f", "freshness": "fresh", "holdings_count": 1,
                             "availability_flags": {}, "primary_sources": [],
                             "top_holdings": [{"ticker": "AAPL", "issuer": "APPLE", "value_usd": 900.0}],
                             "total_value_usd": 1000.0}],
                        "comparison": {"rows": []},
                        "analysis": {"summary": "", "shared_moves": [], "divergences": [],
                                    "confidence": 0.0, "mode": "heuristic"}})
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [{"ticker": "SMCI", "name": "Super Micro",
                                                    "institutions": ["berkshire"], "count": 1,
                                                    "avg_delta_pct": 0.01}],
                                     "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded",
                        lambda days=90: {"bought": [{"ticker": "NVDA", "member_count": 3,
                                                     "members": ["A", "B", "C"],
                                                     "total_amount_mid": 300000}],
                                        "sold": []})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    assert "공통 움직임" in body
    assert "정치인" in body
    dataframes = [df.value for df in at.dataframe]
    assert any("SMCI" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)
    assert any("NVDA" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)


def test_screen_explain_button_gates_llm_call(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})

    calls = []
    monkeypatch.setattr(_cached, "institution_screen_explain",
                        lambda screen, congress: calls.append(1) or
                        {"summary": "설명", "confidence": 0.5, "mode": "llm"})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert calls == [], "버튼 안 눌렀는데 LLM 해설이 호출됨"

    at.button(key="_watch_screen_explain_btn").click().run()

    assert not at.exception, str(at.exception)
    assert calls == [1]
    body = " ".join(str(m.value) for m in at.markdown)
    assert "설명" in body


_ROWS_WITH_FOLDERS = [
    {"ticker": "PLTR", "name": "Palantir", "reason": "AI 테마", "source": "manual",
     "note": None, "added_at": "2026-08-01T00:00:00", "folder": "반도체"},
    {"ticker": "NVDA", "name": "엔비디아", "reason": "GPU 수요", "source": "manual",
     "note": None, "added_at": "2026-07-15T00:00:00", "folder": "미분류"},
]


def test_folder_filter_shows_only_matching_rows(monkeypatch):
    """감사 후속 — 폴더별로 관심종목을 분리해서 볼 수 있어야 한다."""
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS_WITH_FOLDERS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(data, "watchlist_folders", lambda: ["미분류", "반도체"])

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert len(at.dataframe[0].value) == 2

    at.selectbox(key="_watchlist_folder_filter").set_value("반도체").run()

    assert not at.exception, str(at.exception)
    df = at.dataframe[0].value
    assert list(df["티커"]) == ["PLTR"]


def test_folder_move_button_calls_move_watchlist_folder(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS_WITH_FOLDERS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(data, "watchlist_folders", lambda: ["미분류", "반도체"])

    calls = []
    monkeypatch.setattr(data, "move_watchlist_folder",
                        lambda ticker, folder: calls.append((ticker, folder)) or True)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.selectbox(key="_watchlist_move_ticker").set_value("NVDA").run()
    at.selectbox(key="_watchlist_move_folder_pick").set_value("반도체").run()
    at.button(key="_watchlist_move_btn").click().run()

    assert not at.exception, str(at.exception)
    assert calls == [("NVDA", "반도체")]
