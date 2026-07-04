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
            "bench_perf_full": perf(bench)}


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
#  진입점
# ══════════════════════════════════════════════════════════════════════

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
    result = {
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": f"{start_year}~{end_year}", "universe": f"KOSPI 시총 top{UNIVERSE_N}",
        "top_k": TOP_K, "costs_bps": {"buy": BUY_BPS, "sell": SELL_BPS},
        "verdict": verdict,
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
