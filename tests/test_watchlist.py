#!/usr/bin/env python3
"""test_watchlist.py — 실시간 워치리스트 선택 순수함수 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kis_stream as ks


def test_dedup_case_insensitive_and_order():
    sel, dropped = ks.select_watchlist(["005930", "000660", "005930"],
                                       ["AAPL", "aapl", "MSFT"], kr_max=10, us_max=10)
    assert sel["KR"] == ["005930", "000660"]
    assert sel["US"] == ["AAPL", "MSFT"]          # 대소문자 중복 제거
    assert dropped["KR"] == [] and dropped["US"] == []


def test_cap_truncates_and_reports_dropped():
    sel, dropped = ks.select_watchlist(["005930", "000660", "035720"],
                                       ["AAPL", "MSFT", "NVDA", "GOOGL"], kr_max=2, us_max=2)
    assert sel["KR"] == ["005930", "000660"] and sel["US"] == ["AAPL", "MSFT"]
    assert dropped["KR"] == ["035720"] and dropped["US"] == ["NVDA", "GOOGL"]   # 우선순위 밖 드롭


def test_empty_inputs():
    sel, dropped = ks.select_watchlist([], [], kr_max=5, us_max=5)
    assert sel == {"KR": [], "US": []} and dropped == {"KR": [], "US": []}


def test_blank_symbols_filtered():
    sel, _ = ks.select_watchlist(["", "  ", "005930"], [None, "AAPL"], kr_max=10, us_max=10)
    assert sel["KR"] == ["005930"] and sel["US"] == ["AAPL"]


def test_classify_kr_us_strips_suffix():
    """KR 6자리(.KS/.KQ 포함)→바코드 KR, 그 외→US. 빈/None 무시."""
    kr, us = [], []
    for t in ["005930", "035720.KS", "373220.KQ", "AAPL", "MSFT", "", None]:
        ks._classify(t, kr, us)
    assert kr == ["005930", "035720", "373220"]   # 접미 제거 후 바코드
    assert us == ["AAPL", "MSFT"]


def test_core_us_includes_qqq():
    assert "QQQ" in ks._CORE_US                     # 벤치마크 코어 기본값


def test_compute_watchlist_streams_core_qqq():
    """compute_watchlist 는 보유/알림과 무관하게 QQQ(코어)를 최우선 포함."""
    sel, _ = ks.compute_watchlist()
    assert "QQQ" in sel["US"]                        # 코어는 캡 안에서 항상 생존(최우선)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
