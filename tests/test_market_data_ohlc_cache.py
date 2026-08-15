"""tests/test_market_data_ohlc_cache.py — OHLC 디스크 캐시 경로 격리 가능성 (감사 후속 #36).

providers.market_data 의 OHLC parquet 캐시가 실제 ~/reports/ml-cache/ohlc_cache/
를 하드코딩해 테스트 간(같은 자리표시 티커 재사용 시) 캐시가 새던 문제 —
STOCK_REPORT_OHLC_CACHE_DIR 환경변수로 루트를 override 할 수 있어야 한다.
"""
from __future__ import annotations

import pandas as pd

import providers.market_data as md


def _frame():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    return pd.DataFrame({
        "Open": [1.0, 2.0, 3.0], "High": [1.5, 2.5, 3.5], "Low": [0.5, 1.5, 2.5],
        "Close": [1.2, 2.2, 3.2], "Volume": [100.0, 200.0, 300.0],
    }, index=idx)


def test_ohlc_cache_root_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_OHLC_CACHE_DIR", str(tmp_path / "custom_ohlc"))
    assert md._ohlc_cache_root() == tmp_path / "custom_ohlc"


def test_ohlc_cache_written_under_one_root_is_invisible_under_another(monkeypatch, tmp_path):
    """감사 후속 #36 — 서로 다른 캐시 루트를 쓰면 같은 티커라도 캐시가 섞이지
    않아야 한다(테스트 간 격리의 기반)."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"

    monkeypatch.setenv("STOCK_REPORT_OHLC_CACHE_DIR", str(root_a))
    assert md.save_cached_ohlc("TST", "1d", _frame()) is True
    assert md.load_cached_ohlc("TST", "1d") is not None

    monkeypatch.setenv("STOCK_REPORT_OHLC_CACHE_DIR", str(root_b))
    assert md.load_cached_ohlc("TST", "1d") is None, "다른 캐시 루트인데 이전 루트의 데이터가 보임"
