from __future__ import annotations


def test_main_skips_without_error_when_previous_run_still_holds_lock(monkeypatch, tmp_path):
    """감사 #29 — 겹쳐 도는 두 실행이 같은 track 을 각자 read-modify-write 하면
    늦게 쓴 쪽이 앞선 실행의 기록을 덮어써 유실될 수 있었다(락 없음)."""
    import safe_io
    from crons import paper_track as cron

    lock_target = str(tmp_path / "paper_track")
    monkeypatch.setattr(cron, "_LOCK_TARGET", lock_target)

    def _boom(*a, **k):
        raise AssertionError("_run 이 호출되면 안 됨 — 이전 실행이 아직 락을 쥐고 있어야 함")

    monkeypatch.setattr(cron, "_run", _boom)

    with safe_io.file_write_lock(lock_target, timeout=1):
        rc = cron.main()               # 겹치는 실행을 시뮬레이션 — 조용히 스킵돼야 함

    assert rc == 0


def test_main_runs_normally_when_no_lock_held(monkeypatch, tmp_path):
    from crons import paper_track as cron

    lock_target = str(tmp_path / "paper_track")
    monkeypatch.setattr(cron, "_LOCK_TARGET", lock_target)

    calls = []
    monkeypatch.setattr(cron, "_run", lambda: calls.append(1) or 0)

    rc = cron.main()

    assert rc == 0
    assert calls == [1]


# ── A-10 회귀: 시세 결측 포지션은 재정규화 없이 성과 미확정 ──────────────────────

def _closes(path_a, path_b=None, start="2026-03-02"):
    import pandas as pd
    idx = pd.bdate_range(start, periods=len(path_a))
    out = {"A": pd.Series(path_a, index=idx)}
    if path_b is not None:
        out["B"] = pd.Series(path_b, index=idx)
    return out


_A_UP_10 = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]   # 5거래일 +10%
_B_DOWN_100 = [100.0, 80.0, 60.0, 40.0, 20.0, 0.0]       # 5거래일 -100%


def test_a_fix_exec_missing_weighted_price_is_pending_not_renormalized():
    from crons import paper_track as cron

    r = cron._weighted_forward_return({"A": 0.6, "B": 0.4}, _closes(_A_UP_10), "2026-03-02", 5)

    assert r is None


def test_a_fix_exec_full_prices_give_weighted_return():
    from crons import paper_track as cron

    r = cron._weighted_forward_return({"A": 0.6, "B": 0.4}, _closes(_A_UP_10, _B_DOWN_100), "2026-03-02", 5)

    assert abs(r - (-0.34)) < 1e-9


def test_a_fix_exec_explicit_cash_counts_as_zero_return():
    from crons import paper_track as cron

    explicit = cron._weighted_forward_return({"A": 0.6, "CASH": 0.4}, _closes(_A_UP_10), "2026-03-02", 5)
    residual = cron._weighted_forward_return({"A": 0.6}, _closes(_A_UP_10), "2026-03-02", 5)

    assert abs(explicit - 0.06) < 1e-9
    assert abs(residual - 0.06) < 1e-9


def test_a_fix_exec_start_outside_price_window_is_pending():
    from crons import paper_track as cron

    r = cron._weighted_forward_return({"A": 1.0}, _closes(_A_UP_10, start="2026-03-20"), "2026-03-02", 5)

    assert r is None


def test_a_fix_exec_fill_realized_keeps_pending_and_never_rewrites_confirmed(monkeypatch):
    import pandas as pd
    from crons import paper_track as cron
    from ml import data_pipeline

    closes = _closes(_A_UP_10 + [111.0] * 15, _B_DOWN_100 + [0.0] * 15)
    prices = {"A": pd.DataFrame({"Close": closes["A"]})}            # B 시세 결측
    monkeypatch.setattr(data_pipeline, "fetch_prices", lambda tickers, days=120: dict(prices))
    track = {"2026-03-02": {"meta": {"A": 0.6, "B": 0.4}, "rule": {"A": 1.0}}}

    cron.fill_realized(track)

    entry = track["2026-03-02"]
    assert "ret_meta_5d" not in entry and "ret_meta_20d" not in entry   # 미확정 유지
    assert abs(entry["ret_rule_5d"] - 0.10) < 1e-9

    prices["B"] = pd.DataFrame({"Close": closes["B"]})               # 결측 종목이 나중에 도착
    entry["ret_rule_5d"] = 0.123                                     # 확정값은 재계산되지 않아야 함
    cron.fill_realized(track)

    assert abs(entry["ret_meta_5d"] - (-0.34)) < 1e-9
    assert entry["ret_rule_5d"] == 0.123


# ── RA-05 회귀: 부분 결측 시 종목마다 다른 종료일을 섞지 않음 (공통 거래일 시계) ──────

def _series(prices: dict):
    import pandas as pd
    return pd.Series(list(prices.values()), index=pd.to_datetime(list(prices.keys())))


_DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]


def test_a_exec_fix2_mid_gap_does_not_push_one_asset_end_date_later():
    """A 는 1/12 까지 정상, B 는 1/8 봉만 없고 1/13 에 급등. 공통 종료일(1/12) 기준 0%."""
    from crons import paper_track as cron

    a = _series({d: 100.0 for d in _DAYS})
    b = _series({**{d: 100.0 for d in _DAYS if d != "2026-01-08"}, "2026-01-13": 200.0})

    r = cron._weighted_forward_return({"A": 0.6, "B": 0.4}, {"A": a, "B": b}, "2026-01-05", 5)

    assert r == 0.0


def test_a_exec_fix2_missing_common_end_bar_is_pending():
    """공통 종료일 봉이 한 종목에 없으면 다음 봉으로 미루지 않고 미확정."""
    from crons import paper_track as cron

    a = _series({d: 100.0 for d in _DAYS})
    b = _series({**{d: 100.0 for d in _DAYS if d != "2026-01-12"}, "2026-01-13": 200.0})

    assert cron._weighted_forward_return({"A": 0.6, "B": 0.4}, {"A": a, "B": b}, "2026-01-05", 5) is None


def test_a_exec_fix2_missing_common_start_bar_is_pending():
    """서로 다른 첫 관측일 — B 가 공통 시작일 봉이 없으면 늦은 첫 봉을 시작가로 쓰지 않는다."""
    from crons import paper_track as cron

    a = _series({d: 100.0 for d in _DAYS})
    b = _series({**{d: 50.0 for d in _DAYS[1:]}, "2026-01-13": 50.0})

    assert cron._weighted_forward_return({"A": 0.6, "B": 0.4}, {"A": a, "B": b}, "2026-01-05", 5) is None