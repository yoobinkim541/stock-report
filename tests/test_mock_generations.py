"""
test_mock_generations.py — 모의계좌 만기 리셋 시 성과 세대 분리 테스트.

검증:
  - 경계 없을 때 active_snapshots() 는 전체 히스토리 반환
  - close_generation() 이 아카이브 레코드 + 경계 마커를 남김
  - 경계 이후 active_snapshots() 는 새 세대 스냅샷만 반환 (구 세대 인셉션에
    오염되지 않음 — 계좌 리셋이 전략 손실로 오인되는 왜곡 방지)
  - 잔고 조회 실패 시 close_generation() 은 예외 (구 계좌가 죽은 상태에서 마감 금지)
  - generation_count() 는 마감 횟수 + 1
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from lib import mock_generations  # noqa: E402


class _FakeStore:
    def __init__(self):
        self._data: dict[str, list[dict]] = {}

    def all(self, name):
        return list(self._data.get(name, []))

    def append(self, name, item):
        self._data.setdefault(name, []).append(dict(item))
        return len(self._data[name])


def _snap(date, nav):
    return {"date": date, "kind": "snapshot", "nav": nav}


def test_active_snapshots_returns_all_when_no_boundary():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 10_200_000))

    snaps = mock_generations.active_snapshots("kr_mock_history", store_module=store)

    assert len(snaps) == 2
    assert snaps[0]["nav"] == 10_000_000


def test_close_generation_archives_summary_and_marks_boundary():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-04-03 06:40", 11_500_000))

    def get_balance():
        return {
            "ok": True,
            "nav": 11_800_000,
            "pos_value": 10_000_000,
            "cash_krw": 1_800_000,
            "positions": {
                "005930": {"name": "삼성전자", "shares": 10, "value": 10_000_000},
                "000660": {"name": "SK하이닉스", "shares": 0, "value": 0},  # 청산분 제외돼야 함
            },
        }

    summary = mock_generations.close_generation(
        "kr_mock_history",
        get_balance_fn=get_balance,
        reason="3개월 만기 갱신",
        max_drawdown_fn=lambda series: 0.05,
        store_module=store,
    )

    assert summary["generation"] == 1
    assert summary["inception_nav"] == 10_000_000
    assert summary["final_nav"] == 11_800_000
    assert round(summary["cum_return_pct"], 2) == 18.0
    assert summary["mdd_pct"] == 5.0
    assert summary["holdings_at_close"] == [
        {"code": "005930", "name": "삼성전자", "shares": 10, "value": 10_000_000}
    ]

    archived = store.all("kr_mock_history_generations")
    assert len(archived) == 1
    assert archived[0]["reason"] == "3개월 만기 갱신"

    boundaries = [r for r in store.all("kr_mock_history") if r.get("kind") == "generation_boundary"]
    assert len(boundaries) == 1
    assert boundaries[0]["generation"] == 1


def test_active_snapshots_excludes_prior_generation_after_close():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-04-03 06:40", 6_000_000))  # 리셋 직전 큰 낙폭처럼 보일 스냅샷

    mock_generations.close_generation(
        "kr_mock_history",
        get_balance_fn=lambda: {"ok": True, "nav": 6_000_000, "positions": {}},
        max_drawdown_fn=lambda series: 0.4,
        store_module=store,
        now="2026-04-05 09:00",
    )

    # 새 세대: 리셋된 새 계좌가 시드 자본으로 시작
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 10_000_000))

    snaps = mock_generations.active_snapshots("kr_mock_history", store_module=store)

    assert len(snaps) == 1
    assert snaps[0]["nav"] == 10_000_000  # 구 세대(10M→6M 낙폭)가 새 계산에 섞이지 않음


def test_close_generation_rejects_when_balance_fetch_fails():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))

    try:
        mock_generations.close_generation(
            "kr_mock_history",
            get_balance_fn=lambda: {"ok": False},
            store_module=store,
        )
        assert False, "잔고 조회 실패 시 예외가 발생해야 한다"
    except RuntimeError as e:
        assert "잔고 조회 실패" in str(e)

    assert store.all("kr_mock_history_generations") == []


def test_generation_count_tracks_closed_boundaries():
    store = _FakeStore()
    assert mock_generations.generation_count("kr_mock_history", store_module=store) == 1

    store.append("kr_mock_history", {"date": "2026-04-03 06:40", "kind": "generation_boundary", "generation": 1})
    assert mock_generations.generation_count("kr_mock_history", store_module=store) == 2


def test_generation_rollup_stitches_lifetime_curve_across_boundaries():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 8_000_000))
    store.append("kr_mock_history", {"date": "2026-04-03 06:40", "kind": "generation_boundary", "generation": 1})
    store.append("kr_mock_history", _snap("2026-04-04 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-04-05 06:40", 11_000_000))

    roll = mock_generations.generation_rollup(
        "kr_mock_history",
        store_module=store,
        max_drawdown_fn=lambda series: 1.0 - (min(series) / series[0]) if series else 0.0,
    )

    assert roll["generation_count"] == 2
    assert roll["current"]["generation"] == 2
    assert roll["current"]["nav"] == 11_000_000
    assert round(roll["current"]["cum_return_pct"], 2) == 10.0
    assert round(roll["current"]["day_return_pct"], 2) == 10.0
    assert round(roll["lifetime"]["cum_return_pct"], 2) == -12.0
    assert round(roll["lifetime"]["mdd_pct"], 2) == 20.0
    assert [round(r["nav"], 0) for r in roll["lifetime"]["nav_series"]] == [
        10_000_000, 8_000_000, 8_000_000, 8_800_000
    ]


# ── A-01 회귀: 마감 NAV 가 롤업 수익률·MDD 에 정확히 한 번 반영 ─────────────────

def _peak_mdd(series):
    peak, worst = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        worst = max(worst, 1.0 - v / peak)
    return worst


def _close(store, nav, now="2026-04-05 09:00"):
    return mock_generations.close_generation(
        "kr_mock_history",
        get_balance_fn=lambda: {"ok": True, "nav": nav, "positions": {}},
        max_drawdown_fn=_peak_mdd,
        store_module=store,
        now=now,
    )


def _roll(store):
    return mock_generations.generation_rollup(
        "kr_mock_history", store_module=store, max_drawdown_fn=_peak_mdd)


def test_a_fix_exec_close_nav_below_last_snapshot_counts_in_lifetime():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    summary = _close(store, 60)
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 100))

    roll = _roll(store)

    assert round(summary["cum_return_pct"], 6) == -40.0
    assert round(summary["mdd_pct"], 6) == 50.0
    assert round(roll["lifetime"]["cum_return_pct"], 6) == -40.0
    assert round(roll["lifetime"]["mdd_pct"], 6) == 50.0
    assert [round(r["nav"], 6) for r in roll["lifetime"]["nav_series"]] == [100, 120, 60, 60]
    # 세대 요약과 롤업 세그먼트가 같은 시계열을 소비
    closed = mock_generations._segment_summary(
        roll["segments"][0]["snapshots"], max_drawdown_fn=_peak_mdd)
    assert closed["final_nav"] == summary["final_nav"] == 60
    assert closed["cum_return_pct"] == summary["cum_return_pct"]
    assert closed["mdd_pct"] == summary["mdd_pct"]
    # 현재 세대는 새 snapshot 만
    assert roll["current"]["nav"] == 100
    assert mock_generations.active_snapshots("kr_mock_history", store_module=store)[0]["nav"] == 100


def test_a_fix_exec_close_nav_above_last_snapshot_counts_gain():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 110))
    _close(store, 130)
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 100))
    store.append("kr_mock_history", _snap("2026-04-07 06:40", 110))

    roll = _roll(store)

    assert round(roll["lifetime"]["cum_return_pct"], 6) == 43.0  # 1.3 × 1.1


def test_a_fix_exec_close_nav_equal_to_last_snapshot_not_duplicated():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    summary = _close(store, 120)
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 100))

    roll = _roll(store)

    assert round(summary["cum_return_pct"], 6) == 20.0
    assert [r["nav"] for r in roll["segments"][0]["snapshots"]] == [100, 120]
    assert [round(r["nav"], 6) for r in roll["lifetime"]["nav_series"]] == [100, 120, 120]
    assert round(roll["lifetime"]["cum_return_pct"], 6) == 20.0


def test_a_fix_exec_close_without_new_snapshot_reflects_final_nav():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    _close(store, 60)

    roll = _roll(store)

    assert roll["generation_count"] == 2
    assert roll["current"]["nav"] is None
    assert roll["lifetime"]["final_nav"] == 60
    assert round(roll["lifetime"]["cum_return_pct"], 6) == -40.0
    assert round(roll["lifetime"]["mdd_pct"], 6) == 50.0
    assert roll["lifetime"]["end_date"] == "2026-04-05"


def test_a_fix_exec_duplicate_close_is_rejected_without_writes():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    _close(store, 60)
    history_before = store.all("kr_mock_history")
    archive_before = store.all("kr_mock_history_generations")

    def _no_balance():
        raise AssertionError("중복 마감은 잔고를 조회하지 않아야 한다")

    try:
        mock_generations.close_generation(
            "kr_mock_history", get_balance_fn=_no_balance, max_drawdown_fn=_peak_mdd,
            store_module=store, now="2026-04-05 09:05")
        assert False, "중복 마감은 거부돼야 한다"
    except RuntimeError as e:
        assert "이미 마감" in str(e)

    assert store.all("kr_mock_history") == history_before
    assert store.all("kr_mock_history_generations") == archive_before
    assert round(_roll(store)["lifetime"]["cum_return_pct"], 6) == -40.0


def test_a_fix_exec_close_resumes_archive_after_partial_write():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    real_append = store.append

    def failing_append(name, item):
        if name.endswith("_generations"):
            raise OSError("disk full")
        return real_append(name, item)

    store.append = failing_append
    try:
        _close(store, 60)
        assert False, "아카이브 쓰기 실패가 전파돼야 한다"
    except OSError:
        pass
    store.append = real_append
    # 경계 마커(진실원)만 남은 상태에서도 롤업은 마감 NAV 를 반영
    assert round(_roll(store)["lifetime"]["cum_return_pct"], 6) == -40.0

    resumed = _close(store, 999, now="2026-04-05 09:10")  # 재실행: 잔고값이 아니라 마커 요약으로 복구

    assert resumed["final_nav"] == 60 and resumed["generation"] == 1
    assert len(store.all("kr_mock_history_generations")) == 1
    assert len([r for r in store.all("kr_mock_history") if r.get("kind") == "generation_boundary"]) == 1
    try:
        _close(store, 60, now="2026-04-05 09:20")
        assert False, "복구 이후 재마감은 중복으로 거부돼야 한다"
    except RuntimeError:
        pass


def test_a_fix_exec_legacy_boundary_reads_close_nav_from_archive():
    """구 형식 경계 마커(final_nav 없음)는 같은 세대 아카이브의 마감 NAV 를 쓴다."""
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    store.append("kr_mock_history_generations", {
        "date": "2026-04-05 09:00", "generation": 1, "final_nav": 60, "inception_nav": 100})
    store.append("kr_mock_history", {"date": "2026-04-05 09:00", "kind": "generation_boundary",
                                     "generation": 1, "reason": "계좌 리셋"})
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 100))

    roll = _roll(store)

    assert round(roll["lifetime"]["cum_return_pct"], 6) == -40.0
    assert round(roll["lifetime"]["mdd_pct"], 6) == 50.0


# ── RA-06: snapshot 없는 첫 정상 마감의 NAV 가 롤업에서 사라지지 않음 ─────────────────

def test_a_exec_fix2_first_close_without_snapshots_keeps_close_nav():
    store = _FakeStore()
    summary = _close(store, 100)

    roll = _roll(store)
    assert summary["final_nav"] == 100
    assert roll["generation_count"] == 2
    assert roll["current"]["nav"] is None
    assert roll["lifetime"]["final_nav"] == 100
    assert [r["raw_nav"] for r in roll["lifetime"]["nav_series"]] == [100]

    store.append("kr_mock_history", _snap("2026-04-06 06:40", 50))
    store.append("kr_mock_history", _snap("2026-04-07 06:40", 55))
    roll = _roll(store)
    assert [r["raw_nav"] for r in roll["lifetime"]["nav_series"]] == [100, 50, 55]
    assert [round(r["nav"], 6) for r in roll["lifetime"]["nav_series"]] == [100, 100, 110]
    assert round(roll["lifetime"]["cum_return_pct"], 6) == 10.0   # 리셋(100→50)은 손실 아님


def test_a_exec_fix2_legacy_duplicate_empty_boundary_adds_no_fake_point():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 100))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 120))
    store.append("kr_mock_history", {"date": "2026-04-05 09:00", "kind": "generation_boundary",
                                     "generation": 1, "final_nav": 60})
    store.append("kr_mock_history", {"date": "2026-04-05 09:05", "kind": "generation_boundary",
                                     "generation": 2, "final_nav": 999})   # 구 중복 마감
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 100))

    roll = _roll(store)

    assert [r["raw_nav"] for r in roll["lifetime"]["nav_series"]] == [100, 120, 60, 100]
    assert round(roll["lifetime"]["cum_return_pct"], 6) == -40.0


# ── 운영 원장 결함: 무효 snapshot 무시 · 실패 이벤트 · 불완전 잔고 · 리셋 자동 세대 ─────

def test_a_exec_fix2_rollup_ignores_invalid_snapshots():
    store = _FakeStore()
    store.append("us_mock_history", {"date": "2026-08-24 06:40", "kind": "snapshot", "nav": 100.0, "cash": 50.0})
    store.append("us_mock_history", {"date": "2026-08-25 00:03", "kind": "snapshot", "nav": None, "cash": None})
    store.append("us_mock_history", {"date": "2026-08-25 00:04", "kind": "snapshot", "nav": 0.0, "cash": 0.0})
    store.append("us_mock_history", {"date": "2026-08-25 00:05", "kind": "snapshot", "nav": float("nan")})
    store.append("us_mock_history", {"date": "2026-08-25 00:06", "kind": "snapshot", "nav": 50.0,
                                     "cash": 0.0, "invalid": "incomplete_balance"})
    store.append("us_mock_history", {"date": "2026-08-25 06:40", "kind": "snapshot", "nav": 110.0, "cash": 55.0})

    roll = mock_generations.generation_rollup("us_mock_history", store_module=store,
                                              max_drawdown_fn=_peak_mdd)

    assert roll["current"]["nav_series"] == [100.0, 110.0]
    assert roll["current"]["mdd_pct"] == 0.0
    assert round(roll["current"]["cum_return_pct"], 6) == 10.0
    assert roll["latest_failure"] is None


def test_a_exec_fix2_failed_balance_is_recorded_as_event_not_snapshot():
    store = _FakeStore()
    store.append("us_mock_history", {"date": "2026-10-10 06:40", "kind": "snapshot", "nav": 262224.57})

    res = mock_generations.record_snapshot(
        "us_mock_history", nav=None, cash=None, positions={}, at="eod", seed=100_000,
        problem=mock_generations.balance_problem(
            {"ok": True, "nav": None, "positions": {}, "raw": {"rt_cd": "1", "msg1": "모의투자 신청내역을 확인하세요"}}),
        store_module=store, now="2026-10-13 06:40")

    assert res == {"recorded": False, "reason": "broker_rejected: 모의투자 신청내역을 확인하세요", "reset": None}
    rows = store.all("us_mock_history")
    assert rows[-1]["kind"] == "snapshot_failed" and rows[-1]["at"] == "eod"
    assert [s["nav"] for s in mock_generations.active_snapshots("us_mock_history", store_module=store)] == [262224.57]
    assert mock_generations.latest_failure("us_mock_history", store_module=store)["date"] == "2026-10-13 06:40"


def test_a_exec_fix2_half_balance_without_cash_is_rejected():
    store = _FakeStore()
    store.append("us_mock_history", {"date": "2026-08-24 06:40", "kind": "snapshot", "nav": 200_000.0,
                                     "cash": 100_000.0, "positions": 4})

    res = mock_generations.record_snapshot(
        "us_mock_history", nav=100_000.0, cash=0.0, positions=4, seed=100_000,
        store_module=store, now="2026-08-25 00:03")

    assert res["recorded"] is False and res["reason"] == "incomplete_balance"
    assert len(mock_generations.active_snapshots("us_mock_history", store_module=store)) == 1


def test_a_exec_fix2_kis_balance_problems_are_classified():
    bp = mock_generations.balance_problem
    assert bp({"ok": False}) == "balance_failed"
    assert bp(None) == "balance_failed"
    assert bp({"ok": True, "nav": None, "raw": {"rt_cd": "1", "msg1": "모의투자 신청내역을 확인하세요"}}) \
        == "broker_rejected: 모의투자 신청내역을 확인하세요"
    # 어댑터가 거부를 ok=False 로 돌려줘도 사유(메시지)는 보존
    assert bp({"ok": False, "nav": None, "msg": "모의투자 신청내역을 확인하세요",
               "raw": {"rt_cd": "1"}}) == "broker_rejected: 모의투자 신청내역을 확인하세요"
    assert bp({"ok": False, "raw": {"return_code": 1, "return_msg": "모의투자 미신청"}}) \
        == "broker_rejected: 모의투자 미신청"
    assert bp({"ok": True, "nav": None, "raw": {"rt_cd": "0"}}) == "nav_missing"
    assert bp({"ok": True, "nav": float("nan"), "raw": {"rt_cd": "0"}}) == "nav_missing"
    assert bp({"ok": True, "nav": 262_224.57, "cash_usd": 130_000.0, "cash_derived": True,
               "krw_asset": 360_000_000.0, "fx": 1372.9, "raw": {"rt_cd": "0"}}) is None
    assert bp({"ok": True, "nav": 11_000_000, "cash_krw": 1_000_000, "raw": {"return_code": 0}}) is None


def test_a_exec_fix2_detected_reset_closes_generation_at_last_valid_nav():
    store = _FakeStore()
    store.append("us_mock_history", {"date": "2026-10-09 06:40", "kind": "snapshot", "nav": 250_000.0, "positions": 3})
    store.append("us_mock_history", {"date": "2026-10-10 06:40", "kind": "snapshot", "nav": 262_224.57, "positions": 3})

    res = mock_generations.record_snapshot(
        "us_mock_history", nav=100_000.0, cash=100_000.0, positions={}, seed=100_000,
        max_drawdown_fn=_peak_mdd, store_module=store, now="2026-10-13 00:03")

    assert res["recorded"] is True
    assert res["reset"]["final_nav"] == 262_224.57 and res["reset"]["generation"] == 1
    assert res["reset"]["close_nav_date"] == "2026-10-10 06:40"
    assert len(store.all("us_mock_history_generations")) == 1
    roll = mock_generations.generation_rollup("us_mock_history", store_module=store,
                                              max_drawdown_fn=_peak_mdd)
    assert roll["generation_count"] == 2
    assert roll["current"]["nav"] == 100_000.0 and roll["current"]["cum_return_pct"] == 0.0
    assert round(roll["lifetime"]["cum_return_pct"], 4) == round((262_224.57 / 250_000 - 1) * 100, 4)
    assert roll["lifetime"]["mdd_pct"] == 0.0   # 리셋이 -60% 손실·MDD 로 잡히지 않음


def test_a_exec_fix2_reset_requires_flat_seed_nav_and_large_gap():
    for nav, positions, prev in ((100_000.0, 2, 262_224.57),     # 보유 있음
                                 (95_000.0, 0, 262_224.57),      # 시드와 불일치
                                 (100_000.0, 0, 104_000.0)):     # 직전이 시드 근처(정상 운용)
        store = _FakeStore()
        store.append("us_mock_history", {"date": "2026-10-10 06:40", "kind": "snapshot", "nav": prev,
                                         "cash": prev / 2, "positions": 2})
        res = mock_generations.record_snapshot(
            "us_mock_history", nav=nav, cash=nav / 2, positions=positions, seed=100_000,
            store_module=store, now="2026-10-13 00:03")
        assert res["reset"] is None
        assert mock_generations.generation_count("us_mock_history", store_module=store) == 1


def test_a_exec_fix2_close_generation_refuses_rejected_balance_without_writes():
    store = _FakeStore()
    store.append("us_mock_history", {"date": "2026-10-10 06:40", "kind": "snapshot", "nav": 262_224.57})
    try:
        mock_generations.close_generation(
            "us_mock_history", store_module=store, max_drawdown_fn=_peak_mdd,
            get_balance_fn=lambda: {"ok": True, "nav": None, "pos_value": 0.0, "positions": {},
                                    "raw": {"rt_cd": "1", "msg1": "모의투자 신청내역을 확인하세요"}})
        assert False, "브로커가 거부한 잔고로 마감하면 안 된다"
    except RuntimeError as e:
        assert "broker_rejected" in str(e)
    assert [r["kind"] for r in store.all("us_mock_history")] == ["snapshot"]
    assert store.all("us_mock_history_generations") == []
