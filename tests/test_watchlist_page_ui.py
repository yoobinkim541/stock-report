"""tests/test_watchlist_page_ui.py — 관심종목 페이지 (토스식 리스트 · 접지 않는 관리·투자자 섹션).

기존 페이지는 (1) 기관투자자 허브의 LLM 요약(최대 20초)이 매 렌더마다 동기 호출돼
"느리다", (2) 내 관심종목 표에 가격이 없어 "정보가 없다", (3) 토글·접기 뒤에 기능이
숨어 "눌러 봐야 보인다"는 불만으로 이어졌다. 이 테스트는 지금 페이지가 가격·등락을
리스트로 바로 보여주고, 투자자 섹션을 토글 없이 그리되 LLM 은 버튼으로만 부르는지 검증한다.

AppTest.from_string 스크립트는 이 프로세스와 같은 모듈 객체를 공유하므로(별도 서브
프로세스가 아님), 스크립트 안에서 직접 속성을 대입하면 pytest monkeypatch 의 자동
복원 없이 다른 테스트 파일로 오염이 새어나간다 — 반드시 바깥쪽 monkeypatch 로 패치.
"""
from __future__ import annotations

import os
import re
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

_EMPTY_HUB = {"institutions": [], "comparison": {}, "analysis": {}}


@pytest.fixture(autouse=True)
def _stub_investor_data(monkeypatch):
    """투자자 섹션은 늘 그려지므로 기본은 빈 데이터 — 네트워크·13F 파일을 건드리지 않게."""
    monkeypatch.setattr(cached, "institution_watch", lambda *a, **k: _EMPTY_HUB)
    monkeypatch.setattr(cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})
    monkeypatch.setattr(cached, "watchlist_investor_moves", lambda tickers: {})
    monkeypatch.setattr(cached, "holdings", lambda: [])


def _lists(at) -> list[str]:
    return [str(m.value) for m in at.markdown if 'class="tn-sl"' in str(m.value)]


def _watch_list(at) -> str:
    return _lists(at)[0]


def _tickers(html: str) -> list[str]:
    return re.findall(r'href="\?tk=([^"]+)"', html)


def _values(html: str) -> list[str]:
    return re.findall(r'class="tn-sl-val">([^<]*)<', html)


def test_first_render_shows_investor_sections_without_toggle_or_llm(monkeypatch):
    """토글 없이 기관 카드까지 바로 보이지만, LLM 버튼을 안 눌렀으면 with_llm_summary=True 호출은 없다."""
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
    assert len(at.toggle) == 0
    assert len(at.expander) == 0
    body = " ".join(str(m.value) for m in at.markdown)
    assert "버크셔" in body
    caption_body = " ".join(str(c.value) for c in at.caption)
    assert "버튼을 누르면" in caption_body, "LLM 버튼 안 눌렀는데 요약이 이미 뜸 — 지연 로딩 위반"
    assert True not in calls, "with_llm_summary=True 로 호출됨 — 버튼 안 눌렀는데 LLM 요청됨"


def test_watchlist_list_shows_price_and_change_and_links_to_ticker(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {
        "PLTR": {"price": 30.0, "chg_pct": 1.5},
        "NVDA": {"price": 176.0, "chg_pct": -2.2},
    })

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    html = _watch_list(at)
    assert _tickers(html) == ["PLTR", "NVDA"]
    assert _values(html) == ["$30.00", "$176.00"]
    assert "+1.50%" in html and "-2.20%" in html
    assert "상승 1 · 하락 1" in html


def test_watchlist_list_shows_krw_price_mixed_basis_and_per_ticker_failure(monkeypatch):
    rows = [
        {"ticker": "005930.KS", "name": "삼성전자", "reason": "메모리", "source": "manual",
         "note": None, "added_at": "2026-08-01T00:00:00"},
        *_ROWS,
    ]
    monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {
        "005930.KS": {"price": 61200.0, "chg_pct": 0.5, "as_of": "2026-10-23",
                      "currency": "KRW", "source": "yfinance"},
        "PLTR": {"error": "시세 없음"},
        "NVDA": {"price": 176.0, "chg_pct": -2.2, "as_of": "2026-10-22",
                 "currency": "USD", "source": "yfinance"},
    })

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    html = _watch_list(at)
    assert _values(html) == ["₩61,200", "—", "$176.00"]
    assert "시세 미확인 1개" in html
    assert "2026-10-22 기준" in html, "다른 날짜 일봉 종목은 행에 기준일이 보여야 한다"


def test_sort_by_decline_puts_missing_quotes_last(monkeypatch):
    rows = [*_ROWS, {"ticker": "AMD", "name": "AMD", "reason": "", "source": "manual",
                     "note": None, "added_at": "2026-07-01T00:00:00"}]
    monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {
        "PLTR": {"price": 30.0, "chg_pct": 1.5},
        "NVDA": {"price": 176.0, "chg_pct": -2.2},
    })

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.button_group(key="_watchlist_sort").set_value("하락률").run()

    assert not at.exception, str(at.exception)
    assert _tickers(_watch_list(at)) == ["NVDA", "PLTR", "AMD"]


def test_add_and_remove_buttons_use_shared_watchlist_store(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    added, removed = [], []
    monkeypatch.setattr(data, "add_watchlist_ticker",
                        lambda ticker, note=None, folder=None: added.append((ticker, note)) or (True, ticker.upper()))
    monkeypatch.setattr(data, "remove_watchlist_ticker", lambda ticker: removed.append(ticker) or True)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.text_input(key="_watchlist_add_ticker").set_value("amd")
    at.text_input(key="_watchlist_add_note").set_value("MI450")
    at.button(key="_watchlist_add_btn").click().run()
    # 성공하면 입력칸이 비워져 같은 티커를 다시 누르는 중복 추가를 막는다
    assert at.text_input(key="_watchlist_add_ticker").value == ""
    assert at.text_input(key="_watchlist_add_note").value == ""
    at.selectbox(key="_watchlist_remove_ticker").set_value("NVDA").run()
    at.button(key="_watchlist_remove_btn").click().run()
    assert removed == []
    at.button(key="_watchlist_remove_btn_yes").click().run()

    assert not at.exception, str(at.exception)
    assert added == [("amd", "MI450")]
    assert removed == ["NVDA"]


def test_add_shows_validation_error_instead_of_saving(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(data, "add_watchlist_ticker",
                        lambda ticker, note=None, folder=None: (False, "티커 형식이 아닙니다: '$X'"))

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.text_input(key="_watchlist_add_ticker").set_value("$X")
    at.button(key="_watchlist_add_btn").click().run()

    assert not at.exception, str(at.exception)
    assert any("티커 형식" in str(e.value) for e in at.error)


def test_watchlist_search_filters_rows(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert _tickers(_watch_list(at)) == ["PLTR", "NVDA"]

    at.text_input(key="_watchlist_search").set_value("nvda").run()
    assert not at.exception, str(at.exception)
    assert _tickers(_watch_list(at)) == ["NVDA"]


def test_empty_watchlist_shows_info_without_calling_quotes(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: [])

    def _boom(*a, **k):
        raise AssertionError("관심종목이 비었는데 시세 조회가 호출됨")
    monkeypatch.setattr(cached, "watchlist_quotes", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("비어 있습니다" in str(m.value) for m in at.info)


def test_congress_section_shows_transactions_for_searched_member(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    def _fake_congress_trading(name):
        assert name == "Pelosi"
        return [{"date": "01/16/2026", "ticker": "GOOGL", "asset": "Alphabet Inc.",
                 "type": "Purchase", "amount": "$500,001 - $1,000,000", "owner": "Spouse"}]
    monkeypatch.setattr(cached, "congress_trading", _fake_congress_trading)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.text_input(key="_congress_member_search").set_value("Pelosi").run()

    assert not at.exception, str(at.exception)
    dataframes = [df.value for df in at.dataframe]
    assert any("GOOGL" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)


def test_congress_section_no_op_caption_when_search_blank(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    def _boom(*a, **k):
        raise AssertionError("검색어 없는데 congress_trading 호출됨")
    monkeypatch.setattr(cached, "congress_trading", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    caption_body = " ".join(str(c.value) for c in at.caption)
    assert "의원 이름을 입력하면" in caption_body


def test_screening_and_congress_lists_render_on_first_load(monkeypatch):
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
    monkeypatch.setattr(cached, "institution_screener",
                        lambda keys: {"new_buys": [{"ticker": "SMCI", "name": "Super Micro",
                                                    "institutions": ["berkshire"], "count": 1,
                                                    "avg_delta_pct": 0.01}],
                                     "increased": [], "decreased": []})
    monkeypatch.setattr(cached, "congress_top_traded",
                        lambda days=90: {"bought": [{"ticker": "NVDA", "member_count": 3,
                                                     "members": ["A", "B", "C"],
                                                     "total_amount_mid": 300000}],
                                        "sold": []})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    assert "공통 움직임" in body
    assert "정치인" in body
    lists = " ".join(_lists(at)[1:])
    assert "SMCI" in _tickers(lists) and "NVDA" in _tickers(lists)
    assert "기관 1곳" in lists and "+1.0%p" in lists
    assert "$300.0K" in lists and "의원 3명" in lists


def test_screen_explain_button_gates_llm_call(monkeypatch):
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    calls = []
    monkeypatch.setattr(cached, "institution_screen_explain",
                        lambda screen, congress: calls.append(1) or
                        {"summary": "설명", "confidence": 0.5, "mode": "llm"})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

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
    assert _tickers(_watch_list(at)) == ["PLTR", "NVDA"]

    at.button_group(key="_watchlist_folder_filter").set_value("반도체").run()

    assert not at.exception, str(at.exception)
    assert _tickers(_watch_list(at)) == ["PLTR"]


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


def test_editor_persists_normalized_symbols_and_rejects_invalid_input(monkeypatch, tmp_path):
    from lib import watchlist

    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "watchlist.sqlite3"))
    assert data.add_watchlist_ticker("005930", note="국내", folder="관찰") == (True, "005930.KS")
    assert data.add_watchlist_ticker("!!!")[0] is False
    rows = watchlist.list_watchlist()
    assert [(r["ticker"], r["note"], r["folder"]) for r in rows] == [
        ("005930.KS", "국내", "관찰")
    ]
    assert data.remove_watchlist_ticker("005930.KS")
    assert watchlist.list_watchlist() == []


def test_my_stocks_section_buckets_watchlist_and_holdings_by_investor_action(monkeypatch):
    """관심종목·보유 미국 종목만 13F 로 대조 — 보유자 수 순, 매매는 행동별 칸으로."""
    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS + [
        {"ticker": "005930", "name": "삼성전자", "source": "manual", "added_at": "2026-07-01T00:00:00"}])
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "holdings", lambda: [{"ticker": "AAPL", "name": "애플"}])
    seen = {}

    def _moves(tickers):
        seen["tickers"] = tuple(tickers)
        return {
            "NVDA": [{"key": "a", "short_name": "투자자A", "action": "added", "weight_pct": 6.0,
                      "shares_change_pct": 0.5},
                     {"key": "b", "short_name": "투자자B", "action": "held", "weight_pct": 2.0}],
            "AAPL": [{"key": "c", "short_name": "투자자C", "action": "held", "weight_pct": 20.0},
                     {"key": "d", "short_name": "투자자D", "action": "exited", "weight_pct": 0.0,
                      "prior_weight_pct": 3.0}],
        }
    monkeypatch.setattr(cached, "watchlist_investor_moves", _moves)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    assert set(seen["tickers"]) == {"PLTR", "NVDA", "AAPL"}, "국내 종목은 13F 대조 대상이 아님"
    lists = _lists(at)
    holders = next(h for h in lists if "보유 중인 유명 투자자" in h)
    assert _tickers(holders) == ["NVDA", "AAPL"]
    added = next(h for h in lists if "<b>추가 매수</b>" in h)
    exited = next(h for h in lists if "<b>전량 매도</b>" in h and "투자자D" in h)
    assert _tickers(added) == ["NVDA"] and "+50%" in added
    assert _tickers(exited) == ["AAPL"] and "직전 최대 3.0%" in exited
