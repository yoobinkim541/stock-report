"""tests/test_safety_hardening.py — 비평 후속 안전장치 단위 테스트 (무네트워크).

- safe_io: 원자적 쓰기 + 교차 프로세스 쓰기 락(lost update 방지)
- barbell_strategy.leverage_dca_guard: 변동성 캡·절대 상한·낙폭 정지
- barbell_strategy.fetch_qqq_data: 가격 stale 플래그
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import safe_io
import barbell_strategy as b
# fetch_qqq_data·_history_cached·_update_drawdown_anchor 는 providers/market_data.py 로 이전됨.
# fetch_qqq_data 는 market_data 의 모듈 전역 _history_cached/_update_drawdown_anchor 를 참조하므로
# monkeypatch 대상은 providers.market_data (barbell 의 재export 가 아닌 실제 정의 모듈)여야 한다.
import providers.market_data as md


# ── safe_io ───────────────────────────────────────────────────────────────────
def test_atomic_write_round_trip(tmp_path):
    p = str(tmp_path / "snap.json")
    safe_io.atomic_write_json(p, {"a": 1, "한글": "값"})
    assert json.load(open(p, encoding="utf-8")) == {"a": 1, "한글": "값"}
    # temp 잔여 없음
    assert not any(f.endswith(".tmp") for f in os.listdir(tmp_path))


def test_file_write_lock_serializes_no_lost_update(tmp_path):
    p = str(tmp_path / "snap.json")
    safe_io.atomic_write_json(p, {})

    def bump(key):
        with safe_io.file_write_lock(p):
            s = json.load(open(p, encoding="utf-8"))
            s[key] = True
            time.sleep(0.05)            # 락 없으면 read-modify-write 경합으로 유실
            safe_io.atomic_write_json(p, s)

    ts = [threading.Thread(target=bump, args=(k,)) for k in ("x", "y", "z")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    final = json.load(open(p, encoding="utf-8"))
    assert final == {"x": True, "y": True, "z": True}   # 셋 다 보존(lost update 없음)


def test_file_write_lock_timeout(tmp_path):
    p = str(tmp_path / "snap.json")
    safe_io.atomic_write_json(p, {})
    with safe_io.file_write_lock(p):
        # 다른 '프로세스' 시뮬레이션: 이미 잡힌 락을 짧은 timeout 으로 재획득 시도 → 실패
        import multiprocessing  # noqa: F401 (동일 프로세스 별 fd 로도 flock 충돌)
        try:
            with safe_io.file_write_lock(p, timeout=0.3):
                got = True
        except safe_io.LockTimeout:
            got = False
    assert got is False


# ── leverage_dca_guard ─────────────────────────────────────────────────────────
def test_guard_vol_cap_scales_down():
    mult, meta = b.leverage_dca_guard(5.0, realized_vol=0.80)   # 80% > 40% → ×0.5
    assert mult == 2.5
    assert meta["vol_scale"] == 0.5
    assert meta["safety_notes"]


def test_guard_absolute_ceiling():
    mult, meta = b.leverage_dca_guard(6.6, realized_vol=0.20)   # 저변동성, 상한 5.0
    assert mult == b.MAX_DCA_MULTIPLIER == 5.0


def test_guard_drawdown_halt():
    mult, meta = b.leverage_dca_guard(5.0, drawdown_pct=-60.0, realized_vol=0.20)
    assert mult == 1.0
    assert meta["dca_halt"] is True


def test_guard_normal_passthrough():
    mult, meta = b.leverage_dca_guard(2.0, realized_vol=0.20)
    assert mult == 2.0
    assert meta["dca_halt"] is False
    assert meta["vol_scale"] == 1.0


def test_guard_halt_boundary_just_above_floor():
    # 정지 임계(-55%) 바로 위(-54%)는 정지 안 함
    mult, meta = b.leverage_dca_guard(3.0, drawdown_pct=-54.0, realized_vol=0.20)
    assert meta["dca_halt"] is False


# ── fetch_qqq_data 신선도 ───────────────────────────────────────────────────────
def _fake_hist(days_ago: int, n: int = 300):
    end = pd.Timestamp.now().normalize() - pd.Timedelta(days=days_ago)
    idx = pd.bdate_range(end=end, periods=n)
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    return pd.DataFrame({"High": close * 1.01, "Low": close * 0.99, "Close": close}, index=idx)


def test_fetch_qqq_fresh_not_stale(monkeypatch):
    monkeypatch.setattr(md, "_history_cached", lambda *a, **k: _fake_hist(0))
    monkeypatch.setattr(md, "_update_drawdown_anchor", lambda high, cur: high)
    d = b.fetch_qqq_data()
    assert d and d["stale"] is False
    assert d["data_age_days"] <= 3          # 주말 고려


def test_fetch_qqq_old_is_stale(monkeypatch):
    monkeypatch.setattr(md, "_history_cached", lambda *a, **k: _fake_hist(12))
    monkeypatch.setattr(md, "_update_drawdown_anchor", lambda high, cur: high)
    d = b.fetch_qqq_data()
    assert d and d["stale"] is True
    assert d["data_age_days"] >= 9
