#!/usr/bin/env python3
"""test_lib_utils.py — 리팩토링 공유 유틸 (lib/price_utils·http_utils) 단위 (무네트워크)."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_window_feats_values():
    from lib.price_utils import window_feats
    idx = pd.bdate_range("2025-01-01", periods=30)
    closes = pd.Series([100.0 + i for i in range(30)], index=idx)   # 단조 상승
    mom, vol = window_feats(closes, idx[25])       # 직전 21봉 = idx[4..24]
    assert mom == round(124 / 104 - 1, 4)          # closes[24]/closes[4]-1 (4자리 반올림)
    assert vol is not None and vol >= 0


def test_window_feats_insufficient():
    from lib.price_utils import window_feats
    idx = pd.bdate_range("2025-01-01", periods=30)
    closes = pd.Series(range(30), index=idx, dtype=float)
    assert window_feats(closes, idx[10]) == (None, None)            # 직전 10봉 < 21 → 결측


def test_http_utils_importable():
    from lib.http_utils import http_get, DEFAULT_UA, EDGAR_UA
    assert "Mozilla" in DEFAULT_UA and "@" in EDGAR_UA and callable(http_get)


def test_file_cache_roundtrip_and_ttl(tmp_path):
    import time
    from lib.file_cache import is_fresh, read_json, write_json_atomic
    p = tmp_path / "c.json"
    assert is_fresh(p, 12) is False                 # 파일 없음
    assert write_json_atomic(p, {"a": 1, "b": [2, 3]}) is True
    assert is_fresh(p, 12) is True and read_json(p) == {"a": 1, "b": [2, 3]}
    os.utime(p, (time.time() - 100000, time.time() - 100000))   # ~27h 전
    assert is_fresh(p, 12) is False                 # TTL 초과 → stale
    assert read_json(tmp_path / "none.json") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
