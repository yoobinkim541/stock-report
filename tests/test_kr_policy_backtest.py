"""tests/test_kr_policy_backtest.py — KR 선택정책 백테스트 순수로직 (무네트워크·합성 패널).

핵심 감사: ①무룩어헤드(월말 t 피처는 t 이후 데이터와 무관) ②비용 차감 방향
③상폐/결측 처리(직전가 유지) ④워크포워드 폴드가 OOS 연도만 연결 ⑤verdict 정직 분기.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "backtest"))       # conftest 관례 (베어 임포트)

import kr_policy_backtest as bt


def _panels(n_days=520, n_codes=25, seed=7, trend_code="000010"):
    """합성 패널 — trend_code 만 일 +0.3% 추세, 나머지 0 중심 노이즈."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-02", periods=n_days)
    codes = [f"{i:05d}0" for i in range(1, n_codes + 1)]      # 보통주(끝자리 0)
    ret = pd.DataFrame(rng.normal(0.0, 0.01, (n_days, n_codes)), index=dates, columns=codes)
    ret[trend_code] = 0.003 + rng.normal(0.0, 0.002, n_days)
    adj = (1.0 + ret).cumprod()
    marcap = pd.DataFrame(1e12, index=dates, columns=codes)
    marcap.loc[:, trend_code] = 2e12                          # 유니버스 안정 포함
    amount = pd.DataFrame(1e9, index=dates, columns=codes)
    return {"ret": ret, "adj": adj, "amount": amount, "marcap": marcap}


def test_month_ends_last_trading_day():
    dates = pd.bdate_range("2024-01-02", "2024-03-29")
    mes = bt.month_ends(dates)
    assert [d.strftime("%Y-%m-%d") for d in mes] == ["2024-01-31", "2024-02-29", "2024-03-29"]


def test_features_no_lookahead():
    """t 이후 수익률을 극단 변경해도 t 시점 피처 불변 (point-in-time 보증)."""
    p = _panels()
    t = bt.month_ends(p["ret"].index)[-2]                     # 마지막 전 월말
    f1 = bt.features_asof(p, t)
    p2 = {k: v.copy() for k, v in p.items()}
    after = p2["ret"].index > t
    p2["ret"].loc[after] = 0.25                               # 미래 조작
    p2["adj"] = (1.0 + p2["ret"]).cumprod()
    f2 = bt.features_asof(p2, t)
    assert f1 is not None and f2 is not None
    pd.testing.assert_frame_equal(f1, f2)


def test_select_top_momentum_picks_trend():
    p = _panels()
    t = bt.month_ends(p["ret"].index)[-1]
    f = bt.features_asof(p, t)
    assert f is not None
    picks = bt.select_top(f, {"mom12": 1.0}, k=3)
    assert "000010" in picks                                  # 추세 종목 선두 포함


def test_simulate_cost_reduces_return():
    """비용>0 이면 첫 리밸일 수익이 정확히 비용만큼 낮다."""
    p = _panels()
    mes = bt.month_ends(p["ret"].index)
    picks = {mes[-3]: ["000010", "000020", "000030"]}
    r0 = bt.simulate(p["ret"], picks, buy_bps=0.0, sell_bps=0.0)
    r1 = bt.simulate(p["ret"], picks, buy_bps=10.0, sell_bps=0.0)
    assert len(r0) == len(r1) and len(r0) > 0
    # 첫날만 차이 = 10bps(전 종목 신규매수), 이후 동일
    assert abs((r0.iloc[0] - r1.iloc[0]) - 10.0 / 1e4) < 1e-9
    assert np.allclose(r0.iloc[1:].values, r1.iloc[1:].values)


def test_simulate_turnover_cost_only_on_changed_names():
    """직전 바스켓과 동일하면 비용 0, 1종목 교체면 (1/k)·(sell+buy)."""
    p = _panels()
    mes = bt.month_ends(p["ret"].index)
    same = ["000010", "000020", "000030"]
    swap = ["000010", "000020", "000040"]
    keep = bt.simulate(p["ret"], {mes[-4]: same, mes[-3]: same}, buy_bps=10.0, sell_bps=20.0)
    chg = bt.simulate(p["ret"], {mes[-4]: same, mes[-3]: swap}, buy_bps=10.0, sell_bps=20.0)
    d2 = p["ret"].index[p["ret"].index > mes[-3]][0]          # 2차 리밸 첫 거래일
    # keep: 2차 리밸 비용 0. chg: 1/3 매도 20bps + 1/3 매수 10bps = 10bps
    exp = (1 / 3) * (20.0 + 10.0) / 1e4
    r_keep_start = keep.loc[d2]
    # 동일 멤버 구성이 아니라 수익률 자체는 다르므로 '비용 성분'만 근사 비교:
    # swap 케이스에서 000040 대신 000030 을 다시 넣은 무비용 시뮬과의 차가 exp 와 일치
    chg0 = bt.simulate(p["ret"], {mes[-4]: same, mes[-3]: swap}, buy_bps=0.0, sell_bps=0.0)
    assert abs((chg0.loc[d2] - chg.loc[d2]) - exp) < 1e-9
    assert r_keep_start == pytest.approx(
        bt.simulate(p["ret"], {mes[-4]: same, mes[-3]: same}, buy_bps=0.0, sell_bps=0.0).loc[d2])


def test_simulate_missing_prices_carry():
    """멤버 수익률 결측(정지/상폐) → 그 종목 가치 유지(수익 0), 포트폴리오는 계속."""
    p = _panels()
    mes = bt.month_ends(p["ret"].index)
    t = mes[-3]
    dead = "000020"
    after = p["ret"].index > t
    p["ret"].loc[after, dead] = np.nan                        # t 이후 소멸
    r = bt.simulate(p["ret"], {t: ["000010", dead]}, buy_bps=0.0, sell_bps=0.0)
    alive = bt.simulate(p["ret"], {t: ["000010"]}, buy_bps=0.0, sell_bps=0.0)
    assert len(r) == len(alive)
    # dead 가치 고정 → 포트 수익률은 alive 단독의 절반 근방에서 시작(등가중 2종목)
    assert abs(r.iloc[0] - alive.iloc[0] / 2.0) < 5e-3


def test_cap_benchmark_matches_single_stock():
    """유니버스 1종목이면 벤치 수익률 = 그 종목 수익률."""
    p = _panels(n_codes=1, trend_code="000010")
    mes = bt.month_ends(p["ret"].index)
    b = bt.cap_benchmark(p, mes[-4:-1], universe_n=1)
    seg = p["ret"]["000010"].reindex(b.index)
    assert np.allclose(b.values, seg.values, atol=1e-12)


def test_walk_forward_and_verdict_runs():
    """합성 ~5.5년 → 폴드 생성·OOS 연결·verdict 3분기 중 하나. (train_years 축소)"""
    p = _panels(n_days=1400)
    cfgs = {"mom12": {"mom12": 1.0}, "lowvol": {"vol_inv": 1.0}}
    wf = bt.walk_forward(p, cfgs, train_years=2)
    assert not wf.get("error"), wf
    assert wf["folds"], "폴드 없음"
    v = bt.build_verdict(wf, n_trials=len(cfgs))
    assert v["code"] in ("GO", "OBSERVE", "NO-GO")
    # OOS 연결 구간은 폴드 연도 안에만 존재
    fold_years = {f["year"] for f in wf["folds"]}
    assert set(wf["oos_returns"].index.year) <= fold_years


def test_verdict_nogo_when_underperform():
    """전략이 벤치를 명확히 언더퍼폼하면 NO-GO."""
    dates = pd.bdate_range("2022-01-03", periods=300)
    rng = np.random.default_rng(3)
    oos = pd.Series(rng.normal(-0.001, 0.01, 300), index=dates)
    bench = pd.Series(rng.normal(0.001, 0.01, 300), index=dates)
    cfg_daily = pd.DataFrame({"a": oos, "b": oos * 0.5})
    wf = {"oos_returns": oos, "bench_returns": bench, "config_daily": cfg_daily}
    v = bt.build_verdict(wf, n_trials=2)
    assert v["code"] == "NO-GO" and v["net_excess_total"] < 0
