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


def test_eval_policy_uses_top_max_positions_per_date():
    rows = [{"features": {"ranker": s}, "fwd_excess": e, "fwd_mdd": m}
            for s, e, m in [(0.9, 0.20, 0.05), (0.8, 0.10, 0.10), (0.2, -0.10, 0.30), (0.1, -0.20, 0.40)]]
    r = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    # 같은 결정일(날짜 없음 = 하루) 상위 2(0.9,0.8) 바스켓 → 평균 초과수익 0.15 · 평가일 1개
    assert r["excess"] == pytest.approx(0.15)
    assert r["n"] == 1 and r["n_days"] == 1
    assert r["n_candidates"] == 4 and r["n_selected"] == 2


_TD = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-03-02", periods=700)]


def _held(day_i, score, excess, stock_path, idx_path=None, ticker=None):
    """day_i 거래일 결정 · 보유 경로(진입=1) 를 가진 학습행 (backfill 이 남기는 outcome 형식)."""
    days = _TD[day_i:day_i + len(stock_path)]
    idx_path = idx_path or [1.0] * len(stock_path)
    name = ticker or f"T{score}"
    return {"id": f"{_TD[day_i]}:{name}", "date": _TD[day_i], "ticker": name,
            "features": {"ranker": score}, "fwd_excess": excess, "gross_excess": excess,
            "entry_date": days[0], "exit_date": days[-1],
            "path": [[d, s, i] for d, s, i in zip(days, stock_path, idx_path)]}


def test_eval_policy_mdd_follows_holding_path_not_exit_returns():
    """A-fix2 RA-01: 종료 수익은 +10% 여도 보유 중 50% 낙폭은 MDD 에 남는다(종료수익 합성곡선 금지)."""
    d0 = [_held(0, 0.9, 0.10, [1.0, 0.5, 0.9, 1.1]), _held(0, 0.8, 0.10, [1.0, 1.0, 1.0, 1.1]),
          _held(0, 0.1, -0.5, [1.0, 1.0, 1.0, 0.5])]
    d1 = [_held(1, 0.9, 0.06, [1.0, 1.0, 1.0, 1.06]), _held(1, 0.8, 0.06, [1.0, 1.0, 1.0, 1.06])]
    r = L.eval_policy(d0 + d1, {"w_ranker": 1.0}, max_positions=2)
    assert r["excess"] == pytest.approx(0.08)                 # 점추정은 모든 평가일(0.10, 0.06)
    assert (r["n"], r["n_candidates"], r["n_selected"]) == (2, 5, 4)
    # 비중첩: d0 코호트(종료 _TD[3]) 보유 중 시작한 d1 은 독립 관측이 아니다
    assert r["series"] == pytest.approx([0.10]) and r["n_independent"] == 1
    assert r["series_days"] == [_TD[0]]
    assert sorted(r["series_ids"][0]) == sorted([d0[0]["id"], d0[1]["id"]])
    # 바스켓 일별 가치 1 → (0.5+1)/2=0.75 → 0.95 → 1.10 → MDD 0.25, 지수 평탄 → 0
    assert r["mdd"] == pytest.approx(0.25) and r["index_mdd"] == pytest.approx(0.0)
    assert r["mdd_basis"] == "holding_path"


def test_eval_policy_without_paths_has_no_risk_or_independent_evidence():
    """legacy 행(경로·종료일 없음): 후보 수를 표본으로 세지 않고, 독립 증거·위험 곡선도 만들지 않는다."""
    rows = []
    for i in range(20):
        day = f"2026-03-{i + 1:02d}"
        good = i < 5
        rows.append({"date": day, "ticker": "G", "features": {"ranker": 0.9 if good else 0.6},
                     "fwd_excess": 0.10 if good else -0.90})
        rows.append({"date": day, "ticker": "B", "features": {"ranker": 0.1}, "fwd_excess": -0.90})
    r = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=5)
    assert r["n"] == 20 and r["n_candidates"] == 40 and r["n_selected"] == 40
    assert r["excess"] < 0                               # 매일 바스켓이 -90% 행을 포함
    assert r["n_independent"] == 0 and r["series"] == [] and r["n_unresolved_days"] == 20
    assert r["mdd"] is None and r["index_mdd"] is None and r["mdd_basis"] == "unavailable"


def test_eval_policy_independent_series_is_first_day_of_each_label_block():
    """A-fix2 RA-02 재현 2: 블록 첫날 -2%·나머지 +2%. 검정 입력은 실제 비중첩 관측(블록 첫날)이어야 한다.

    예전엔 전체 400 평가일 열로 PSR .957·ok=True 를 냈다(보고 표본과 검정 관측열 불일치)."""
    from ml.adaptive import learner
    rows = []
    for b in range(20):
        for j in range(20):
            day_i = b * 20 + j
            ex = (-0.02 + 0.001 * (b % 3)) if j == 0 else (0.02 + 0.001 * (day_i % 3))
            rows.append(_held(day_i, 0.9, ex, [1.0] * 20 + [1.0 + ex], ticker="X"))
    r = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=1)
    assert r["series_days"] == [_TD[b * 20] for b in range(20)]
    by_id = {row["id"]: row["fwd_excess"] for row in rows}
    assert r["series"] == pytest.approx([by_id[ids[0]] for ids in r["series_ids"]])
    assert r["n_independent"] == len(r["series"]) == 20
    ev = learner.adoption_evidence(r)
    assert ev["ok"] is False and ev["n_independent"] == 20


def _win(stock_ret, idx_ret, stock_mdd, idx_mdd, entry="2026-05-01", exit_="2026-05-29"):
    """_holding_window 형식(보유 경로 포함) 가짜 결과."""
    return {"stock_ret": stock_ret, "idx_ret": idx_ret, "stock_mdd": stock_mdd, "idx_mdd": idx_mdd,
            "entry_date": entry, "exit_date": exit_,
            "path": [[entry, 1.0, 1.0], [exit_, 1.0 + stock_ret, 1.0 + idx_ret]]}


def test_backfill_matures_entries_only_and_idempotent(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.log_decision({"date": "2026-05-01", "ticker": "005930.KS", "code": "005930", "side": "편입",
                     "features": {"ranker": 0.8}})
    lg.log_decision({"date": "2026-05-01", "ticker": "000660.KS", "code": "000660", "side": "퇴출"})
    lg.log_decision({"date": "2026-05-02", "ticker": "035720.KS", "code": "035720", "side": "편입",
                     "features": {"ranker": 0.3}})

    def fake_window(ticker, date, horizon):
        if ticker == "005930.KS":
            return _win(0.12, 0.04, 0.06, 0.03)   # (종목수익, 지수수익, 종목MDD, 지수MDD)
        return None                                # 035720 미성숙

    added = L.backfill_outcomes(lg, window_fn=fake_window)
    assert added == 1                                   # 편입·성숙분만 (퇴출 제외, 미성숙 제외)
    from ml.adaptive import costs
    rt = costs.round_trip_frac("KR")
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"
    assert outs[0]["gross_excess"] == pytest.approx(0.08)                          # 원 초과
    assert outs[0]["fwd_excess"] == pytest.approx(0.08 - rt) and outs[0]["success"] is True  # net(왕복비용 차감)
    assert outs[0]["fwd_mdd"] == pytest.approx(0.06) and outs[0]["idx_fwd_mdd"] == pytest.approx(0.03)
    # 위험곡선·비중첩 판정의 근거(실제 진입/종료 거래일·보유 경로)를 outcome 에 보존
    assert (outs[0]["entry_date"], outs[0]["exit_date"]) == ("2026-05-01", "2026-05-29")
    assert [p[0] for p in outs[0]["path"]] == ["2026-05-01", "2026-05-29"]
    assert [p[1:] for p in outs[0]["path"]] == [pytest.approx([1.0, 1.0]), pytest.approx([1.12, 1.04])]

    # 멱등: 재실행해도 중복 outcome 없음
    assert L.backfill_outcomes(lg, window_fn=fake_window) == 0
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

    added = L.backfill_outcomes(lg, window_fn=lambda t, d, h: _win(0.12, 0.04, 0.06, 0.03))
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

    fake_window = lambda ticker, date, horizon: _win(0.12, 0.04, 0.06, 0.03)

    # 기본 호출은 '관측'을 무시(라이브 규칙 그대로)
    assert L.backfill_outcomes(lg, window_fn=fake_window) == 0
    # 섀도 전용 호출만 성숙
    added = L.backfill_outcomes(lg, window_fn=fake_window, sides=("관측",))
    assert added == 1
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"


def test_default_holding_window_records_trading_day_path(tmp_path, monkeypatch):
    """기본 백필 경로: 공통 거래일 정렬 후 실제 진입/종료 거래일과 일별 경로(진입=1)를 낸다."""
    from providers import market_data
    idx = pd.bdate_range("2026-05-01", periods=6)
    stock = pd.Series([100.0, 80.0, 90.0, 105.0, 110.0, 120.0], index=idx)
    bench = pd.Series([200.0, 200.0, 190.0, 200.0, 210.0, 220.0], index=idx)
    from ml.data_pipeline import KR_BENCHMARK
    series = {"005930.KS": stock, KR_BENCHMARK: bench}
    monkeypatch.setattr(market_data, "load_close_series_upto",
                        lambda t, need_last=None, fetch_fn=None: series.get(t))
    w = L._holding_window("005930.KS", "2026-05-01", 4)
    assert (w["entry_date"], w["exit_date"]) == ("2026-05-01", "2026-05-07")
    assert [p[0] for p in w["path"]] == [d.strftime("%Y-%m-%d") for d in idx[:5]]
    assert [p[1] for p in w["path"]] == pytest.approx([1.0, 0.8, 0.9, 1.05, 1.1])
    assert w["stock_ret"] == pytest.approx(0.10) and w["stock_mdd"] == pytest.approx(0.20)
    assert L._default_price_fn("005930.KS", "2026-05-01", 4) == pytest.approx(
        (w["stock_ret"], w["idx_ret"], w["stock_mdd"], w["idx_mdd"]))


def _risk_rows(risk_path_fn, n_days=500):
    """매 결정일 SAFE(무수익·무낙폭)·RISK(20일 종료 +1~2%) 두 후보. 지수 평탄."""
    rows = []
    for j in range(n_days):
        ex = 0.01 + 0.005 * (j % 3)
        safe = _held(j, 0.4, 0.0, [1.0] * 21, ticker="SAFE")
        safe["features"] = {"ranker": 0.4, "fund": 1.0, "signal": 1.0}
        risk = _held(j, 0.6, ex, risk_path_fn(ex), ticker="RISK")
        risk["features"] = {"ranker": 0.6, "fund": 0.0, "signal": 0.0}
        safe["index_ret"] = risk["index_ret"] = 0.0
        rows += [safe, risk]
    return rows


def _refit_risk(tmp_path, monkeypatch, rows):
    from ml import kr_policy
    from ml.adaptive import learner, policy as pol
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    policy = kr_policy.get_policy()                        # 챔피언 = 기본 가중 → SAFE 선택
    saved = []
    monkeypatch.setattr(policy, "save", lambda params, meta=None: saved.append((params, meta)))
    candidate = {k: 0.0 for k in kr_policy.DEFAULT_POLICY} | {"w_ranker": 1.0}   # → RISK 선택
    out = learner.refit_and_adopt(rows, policy, lambda tr: dict(candidate),
                                  lambda oos, params: L.eval_policy(oos, params, 1),
                                  index_mdd=0.0, min_samples=40, embargo=L.HORIZON)
    return out, saved


def test_refit_blocks_candidate_with_80pct_holding_drawdown(tmp_path, monkeypatch):
    """A-fix2 RA-01 회귀: 종료 수익은 +1~2% 지만 보유 중 80% 낙폭 → 예전엔 MDD 0·저장 1회."""
    rows = _risk_rows(lambda ex: [1.0, 0.2] + [0.2] * 18 + [1.0 + ex])
    out, saved = _refit_risk(tmp_path, monkeypatch, rows)
    chal = out["challenger"]
    assert chal["mdd"] >= 0.79 and chal["index_mdd"] == pytest.approx(0.0)
    assert out["adopted"] is False and saved == []


def test_refit_adopts_same_candidate_without_holding_drawdown(tmp_path, monkeypatch):
    """대조군: 같은 종료 수익·보유 낙폭 0 → 위험·통계 게이트 모두 통과해 저장 1회(게이트 상시 폐쇄 아님)."""
    rows = _risk_rows(lambda ex: [1.0] * 20 + [1.0 + ex])
    out, saved = _refit_risk(tmp_path, monkeypatch, rows)
    assert out["challenger"]["mdd"] == pytest.approx(0.0)
    assert out["challenger"]["n_independent"] >= 8
    assert out["adopted"] is True and len(saved) == 1
