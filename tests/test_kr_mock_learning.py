#!/usr/bin/env python3
"""test_kr_mock_learning.py — KR 정책 강화(보상 백필·fit·eval·게이트) 무네트워크."""
import os
import sys
import pickle
from pathlib import Path

import pandas as pd
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import kr_mock_learn as L                    # noqa: E402
from ml.adaptive.ledger import Ledger        # noqa: E402


def _write_cached_ohlc(base: Path, symbol: str, closes: list[float], period: str = "max") -> None:
    cache_dir = base / "reports" / "ml-cache" / "ohlc_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in ".-_") else "_" for c in str(symbol))
    idx = pd.date_range("2026-05-01", periods=len(closes), freq="B")
    df = pd.DataFrame({
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
    }, index=idx)
    df.to_parquet(cache_dir / f"{safe}__{period}.parquet")


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
    cache_key = f"price_{symbol}_{days}d_test"
    (cache_dir / f"{cache_key}.pkl").write_bytes(pickle.dumps(df))


def test_pearson():
    assert L._pearson([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert L._pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert L._pearson([1, 1], [1, 1]) == 0.0     # 표본 부족


def test_fit_policy_weights_sum_to_one_and_favor_correlated():
    # ranker 가 보상과 완전 상관, 나머지 무상관 → ranker 가중 최대
    rows = []
    for i in range(20):
        ex = i / 20.0
        rows.append({"features": {"ranker": ex, "fund": 0.5, "signal": 0.5, "conf": 0.5, "mom": 0.5},
                     "fwd_excess": ex})
    p = L.fit_policy(rows)
    ws = {k: v for k, v in p.items() if k.startswith("w_")}
    assert sum(ws.values()) == pytest.approx(1.0, abs=1e-3)
    assert p["w_ranker"] == max(ws.values())     # 상관 높은 피처가 최대 가중
    assert "score_threshold" not in p             # 죽은 파라미터 제거됨


def test_fit_policy_falls_back_when_no_signal():
    from ml import kr_policy
    # 전 피처 상수 → 양의 상관 합 0 → DEFAULT 가중 폴백(전부-0 붕괴 방지)
    rows = [{"features": {"ranker": 0.5, "fund": 0.5, "signal": 0.5, "conf": 0.5, "mom": 0.5},
             "fwd_excess": (i % 2) * 0.1 - 0.05} for i in range(20)]
    p = L.fit_policy(rows)
    assert p["w_ranker"] == kr_policy.DEFAULT_POLICY["w_ranker"]


def test_eval_policy_uses_top_max_positions_and_real_mdd():
    rows = [{"features": {"ranker": s}, "fwd_excess": e, "fwd_mdd": m}
            for s, e, m in [(0.9, 0.20, 0.05), (0.8, 0.10, 0.10), (0.2, -0.10, 0.30), (0.1, -0.20, 0.40)]]
    r = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    # 배치와 동일 상위 2(0.9,0.8) 선택 → 평균 초과수익 0.15, 보유기간 MDD 평균 0.075
    assert r["excess"] == pytest.approx(0.15)
    assert r["mdd"] == pytest.approx(0.075)       # (0.05+0.10)/2 — 실제 낙폭 단위
    assert r["n"] == 4


def test_backfill_matures_entries_only_and_idempotent(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.log_decision({"date": "2026-05-01", "ticker": "005930.KS", "code": "005930", "side": "편입",
                     "features": {"ranker": 0.8}})
    lg.log_decision({"date": "2026-05-01", "ticker": "000660.KS", "code": "000660", "side": "퇴출"})
    lg.log_decision({"date": "2026-05-02", "ticker": "035720.KS", "code": "035720", "side": "편입",
                     "features": {"ranker": 0.3}})

    def fake_price(ticker, date, horizon):
        if ticker == "005930.KS":
            return (0.12, 0.04, 0.06, 0.03)   # (종목수익, 지수수익, 종목MDD, 지수MDD)
        return None                            # 035720 미성숙

    added = L.backfill_outcomes(lg, price_fn=fake_price)
    assert added == 1                                   # 편입·성숙분만 (퇴출 제외, 미성숙 제외)
    from ml.adaptive import costs
    rt = costs.round_trip_frac("KR")
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"
    assert outs[0]["gross_excess"] == pytest.approx(0.08)                          # 원 초과
    assert outs[0]["fwd_excess"] == pytest.approx(0.08 - rt) and outs[0]["success"] is True  # net(왕복비용 차감)
    assert outs[0]["fwd_mdd"] == pytest.approx(0.06) and outs[0]["idx_fwd_mdd"] == pytest.approx(0.03)

    # 멱등: 재실행해도 중복 outcome 없음
    assert L.backfill_outcomes(lg, price_fn=fake_price) == 0
    assert len(lg.read_outcomes()) == 1

    # 학습셋 조인(성숙분만)
    ts = lg.training_set()
    assert len(ts) == 1 and ts[0]["fwd_excess"] == pytest.approx(0.08 - rt)


def test_backfill_excludes_unfilled_decisions(tmp_path):
    """미집행(ok=False) 결정은 보상 평가에서 제외 — 팬텀 트레이드 오염 방지(감사 확정·US S6 미러)."""
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.log_decision({"date": "2026-05-01", "ticker": "005930.KS", "code": "005930", "side": "편입",
                     "features": {"ranker": 0.8}, "ok": True})
    lg.log_decision({"date": "2026-05-01", "ticker": "000660.KS", "code": "000660", "side": "편입",
                     "features": {"ranker": 0.7}, "ok": False})   # 주문 거부 → 학습 제외돼야

    def fake_price(ticker, date, horizon):
        return (0.12, 0.04, 0.06, 0.03)

    added = L.backfill_outcomes(lg, price_fn=fake_price)
    assert added == 1                                            # ok=True 편입만 성숙(ok=False 제외)
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"


def test_default_price_fn_uses_cached_ohlc_when_yfinance_unavailable(tmp_path, monkeypatch):
    """학습 크론은 로컬 OHLC 캐시를 먼저 사용해야 네트워크 없이도 outcome 이 성숙한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # providers.market_data 의 OHLC 캐시는 STOCK_REPORT_OHLC_CACHE_DIR 를 HOME 보다
    # 우선하므로(테스트 간 격리용, 감사 후속 #36), 이 테스트가 쓰는 캐시 경로도
    # 같은 env var 로 맞춰야 _default_price_fn 이 실제로 읽어간다.
    monkeypatch.setenv("STOCK_REPORT_OHLC_CACHE_DIR", str(tmp_path / "reports" / "ml-cache" / "ohlc_cache"))
    _write_cached_ohlc(tmp_path, "005930.KS", [100, 101, 102, 103, 104])
    _write_cached_ohlc(tmp_path, "^KS11", [200, 200, 200, 200, 200])

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    # 캐시가 목표일에 못 미치면 라이브 재조회를 시도하므로(감사 2026-08-21 staleness 수정),
    # yf.download 뿐 아니라 그 경로(_fetch_close_series_live)도 오프라인으로 막아야
    # 이 테스트가 "캐시만으로 동작" 을 실제로 검증한다(안 막으면 네트워크로 새어 실패).
    from providers import market_data as _md
    monkeypatch.setattr(_md, "_fetch_close_series_live", lambda *a, **k: None)

    out = L._default_price_fn("005930.KS", "2026-05-01", 3)
    assert out is not None
    stock_ret, idx_ret, stock_mdd, idx_mdd = out
    assert stock_ret == pytest.approx(0.03, abs=1e-4)
    assert idx_ret == pytest.approx(0.0, abs=1e-4)
    assert stock_mdd == pytest.approx(0.0, abs=1e-4)
    assert idx_mdd == pytest.approx(0.0, abs=1e-4)


def test_default_price_fn_uses_price_cache_when_ohlc_cache_missing(tmp_path, monkeypatch):
    """price_*.pkl 캐시만 있어도 학습 크론이 벤치마크를 읽을 수 있어야 한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_price_cache(tmp_path, "005930.KS", [100, 101, 102, 103, 104])
    _write_price_cache(tmp_path, "^KS11", [200, 200, 200, 200, 200])

    from providers import market_data
    assert market_data._cached_price_paths("005930.KS")
    assert market_data._cached_price_paths("^KS11")

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    # 캐시가 목표일에 못 미치면 라이브 재조회를 시도하므로(감사 2026-08-21 staleness 수정),
    # yf.download 뿐 아니라 그 경로(_fetch_close_series_live)도 오프라인으로 막아야
    # 이 테스트가 "캐시만으로 동작" 을 실제로 검증한다(안 막으면 네트워크로 새어 실패).
    from providers import market_data as _md
    monkeypatch.setattr(_md, "_fetch_close_series_live", lambda *a, **k: None)

    out = L._default_price_fn("005930.KS", "2026-05-01", 3)
    assert out is not None
    stock_ret, idx_ret, stock_mdd, idx_mdd = out
    assert stock_ret == pytest.approx(0.03, abs=1e-4)
    assert idx_ret == pytest.approx(0.0, abs=1e-4)
    assert stock_mdd == pytest.approx(0.0, abs=1e-4)
    assert idx_mdd == pytest.approx(0.0, abs=1e-4)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_new_axis_stability_gate():
    """신규 축(E): 표본<20 미측정 · 전/후반 부호 불일치 → 0 · 일관 양상관 → 가중 (learner 공용)."""
    from ml.adaptive.learner import robust_axis_weight
    up = [(i / 30, i / 300) for i in range(30)]              # 일관 양상관 30쌍
    assert robust_axis_weight(up, min_pairs=20, stability=True) > 0.9
    assert robust_axis_weight(up[:10], min_pairs=20, stability=True) is None   # 소표본 미측정
    flip = [(i / 15, i / 150) for i in range(15)] + [(i / 15, -i / 150) for i in range(15)]
    assert robust_axis_weight(flip, min_pairs=20, stability=True) == 0.0       # 부호 불일치 → 0
    # 기존 축은 완화 게이트(5쌍·안정성 미요구) 유지
    assert robust_axis_weight(up[:6], min_pairs=5, stability=False) > 0.9


def test_backfill_supports_shadow_observation_side(tmp_path):
    """랭킹 섀도(side='관측')도 성숙시켜야 IC 측정이 된다 — 기본 sides 엔 미포함(라이브 불변)."""
    lg = Ledger("kr_mock_shadow", base_dir=tmp_path)
    lg.log_decision({"date": "2026-05-01", "ticker": "005930.KS", "code": "005930",
                     "side": "관측", "shadow": True, "ok": True, "policy_score": 0.8})

    def fake_price(ticker, date, horizon):
        return (0.12, 0.04, 0.06, 0.03)

    # 기본 호출은 '관측'을 무시(라이브 규칙 그대로)
    assert L.backfill_outcomes(lg, price_fn=fake_price) == 0
    # 섀도 전용 호출만 성숙
    added = L.backfill_outcomes(lg, price_fn=fake_price, sides=("관측",))
    assert added == 1
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"
