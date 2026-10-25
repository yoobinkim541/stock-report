#!/usr/bin/env python3
"""test_us_mock_learn.py — US 모의 보상 백필 + 정책 적합 (무네트워크·fake ledger/price)."""
import os
import sys
import pickle
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import us_mock_learn as L


def _write_cached_ohlc(base: Path, symbol: str, closes: list[float], period: str = "max") -> None:
    cache_dir = base / "reports" / "ml-cache" / "ohlc_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range("2026-05-01", periods=len(closes), freq="B")
    df = pd.DataFrame({
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
    }, index=idx)
    df.to_parquet(cache_dir / f"{symbol}__{period}.parquet")


def _write_price_cache(base: Path, symbol: str, closes: list[float], days: int = 756) -> None:
    cache_dir = base / "reports" / "ml-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range("2026-05-01", periods=len(closes), freq="B")
    df = pd.DataFrame({
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
    }, index=idx)
    (cache_dir / f"price_{symbol}_{days}d_test.pkl").write_bytes(pickle.dumps(df))


class _FakeLedger:
    def __init__(self, pending):
        self._p = pending
        self.outcomes = []

    def pending(self):
        return self._p

    def log_outcome(self, o):
        self.outcomes.append(o)


def _win(stock_ret, idx_ret, stock_mdd, idx_mdd, entry="2026-01-02", exit_="2026-02-02"):
    """_holding_window 형식(보유 경로 포함) 가짜 결과."""
    return {"stock_ret": stock_ret, "idx_ret": idx_ret, "stock_mdd": stock_mdd, "idx_mdd": idx_mdd,
            "entry_date": entry, "exit_date": exit_,
            "path": [[entry, 1.0, 1.0], [exit_, 1.0 + stock_ret, 1.0 + idx_ret]]}


def test_backfill_side_aware_correct():
    pending = [
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입"},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "퇴출"},
        {"id": 3, "ticker": "C", "date": "2026-01-01", "side": "편입"},
    ]
    px = {"A": _win(0.10, 0.04, 0.05, 0.03),   # 편입 초과 +0.06 → 적중
          "B": _win(0.01, 0.05, 0.04, 0.03),   # 퇴출, 종목 -0.04 미달 → 잘 뺌(적중)
          "C": _win(0.02, 0.06, 0.05, 0.03)}   # 편입 초과 -0.04 → 오답
    led = _FakeLedger(pending)
    added = L.backfill_outcomes(led, window_fn=lambda t, d, h: px[t])
    assert added == 3
    by = {o["decision_id"]: o for o in led.outcomes}
    from ml.adaptive import costs
    rt = costs.round_trip_frac("US")
    assert by[1]["correct"] is True
    assert by[1]["gross_excess"] == pytest.approx(0.06)          # 원(gross) 초과
    assert by[1]["fwd_excess"] == pytest.approx(0.06 - rt)       # 매수 net = gross − 왕복비용
    assert (by[1]["entry_date"], by[1]["exit_date"]) == ("2026-01-02", "2026-02-02")
    assert by[1]["path"] == px["A"]["path"]                      # 위험곡선 근거 보존
    assert by[2]["correct"] is True                              # 퇴출 회피 적중
    assert by[2]["fwd_excess"] == pytest.approx(-0.04)           # 매도는 gross(비용 미차감)
    assert by[3]["correct"] is False                            # 편입 오답


def test_backfill_cost_flips_marginal_buy():
    """왕복 비용보다 작은 초과수익 매수는 net 음수 → 오답(정직): 비용 넘는 엣지만 인정."""
    from ml.adaptive import costs
    rt = costs.round_trip_frac("US")
    led = _FakeLedger([{"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입"}])
    # 종목이 지수를 rt/2 만큼만 이김 → gross>0 이지만 net<0
    L.backfill_outcomes(led, window_fn=lambda t, d, h: _win(0.04 + rt / 2, 0.04, 0.02, 0.02))
    o = led.outcomes[0]
    assert o["gross_excess"] == pytest.approx(rt / 2) and o["gross_excess"] > 0
    assert o["fwd_excess"] < 0 and o["correct"] is False        # 비용 넘지 못함 → 오답


def test_backfill_skips_immature():
    led = _FakeLedger([{"id": 1, "ticker": "A", "date": "2026-06-01", "side": "편입"}])
    assert L.backfill_outcomes(led, window_fn=lambda t, d, h: None) == 0   # 미성숙


def test_backfill_skips_failed_orders():
    """주문 실패(ok=False) 결정은 forward 보상 산출 제외 — 팬텀 트레이드 오염 방지(S6)."""
    led = _FakeLedger([
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입", "ok": False},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "편입", "ok": True},
    ])
    added = L.backfill_outcomes(led, window_fn=lambda t, d, h: _win(0.10, 0.04, 0.05, 0.03))
    assert added == 1                                    # 집행건만
    assert [o["decision_id"] for o in led.outcomes] == [2]


def test_default_price_fn_uses_cached_ohlc_when_yfinance_unavailable(tmp_path, monkeypatch):
    """US 학습 크론도 로컬 OHLC 캐시를 먼저 사용해 네트워크 없이 outcome 을 성숙시켜야 한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # providers.market_data 의 OHLC 캐시는 STOCK_REPORT_OHLC_CACHE_DIR 를 HOME 보다
    # 우선하므로(테스트 간 격리용, 감사 후속 #36), 이 테스트가 쓰는 캐시 경로도
    # 같은 env var 로 맞춰야 _default_price_fn 이 실제로 읽어간다.
    monkeypatch.setenv("STOCK_REPORT_OHLC_CACHE_DIR", str(tmp_path / "reports" / "ml-cache" / "ohlc_cache"))
    _write_cached_ohlc(tmp_path, "AAPL", [100, 101, 102, 103, 104])
    _write_cached_ohlc(tmp_path, "QQQ", [200, 200, 200, 200, 200])

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    # 캐시가 목표일에 못 미치면 라이브 재조회를 시도하므로(감사 2026-08-21 staleness 수정),
    # yf.download 뿐 아니라 그 경로(_fetch_close_series_live)도 오프라인으로 막아야
    # 이 테스트가 "캐시만으로 동작" 을 실제로 검증한다(안 막으면 네트워크로 새어 실패).
    from providers import market_data as _md
    monkeypatch.setattr(_md, "_fetch_close_series_live", lambda *a, **k: None)

    out = L._default_price_fn("AAPL", "2026-05-01", 3)
    assert out is not None
    stock_ret, bench_ret, stock_mdd, bench_mdd = out
    assert stock_ret == pytest.approx(0.03, abs=1e-4)
    assert bench_ret == pytest.approx(0.0, abs=1e-4)
    assert stock_mdd == pytest.approx(0.0, abs=1e-4)
    assert bench_mdd == pytest.approx(0.0, abs=1e-4)


def test_default_price_fn_uses_price_cache_when_ohlc_cache_missing(tmp_path, monkeypatch):
    """price_*.pkl 캐시만 있어도 US 학습 크론이 벤치마크를 읽을 수 있어야 한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_price_cache(tmp_path, "AAPL", [100, 101, 102, 103, 104])
    _write_price_cache(tmp_path, "QQQ", [200, 200, 200, 200, 200])

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    # 캐시가 목표일에 못 미치면 라이브 재조회를 시도하므로(감사 2026-08-21 staleness 수정),
    # yf.download 뿐 아니라 그 경로(_fetch_close_series_live)도 오프라인으로 막아야
    # 이 테스트가 "캐시만으로 동작" 을 실제로 검증한다(안 막으면 네트워크로 새어 실패).
    from providers import market_data as _md
    monkeypatch.setattr(_md, "_fetch_close_series_live", lambda *a, **k: None)

    out = L._default_price_fn("AAPL", "2026-05-01", 3)
    assert out is not None
    stock_ret, bench_ret, stock_mdd, bench_mdd = out
    assert stock_ret == pytest.approx(0.03, abs=1e-4)
    assert bench_ret == pytest.approx(0.0, abs=1e-4)
    assert stock_mdd == pytest.approx(0.0, abs=1e-4)
    assert bench_mdd == pytest.approx(0.0, abs=1e-4)


def test_fit_policy_positive_correlation_dominates():
    rows = [{"side": "편입", "features": {"value": v}, "fwd_excess": v * 0.1}
            for v in (0.1, 0.3, 0.5, 0.7, 0.9)]
    w = L.fit_policy(rows)
    assert w["w_value"] == max(w.values())     # value↔초과수익 양상관 → 최대 가중


def test_fit_policy_empty_fallback_normalized():
    w = L.fit_policy([])
    assert set(w) == {"w_ranker", "w_value", "w_quality", "w_mom", "w_conf",
                      "w_mom12", "w_hi52", "w_lowvol", "w_pead", "w_news"}   # ★가격 축 3종 + PEAD + 뉴스
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)   # DEFAULT 정규화 폴백(신규 축 0)


def test_fit_policy_unmeasured_axis_gets_zero_not_default():
    """측정 축에 신호가 있으면 미측정 축(원장에 없는 신규 축)은 0 — DEFAULT 쏠림 함정 방지."""
    rows = [{"side": "편입", "features": {"value": v}, "fwd_excess": v * 0.1}
            for v in (0.1, 0.3, 0.5, 0.7, 0.9)]
    w = L.fit_policy(rows)
    assert w["w_value"] == pytest.approx(1.0)              # 유일한 측정·유신호 축
    assert w["w_mom12"] == 0.0 and w["w_hi52"] == 0.0 and w["w_lowvol"] == 0.0


def test_eval_policy_basket_excess():
    from ml import us_policy
    rows = [{"side": "편입", "features": {"ranker": 0.9}, "fwd_excess": 0.05},
            {"side": "편입", "features": {"ranker": 0.5}, "fwd_excess": 0.02},
            {"side": "편입", "features": {"ranker": 0.1}, "fwd_excess": -0.03}]
    out = L.eval_policy(rows, us_policy.DEFAULT_POLICY, max_positions=2)
    assert out["excess"] == pytest.approx(0.035, abs=1e-3)   # 상위 2 평균
    assert out["n"] == 1                                      # 평가일 1개(후보 3행 ≠ 표본 3)
    assert out["n_candidates"] == 3 and out["n_selected"] == 2


def _bday_path_row(day_i, rank, ex, ticker, side="편입"):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-06-01", periods=80)][day_i:day_i + 21]
    return {"side": side, "id": f"{days[0]}:{ticker}", "date": days[0], "ticker": ticker,
            "features": {"ranker": rank}, "fwd_excess": ex, "gross_excess": ex,
            "entry_date": days[0], "exit_date": days[-1],
            "path": [[d, 1.0 + ex * k / 20, 1.0] for k, d in enumerate(days)]}


def test_eval_policy_evaluates_every_date_and_excludes_sell_rows():
    """날짜별 top-K 가 모두 평가되고 매도측 행은 후보가 아니다. 독립 관측은 실제 라벨 종료일로 정한다."""
    rows = []
    for day_i, top in ((0, 0.04), (25, -0.02)):
        for i, rank in enumerate((0.9, 0.8, 0.7, 0.6, 0.5, 0.4)):
            rows.append(_bday_path_row(day_i, rank, top if i < 2 else -0.5, f"A{i}"))
        rows.append(_bday_path_row(day_i, 1.0, 9.9, "S", side="퇴출"))
    out = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    assert out["series"] == pytest.approx([0.04, -0.02])
    assert out["excess"] == pytest.approx(0.01)
    assert (out["n"], out["n_candidates"], out["n_selected"]) == (2, 12, 4)
    assert out["n_independent"] == 2                          # 25거래일 뒤 = 첫 라벨(20거래일) 종료 후
    assert out["mdd"] == pytest.approx(0.02)                  # 1→1.04 보유 후 1.04→1.0192 (−2%)
    assert out["index_mdd"] == pytest.approx(0.0)


# ── RA-02: 독립 표본 = 실제 라벨 종료(휴장·거래정지 반영) ─────────────────────────

_US_HOLIDAYS = {"2026-01-19", "2026-02-16"}          # MLK·Presidents Day (NYSE 휴장)
_CAL = pd.DatetimeIndex([d for d in pd.bdate_range("2026-01-02", "2026-05-29")
                         if d.strftime("%Y-%m-%d") not in _US_HOLIDAYS])


def _px(seed, missing=()):
    import numpy as np
    vals = 100.0 * np.cumprod(1.0 + np.random.default_rng(seed).normal(0.001, 0.01, len(_CAL)))
    s = pd.Series(vals, index=_CAL)
    return s.drop(pd.DatetimeIndex(list(missing))) if missing else s


def _calendar_row(ticker, day, stock, bench, rank):
    from ml.adaptive import oos_eval
    from ohlc_utils import align_common_index
    s, b = align_common_index(stock, bench, start=day)
    w = oos_eval.holding_window(s, b, L.HORIZON)
    ex = w["stock_ret"] - w["idx_ret"]
    return {"side": "편입", "id": f"{day}:{ticker}", "date": day, "ticker": ticker,
            "features": {"ranker": rank}, "fwd_excess": ex, "gross_excess": ex,
            "entry_date": w["entry_date"], "exit_date": w["exit_date"], "path": w["path"]}


def test_independent_cohorts_respect_us_holiday_label_end():
    """리뷰 재현: 1/5 시작 20거래일 라벨은 MLK 휴장 때문에 2/3 종료 → 2/2 결정은 독립이 아니다."""
    qqq, a = _px(1), _px(2)
    rows = [_calendar_row("A", d, a, qqq, 0.9) for d in ("2026-01-05", "2026-02-02", "2026-02-03")]
    assert rows[0]["exit_date"] == "2026-02-03"
    out = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=1)
    assert out["series_days"] == ["2026-01-05", "2026-02-03"]
    assert out["n_independent"] == 2


def test_independent_cohorts_wait_for_suspended_member_exit():
    """거래정지(1/7~1/9 시세 없음) 종목의 라벨은 3거래일 늦게 끝난다 — 코호트 종료 = 구성원 최댓값."""
    qqq, a = _px(1), _px(2)
    b = _px(3, missing=("2026-01-07", "2026-01-08", "2026-01-09"))
    rows = []
    for d in ("2026-01-05", "2026-02-03", "2026-02-06"):
        rows += [_calendar_row("A", d, a, qqq, 0.9), _calendar_row("B", d, b, qqq, 0.8)]
    assert rows[1]["exit_date"] == "2026-02-06"                 # B(1/5) 의 실제 종료
    out = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    assert out["series_days"] == ["2026-01-05", "2026-02-06"]


@pytest.mark.parametrize("step", [1, 5])
def test_independent_cohorts_never_overlap_and_match_reported_rows(step):
    """평가일 밀도(매일/주1회)와 무관하게: 채택 코호트의 라벨 구간이 겹치지 않고, 검정 입력 = 보고 행."""
    qqq, a, b = _px(1), _px(2), _px(3, missing=("2026-03-10", "2026-03-11"))
    days = [d.strftime("%Y-%m-%d") for d in _CAL[1:-25:step]]
    rows = []
    for d in days:
        rows += [_calendar_row("A", d, a, qqq, 0.9), _calendar_row("B", d, b, qqq, 0.8)]
    out = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    by_id = {r["id"]: r for r in rows}
    assert out["n_independent"] == len(out["series"]) == len(out["series_ids"]) >= 3
    prev_exit = ""
    for ids, value in zip(out["series_ids"], out["series"]):
        members = [by_id[i] for i in ids]
        assert min(m["entry_date"] for m in members) >= prev_exit
        prev_exit = max(m["exit_date"] for m in members)
        assert value == pytest.approx(sum(m["fwd_excess"] for m in members) / len(members), abs=1e-8)


def test_backfill_shadow_observation_is_treated_as_entry_side():
    """섀도 '관측'은 편입 계열 — net(왕복비용 차감)·correct=net>0 이어야 부호가 안 뒤집힌다."""
    pending = [{"id": 9, "ticker": "AAPL", "date": "2026-05-01", "side": "관측",
                "shadow": True, "ok": True, "policy_score": 0.7}]

    # 기본 sides 엔 '관측' 미포함 — 라이브 규칙 불변
    assert L.backfill_outcomes(_FakeLedger(list(pending)),
                               window_fn=lambda t, d, h: _win(0.10, 0.02, 0.03, 0.02)) == 0

    led = _FakeLedger(list(pending))
    added = L.backfill_outcomes(led, window_fn=lambda t, d, h: _win(0.10, 0.02, 0.03, 0.02),
                                sides=("관측",))
    assert added == 1
    from ml.adaptive import costs
    o = led.outcomes[0]
    assert o["gross_excess"] == pytest.approx(0.08)
    assert o["fwd_excess"] == pytest.approx(0.08 - costs.round_trip_frac("US"))   # 편입처럼 net
    assert o["correct"] is True                                                   # 부호 정상(반전 아님)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
