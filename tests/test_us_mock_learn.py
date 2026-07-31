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


def test_backfill_side_aware_correct():
    pending = [
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입"},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "퇴출"},
        {"id": 3, "ticker": "C", "date": "2026-01-01", "side": "편입"},
    ]
    px = {"A": (0.10, 0.04, 0.05, 0.03),   # 편입 초과 +0.06 → 적중
          "B": (0.01, 0.05, 0.04, 0.03),   # 퇴출, 종목 -0.04 미달 → 잘 뺌(적중)
          "C": (0.02, 0.06, 0.05, 0.03)}   # 편입 초과 -0.04 → 오답
    led = _FakeLedger(pending)
    added = L.backfill_outcomes(led, price_fn=lambda t, d, h: px[t])
    assert added == 3
    by = {o["decision_id"]: o for o in led.outcomes}
    from ml.adaptive import costs
    rt = costs.round_trip_frac("US")
    assert by[1]["correct"] is True
    assert by[1]["gross_excess"] == pytest.approx(0.06)          # 원(gross) 초과
    assert by[1]["fwd_excess"] == pytest.approx(0.06 - rt)       # 매수 net = gross − 왕복비용
    assert by[2]["correct"] is True                              # 퇴출 회피 적중
    assert by[2]["fwd_excess"] == pytest.approx(-0.04)           # 매도는 gross(비용 미차감)
    assert by[3]["correct"] is False                            # 편입 오답


def test_backfill_cost_flips_marginal_buy():
    """왕복 비용보다 작은 초과수익 매수는 net 음수 → 오답(정직): 비용 넘는 엣지만 인정."""
    from ml.adaptive import costs
    rt = costs.round_trip_frac("US")
    led = _FakeLedger([{"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입"}])
    # 종목이 지수를 rt/2 만큼만 이김 → gross>0 이지만 net<0
    L.backfill_outcomes(led, price_fn=lambda t, d, h: (0.04 + rt / 2, 0.04, 0.02, 0.02))
    o = led.outcomes[0]
    assert o["gross_excess"] == pytest.approx(rt / 2) and o["gross_excess"] > 0
    assert o["fwd_excess"] < 0 and o["correct"] is False        # 비용 넘지 못함 → 오답


def test_backfill_skips_immature():
    led = _FakeLedger([{"id": 1, "ticker": "A", "date": "2026-06-01", "side": "편입"}])
    assert L.backfill_outcomes(led, price_fn=lambda t, d, h: None) == 0   # 미성숙


def test_backfill_skips_failed_orders():
    """주문 실패(ok=False) 결정은 forward 보상 산출 제외 — 팬텀 트레이드 오염 방지(S6)."""
    led = _FakeLedger([
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입", "ok": False},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "편입", "ok": True},
    ])
    added = L.backfill_outcomes(led, price_fn=lambda t, d, h: (0.10, 0.04, 0.05, 0.03))
    assert added == 1                                    # 집행건만
    assert [o["decision_id"] for o in led.outcomes] == [2]


def test_default_price_fn_uses_cached_ohlc_when_yfinance_unavailable(tmp_path, monkeypatch):
    """US 학습 크론도 로컬 OHLC 캐시를 먼저 사용해 네트워크 없이 outcome 을 성숙시켜야 한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_cached_ohlc(tmp_path, "AAPL", [100, 101, 102, 103, 104])
    _write_cached_ohlc(tmp_path, "QQQ", [200, 200, 200, 200, 200])

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))

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
    assert out["n"] == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
