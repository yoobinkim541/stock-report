#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/us_policy_backtest.py — US 선택정책(모의 top-5) ★아웃퍼폼 검증 게이트. kr_policy_backtest 미국판.

질문: "US 선택 로직(가격 축)이 QQQ 를 순비용으로 이길 수 있나?" — KR 하네스와 동일 규율.

설계(정직 — KR 과의 차이를 명시):
- **생존편향 부분 통제**: 유니버스는 fja05680 S&P500 **시점별 멤버십**(1996~·상폐종목 포함
  목록) 마스킹 — '오늘의 생존자로 과거를 뽑는' 1차 편향은 차단. 단 **상폐 종목의 가격**은
  yfinance 에 없어 시뮬 대상에서 빠진다(잔존 상방편향) → **커버리지 계기**로 정량화하고
  커버리지 < COVERAGE_MIN 이면 GO 를 OBSERVE 로 강등(과대 주장 차단). KR(marcap 완전 무편향)
  대비 약한 검증임을 verdict 에 명시.
- 가격 = yfinance auto_adjust(분할·배당 조정) → 일수익률. **네트워크 필요 — 서버 실행 전용**
  (이 저장소의 개발 샌드박스는 yahoo 차단 → error 반환·크론은 graceful skip).
- 벤치마크 = QQQ 총수익 — 라이브 ★목적함수(us_mock: 아웃퍼폼 vs QQQ·MDD≤지수)와 동일 기준.
- 비용 = US 왕복 30bps(편도 15bps, ml/adaptive/costs 정합). 월말 피처→익일 편입(무룩어헤드).
- 다중검정 = config 그리드 전체 n_trials 로 DSR deflate + PBO(CSCV) — kr 하네스 재사용.

축·매핑: mom12·rev1·vol_inv·hi52 (전부 kr_policy_backtest.AXIS_TO_POLICY 매핑 가능) →
current_recommendation 이 us_policy 가중(w_mom12/w_hi52/w_lowvol/w_mom) 권고 산출.

실행: uv run python backtest/us_policy_backtest.py            (2005~ · 서버에서)
출력: ~/reports/ml-cache/us_policy_backtest.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from kr_policy_backtest import (_objective, _z, build_verdict, chosen_history,
                                current_recommendation, month_ends, perf, select_top, simulate)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESULT_PATH = os.path.expanduser("~/reports/ml-cache/us_policy_backtest.json")

TOP_K = 5              # US 모의와 동일 (US_MOCK_MAX_POS)
BUY_BPS = 15.0         # ml/adaptive/costs US 정합
SELL_BPS = 15.0
MIN_OBS_252 = 200
TRAIN_YEARS = 5
COVERAGE_MIN = 0.90    # 시점 멤버십 대비 가격 확보율 — 미달 시 GO→OBSERVE 강등(생존편향 잔존)
BENCH = "QQQ"          # 라이브 ★목적함수 벤치와 동일

# 전 config 매핑 가능(가격 축만) — n_trials. rev1 은 현행 w_mom 축 직접 판정용.
CONFIGS: dict[str, dict] = {
    "mom12":        {"mom12": 1.0},
    "rev1(현행축)":  {"rev1": 1.0},
    "lowvol":       {"vol_inv": 1.0},
    "hi52":         {"hi52": 1.0},
    "mom12+lowvol": {"mom12": 0.6, "vol_inv": 0.4},
    "mom12+hi52":   {"mom12": 0.6, "hi52": 0.4},
    "lowvol+hi52":  {"vol_inv": 0.6, "hi52": 0.4},
    "balanced":     {"mom12": 0.4, "vol_inv": 0.3, "hi52": 0.3},
}


# ══════════════════════════════════════════════════════════════════════
#  데이터 (네트워크 — 멤버십 GitHub raw + 가격 yfinance·서버 전용)
# ══════════════════════════════════════════════════════════════════════

def build_panels(start_year: int, end_year: int) -> dict | None:
    """{ret(일수익 pivot), intervals(멤버십 구간)} 조립. 실패/오프라인 시 None.

    티커 = 기간과 겹치는 역대 멤버 전체(상폐 포함 목록). 가격이 없는 티커는 자동 누락
    → features_asof 가 커버리지로 정량화.
    """
    try:
        from providers import index_membership as im
        iv = im.membership_intervals("sp500")
    except Exception as e:
        logger.warning("멤버십 로드 실패: %s", e)
        return None
    if not iv:
        return None
    start, end = f"{start_year}-01-01", f"{end_year}-12-31"
    tickers = sorted({t for t, spans in iv.items()
                      for (a, b) in spans if a <= end and (b or "9999") >= start})
    logger.info("역대 멤버 %d티커 (%s~%s) — 가격 배치 수신", len(tickers), start_year, end_year)
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        frames = []
        for i in range(0, len(tickers), 200):
            batch = tickers[i:i + 200]
            df = yf.download(batch, start=start, end=end, auto_adjust=True,
                             progress=False, group_by="column", threads=True)
            close = df["Close"] if "Close" in df else None
            if close is not None and len(close):
                frames.append(close if isinstance(close, pd.DataFrame) else close.to_frame(batch[0]))
        if not frames:
            return None
        close = pd.concat(frames, axis=1)
        close = close.loc[:, ~close.columns.duplicated()].sort_index().astype("float32")
        close = close.dropna(axis=1, how="all")
    except Exception as e:
        logger.warning("가격 배치 수신 실패(오프라인?): %s", e)
        return None
    if close.shape[1] < 50:
        logger.warning("가격 커버리지 과소(%d티커) — 중단", close.shape[1])
        return None
    ret = close.pct_change(fill_method=None).clip(-0.6, 1.0)   # 극단 데이터오류 클립
    return {"ret": ret, "close": close, "intervals": iv}


def bench_returns(start_year: int, end_year: int) -> pd.Series | None:
    """QQQ 총수익 일수익률 (auto_adjust). 실패 시 None."""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        h = yf.download(BENCH, start=f"{start_year}-01-01", end=f"{end_year}-12-31",
                        auto_adjust=True, progress=False)
        c = h["Close"].dropna()
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        return c.pct_change().dropna()
    except Exception as e:
        logger.warning("벤치(%s) 수신 실패: %s", BENCH, e)
        return None


# ══════════════════════════════════════════════════════════════════════
#  피처 (순수 — 시점 멤버십 마스킹 + 커버리지)
# ══════════════════════════════════════════════════════════════════════

def features_asof(panels: dict, t: pd.Timestamp,
                  top_k: int = TOP_K) -> tuple[pd.DataFrame | None, float]:
    """월말 t 피처 z-score + 멤버십 커버리지(가격 확보 멤버/전체 멤버). 데이터 부족 시 (None, cov).

    유니버스 = **t 시점 S&P500 멤버**(상폐예정 포함) ∩ 가격 이력 충분 ∩ 최근 거래.
    """
    from providers.index_membership import is_member_asof
    ret = panels["ret"]
    hist = ret.loc[:t]
    if len(hist) < 260:
        return None, 0.0
    date_s = str(t.date())
    iv = panels["intervals"]
    members = {tk for tk in iv if is_member_asof(iv, tk, date_s)}
    if not members:
        return None, 0.0
    win = hist.iloc[-253:]
    have = [c for c in ret.columns if c in members]
    ok = [c for c in have
          if win[c].notna().sum() >= MIN_OBS_252 and win[c].iloc[-5:].notna().sum() >= 1]
    coverage = len(ok) / len(members)
    if len(ok) < top_k * 4:
        return None, coverage
    adj = (1.0 + win[ok].fillna(0.0)).cumprod()
    px, px_21, px_252 = adj.iloc[-1], adj.iloc[-22], adj.iloc[0]
    f = pd.DataFrame(index=ok)
    f["mom12"] = (px_21 / px_252 - 1.0)
    f["rev1"] = (px / px_21 - 1.0)
    f["vol_inv"] = -win[ok].iloc[-60:].std()
    f["hi52"] = (px / adj.max())
    f = f.dropna()
    if len(f) < top_k * 4:
        return None, coverage
    return f.apply(_z), coverage


# ══════════════════════════════════════════════════════════════════════
#  워크포워드 (kr 하네스 프리미티브 재사용)
# ══════════════════════════════════════════════════════════════════════

def walk_forward(panels: dict, configs: dict, bench: pd.Series, *,
                 train_years: int = TRAIN_YEARS, top_k: int = TOP_K) -> dict:
    ret = panels["ret"]
    feats, covs = {}, {}
    for t in month_ends(ret.index):
        f, cov = features_asof(panels, t, top_k)
        if f is not None:
            feats[t] = f
            covs[t] = cov
    if not feats:
        return {"error": "피처 산출 불가(데이터 부족)"}
    rebal = sorted(feats.keys())
    coverage = round(float(np.mean(list(covs.values()))), 4) if covs else 0.0
    # 연도별 커버리지 (D — 과거로 갈수록 상폐 가격 부재 심화: 어느 폴드가 신뢰 약한지 정직 공개)
    cov_by_year: dict = {}
    for t, c in covs.items():
        cov_by_year.setdefault(t.year, []).append(c)
    coverage_by_year = {str(y): round(float(np.mean(v)), 3) for y, v in sorted(cov_by_year.items())}
    logger.info("리밸런스 %d개 (%s~%s) · 평균 멤버십 커버리지 %.0f%% · 최저연도 %s",
                len(rebal), rebal[0].date(), rebal[-1].date(), coverage * 100,
                min(coverage_by_year.items(), key=lambda kv: kv[1]) if coverage_by_year else "—")

    ret_by_cfg = {}
    for name, cfg in configs.items():
        picks = {t: select_top(feats[t], cfg, top_k) for t in rebal}
        ret_by_cfg[name] = simulate(ret, picks, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
        logger.info("config %-14s 시뮬 완료 (%s)", name, perf(ret_by_cfg[name]))
    bench = bench.reindex(ret.index).fillna(0.0)

    years = sorted({t.year for t in rebal})
    oos_parts, folds = [], []
    for y in years:
        tr0, tr1 = pd.Timestamp(f"{y - train_years}-01-01"), pd.Timestamp(f"{y - 1}-12-31")
        oos0, oos1 = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")
        best_name, best_obj = None, -np.inf
        for name, r in ret_by_cfg.items():
            tr = r.loc[tr0:tr1]
            if len(tr) < 252 * min(3, train_years):
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
                      "oos": perf(oos_r), "oos_bench": perf(bench.loc[oos0:oos1])})
    if not oos_parts:
        return {"error": "OOS 폴드 없음(기간 부족)"}
    oos = pd.concat(oos_parts).sort_index()
    return {"oos_returns": oos, "bench_returns": bench, "folds": folds,
            "config_daily": pd.DataFrame(ret_by_cfg).dropna(how="all"),
            "full_period": {n: perf(r) for n, r in ret_by_cfg.items()},
            "bench_perf_full": perf(bench.loc[rebal[0]:]), "coverage": coverage,
            "coverage_by_year": coverage_by_year}


def degrade_for_coverage(verdict: dict, coverage: float,
                         min_cov: float = COVERAGE_MIN) -> dict:
    """상폐종목 가격 부재(잔존 상방편향) — 커버리지 미달이면 GO 를 OBSERVE 로 강등. 순수."""
    v = dict(verdict)
    v["coverage"] = round(coverage, 4)
    if coverage < min_cov and v.get("code") == "GO":
        v["code"] = "OBSERVE"
        v["label"] = (f"👀 OBSERVE — 통계 관문 통과했으나 멤버십 가격 커버리지 "
                      f"{coverage*100:.0f}%<{min_cov*100:.0f}% (상폐종목 누락 = 상방편향 가능)")
    elif coverage < min_cov:
        v["label"] += f" · 커버리지 {coverage*100:.0f}% (상폐 누락 편향 주의)"
    return v


# ══════════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════════

def run(start_year: int = 2005, end_year: int | None = None) -> dict:
    end_year = end_year or datetime.now().year
    logger.info("US 패널 조립 %d~%d …", start_year, end_year)
    panels = build_panels(start_year, end_year)
    if panels is None:
        return {"error": "패널 조립 실패 — yfinance 필요(서버 실행 전용) 또는 멤버십 로드 실패"}
    bench = bench_returns(start_year, end_year)
    if bench is None or len(bench) < 500:
        return {"error": f"벤치({BENCH}) 데이터 부족"}
    wf = walk_forward(panels, CONFIGS, bench)
    if wf.get("error"):
        return wf
    verdict = degrade_for_coverage(build_verdict(wf, n_trials=len(CONFIGS)), wf["coverage"])
    result = {
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": f"{start_year}~{end_year}",
        "universe": "S&P500 시점 멤버십(상폐목록 포함·가격은 yfinance 가용분)",
        "top_k": TOP_K, "costs_bps": {"buy": BUY_BPS, "sell": SELL_BPS}, "bench": BENCH,
        "coverage": wf["coverage"],
        "coverage_by_year": wf.get("coverage_by_year", {}),
        "verdict": verdict,
        "recommendation": current_recommendation(wf["config_daily"], wf["bench_returns"],
                                                 configs=CONFIGS),
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
    print("\n══════ US 선택정책 ★아웃퍼폼 검증 (멤버십 마스킹·순비용·워크포워드) ══════")
    print(f"기간 {res['period']} · {res['universe']} · top{res['top_k']} 월리밸"
          f" · 비용 {res['costs_bps']['buy']}/{res['costs_bps']['sell']}bps · 벤치 {res['bench']}"
          f" · 커버리지 {res['coverage']*100:.0f}%")
    print(f"\n{v['label']}")
    print(f"  OOS 연결: 전략 CAGR {v['oos']['cagr']*100:+.1f}% vs {res['bench']} {v['bench']['cagr']*100:+.1f}%"
          f" → 순초과 {v['net_excess_cagr']*100:+.2f}%p/년")
    print(f"  MDD: {v['oos']['mdd']*100:.1f}% vs {v['bench']['mdd']*100:.1f}%"
          f" {'✅' if v['mdd_ok'] else '⚠️'} · DSR {v['dsr']} · PBO {v['pbo']}")
    print("\n  폴드별 채택:", res["chosen_history"])
    if res.get("recommendation"):
        r = res["recommendation"]
        print(f"  📌 현재 권고: {r['chosen']} → {r['policy_weights']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2005)
    a = ap.parse_args()
    res = run(start_year=a.start)
    _print_report(res)
    return 0 if not res.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
