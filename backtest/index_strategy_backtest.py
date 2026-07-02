#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest/index_strategy_backtest.py — 생존편향 제거 KR 백테스트 (Phase A / §E).

핵심 질문(사용자 thesis): **부실 퇴출 위험 종목을 회피하면 실제로 수익↑·MDD↓ 되는가?**

설계(정직·무룩어헤드):
  - 유니버스: marcap 시총 상위 N (시점별 — 생존편향 0; 상폐주도 재임구간 포함).
  - 퇴출모델: 백테스트 시작 *이전* 구간으로만 학습(ml.deletion_risk) → OOS 적용.
  - 비교: baseline(top-N 동일가중, 미래 상폐주 포함) vs avoid(예측 고위험 상위 frac 제외).
  - 전방수익: marcap 가격. 상폐주는 구간 내 마지막 가격(정리매매 폭락 포착)= 손실 실현 → 회피 효과 측정.
    (가격 소실 종목은 마지막 정상가 사용 → 손실 *과소* → 회피효과는 **보수적**으로 추정.)

전부 marcap(캐시) 기반. portfolio_metrics 는 순수(테스트). run_backtest 는 실데이터 통합.
"""
from __future__ import annotations

import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root (ml·providers)

logger = logging.getLogger(__name__)


def portfolio_metrics(nav: list[float]) -> dict:
    """NAV 시계열 → 총수익%·MDD%(양수)·Sharpe(분기→연환산 근사)·구간수. 순수 함수."""
    from ml.adaptive import reward
    if not nav or len(nav) < 2:
        return {"total_ret": 0.0, "mdd": 0.0, "sharpe": 0.0, "n": 0}
    rets = [nav[i + 1] / nav[i] - 1.0 for i in range(len(nav) - 1) if nav[i] > 0]
    total = (nav[-1] / nav[0] - 1.0) * 100.0
    mdd = reward.max_drawdown(nav) * 100.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (statistics.mean(rets) / sd * (4 ** 0.5)) if sd > 0 else 0.0   # 분기 리밸런스 가정
    return {"total_ret": round(total, 1), "mdd": round(mdd, 1), "sharpe": round(sharpe, 2), "n": len(rets)}


def _panel(start_year: int, end_year: int, market: str):
    """월별 [Code, ym, Close, Marcap] 패널 (marcap). 실패 시 None."""
    import pandas as pd
    from providers import kr_market_data as km
    frames = []
    for y in range(start_year, end_year + 1):
        df = km._marcap_year(y)
        if df is None:
            continue
        if market and "Market" in df.columns:
            df = df[df["Market"] == market]
        sub = df[["Code", "Date", "Close", "Marcap"]].copy()
        sub["Date"] = pd.to_datetime(sub["Date"])
        sub["ym"] = sub["Date"].dt.to_period("M")
        frames.append(sub.sort_values("Date").groupby(["Code", "ym"], as_index=False).last())
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def run_backtest(start_year: int, end_year: int, *, top_n: int = 100, split_year: int | None = None,
                 rebalance_m: int = 3, avoid_frac: float = 0.20, market: str = "KOSPI") -> dict:
    """생존편향 제거 백테스트 — baseline vs avoid(고위험 제외) vs 모델 OOS AUC. 실데이터."""
    import numpy as np
    import pandas as pd
    from ml import deletion_risk as dr

    split_year = split_year or (start_year + (end_year - start_year) // 2)
    split_date = f"{split_year}-01-01"

    # 1) 피처·라벨(재사용) → 시작 이전 구간으로만 퇴출모델 학습(OOS)
    # 12개월 라벨 호라이즌 갭 — split 직전 1년 표본은 라벨이 backtest 구간을 내다봐 누수(감사 확정) → 제외
    gap_date = f"{split_year - 1}-01-01"
    rows, labels, meta = dr.build_training_set(start_year, end_year, market=market, train_universe_n=2000)
    tr_rows = [r for r, m in zip(rows, meta) if m["date"] < gap_date]
    tr_lab = [l for l, m in zip(labels, meta) if m["date"] < gap_date]
    res = dr.train_deletion_model(tr_rows, tr_lab)
    model = res.get("model")
    risk_map = {}
    if model is not None:
        for p, m in zip(dr.predict_risk(model, rows), meta):
            risk_map[(m["code"], m["date"])] = p

    # 2) 가격·시총 패널 → 피벗
    panel = _panel(start_year, end_year, market)
    if panel is None:
        return {"error": "marcap 패널 없음", "model": res.get("reason")}
    close = panel.pivot_table(index="ym", columns="Code", values="Close")
    mcap = panel.pivot_table(index="ym", columns="Code", values="Marcap")
    months = sorted(close.index)
    bt_months = [m for m in months if m.to_timestamp() >= pd.Timestamp(split_date)]
    rebs = bt_months[::rebalance_m]
    if len(rebs) < 3:
        return {"error": "백테스트 구간 부족", "model": res.get("reason")}

    def simulate(avoid: bool) -> list[float]:
        nav, cur = [1.0], 1.0
        for i in range(len(rebs) - 1):
            t, tn = rebs[i], rebs[i + 1]
            mc_t = mcap.loc[t].dropna()
            uni = list(mc_t.nlargest(top_n).index)
            if avoid and model is not None:
                ds = str(t.to_timestamp().date())
                ranked = sorted(uni, key=lambda c: -risk_map.get((c, ds), 0.0))
                k = int(len(uni) * avoid_frac)
                drop = set(ranked[:k])
                uni = [c for c in uni if c not in drop] or uni   # 전부 제외 방지
            rets = []
            for c in uni:
                if c not in close.columns:
                    continue
                p0 = close.loc[t, c]
                if pd.isna(p0) or p0 <= 0:
                    continue
                win = close.loc[t:tn, c].dropna()
                if len(win) < 2:
                    continue
                p1 = win.iloc[-1]               # 구간 마지막 가격(상폐 시 정리매매 폭락 포착)
                if p1 > 0:
                    rets.append(p1 / p0 - 1.0)
            r = float(np.mean(rets)) if rets else 0.0
            cur *= (1.0 + r)
            nav.append(cur)
        return nav

    base_nav = simulate(False)
    avoid_nav = simulate(True)
    return {
        "baseline": portfolio_metrics(base_nav),
        "avoid": portfolio_metrics(avoid_nav),
        "oos_auc": res.get("oos_auc"),
        "model": res.get("reason"),
        "params": {"start": start_year, "end": end_year, "split": split_year,
                   "top_n": top_n, "rebalance_m": rebalance_m, "avoid_frac": avoid_frac},
    }


if __name__ == "__main__":
    import json
    out = run_backtest(2010, 2024, top_n=100, rebalance_m=3, avoid_frac=0.20)
    print(json.dumps(out, ensure_ascii=False, indent=2))
