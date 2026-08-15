"""tests/test_portfolio_tracker_isolation.py — DATA_DIR 격리 (감사 후속 #37).

portfolio_tracker 의 레거시 마이그레이션 원본(qqqi_dividends.json 등)이 실제
홈 디렉터리를 하드코딩해, store DB 는 테스트 격리돼도 첫 조회 시 legacy 파일의
실제 배당 기록이 자동 마이그레이션돼 "빈 상태에서 시작" 가정이 깨지던 문제.
"""
from __future__ import annotations

import os

import portfolio_tracker as pt


def test_data_dir_respects_env_override():
    assert str(pt.DATA_DIR) == os.environ["STOCK_REPORT_DATA_DIR"]


def test_dividend_summary_starts_empty_without_leaking_real_legacy_file():
    """감사 후속 #37 — DATA_DIR 이 격리되지 않으면 이 assert 가 실제 홈 디렉터리의
    qqqi_dividends.json 내용에 따라 통과/실패가 갈렸다(순서·환경 의존)."""
    summary = pt.get_dividend_summary()
    assert summary["count"] == 0
    assert summary["records"] == []

    pt.record_dividend(22.15, "ORCL", "테스트 배당")
    summary = pt.get_dividend_summary()

    assert summary["count"] == 1
    assert abs(summary["total"] - 22.15) < 1e-6
