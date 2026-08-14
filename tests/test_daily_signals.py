"""tests/test_daily_signals.py — detect_signals 타임스탬프 KST 명시 (감사 #35)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import reports.daily_signals as ds


class _FakeTicker:
    info: dict = {}
    news: list = []

    def history(self, period="1mo"):
        return pd.DataFrame()  # 빈 이력 — 가격 신호 계산은 조기 반환, timestamp 는 영향 없음


def test_detect_signals_timestamp_uses_kst_not_naive_local_time(monkeypatch):
    """timestamp 가 naive datetime.now()(서버 로컬, 보통 UTC) 라 KST 기준
    날짜/시각과 어긋날 수 있었음 — datetime.now(KST) 로 명시해야 한다."""

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return cls(2026, 8, 15, 0, 30, 0, tzinfo=tz)   # KST 자정 직후
            return cls(2026, 8, 14, 15, 30, 0)                  # naive UTC (버그 시나리오)

    monkeypatch.setattr(ds, "datetime", _FakeDatetime)

    result = ds.detect_signals("MSFT", ticker_obj=_FakeTicker())

    assert result["timestamp"] == "2026-08-15 00:30:00"
