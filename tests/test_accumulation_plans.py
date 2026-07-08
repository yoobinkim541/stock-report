"""lib/accumulation — 자동 모으기 플랜 (store 격리·무네트워크).

conftest 가 STOCK_REPORT_DB 를 tmp 로 리다이렉트 — store 문서 격리.
"""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import accumulation as ac  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    ac.save_plans([])
    yield
    ac.save_plans([])


def test_upsert_remove_roundtrip():
    msg = ac.upsert_plan("nvda", 5000, "KRW", "매일")
    assert msg.startswith("🔁") and "NVDA" in msg
    p = ac.plan_for("NVDA")
    assert p and p["amount"] == 5000 and p["currency"] == "KRW" and p["enabled"]
    ac.upsert_plan("NVDA", 10.0, "USD", "매주")            # 같은 티커 교체
    p2 = ac.plan_for("nvda")
    assert p2["currency"] == "USD" and p2["freq"] == "매주"
    assert len(ac.load_plans()) == 1
    assert ac.remove_plan("NVDA") and ac.plan_for("NVDA") is None
    assert ac.upsert_plan("X", 0, "KRW", "매일").startswith("❌")
    assert ac.upsert_plan("X", 1, "KRW", "격주").startswith("❌")


def test_due_today_freqs():
    base = {"enabled": True, "freq": "매일", "last_run": None}
    assert ac.due_today(base, date(2026, 7, 8))                       # 첫 실행
    daily = dict(base, last_run="2026-07-07")
    assert ac.due_today(daily, date(2026, 7, 8))
    assert not ac.due_today(dict(base, last_run="2026-07-08"), date(2026, 7, 8))  # 멱등
    weekly = dict(base, freq="매주", last_run="2026-07-06")           # 월요일 실행됨
    assert not ac.due_today(weekly, date(2026, 7, 8))                 # 같은 ISO 주
    assert ac.due_today(weekly, date(2026, 7, 13))                    # 다음 주 첫 거래일
    monthly = dict(base, freq="매월", last_run="2026-07-01")
    assert not ac.due_today(monthly, date(2026, 7, 31))
    assert ac.due_today(monthly, date(2026, 8, 3))
    assert not ac.due_today(dict(base, enabled=False), date(2026, 7, 8))


def test_run_once_records_and_marks():
    ac.upsert_plan("NVDA", 14_000, "KRW", "매일")
    ac.upsert_plan("MSFT", 10.0, "USD", "매일")
    ac.upsert_plan("UNH", 5_000, "KRW", "매일")
    ac.set_enabled("UNH", False)                                      # OFF → 제외
    session = date(2026, 7, 8)
    calls = []

    def record(t, qty, price, note):
        calls.append((t, qty, price, note))
        return "ok"

    res = ac.run_once(get_close=lambda t: (200.0, session) if t != "UNH" else None,
                      get_fx=lambda: 1400.0, record=record)
    assert len(res["recorded"]) == 2 and not res["errors"]
    by = {c[0]: c for c in calls}
    assert by["NVDA"][1] == pytest.approx(round(14_000 / 1400.0 / 200.0, 4))  # 0.05주
    assert by["MSFT"][1] == pytest.approx(0.05)
    assert "종가" in by["NVDA"][3] and "@1,400" in by["NVDA"][3]
    assert ac.plan_for("NVDA")["last_run"] == "2026-07-08"            # 멱등 마킹
    # 재실행 — 전부 스킵 (멱등)
    res2 = ac.run_once(get_close=lambda t: (200.0, session),
                       get_fx=lambda: 1400.0, record=record)
    assert not res2["recorded"] and len(calls) == 2


def test_run_once_holiday_skip():
    ac.upsert_plan("NVDA", 10.0, "USD", "매일")
    res = ac.run_once(get_close=lambda t: None,                       # 휴장
                      get_fx=lambda: 1400.0, record=lambda *a: "ok")
    assert not res["recorded"] and any("휴장" in s for s in res["skipped"])
    assert ac.plan_for("NVDA")["last_run"] is None                    # 미마킹
