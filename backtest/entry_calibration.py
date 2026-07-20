#!/usr/bin/env python3
"""entry_calibration.py — entry_analyzer 점수 가중치·임계값 walk-forward 캘리브레이션

진입 점수의 수작업 가중치(승률 40% / 손익비 30% / RSI 15% / 낙폭 15% / 다이버전스 0%)와
enter 임계값(0.62)을 과거 데이터로 검증·재추정한다. 다이버전스(w_div)는 신규 축이라
기본 가중치 0 — 이 스크립트의 OOS 재추정이 실제로 개선을 확인해야만 채택된다.

방법:
  1. 유니버스 전 종목 × 과거 평가일(5거래일 간격)에 대해 점수 구성요소를 재현
     — 유사기간 탐색은 평가일 기준 lookback=21로 제한 (20일 선행수익 미실현분 리크 방지)
  2. 평가일 기준 앞 60%로 가중치·임계값 그리드 탐색 (목적: enter 신호의 평균 20일 수익,
     최소 신호 수 제약), 뒤 40%로 OOS 검증
  3. OOS에서 기본값보다 우수할 때만 ~/reports/ml-cache/entry_score_params.json 저장
     (entry_analyzer.get_score_params()가 자동 반영)

실행:
    uv run python backtest/entry_calibration.py
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from ml.entry_analyzer import (
    DEFAULT_SCORE_PARAMS, LEVERAGE_ETFS, LEVERAGE_UNDERLYING, PORTFOLIO_STOCKS,
    SCORE_PARAMS_PATH, _compute_ticker_features, _find_similar,
    _weighted_quantile, compute_entry_score,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EVAL_STEP   = 5     # 평가일 간격 (거래일)
WARMUP_DAYS = 252   # 평가 시작 전 최소 히스토리
LOOKBACK    = 21    # 유사기간 탐색 시 최근 제외 일수 (리크 방지)
MIN_SIGNALS = 50    # 그리드 후보의 캘리브레이션 구간 최소 enter 신호 수


def build_samples(days: int = 1260) -> pd.DataFrame:
    """전 종목 × 평가일 점수 구성요소 + 실제 20일 선행수익 테이블 생성."""
    from ml.data_pipeline import US_TOP50, KR_TOP10, fetch_prices

    stock_tickers = list(dict.fromkeys(list(PORTFOLIO_STOCKS) + list(US_TOP50) + list(KR_TOP10)))
    all_tickers   = list(set(stock_tickers + LEVERAGE_ETFS + ["QQQ", "SPY", "^VIX"]))
    prices        = fetch_prices(all_tickers, days=days)

    vix_df = prices.get("^VIX", pd.DataFrame())
    vix_s  = vix_df.get("Close") if hasattr(vix_df, "get") else None
    if vix_s is None or len(vix_s) == 0:
        raise RuntimeError("VIX 데이터 없음")
    qqq = prices.get("QQQ", pd.DataFrame()).get("Close")
    spy = prices.get("SPY", pd.DataFrame()).get("Close")

    rows = []
    targets = [(t, "leverage") for t in LEVERAGE_ETFS] + [(t, "stock") for t in stock_tickers]
    for ticker, category in targets:
        df = prices.get(ticker)
        if df is None or len(df) < WARMUP_DAYS + 50:
            continue
        price = df["Close"].dropna()
        if category == "leverage":
            und = LEVERAGE_UNDERLYING.get(ticker, "QQQ")
            signal_price = qqq if und == "QQQ" else spy
        else:
            signal_price = price
        if signal_price is None:
            continue

        feat = _compute_ticker_features(signal_price, vix_s)
        if feat.empty or len(feat) < WARMUP_DAYS:
            continue

        fwd_price = price.reindex(feat.index)
        fwd_20d   = fwd_price.pct_change(20).shift(-20)
        high_52w  = price.rolling(252, min_periods=60).max()
        own_dd    = (price / high_52w - 1).reindex(feat.index)

        eval_pos = range(WARMUP_DAYS, len(feat) - 21, EVAL_STEP)
        for i in eval_pos:
            t   = feat.index[i]
            fwd = fwd_20d.iloc[i]
            if not np.isfinite(fwd):
                continue
            cur  = feat.iloc[i]
            hist = feat.iloc[: i + 1]
            sim_idx, sim_w = _find_similar(cur, hist, n=30, lookback=LOOKBACK)
            if len(sim_idx) == 0:
                continue
            r20 = fwd_20d.reindex(sim_idx)
            m   = r20.notna()
            if m.sum() < 3:
                continue
            rets, w = r20[m].to_numpy(), np.asarray(sim_w)[m.to_numpy()]
            win_20 = float(np.average(rets > 0, weights=w))
            exp_20 = _weighted_quantile(rets, w, 0.5)
            p25_20 = _weighted_quantile(rets, w, 0.25)
            dd_v   = float(own_dd.iloc[i]) if np.isfinite(own_dd.iloc[i]) else 0.0
            rows.append({
                "ticker": ticker, "date": t, "category": category,
                "win_20": win_20, "exp_20": exp_20, "p25_20": p25_20,
                "rsi": float(cur["rsi"]), "dd": dd_v,
                "div": float(cur.get("divergence", 0.0)), "fwd_20": float(fwd),
            })
        logger.info("%s: 샘플 누적 %d", ticker, len(rows))

    return pd.DataFrame(rows)


def evaluate(samples: pd.DataFrame, params: dict) -> dict:
    """주어진 파라미터로 enter 신호 성과 평가."""
    scores = samples.apply(
        lambda r: compute_entry_score(
            r["win_20"], r["exp_20"], r["p25_20"], r["rsi"], r["dd"],
            r.get("div", 0.0), r["category"], params=params,
        )[0],
        axis=1,
    )
    enters = samples[scores >= params["enter_threshold"]]
    if len(enters) == 0:
        return {"n": 0, "mean_fwd": float("-inf"), "win_rate": 0.0}
    return {
        "n":        len(enters),
        "mean_fwd": float(enters["fwd_20"].mean()),
        "win_rate": float((enters["fwd_20"] > 0).mean()),
    }


def grid_search(cal: pd.DataFrame) -> tuple[dict, dict]:
    """가중치·임계값 그리드 탐색 — enter 신호 평균 20일 수익 최대화.

    w_div(RSI 다이버전스) 는 신규 축이라 0.0(미채용)을 그리드에 포함해 기존 4축
    조합과 동등 비교 — OOS 에서 진짜 개선일 때만 evaluate()가 자연히 채택.
    """
    best_params, best_metrics = None, {"mean_fwd": float("-inf")}
    grid = itertools.product(
        [0.30, 0.40, 0.50],          # w_win
        [0.20, 0.30, 0.40],          # w_rr
        [0.05, 0.15],                # w_rsi
        [0.0, 0.10],                 # w_div
        [0.55, 0.62, 0.68, 0.74],    # enter_threshold
    )
    for w_win, w_rr, w_rsi, w_div, thr in grid:
        w_dd = round(1.0 - w_win - w_rr - w_rsi - w_div, 4)
        if not (0.0 <= w_dd <= 0.35):
            continue
        params = {"w_win": w_win, "w_rr": w_rr, "w_rsi": w_rsi, "w_dd": w_dd,
                  "w_div": w_div, "enter_threshold": thr, "wait_threshold": 0.40}
        m = evaluate(cal, params)
        if m["n"] >= MIN_SIGNALS and m["mean_fwd"] > best_metrics["mean_fwd"]:
            best_params, best_metrics = params, m
    return best_params, best_metrics


def main() -> int:
    logger.info("=== entry score 캘리브레이션 시작 ===")
    samples = build_samples()
    if samples.empty:
        logger.error("샘플 없음")
        return 1
    samples = samples.sort_values("date").reset_index(drop=True)
    split   = samples["date"].quantile(0.6)
    cal, val = samples[samples["date"] <= split], samples[samples["date"] > split]
    logger.info("샘플 %d건 (캘리브레이션 %d / 검증 %d, 분할일 %s)",
                len(samples), len(cal), len(val), split.date())

    base_val = evaluate(val, DEFAULT_SCORE_PARAMS)
    logger.info("[기본값 OOS] n=%d  평균20d수익=%+.2f%%  승률=%.0f%%",
                base_val["n"], base_val["mean_fwd"] * 100, base_val["win_rate"] * 100)

    best, cal_m = grid_search(cal)
    if best is None:
        logger.warning("그리드 탐색 실패 (최소 신호 수 미달) — 기본값 유지")
        return 0
    best_val = evaluate(val, best)
    logger.info("[그리드 최적 (캘리브레이션)] %s  n=%d  평균=%+.2f%%  승률=%.0f%%",
                best, cal_m["n"], cal_m["mean_fwd"] * 100, cal_m["win_rate"] * 100)
    logger.info("[그리드 최적 OOS] n=%d  평균20d수익=%+.2f%%  승률=%.0f%%",
                best_val["n"], best_val["mean_fwd"] * 100, best_val["win_rate"] * 100)

    # OOS에서 기본값을 이겨야만 채택 (신호 수 최소한도 유지)
    if best_val["n"] >= 20 and best_val["mean_fwd"] > base_val["mean_fwd"]:
        SCORE_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCORE_PARAMS_PATH.write_text(json.dumps(best, indent=2))
        logger.info("✅ 채택 — %s 저장", SCORE_PARAMS_PATH)
    else:
        logger.info("⏸️ 기본값 유지 (OOS 개선 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
