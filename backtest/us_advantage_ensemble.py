#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""미국 모의와 따로 두는 가격 앙상블. 한국 2018년 이전 순위로 슬리브를 고르지 않는다.

슬리브는 미국 논문의 서로 다른 장점만 쓴다.
- mom_scaled: Jegadeesh-Titman 12-1 을 Barroso 방식으로 노출 축소. 수익은 모멘텀, 장점은 낙폭 축소.
- hi52: George-Hwang 52주 고가 근접. 모멘텀과 다른 횡단면.
- lowvol: Blitz 저변동. 방어 슬리브.
절대모멘텀(Antonacci): 벤치 12개월 수익이 0 이하면 다음 달은 현금.

비중은 직전 36개월 목적함수가 양수인 슬리브만. 라이브 us_policy 는 랭커·밸류·퀄리티가 본체이고
이 패널에 그 경로가 없으므로, QQQ 를 이겨도 이 스크립트는 가중치를 저장하지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kr_policy_backtest as kb  # noqa: E402
from ml.adaptive.reward import should_adopt  # noqa: E402
from ml.x_strategies import blend_sleeves, scale_to_vol  # noqa: E402
from us_policy_backtest import (  # noqa: E402
    BENCH, BUY_BPS, SELL_BPS, TOP_K, bench_returns, build_panels, features_asof,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("us_advantage")

OUT_PATH = os.path.expanduser("~/reports/ml-cache/us_advantage_ensemble.json")
MIN_SAMPLES = 252
US_SLEEVES = {
    "mom_scaled": {"mom12": 1.0},
    "hi52": {"hi52": 1.0},
    "lowvol": {"vol_inv": 1.0},
}


def _risk_on(bench: pd.Series) -> pd.Series:
    """종가 t 의 12개월 수익. 다음 거래일부터 적용하는 쪽이 blend 에 넘긴다."""
    nav = (1.0 + bench.fillna(0.0)).cumprod()
    return nav / nav.shift(252) - 1.0 > 0.0


def run(start_year: int, end_year: int) -> dict:
    panels = build_panels(start_year, end_year)
    bench = bench_returns(start_year, end_year)
    if panels is None or bench is None or bench.empty:
        return {"replace_live": False, "error": "미국 패널 또는 QQQ 를 받지 못했다"}
    feats = {}
    covs = []
    for t in kb.month_ends(panels["ret"].index):
        frame, cov = features_asof(panels, t, TOP_K)
        if frame is None:
            continue
        feats[t] = frame
        covs.append(cov)
    if len(feats) < 36:
        return {"replace_live": False, "error": f"피처 월 {len(feats)}"}
    coverage = float(sum(covs) / len(covs)) if covs else 0.0
    sleeves = {}
    for name, cfg in US_SLEEVES.items():
        picks = {t: kb.select_top(frame, cfg, TOP_K) for t, frame in feats.items()}
        series = kb.simulate(panels["ret"], picks, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
        if name == "mom_scaled":
            series = scale_to_vol(series)
        sleeves[name] = series
        log.info("%s n=%d", name, len(series))
    bench = bench.reindex(panels["ret"].index).fillna(0.0)
    blend, weights = blend_sleeves(
        sleeves, bench, buy_bps=BUY_BPS, sell_bps=SELL_BPS,
        rebalance=sorted(feats), risk_on=_risk_on(bench),
    )
    if blend.empty:
        return {"replace_live": False, "error": "앙상블 구간이 없다", "coverage": round(coverage, 4)}
    row = kb.perf(blend)
    b = kb.perf(bench.reindex(blend.index).fillna(0.0))
    excess = round(row["total"] - b["total"], 4)
    adopt = should_adopt(
        {"excess": excess, "mdd": row["mdd"], "n": row["n"]},
        {"excess": 0.0, "mdd": b["mdd"], "n": b["n"]},
        b["mdd"],
        min_samples=MIN_SAMPLES,
    )
    # 커버리지가 낮으면 상폐 가격 공백이 남는다. 숫자만으로 교체하지 않는다.
    replace = bool(adopt and coverage >= 0.90)
    mean_w = {} if weights.empty else {k: round(float(v), 4) for k, v in weights.mean().items()}
    sleeve_rows = {}
    for name, series in sleeves.items():
        aligned = series.reindex(blend.index).fillna(0.0)
        s, sb = kb.perf(aligned), kb.perf(bench.reindex(aligned.index).fillna(0.0))
        sleeve_rows[name] = {**s, "excess": round(s["total"] - sb["total"], 4), "bench_mdd": sb["mdd"]}
    return {
        "asof": datetime.now().isoformat(timespec="seconds"),
        "market": "US",
        "benchmark": BENCH,
        "costs_bps": {"buy": BUY_BPS, "sell": SELL_BPS},
        "top_k": TOP_K,
        "sleeves": list(US_SLEEVES),
        "coverage": round(coverage, 4),
        "ensemble": {**row, "excess": excess, "bench_total": b["total"], "bench_mdd": b["mdd"]},
        "sleeve_rows": sleeve_rows,
        "mean_weight": mean_w,
        "cash_fraction": 0.0 if weights.empty else round(float((weights.sum(axis=1) <= 0).mean()), 4),
        "beats_qqq_gate": bool(adopt),
        "replace_live": replace,
        "note": (
            "비교 대상은 QQQ 다. 미국 모의 본체는 랭커·밸류·퀄리티이고 이 패널에 없다. "
            "QQQ 게이트를 통과해도 us_policy 가중은 이 스크립트가 바꾸지 않는다."
        ),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=2005)
    ap.add_argument("--end", type=int, default=datetime.now().year)
    args = ap.parse_args()
    payload = run(args.start, args.end)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("저장 %s replace=%s", OUT_PATH, payload.get("replace_live"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if "error" not in payload else 1


if __name__ == "__main__":
    sys.exit(main())