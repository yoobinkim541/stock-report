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


# ── 현재 권고 (crons/kr_axes_eval — 매핑·게이트) ──────────────────────────────

def test_mappable_configs_excludes_unmappable_and_negative():
    cfgs = {"ok": {"mom12": 0.6, "hi52": 0.4},
            "has_liq": {"liq": 1.0},                      # 정책 축 없음
            "has_neg": {"mom12": 0.7, "rev1": -0.3}}      # 음수 — bounds 밖
    m = bt.mappable_configs(cfgs)
    assert set(m) == {"ok"}


def test_current_recommendation_picks_best_and_maps_budget():
    dates = pd.bdate_range("2018-01-02", periods=252 * 6)
    rng = np.random.default_rng(5)
    bench = pd.Series(rng.normal(0.0003, 0.01, len(dates)), index=dates)
    good = bench + 0.001                                   # 지수 지속 아웃퍼폼
    bad = bench - 0.001
    cfg_daily = pd.DataFrame({"winner": good, "loser": bad})
    cfgs = {"winner": {"hi52": 0.6, "vol_inv": 0.4}, "loser": {"rev1": 1.0}}
    rec = bt.current_recommendation(cfg_daily, bench, train_years=5, configs=cfgs)
    assert rec and rec["chosen"] == "winner"
    pw = rec["policy_weights"]
    assert pw["w_hi52"] == pytest.approx(bt.AXES_BUDGET * 0.6)
    assert pw["w_lowvol"] == pytest.approx(bt.AXES_BUDGET * 0.4)
    assert pw["w_mom12"] == 0.0 and pw["w_mom"] == 0.0     # 미포함 축 0 명시
    assert sum(pw.values()) == pytest.approx(bt.AXES_BUDGET, abs=1e-6)


def test_current_recommendation_none_when_short():
    dates = pd.bdate_range("2024-01-02", periods=100)      # 3년 미만
    s = pd.Series(0.001, index=dates)
    rec = bt.current_recommendation(pd.DataFrame({"winner": s}), s,
                                    configs={"winner": {"hi52": 1.0}})
    assert rec is None


# ── 레짐 방어 오버레이 + 비용 스윕 (P4) ───────────────────────────────────────

def test_picks_hysteresis_keeps_holdings_in_buffer():
    """직전 보유가 top-(k+buffer) 안이면 유지 (회전율 억제)."""
    idx = pd.bdate_range("2020-01-02", periods=3, freq="ME")
    # 3개 리밸 시점 · 6종목, hi52 순위를 시점마다 살짝 뒤섞어 경계 유지 검증
    feats = {}
    for i, t in enumerate(idx):
        codes = [f"{j:05d}0" for j in range(1, 7)]
        vals = [6, 5, 4, 3, 2, 1] if i == 0 else [5, 6, 4, 3, 1, 2]  # 1·2위 스왑
        feats[t] = pd.DataFrame({"hi52": vals}, index=codes).apply(bt._z)
    rebal = list(idx)
    # buffer0: top2 매 시점 그대로 = 시점1에 000010↔000020 스왑 발생
    p0 = bt._picks_hysteresis(feats, rebal, {"hi52": 1.0}, k=2, buffer=0)
    # buffer2: top(2+2)=top4 안이면 유지 → 보유 000010·000020 이 top4 안이라 무교체
    p2 = bt._picks_hysteresis(feats, rebal, {"hi52": 1.0}, k=2, buffer=2)
    assert bt._avg_turnover(p2, 2) <= bt._avg_turnover(p0, 2)


def test_avg_turnover_bounds():
    picks = {"a": ["1", "2", "3"], "b": ["1", "2", "3"], "c": ["1", "4", "5"]}
    # b: 무교체(0), c: 2/3 교체 → 평균 (0 + 0.667)/2 = 0.333 (표시용 3자리 반올림)
    assert bt._avg_turnover(picks, 3) == 0.333


def test_regime_overlay_defense_verdict(monkeypatch):
    """레짐 오버레이 — 방어 verdict 3분기·MDD 지표·DSR 산출 (합성 무예외)."""
    p = _panels(n_days=1600)
    feats = {t: f for t in bt.month_ends(p["ret"].index) if (f := bt.features_asof(p, t)) is not None}
    rebal = sorted(feats.keys())
    bench = bt.cap_benchmark(p, rebal)
    out = bt.regime_overlay_eval(p, feats, rebal, bench, ma=100)
    assert out["code"] in ("GO", "OBSERVE", "NO-GO")
    assert "overlay" in out and "offense_alone" in out and "bench" in out
    assert "/" in out["bear_defend_years"] and out["dsr"] is None or isinstance(out["dsr"], float)


def test_cost_sensitivity_drag_and_best(monkeypatch):
    """비용 스윕 — 각 스킴 drag≥0·best 는 순CAGR 최대·현재 대비 gain 산출."""
    p = _panels(n_days=1600)
    feats = {t: f for t in bt.month_ends(p["ret"].index) if (f := bt.features_asof(p, t)) is not None}
    rebal = sorted(feats.keys())
    bench = bt.cap_benchmark(p, rebal)
    cs = bt.cost_sensitivity(p, feats, rebal, bench)
    assert len(cs["rows"]) == 5
    assert all(r["drag_pp"] >= -1e-9 for r in cs["rows"])          # 무비용이 유비용보다 나쁠 수 없음
    assert cs["best"]["net_cagr"] == max(r["net_cagr"] for r in cs["rows"])
    assert cs["current"]["scheme"] == "월간·버퍼2"
