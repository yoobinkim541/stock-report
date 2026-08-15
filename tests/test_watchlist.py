"""tests/test_watchlist.py — lib/watchlist.py 관심종목 CRUD (store.py SQLite 백엔드)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import watchlist  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "store.db"))


def test_add_ticker_creates_entry(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    entry = watchlist.add_ticker("aapl", reason="버핏 신규 편입", source="notable_investor:berkshire")

    assert entry["ticker"] == "AAPL"
    assert entry["reason"] == "버핏 신규 편입"
    assert entry["source"] == "notable_investor:berkshire"
    assert entry["note"] is None
    assert entry["added_at"]
    assert entry["updated_at"] == entry["added_at"]


def test_add_ticker_upserts_existing_and_keeps_original_added_at(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    first = watchlist.add_ticker("NVDA", reason="첫 사유", source="manual")
    second = watchlist.add_ticker("nvda", reason="갱신된 사유", source="notable_investor:berkshire",
                                  note="재확인")

    all_entries = watchlist.list_watchlist()
    assert len(all_entries) == 1
    assert second["reason"] == "갱신된 사유"
    assert second["source"] == "notable_investor:berkshire"
    assert second["note"] == "재확인"
    assert second["added_at"] == first["added_at"]           # 최초 추가 시각 보존(대소문자 정규화도 함께 검증)


def test_list_watchlist_sorted_by_added_at_descending(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    watchlist.add_ticker("AAA", reason="첫번째", source="manual")
    watchlist.add_ticker("BBB", reason="두번째", source="manual")
    watchlist.add_ticker("CCC", reason="세번째", source="manual")

    tickers = [e["ticker"] for e in watchlist.list_watchlist()]
    assert tickers == ["CCC", "BBB", "AAA"]


def test_remove_ticker_deletes_and_returns_true(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    watchlist.add_ticker("MSFT", reason="테스트", source="manual")

    ok = watchlist.remove_ticker("msft")

    assert ok is True
    assert watchlist.list_watchlist() == []


def test_remove_ticker_missing_returns_false(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    assert watchlist.remove_ticker("ZZZZ") is False


def test_list_watchlist_empty_by_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    assert watchlist.list_watchlist() == []


def test_add_ticker_defaults_to_unsorted_folder(monkeypatch, tmp_path):
    """감사 후속 — 종목 분석페이지 ⭐ 추가 요청: 폴더 미지정 시 '미분류'."""
    _isolate(monkeypatch, tmp_path)

    entry = watchlist.add_ticker("AAPL", reason="사유", source="manual")

    assert entry["folder"] == "미분류"


def test_add_ticker_accepts_explicit_folder(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    entry = watchlist.add_ticker("AAPL", reason="사유", source="manual", folder="반도체")

    assert entry["folder"] == "반도체"


def test_add_ticker_upsert_keeps_folder_when_not_specified(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    watchlist.add_ticker("AAPL", reason="첫 사유", source="manual", folder="반도체")

    second = watchlist.add_ticker("AAPL", reason="갱신 사유", source="manual")

    assert second["folder"] == "반도체"


def test_set_folder_moves_existing_ticker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    watchlist.add_ticker("AAPL", reason="사유", source="manual")

    ok = watchlist.set_folder("aapl", "성장주")

    assert ok is True
    entry = next(e for e in watchlist.list_watchlist() if e["ticker"] == "AAPL")
    assert entry["folder"] == "성장주"


def test_set_folder_missing_ticker_returns_false(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    assert watchlist.set_folder("ZZZZ", "성장주") is False


def test_set_folder_blank_falls_back_to_unsorted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    watchlist.add_ticker("AAPL", reason="사유", source="manual", folder="반도체")

    watchlist.set_folder("AAPL", "  ")

    entry = next(e for e in watchlist.list_watchlist() if e["ticker"] == "AAPL")
    assert entry["folder"] == "미분류"


def test_list_folders_includes_unsorted_and_dedupes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    watchlist.add_ticker("AAPL", reason="사유", source="manual", folder="반도체")
    watchlist.add_ticker("NVDA", reason="사유", source="manual", folder="반도체")
    watchlist.add_ticker("KO", reason="사유", source="manual")

    assert watchlist.list_folders() == ["미분류", "반도체"]


def test_list_folders_empty_watchlist_still_has_unsorted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    assert watchlist.list_folders() == ["미분류"]
