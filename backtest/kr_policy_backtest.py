#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/kr_policy_backtest.py — KR 선택정책(모의 top-5) ★아웃퍼폼 정밀 검증 + 개선 탐색 게이트.

질문: "ML/RL 선택 로직이 KOSPI 를 **순비용으로** 이길 수 있나? 이긴다면 어떤 피처 축인가?"

설계(정직 — 6티어 규율 그대로):
- **무생존편향**: marcap 전종목 패널(상폐 종목 포함, providers/kr_market_data). 유니버스는
  각 리밸런스 시점의 시총 상위 N (point-in-time — 오늘의 생존자 목록 아님).
- **무수정주가 함정 차단**: marcap `Close` 는 무수정이라 액면분할(삼성전자 2018 50:1 →
  −98% 가짜 폭락)·감자(+900% 가짜 급등)가 수익률을 오염시킨다. 일수익률은 KRX 가
  **기준가 조정으로 산출한 `ChangesRatio`** 를 쓰고(분할일 실측 −2.08% 확인), 가격류
  피처(모멘텀·변동성·52주고가)는 그 누적곱으로 만든 **합성 수정주가** 로 계산한다.
- **무룩어헤드**: 피처는 월말 t 까지 데이터만, 포지션은 t 익일부터 유효. 워크포워드 학습은
  train 구간에서만 config 선택 → OOS 연도에 적용(선택 시점에 미래 정보 0).
- **KR 비대칭 비용**: 매수 2bps·매도 20bps(증권거래세, ml/adaptive/costs 정합) — 교체 종목만 부과.
- **벤치마크**: 같은 패널의 시총가중 상위 200 (KOSPI200 프록시·무비용 — 전략에 보수적).
  전략·벤치 모두 배당 제외(총수익 아님) — 비교는 대칭이나 고배당 축은 소폭 과소평가.
- **다중검정 정직**: config 그리드 전체를 n_trials 로 DSR deflate + PBO(CSCV) 산출.
  "그리드에서 좋아 보이는 것"이 아니라 "워크포워드 OOS 연결 성과"만 판정에 사용.
- **상폐/거래정지**: 결측일 수익률 0(직전가 유지), 기간 내 소멸 시 마지막 관측가 청산
  (정리매매 급락은 등락률에 반영. 최근 5일 무거래 종목은 편입 배제 — 정지 종목 매수 불가).

피처 축(모두 t 시점 계산가능 — 라이브 kr_policy 의 mom 축과 겹치는 rev1 포함):
  mom12   12-1개월 모멘텀 (252~21일 전 수익률) — 문헌 표준 모멘텀
  rev1    최근 1개월 수익률 — **현행 정책 w_mom 축과 동일 방향**(+1M) → 이 축이 실제로
          도움인지/역효과인지 직접 판정된다 (KR 은 1M 단기반전 문헌 다수)
  vol_inv 저변동성 (60일 일수익 표준편차 역부호 z)
  liq     유동성 (60일 평균 거래대금/시총)
  size    소형주 틸트 (−log 시총 z)
  hi52    52주 고가 근접도

판정(★목적함수 + Tier2 formalism):
  GO      = OOS 연결 순초과수익>0 AND OOS MDD≤지수 AND DSR≥0.95 AND PBO<0.5
  OBSERVE = OOS 순초과>0 인데 통계 관문 일부 미달(표본 축적/재검 대상)
  NO-GO   = OOS 순초과≤0 또는 MDD 하드 위반(지수 1.3배 초과)

실행: uv run python backtest/kr_policy_backtest.py           (전체 2001~ · 수분 소요)
      uv run python backtest/kr_policy_backtest.py --quick   (2015~ 축약)
출력: ~/reports/ml-cache/kr_policy_backtest.json (verdict·GO config·폴드별 채택 이력)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESULT_PATH = os.path.expanduser("~/reports/ml-cache/kr_policy_backtest.json")

TOP_K = 5            # KR 모의와 동일 (KR_MOCK_MAX_POS)
UNIVERSE_N = 200     # point-in-time 시총 상위 N (KOSPI200 급)
BUY_BPS = 2.0        # ml/adaptive/costs KR 정합
SELL_BPS = 20.0
MIN_OBS_252 = 200    # 12M 창 내 최소 거래일(피처 신뢰) — 신규상장·장기정지 배제
RET_CLIP = 0.35      # KR 가격제한폭(±30%) 밖 등락률 = 데이터 오류로 간주해 클립
TRAIN_YEARS = 5      # 워크포워드 train 창
HARD_MDD_MULT = 1.3  # reward.HARD_MDD_MULT 정합

# config 그리드 — 이 전체 개수가 DSR 의 n_trials (다중검정 정직).
CONFIGS: dict[str, dict] = {
    "mom12":        {"mom12": 1.0},
    "rev1(현행축)":  {"rev1": 1.0},                      # 현행 kr_policy w_mom 방향
    "lowvol":       {"vol_inv": 1.0},
    "liq":          {"liq": 1.0},
    "small":        {"size": 1.0},
    "hi52":         {"hi52": 1.0},
    "mom12+lowvol": {"mom12": 0.6, "vol_inv": 0.4},
    "mom12+hi52":   {"mom12": 0.6, "hi52": 0.4},
    "mom12+liq":    {"mom12": 0.6, "liq": 0.4},
    "lowvol+hi52":  {"vol_inv": 0.6, "hi52": 0.4},
    "mom12-rev1":   {"mom12": 0.7, "rev1": -0.3},        # 모멘텀 + 단기반전 회피
    "quality-ish":  {"vol_inv": 0.4, "mom12": 0.4, "liq": 0.2},
    "balanced":     {"mom12": 0.35, "vol_inv": 0.25, "hi52": 0.2, "liq": 0.2},
    "anti-small":   {"size": -1.0},                     # 대형주 틸트(시총가중과 구분)
}


# ══════════════════════════════════════════════════════════════════════
#  데이터 (네트워크 — marcap 연도별 parquet · 이후 전부 순수)
# ══════════════════════════════════════════════════════════════════════

def build_panels(start_year: int, end_year: int, market: str = "KOSPI") -> dict | None:
    """marcap → {ret, adj, amount, marcap} 일별 pivot. 실패 시 None.

    ret = ChangesRatio/100 (KRX 기준가 조정 등락률 — 분할/감자 안전) ±RET_CLIP 클립.
    adj = (1+ret) 누적곱 합성 수정주가 (가격류 피처용 — 절대수준 무의미·비율만 사용).
    """
    from providers import kr_market_data as kmd
    frames = []
    for y in range(start_year, end_year + 1):
        df = kmd._marcap_year(y)
        if df is None:
            continue
        if "Market" in df.columns and market:
            df = df[df["Market"] == market]
        cols = ["Code", "Date", "ChangesRatio", "Amount", "Marcap"]
        if any(c not in df.columns for c in cols):
            continue
        frames.append(df[cols])
    if not frames:
        return None
    panel = pd.concat(frames, ignore_index=True)
    panel["Date"] = pd.to_datetime(panel["Date"])

    def _pivot(col):
        return (panel.pivot_table(index="Date", columns="Code", values=col,
                                  aggfunc="last").astype("float32").sort_index())

    ret = (_pivot("ChangesRatio") / 100.0).clip(-RET_CLIP, RET_CLIP)
    adj = (1.0 + ret.fillna(0.0)).cumprod()
    adj = adj.where(ret.notna().cummax())        # 상장 전 구간은 NaN 유지
    return {"ret": ret, "adj": adj, "amount": _pivot("Amount"), "marcap": _pivot("Marcap")}


# ══════════════════════════════════════════════════════════════════════
#  피처 (순수 — 월말 t 까지 데이터만)
# ══════════════════════════════════════════════════════════════════════

def month_ends(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """월별 마지막 거래일."""
    s = pd.Series(dates, index=dates)
    return list(s.groupby(dates.to_period("M")).max())


def _z(s: pd.Series) -> pd.Series:
    """횡단면 z-score (±3 윈저라이즈). 표준편차 0 → 0."""
    sd = s.std()
    if not sd or np.isnan(sd):
        return s * 0.0
    return ((s - s.mean()) / sd).clip(-3.0, 3.0)


def features_asof(panels: dict, t: pd.Timestamp, universe_n: int = UNIVERSE_N) -> pd.DataFrame | None:
    """월말 t 시점 유니버스(시총 상위 N)의 피처 z-score DataFrame. 데이터 부족 시 None.

    모든 윈도는 t **이하** 데이터만 사용(룩어헤드 0). 유니버스 요건:
    시총 상위 N ∩ 12M 창 거래일 ≥ MIN_OBS_252 ∩ 최근 5일 거래 존재(정지 배제).
    """
    ret, adj, amount, marcap = panels["ret"], panels["adj"], panels["amount"], panels["marcap"]
    hist_r = ret.loc[:t]
    if len(hist_r) < 260:
        return None
    win = hist_r.iloc[-253:]
    mc_row = marcap.loc[:t].iloc[-1]
    tradable = (win.iloc[-5:].notna().sum() >= 1) & (win.notna().sum() >= MIN_OBS_252)
    valid = mc_row.dropna()
    valid = valid[tradable.reindex(valid.index).fillna(False)]
    # 보통주만(코드 끝자리 0) — 우선주 연속상한가류 실매매 불가 이상치가 축 성과를 오염 방지
    valid = valid[[str(c).endswith("0") for c in valid.index]]
    uni = valid.nlargest(universe_n).index
    if len(uni) < TOP_K * 4:
        return None
    h = adj.loc[:t, uni].iloc[-253:].ffill()
    px, px_21, px_252 = h.iloc[-1], h.iloc[-22], h.iloc[0]
    f = pd.DataFrame(index=uni)
    f["mom12"] = (px_21 / px_252 - 1.0)
    f["rev1"] = (px / px_21 - 1.0)
    f["vol_inv"] = -win[uni].iloc[-60:].std()
    amt60 = amount.loc[:t, uni].iloc[-60:].mean()
    f["liq"] = (amt60 / mc_row.reindex(uni)).replace([np.inf, -np.inf], np.nan)
    f["size"] = -np.log(mc_row.reindex(uni).astype(float))
    f["hi52"] = (px / h.max())
    f = f.dropna(subset=["mom12", "rev1", "vol_inv"])
    if len(f) < TOP_K * 4:
        return None
    return f.apply(_z)


def select_top(feats: pd.DataFrame, config: dict, k: int = TOP_K) -> list[str]:
    """config 가중 z 합산 점수 상위 k 종목코드."""
    score = pd.Series(0.0, index=feats.index)
    for name, w in config.items():
        if name in feats.columns:
            score = score + float(w) * feats[name].fillna(0.0)
    return list(score.nlargest(k).index)


# ══════════════════════════════════════════════════════════════════════
#  시뮬레이션 (순수 — 일별 NAV·비용)
# ══════════════════════════════════════════════════════════════════════

def simulate(ret: pd.DataFrame, picks: dict, *,
             buy_bps: float = BUY_BPS, sell_bps: float = SELL_BPS) -> pd.Series:
    """picks {월말 t: [codes]} → 일별 순수익률 Series (t 익일 편입·다음 월말까지 보유).

    기간 내 등가중 buy&hold(드리프트 허용) — 멤버 누적가치 = (1+ret) 누적곱(결측=0 유지).
    리밸런스 첫날에 교체분 비용(등가중 명수 비례 근사) 차감.
    """
    dates = ret.index
    rebal = sorted(picks.keys())
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    prev_set: set[str] = set()
    for i, t in enumerate(rebal):
        start_pos = dates.searchsorted(t, side="right")     # t 익일부터
        end = rebal[i + 1] if i + 1 < len(rebal) else dates[-1]
        end_pos = dates.searchsorted(end, side="right")
        if start_pos >= end_pos:
            continue
        cur = [c for c in picks[t] if c in ret.columns]
        if not cur:
            prev_set = set()
            continue
        seg = ret.iloc[start_pos:end_pos][cur].fillna(0.0)
        vals = (1.0 + seg).cumprod()                        # 멤버 누적가치 (시작 1)
        port = vals.mean(axis=1)
        r = port / port.shift(1).fillna(1.0) - 1.0
        n_sell = len(prev_set - set(cur))
        n_buy = len(set(cur) - prev_set)
        denom = max(len(prev_set), 1) if prev_set else len(cur)
        cost = (n_sell / max(denom, 1)) * sell_bps / 1e4 + (n_buy / len(cur)) * buy_bps / 1e4
        r.iloc[0] -= cost
        out.iloc[start_pos:end_pos] = r.values
        active.iloc[start_pos:end_pos] = True
        prev_set = set(cur)
    return out[active]


def cap_benchmark(panels: dict, rebal_dates: list, universe_n: int = UNIVERSE_N) -> pd.Series:
    """시총가중 상위 N 벤치마크(월별 재구성·무비용) 일별 수익률 — KOSPI200 프록시."""
    ret, marcap = panels["ret"], panels["marcap"]
    dates = ret.index
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    rebal = sorted(rebal_dates)
    for i, t in enumerate(rebal):
        start_pos = dates.searchsorted(t, side="right")
        end = rebal[i + 1] if i + 1 < len(rebal) else dates[-1]
        end_pos = dates.searchsorted(end, side="right")
        if start_pos >= end_pos:
            continue
        mc = marcap.loc[:t].iloc[-1].dropna()
        mc = mc[[str(c).endswith("0") for c in mc.index]]   # 보통주만 (전략과 대칭)
        uni = mc.nlargest(universe_n)
        w = (uni / uni.sum()).astype(float)
        seg = ret.iloc[start_pos:end_pos][w.index].fillna(0.0)
        vals = (1.0 + seg).cumprod()
        port = vals.mul(w, axis=1).sum(axis=1)
        r = port / port.shift(1).fillna(w.sum()) - 1.0
        r.iloc[0] = float(vals.iloc[0].mul(w).sum() / w.sum() - 1.0)
        out.iloc[start_pos:end_pos] = r.values
        active.iloc[start_pos:end_pos] = True
    return out[active]


def perf(returns: pd.Series) -> dict:
    """총수익·연율·MDD·Sharpe (일별 수익률 입력)."""
    from ml.adaptive import reward
    r = returns.dropna()
    if len(r) < 2:
        return {"total": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "n": len(r)}
    nav = (1.0 + r).cumprod()
    total = float(nav.iloc[-1] - 1.0)
    yrs = len(r) / 252.0
    cagr = float(nav.iloc[-1] ** (1.0 / yrs) - 1.0) if yrs > 0 and nav.iloc[-1] > 0 else -1.0
    sd = float(r.std())
    return {"total": round(total, 4), "cagr": round(cagr, 4),
            "mdd": round(reward.max_drawdown(list(nav.values)), 4),
            "sharpe": round(float(r.mean()) / sd * (252 ** 0.5), 2) if sd > 0 else 0.0,
            "n": len(r)}


# ══════════════════════════════════════════════════════════════════════
#  워크포워드 (train 에서 config 선택 → OOS 적용)
# ══════════════════════════════════════════════════════════════════════

def _objective(strat: pd.Series, bench: pd.Series) -> float | None:
    """★목적함수 — 순초과수익 − MDD 초과 패널티. 하드 위반 None (reward.objective_score)."""
    from ml.adaptive import reward
    s, b = perf(strat), perf(bench.reindex(strat.index).fillna(0.0))
    return reward.objective_score(s["total"] - b["total"], s["mdd"], b["mdd"])


def walk_forward(panels: dict, configs: dict, *, train_years: int = TRAIN_YEARS,
                 top_k: int = TOP_K) -> dict:
    """연 단위 워크포워드: train 창에서 ★목적함수 최대 config 선택 → OOS 1년 적용.

    반환 {oos_returns(연결), bench_returns, folds[{year, chosen, train_obj}], config_daily}.
    config_daily = 전기간 config별 일별수익 DataFrame (DSR/PBO 용).
    """
    ret = panels["ret"]
    mes = month_ends(ret.index)
    feats = {}
    for t in mes:
        f = features_asof(panels, t)
        if f is not None:
            feats[t] = f
    if not feats:
        return {"error": "피처 산출 불가(데이터 부족)"}
    rebal = sorted(feats.keys())
    logger.info("리밸런스 시점 %d개 (%s ~ %s)", len(rebal), rebal[0].date(), rebal[-1].date())

    # config 별 전기간 일별수익 (재사용: train 평가·OOS 연결·PBO 행렬)
    ret_by_cfg = {}
    for name, cfg in configs.items():
        picks = {t: select_top(feats[t], cfg, top_k) for t in rebal}
        ret_by_cfg[name] = simulate(ret, picks)
        logger.info("config %-14s 시뮬 완료 (%s)", name, perf(ret_by_cfg[name]))
    bench = cap_benchmark(panels, rebal)

    years = sorted({t.year for t in rebal})
    oos_parts, folds = [], []
    for y in years:
        tr0, tr1 = pd.Timestamp(f"{y - train_years}-01-01"), pd.Timestamp(f"{y - 1}-12-31")
        oos0, oos1 = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")
        best_name, best_obj = None, -np.inf
        for name, r in ret_by_cfg.items():
            tr = r.loc[tr0:tr1]
            if len(tr) < 252 * min(3, train_years):        # train 최소 3년치
                continue
            obj = _objective(tr, bench)
            if obj is not None and obj > best_obj:
                best_name, best_obj = name, obj
        if best_name is None:
            continue
        oos_r = ret_by_cfg[best_name].loc[oos0:oos1]
        if len(oos_r) < 60:
            continue
        oos_parts.append(oos_r)
        folds.append({"year": y, "chosen": best_name, "train_obj": round(float(best_obj), 4),
                      "oos": perf(oos_r),
                      "oos_bench": perf(bench.loc[oos0:oos1])})
    if not oos_parts:
        return {"error": "OOS 폴드 없음(기간 부족)"}
    oos = pd.concat(oos_parts).sort_index()
    return {"oos_returns": oos, "bench_returns": bench, "folds": folds,
            "config_daily": pd.DataFrame(ret_by_cfg).dropna(how="all"),
            "full_period": {n: perf(r) for n, r in ret_by_cfg.items()},
            "bench_perf_full": perf(bench),
            "feats": feats, "rebal": rebal}   # 후속 평가(레짐·비용·라이브조합) 재사용 — 재계산 방지


# ══════════════════════════════════════════════════════════════════════
#  판정 (Tier2 formalism + ★목적함수)
# ══════════════════════════════════════════════════════════════════════

def build_verdict(wf: dict, n_trials: int) -> dict:
    """OOS 연결 성과 + DSR/PBO → GO/OBSERVE/NO-GO."""
    from ml import validation
    oos, bench = wf["oos_returns"], wf["bench_returns"]
    b = bench.reindex(oos.index).fillna(0.0)
    excess = oos - b
    p_s, p_b = perf(oos), perf(b)
    net_excess_total = round(p_s["total"] - p_b["total"], 4)
    net_excess_cagr = round(p_s["cagr"] - p_b["cagr"], 4)

    # DSR: n_trials 다중검정 deflate (trial Sharpe 분산 = 전기간 config 일별초과 Sharpe 분산)
    cfg_daily = wf["config_daily"]
    b_full = bench.reindex(cfg_daily.index).fillna(0.0)
    trial_sharpes = []
    for c in cfg_daily.columns:
        e = (cfg_daily[c] - b_full).dropna()
        trial_sharpes.append(validation.sharpe_ratio(e)["pp"])
    dsr = validation.deflated_sharpe_ratio(excess.values, n_trials, trial_sharpes=trial_sharpes)
    pbo_res = validation.pbo_cscv(cfg_daily.sub(b_full, axis=0).dropna().values, n_splits=10)
    pbo = pbo_res["pbo"] if pbo_res else None
    val = validation.validate_strategy(oos.values, benchmark_returns=b.values, n_trials=n_trials)

    mdd_ok = p_s["mdd"] <= p_b["mdd"]
    mdd_hard_bad = p_s["mdd"] > p_b["mdd"] * HARD_MDD_MULT
    stats_ok = (dsr is not None and dsr >= 0.95) and (pbo is not None and pbo < 0.5)
    if net_excess_total > 0 and mdd_ok and stats_ok:
        code, label = "GO", "✅ GO — OOS 순초과·MDD·통계 관문 모두 통과 (희귀 — 재검 후 shadow)"
    elif net_excess_total > 0 and not mdd_hard_bad:
        code, label = "OBSERVE", "👀 OBSERVE — OOS 순초과>0 이나 통계/MDD 관문 미달 (엣지 주장 불가)"
    else:
        code, label = "NO-GO", "➖ NO-GO — 순비용 OOS 아웃퍼폼 실패 (정직: 선택 스킬 미확인)"
    return {"code": code, "label": label,
            "oos": p_s, "bench": p_b,
            "net_excess_total": net_excess_total, "net_excess_cagr": net_excess_cagr,
            "ir": round(float(validation.sharpe_ratio(excess)["ann"]), 3),
            "dsr": (None if dsr is None else round(dsr, 4)),
            "pbo": (None if pbo is None else round(pbo, 4)),
            "psr_excess": (val or {}).get("psr_excess"),
            "n_trials": n_trials, "mdd_ok": mdd_ok}


def chosen_history(folds: list[dict]) -> dict:
    """폴드별 채택 config 빈도 — '개선 방향' 신호(가장 자주 뽑힌 축)."""
    from collections import Counter
    c = Counter(f["chosen"] for f in folds)
    return dict(c.most_common())


# ══════════════════════════════════════════════════════════════════════
#  현재 시점 권고 (라이브 KR 정책 축 매핑 — crons/kr_axes_eval 소비)
# ══════════════════════════════════════════════════════════════════════

# 백테스트 축 → kr_policy 가중 키 (liq·size 는 라이브 정책에 대응 축 없음 → 매핑 불가)
AXIS_TO_POLICY = {"mom12": "w_mom12", "hi52": "w_hi52", "vol_inv": "w_lowvol", "rev1": "w_mom"}
AXES_BUDGET = 0.35   # kr_policy 기본 가격축 합(hi52 .15 + lowvol .10 + mom12 .05 + mom .05) — 권고 총량 상한


def mappable_configs(configs: dict | None = None) -> dict:
    """kr_policy 가중으로 옮길 수 있는 config 만 (전 축 매핑 가능 + 양의 가중 — 음수는 bounds 밖)."""
    out = {}
    for name, cfg in (configs or CONFIGS).items():
        if cfg and all(a in AXIS_TO_POLICY and w > 0 for a, w in cfg.items()):
            out[name] = cfg
    return out


def current_recommendation(config_daily, bench, *, train_years: int = TRAIN_YEARS,
                           configs: dict | None = None) -> dict | None:
    """트레일링 train_years 창 ★목적함수 최적의 **매핑 가능** config → kr_policy 가격축 가중 권고.

    워크포워드 폴드 선택과 동일 로직을 '지금' 시점에 적용한 것 — 라이브 반영은
    ADAPTIVE_KR_AXES_ENABLED 게이트 + 모의(paper) 한정. 데이터 부족 시 None.
    """
    cand = mappable_configs(configs)
    if not cand or config_daily is None or len(config_daily) == 0:
        return None
    end = config_daily.index.max()
    start = end - pd.DateOffset(years=train_years)
    best = None
    for name in cand:
        if name not in config_daily.columns:
            continue
        r = config_daily[name].loc[start:end].dropna()
        if len(r) < 252 * 3:                              # 워크포워드와 동일 최소 3년
            continue
        obj = _objective(r, bench)
        if obj is not None and (best is None or obj > best[1]):
            best = (name, obj)
    if best is None:
        return None
    name, obj = best
    w = cand[name]
    tot = sum(w.values())
    pw = {AXIS_TO_POLICY[a]: round(AXES_BUDGET * v / tot, 4) for a, v in w.items()}
    for k in AXIS_TO_POLICY.values():                     # 미포함 축은 0 명시(교체 의미 명확)
        pw.setdefault(k, 0.0)
    tr = config_daily[name].loc[start:end].dropna()
    return {"chosen": name, "train_obj": round(float(obj), 4),
            "policy_weights": pw,
            "train_perf": perf(tr),
            "bench_perf": perf(bench.reindex(tr.index).fillna(0.0)),
            "window": [str(start.date()), str(end.date())]}


# ══════════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  레짐 방어 오버레이 (시장상태 조건부 축 전환 — 방어 verdict·표시 전용)
# ══════════════════════════════════════════════════════════════════════

REGIME_MA = 200                 # 지수 추세 필터 (일)
REGIME_OFFENSE = {"hi52": 1.0}  # 강세(지수>MA): 고가근접 모멘텀
REGIME_DEFENSE = {"vol_inv": 1.0}  # 약세(지수<MA): 저변동 방어
DEFENSE_MDD_MARGIN = 0.03       # 오버레이가 순공격 대비 MDD 를 이만큼 낮춰야 '방어 유효'


def _picks_hysteresis(feats: dict, rebal: list, cfg: dict, *, k: int = TOP_K,
                      buffer: int = 0) -> dict:
    """축 cfg 로 top-k 선택하되 직전 보유가 top-(k+buffer) 안이면 유지 (회전율 억제)."""
    picks, prev = {}, []
    for t in rebal:
        f = feats[t]
        score = pd.Series(0.0, index=f.index)
        for a, w in cfg.items():
            if a in f.columns:
                score = score + float(w) * f[a].fillna(0.0)
        wide = list(score.nlargest(k + max(0, buffer)).index)
        keep = [c for c in prev if c in wide]
        fresh = [c for c in score.nlargest(k).index if c not in keep]
        cur = (keep + fresh)[:k]
        picks[t] = cur
        prev = cur
    return picks


def _avg_turnover(picks: dict, k: int = TOP_K) -> float:
    """리밸런스당 평균 교체 종목수 / k (편도 회전율)."""
    dates = sorted(picks)
    prev, tot, n = set(), 0.0, 0
    for t in dates:
        cur = set(picks[t])
        if prev:
            tot += len(cur - prev) / max(k, 1)
            n += 1
        prev = cur
    return round(tot / n, 3) if n else 0.0


def regime_overlay_eval(panels: dict, feats: dict, rebal: list, bench, *,
                        offense: dict | None = None, defense: dict | None = None,
                        ma: int = REGIME_MA, config_daily=None) -> dict:
    """시장상태 조건부 축 전환(강세=고가모멘텀·약세=저변동)의 **방어 오버레이 verdict**.

    정직 규율: 이건 종목선택 알파가 아니라 **낙폭 방어 오버레이**다. 판정 기준을 수익이 아니라
    MDD 에 둔다 — 순공격(offense 단독) 대비 MDD 를 유의미하게 낮추면서 지수 초과·MDD≤지수를
    유지하면 방어 유효. 단 초과수익 DSR 은 그대로 보고(위기집중·whipsaw 위험 정직 공개).
    무룩어헤드: 레짐 신호는 t 시점까지 지수 종가만 사용, 축 적용은 t 익일부터(simulate 규약).
    """
    from ml import validation
    offense = offense or REGIME_OFFENSE
    defense = defense or REGIME_DEFENSE
    ret = panels["ret"]
    bench_nav = (1.0 + bench).cumprod()
    ma_s = bench_nav.rolling(ma).mean()
    picks = {}
    for t in rebal:
        v = ma_s.loc[:t].iloc[-1] if t in ma_s.index else float("nan")
        up = bool(bench_nav.loc[:t].iloc[-1] > v) if (v == v) else True
        picks[t] = select_top(feats[t], offense if up else defense, TOP_K)
    r = simulate(ret, picks)
    # 순공격 = hi52 단독 — walk_forward 의 config_daily 있으면 재사용(중복 시뮬 방지·C)
    if config_daily is not None and offense == REGIME_OFFENSE and "hi52" in getattr(config_daily, "columns", []):
        off = config_daily["hi52"].dropna()
    else:
        off = simulate(ret, {t: select_top(feats[t], offense, TOP_K) for t in rebal})
    b = bench.reindex(r.index).fillna(0.0)
    pr, pb, po = perf(r), perf(b), perf(off.reindex(r.index).fillna(0.0))

    # 연도별 방어 성공 — 지수 대비 MDD 개선 비율 (특히 약세해)
    years = sorted({d.year for d in r.index})
    mdd_wins, bear_defends, bear_years = 0, 0, 0
    for y in years:
        m = r.index.year == y
        if m.sum() < 60:
            continue
        ry, by = perf(r[m]), perf(b.reindex(r[m].index).fillna(0.0))
        if ry["mdd"] <= by["mdd"]:
            mdd_wins += 1
        if by["total"] < 0:                       # 지수 약세해
            bear_years += 1
            if ry["total"] > by["total"]:
                bear_defends += 1
    n_years = len([y for y in years if (r.index.year == y).sum() >= 60])

    # DSR: 축 그리드 + 오버레이를 trial 로 (다중검정 정직) — config_daily 재사용(중복 시뮬 방지·C)
    b_full = bench.reindex(ret.index).fillna(0.0)
    trials = []
    if config_daily is not None and len(getattr(config_daily, "columns", [])) >= 2:
        for c in config_daily.columns:
            e = (config_daily[c] - b_full.reindex(config_daily.index).fillna(0.0)).dropna()
            trials.append(validation.sharpe_ratio(e)["pp"])
    else:
        for name, cfg in CONFIGS.items():
            tr = simulate(ret, {t: select_top(feats[t], cfg, TOP_K) for t in rebal})
            trials.append(validation.sharpe_ratio((tr - b_full.reindex(tr.index).fillna(0.0)).dropna())["pp"])
    excess = (r - b).dropna()
    trials.append(validation.sharpe_ratio(excess)["pp"])
    dsr = validation.deflated_sharpe_ratio(excess.values, len(trials), trial_sharpes=trials)

    return_ok = (pr["total"] - pb["total"]) > 0
    mdd_ok = pr["mdd"] <= pb["mdd"]
    defends = pr["mdd"] < po["mdd"] - DEFENSE_MDD_MARGIN
    stat_ok = dsr is not None and dsr >= 0.95
    if return_ok and mdd_ok and defends and stat_ok:
        code, label = "GO", "✅ GO(방어) — MDD 방어 + 초과수익 통계 유의 (희귀)"
    elif return_ok and mdd_ok and defends:
        code, label = "OBSERVE", ("👀 OBSERVE(방어) — 순공격 대비 MDD 개선·지수 아웃퍼폼 확인, "
                                  "단 초과수익 통계 미달·이득 위기집중·반등 whipsaw 위험")
    else:
        code, label = "NO-GO", "➖ NO-GO(방어) — MDD 방어 미확인 또는 지수 언더퍼폼"
    return {"code": code, "label": label,
            "regime": {"offense": list(offense), "defense": list(defense), "ma": ma},
            "overlay": pr, "offense_alone": po, "bench": pb,
            "mdd_vs_offense_pp": round((po["mdd"] - pr["mdd"]) * 100, 1),
            "net_excess_cagr": round(pr["cagr"] - pb["cagr"], 4),
            "ir": round(float(validation.sharpe_ratio(excess)["ann"]), 3),
            "dsr": (None if dsr is None else round(dsr, 4)),
            "mdd_win_years": f"{mdd_wins}/{n_years}",
            "bear_defend_years": f"{bear_defends}/{bear_years}"}


# ══════════════════════════════════════════════════════════════════════
#  비용·회전율 최적화 (무거래밴드·리밸 주기 스윕 → 순비용 최적 스킴)
# ══════════════════════════════════════════════════════════════════════

def cost_sensitivity(panels: dict, feats: dict, rebal: list, bench, *,
                     axis_cfg: dict | None = None, top_k: int = TOP_K) -> dict:
    """리밸 주기 × 히스테리시스 버퍼 스윕 → 회전율·비용드래그·순CAGR. 순수·확실도 높음.

    비용은 통계 불확실성 0 이라 여기 결론은 OBSERVE 가 아니라 **실행 가능 권고**다.
    gross = 무비용 시뮬, net = KR 비대칭 비용(매수2/매도20bps) → drag = gross−net CAGR.
    현 라이브 기본(월간·버퍼2 ≈ REBAL_BAND 0.25) 대비 순CAGR 최대 스킴을 권고.
    """
    axis_cfg = axis_cfg or REGIME_OFFENSE      # 게이트 최다 채택 축(hi52)로 스윕
    ret = panels["ret"]
    b = bench.reindex(ret.index).fillna(0.0)
    schemes = [("월간·버퍼0", 1, 0), ("월간·버퍼2", 1, 2), ("분기·버퍼0", 3, 0),
               ("분기·버퍼2", 3, 2), ("반기·버퍼2", 6, 2)]
    rows = []
    for name, freq, buf in schemes:
        sub = rebal[::freq]
        picks = _picks_hysteresis(feats, sub, axis_cfg, k=top_k, buffer=buf)
        net = simulate(ret, picks, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
        gross = simulate(ret, picks, buy_bps=0.0, sell_bps=0.0)
        pn, pg = perf(net), perf(gross)
        pbn = perf(b.reindex(net.index).fillna(0.0))
        rows.append({"scheme": name, "freq_m": freq, "buffer": buf,
                     "net_cagr": pn["cagr"], "gross_cagr": pg["cagr"],
                     "drag_pp": round((pg["cagr"] - pn["cagr"]) * 100, 2),
                     "mdd": pn["mdd"], "net_excess_pp": round((pn["cagr"] - pbn["cagr"]) * 100, 2),
                     "n_rebal": len(sub), "turnover": _avg_turnover(picks, top_k)})
    best = max(rows, key=lambda r: r["net_cagr"])
    cur = next((r for r in rows if r["scheme"] == "월간·버퍼2"), rows[0])
    oos = _cost_oos_robustness(panels, feats, rebal, bench, axis_cfg, top_k)
    return {"axis": list(axis_cfg), "rows": rows, "best": best, "current": cur,
            "gain_pp": round((best["net_cagr"] - cur["net_cagr"]) * 100, 2),
            "drag_saved_pp": round(cur["drag_pp"] - best["drag_pp"], 2),
            "oos": oos}


def _cost_oos_robustness(panels: dict, feats: dict, rebal: list, bench,
                         axis_cfg: dict, top_k: int) -> dict:
    """'덜 거래' 이점의 견고성 — 연도 승률·gross 보존·다른 축 확인 → adopt-worthy verdict.

    정직 규율: 전기간 best 는 과적합 위험. (a) 반기가 월간을 이긴 OOS 연도 비율,
    (b) gross(무비용)가 보존되나?(=이득이 비용절감이지 gross 우연이 아님), (c) 다른 축
    (lowvol)서도 재현? — 셋 다 충족해야 ROBUST. 고정주기 위상위험(분기 함정·특정해 꼬리)
    때문에 라이브 반영은 **최소 보유기간(연속)** 으로 권고(고정 cadence 아님).
    """
    ret = panels["ret"]

    def sret(freq, buf, cfg):
        sub = rebal[::freq]
        return simulate(ret, _picks_hysteresis(feats, sub, cfg, k=top_k, buffer=buf),
                        buy_bps=BUY_BPS, sell_bps=SELL_BPS)

    mo, semi = sret(1, 2, axis_cfg), sret(6, 2, axis_cfg)
    years = sorted(set(mo.index.year))
    wins = n = 0
    for y in years:
        m = mo.index.year == y
        if m.sum() < 60:
            continue
        n += 1
        if perf(semi[m])["total"] > perf(mo[m])["total"]:
            wins += 1
    # gross 보존: 반기 gross ≈ 월간 gross (이득이 비용에서 옴)
    g_mo = perf(simulate(ret, _picks_hysteresis(feats, rebal[::1], axis_cfg, k=top_k, buffer=2),
                         buy_bps=0.0, sell_bps=0.0))["cagr"]
    g_semi = perf(simulate(ret, _picks_hysteresis(feats, rebal[::6], axis_cfg, k=top_k, buffer=2),
                           buy_bps=0.0, sell_bps=0.0))["cagr"]
    gross_preserved = g_semi >= g_mo - 0.01          # gross 손실 <1%p
    # 다른 축 재현 (lowvol)
    alt = {"vol_inv": 1.0}
    alt_win = perf(sret(6, 2, alt))["cagr"] > perf(sret(1, 2, alt))["cagr"]
    win_rate = round(wins / n, 2) if n else 0.0
    robust = win_rate >= 0.6 and gross_preserved and alt_win
    verdict = ("ROBUST" if robust else ("MIXED" if win_rate >= 0.5 else "IN-SAMPLE"))
    return {"year_win_rate": win_rate, "n_years": n, "gross_preserved": bool(gross_preserved),
            "gross_mo": round(g_mo, 4), "gross_semi": round(g_semi, 4),
            "cross_axis_confirmed": bool(alt_win), "verdict": verdict,
            # 라이브 반영 권고 — 최소 보유기간(연속·고정주기 위상위험 회피)
            "live_reco": {"min_hold_days": 60 if robust else 0,
                          "expected_drag_save_pp": 2.0 if robust else 0.0,
                          "caveat": "특정해 꼬리위험(2023 등)·모의로 라이브 검증 후 실계좌 고려"}}


# ══════════════════════════════════════════════════════════════════════
#  라이브 조합 검증 (A) — 실제 모의 구성(고빈도 결정+히스테리시스+min_hold+트란치) 그대로
# ══════════════════════════════════════════════════════════════════════

LIVE_MIN_HOLD_D = 60      # KR_MOCK_MIN_HOLD_DAYS 기본 정합 (거래일 아닌 달력일 근사 → 거래일 ~41)
LIVE_TRANCHES = 3         # KR_MOCK_TRANCHES 기본 정합
LIVE_EXIT_BUFFER = 2      # KR_MOCK_EXIT_BUFFER 정합
LIVE_STUB_FRAC = 0.5      # 부분체결 스텁(목표의 절반 미만) — min_hold 보호 예외 (B)


def simulate_live(panels: dict, feats: dict, decision_dates: list, *, top_k: int = TOP_K,
                  exit_buffer: int = LIVE_EXIT_BUFFER, min_hold_days: int = LIVE_MIN_HOLD_D,
                  tranches: int = LIVE_TRANCHES, stub_frac: float = LIVE_STUB_FRAC,
                  buy_bps: float = BUY_BPS, sell_bps: float = SELL_BPS,
                  axis_cfg: dict | None = None) -> pd.Series:
    """상태보존 NAV 시뮬 — 라이브 모의의 실제 브레이크 조합을 그대로 모델링. 순수.

    월간 배치 simulate() 와 달리 결정일마다: 히스테리시스 keep → min_hold(스텁 예외) 청산 게이트
    → 트란치 상한 매수/매도. 가격 = 합성 수정주가(adj). 체결 = 결정일 종가(피처는 ≤t 데이터만
    → 무룩어헤드). min_hold 는 **거래일 환산**(달력일×5/7) — 라이브는 달력일 기준이라 보수 근사.
    반환: 일별 수익률 Series (전 기간·현금 수익 0).
    """
    axis_cfg = axis_cfg or REGIME_OFFENSE                    # hi52 (게이트 최다 채택 축)
    ret, adj = panels["ret"], panels["adj"]
    dates = ret.index
    dset = set(decision_dates)
    min_hold_td = int(min_hold_days * 5 / 7)                 # 달력일 → 거래일 근사

    cash = 1.0
    shares: dict[str, float] = {}                            # code → 주수 (합성가 기준)
    entry_di: dict[str, int] = {}                            # code → 편입 거래일 인덱스
    navs = []
    prev_px = None
    for di, d in enumerate(dates):
        px = adj.loc[d]
        # 보유 평가 (결측일 = 직전가 유지: adj 는 ffill 성질[ret NaN→0 누적곱]이라 자연 유지)
        pos_val = sum(sh * float(px.get(c) or (prev_px.get(c) if prev_px is not None else 0) or 0)
                      for c, sh in shares.items())
        nav = cash + pos_val
        navs.append(nav)
        prev_px = px

        if d not in dset or d not in feats:
            continue
        f = feats[d]
        score = pd.Series(0.0, index=f.index)
        for a, w in axis_cfg.items():
            if a in f.columns:
                score = score + float(w) * f[a].fillna(0.0)
        ranked = list(score.nlargest(top_k + max(0, exit_buffer)).index)
        target = set(score.nlargest(top_k).index)
        keep = set(ranked)
        per = nav * 0.9 / max(top_k, 1)                      # INVEST 0.9 정합

        # 1) 청산 게이트: keep 밖 + (min_hold 충족 또는 스텁) → 트란치 상한 매도
        for c in list(shares.keys()):
            if c in keep:
                continue
            p = float(px.get(c) or 0)
            if p <= 0:
                continue
            val = shares[c] * p
            held = di - entry_di.get(c, -10 ** 9)
            protected = (min_hold_days > 0 and held < min_hold_td
                         and val >= stub_frac * per)         # 스텁(반쪽 미만)은 보호 예외 (B)
            if protected:
                continue
            # 트란치 상한 — 값 기반 연속(합성가 소수주 체계: 정수 ceil 은 캡 무력화 함정)
            cap = ((per / tranches) / p) if tranches > 1 else shares[c]
            q = min(shares[c], cap)
            cash += q * p * (1.0 - sell_bps / 1e4)
            shares[c] -= q
            if shares[c] <= 1e-9:
                shares.pop(c), entry_di.pop(c, None)

        # 2) 매수: 목표 top-k 중 미달분 → 트란치 상한
        for c in target:
            p = float(px.get(c) or 0)
            if p <= 0:
                continue
            cur_val = shares.get(c, 0.0) * p
            gap = per - cur_val
            if gap <= per * 0.25:                            # 무거래 밴드(REBAL_BAND 정합)
                continue
            cap_sh = ((per / tranches) / p) if tranches > 1 else (gap / p)
            q = min(gap / p, cap_sh, cash / (p * (1.0 + buy_bps / 1e4)) if p > 0 else 0)
            if q <= 0:
                continue
            cash -= q * p * (1.0 + buy_bps / 1e4)
            if c not in shares:
                entry_di[c] = di
            shares[c] = shares.get(c, 0.0) + q

    nav_s = pd.Series(navs, index=dates)
    return nav_s.pct_change().fillna(0.0)


def live_combo_eval(panels: dict, bench, *, cadence: int = 5) -> dict:
    """A: 라이브 브레이크 조합(주간 결정+히스테리시스+min_hold60+3분할+스텁예외)의 순효과 검증.

    비교 3종: ①라이브 조합 ②동일 주간 결정·브레이크 없음(일괄) ③월간 배치(기존 백테스트 가정).
    → "min_hold+트란치 조합이 실제 라이브 구성에서도 비용을 회수하나" 를 직접 판정.
    cadence=5 거래일(주간) — 일간 크론의 무거래일 다수를 근사(브레이크가 거래를 걸러 등가).
    """
    ret = panels["ret"]
    week_dates = list(ret.index[::cadence])
    feats_w = {}
    for t in week_dates:
        f = features_asof(panels, t)
        if f is not None:
            feats_w[t] = f
    if len(feats_w) < 60:
        return {"error": "피처 부족(기간 짧음)"}
    decision_dates = sorted(feats_w.keys())

    combo = simulate_live(panels, feats_w, decision_dates)
    naked = simulate_live(panels, feats_w, decision_dates,
                          min_hold_days=0, tranches=1, exit_buffer=0)
    # 월간 배치(기존 가정) — 월말만 결정
    monthly_dates = [t for t in month_ends(ret.index) if t in feats_w]
    monthly = simulate_live(panels, feats_w, monthly_dates,
                            min_hold_days=0, tranches=1, exit_buffer=LIVE_EXIT_BUFFER)

    b = bench.reindex(combo.index).fillna(0.0)
    pc, pn, pm, pb = perf(combo), perf(naked), perf(monthly), perf(b)
    brakes_help = pc["cagr"] > pn["cagr"]
    verdict = ("CONFIRMED" if brakes_help and pc["cagr"] >= pm["cagr"] - 0.01 else
               ("PARTIAL" if brakes_help else "NOT-CONFIRMED"))
    return {"verdict": verdict,
            "combo": pc, "no_brakes": pn, "monthly_batch": pm, "bench": pb,
            "brake_gain_pp": round((pc["cagr"] - pn["cagr"]) * 100, 2),
            "vs_monthly_pp": round((pc["cagr"] - pm["cagr"]) * 100, 2),
            "note": ("주간 결정 근사(일간 크론의 무거래일 다수) · min_hold 달력60일≈거래41일 · "
                     "CONFIRMED=브레이크가 무브레이크 대비 순이득 & 월간 가정 대비 동등 이상")}


def run(start_year: int = 2001, end_year: int | None = None) -> dict:
    end_year = end_year or datetime.now().year
    logger.info("marcap 패널 조립 %d~%d …", start_year, end_year)
    panels = build_panels(start_year, end_year)
    if panels is None:
        return {"error": "marcap 패널 조립 실패(네트워크/캐시 확인)"}
    logger.info("패널: %d일 × %d종목", len(panels["ret"]), panels["ret"].shape[1])
    wf = walk_forward(panels, CONFIGS)
    if wf.get("error"):
        return wf
    verdict = build_verdict(wf, n_trials=len(CONFIGS))
    # walk_forward 산출물 재사용 (피처·리밸·config_daily — 중복 재계산 방지·크론 런타임 ↓)
    _feats, _rebal = wf["feats"], wf["rebal"]
    try:
        regime = regime_overlay_eval(panels, _feats, _rebal, wf["bench_returns"],
                                     config_daily=wf["config_daily"])
    except Exception as e:
        logger.warning("레짐 오버레이 평가 실패: %s", e)
        regime = {"error": str(e)}
    try:
        costs = cost_sensitivity(panels, _feats, _rebal, wf["bench_returns"])
    except Exception as e:
        logger.warning("비용 스윕 실패: %s", e)
        costs = {"error": str(e)}
    try:
        live = live_combo_eval(panels, wf["bench_returns"])
    except Exception as e:
        logger.warning("라이브 조합 검증 실패: %s", e)
        live = {"error": str(e)}
    result = {
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": f"{start_year}~{end_year}", "universe": f"KOSPI 시총 top{UNIVERSE_N}",
        "top_k": TOP_K, "costs_bps": {"buy": BUY_BPS, "sell": SELL_BPS},
        "verdict": verdict,
        "recommendation": current_recommendation(wf["config_daily"], wf["bench_returns"]),
        "regime_overlay": regime,
        "cost_sensitivity": costs,
        "live_combo": live,
        "folds": wf["folds"],
        "chosen_history": chosen_history(wf["folds"]),
        "full_period_by_config": wf["full_period"],
        "bench_full": wf["bench_perf_full"],
    }
    try:
        os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=str)
        logger.info("결과 저장: %s", RESULT_PATH)
    except Exception as e:
        logger.warning("결과 저장 실패: %s", e)
    return result


def _print_report(res: dict) -> None:
    if res.get("error"):
        print("오류:", res["error"])
        return
    v = res["verdict"]
    print("\n══════ KR 선택정책 ★아웃퍼폼 검증 (무생존편향·순비용·워크포워드) ══════")
    print(f"기간 {res['period']} · 유니버스 {res['universe']} · top{res['top_k']} 등가중 월리밸"
          f" · 비용 매수{res['costs_bps']['buy']}/매도{res['costs_bps']['sell']}bps")
    print(f"\n{v['label']}")
    print(f"  OOS 연결: 전략 CAGR {v['oos']['cagr']*100:+.1f}% vs 지수 {v['bench']['cagr']*100:+.1f}%"
          f" → 순초과 {v['net_excess_cagr']*100:+.2f}%p/년")
    print(f"  MDD: 전략 {v['oos']['mdd']*100:.1f}% vs 지수 {v['bench']['mdd']*100:.1f}%"
          f" {'✅' if v['mdd_ok'] else '⚠️'}  · IR {v['ir']}")
    print(f"  DSR {v['dsr']} (n_trials={v['n_trials']}) · PBO {v['pbo']} · PSR(초과) {v['psr_excess']}")
    print("\n  폴드별 채택 config:", res["chosen_history"])

    ro = res.get("regime_overlay") or {}
    if ro and not ro.get("error"):
        print(f"\n  🛡️ 레짐 방어 오버레이: {ro['label']}")
        print(f"     오버레이 CAGR {ro['overlay']['cagr']*100:+.1f}% (지수 {ro['bench']['cagr']*100:+.1f}%"
              f"·순공격 {ro['offense_alone']['cagr']*100:+.1f}%) · MDD {ro['overlay']['mdd']*100:.0f}%"
              f" (순공격比 {ro['mdd_vs_offense_pp']:+.0f}%p·지수 {ro['bench']['mdd']*100:.0f}%)")
        print(f"     DSR {ro['dsr']} · 약세해방어 {ro['bear_defend_years']} · MDD승 {ro['mdd_win_years']}")

    cs = res.get("cost_sensitivity") or {}
    if cs and not cs.get("error"):
        print(f"\n  💸 비용·회전율 (축 {cs['axis']}) — 확실한 실행 권고(통계불확실 0):")
        for x in cs["rows"]:
            mk = " ★best" if x["scheme"] == cs["best"]["scheme"] else (" (현재)" if x["scheme"] == cs["current"]["scheme"] else "")
            print(f"     {x['scheme']:<12} 순CAGR {x['net_cagr']*100:+5.1f}%  드래그 {x['drag_pp']:4.2f}%p"
                  f"  회전 {x['turnover']:.2f}  MDD {x['mdd']*100:.0f}%{mk}")
        print(f"     → 현재(월간) 드래그 {cs['current']['drag_pp']:.2f}%p 중 ~{cs['drag_saved_pp']:.1f}%p 는 주기↓로 확실 회수"
              f" · gross 상호작용 비단조라 '{cs['best']['scheme']}' 채택은 OOS 재검 필요")

    lv = res.get("live_combo") or {}
    if lv and not lv.get("error"):
        print(f"\n  🔩 라이브 조합 검증(A): {lv['verdict']} — 브레이크 이득 {lv['brake_gain_pp']:+.1f}%p/년"
              f" · vs 월간가정 {lv['vs_monthly_pp']:+.1f}%p")
        for k, lab in (("combo", "라이브(주간+브레이크)"), ("no_brakes", "무브레이크"),
                       ("monthly_batch", "월간 배치가정")):
            p = lv[k]
            print(f"     {lab:<16} CAGR {p['cagr']*100:+6.1f}%  MDD {p['mdd']*100:5.1f}%")

    print("\n  전기간 config별 (참고 — 판정은 OOS 연결만):")
    bench = res["bench_full"]
    print(f"    {'벤치(시총가중)':<16} CAGR {bench['cagr']*100:+6.1f}%  MDD {bench['mdd']*100:5.1f}%  Sharpe {bench['sharpe']}")
    for name, p in sorted(res["full_period_by_config"].items(), key=lambda kv: -kv[1]["cagr"]):
        print(f"    {name:<16} CAGR {p['cagr']*100:+6.1f}%  MDD {p['mdd']*100:5.1f}%  Sharpe {p['sharpe']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="2015~ 축약 실행")
    ap.add_argument("--start", type=int, default=None)
    a = ap.parse_args()
    start = a.start or (2015 if a.quick else 2001)
    res = run(start_year=start)
    _print_report(res)
    return 0 if not res.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
