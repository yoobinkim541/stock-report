#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X·논문 가격 전략을 현재 모의 가격축과 같은 비용·목적함수로 비교.

채택 게이트(이 파일에 고정. 결과 보고 바꾸지 않는다):
  1) 2002-2018 목적함수 1위 규칙을 2019-2026에만 평가.
  2) 워크포워드(학습 5년 → 다음 해)로 이어 붙인 수익.
  둘 다 ml.adaptive.reward.should_adopt 를 현재 믹스 대비 통과해야 교체 후보.
  통과 못 하면 NO-GO. 이 스크립트는 라이브 가중을 고치지 않는다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kr_policy_backtest as kb  # noqa: E402

BUY_BPS, RESULT_PATH = kb.BUY_BPS, kb.RESULT_PATH
SELL_BPS, TOP_K, TRAIN_YEARS, UNIVERSE_N = kb.SELL_BPS, kb.TOP_K, kb.TRAIN_YEARS, kb.UNIVERSE_N
_objective, build_panels, cap_benchmark = kb._objective, kb.build_panels, kb.cap_benchmark
features_asof, month_ends, perf = kb.features_asof, kb.month_ends, kb.perf
from ml.adaptive.reward import should_adopt  # noqa: E402
from ml.trader_knowhow import TESTED, UNTESTED  # noqa: E402
from ml.x_strategies import (  # noqa: E402
    KR_ADVANTAGE_SLEEVES, STRATEGY_IDS, attach_han_trend, blend_sleeves,
    current_weights, enrich, picks_for, scale_to_vol,
    simulate_invvol, simulate_stop, simulate_with_cash,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("x_strategy")

OUT_PATH = os.path.expanduser("~/reports/ml-cache/x_strategy_backtest.json")
HOLDOUT_START = 2019
MIN_SAMPLES = 252


def _row(returns: pd.Series, bench: pd.Series) -> dict:
    aligned = bench.reindex(returns.index).fillna(0.0)
    s, b = perf(returns), perf(aligned)
    obj = _objective(returns, aligned)
    return {
        **s,
        "bench_total": b["total"],
        "bench_mdd": b["mdd"],
        "excess": round(s["total"] - b["total"], 4),
        "objective": None if obj is None else round(float(obj), 4),
    }


def _slice(series: pd.Series, start: int | None, end: int | None) -> pd.Series:
    out = series
    if start is not None:
        out = out[out.index.year >= start]
    if end is not None:
        out = out[out.index.year <= end]
    return out


def build_frames(panels: dict) -> list[tuple[pd.Timestamp, pd.DataFrame, dict]]:
    frames = []
    for t in month_ends(panels["ret"].index):
        base = features_asof(panels, t, universe_n=UNIVERSE_N)
        if base is None:
            continue
        frame, state = enrich(panels, t, base)
        frames.append((t, frame, state))
        if len(frames) % 12 == 0:
            log.info("피처 %d개월 (%s)", len(frames), t.date())
    return frames


def _simulate(name: str, ret: pd.DataFrame, adj: pd.DataFrame, picks: dict) -> pd.Series:
    if name == "oneil_stop8":
        return simulate_stop(ret, adj, picks, stop=0.08, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
    if name == "invvol_current":
        return simulate_invvol(ret, picks, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
    series = simulate_with_cash(ret, picks, buy_bps=BUY_BPS, sell_bps=SELL_BPS)
    if name == "barroso_vol_mom12":
        return scale_to_vol(series)
    return series


def run_all(frames, panels) -> dict:
    weights = current_weights()
    ret, adj = panels["ret"], panels["adj"]
    attach_han_trend(frames, ret)
    picks = {name: {} for name in STRATEGY_IDS}
    for t, frame, state in frames:
        for name in STRATEGY_IDS:
            picks[name][t] = picks_for(name, frame, state, TOP_K, weights)
    rebal = [t for t, _, _ in frames]
    bench = cap_benchmark(panels, rebal)
    series = {name: _simulate(name, ret, adj, picks[name]) for name in STRATEGY_IDS}
    full = {}
    for name in STRATEGY_IDS:
        full[name] = _row(series[name], bench)
        log.info("%s obj=%s excess=%s mdd=%s", name,
                 full[name]["objective"], full[name]["excess"], full[name]["mdd"])
    return {"weights": weights, "full": full, "series": series, "bench": bench}


def walk_forward(series: dict, bench: pd.Series) -> dict:
    years = sorted({int(y) for s in series.values() for y in s.index.year})
    folds, parts_s, parts_c = [], [], []
    for year in years:
        scores = {}
        for name in STRATEGY_IDS:
            train = _slice(series[name], year - TRAIN_YEARS, year - 1)
            if len(train) < MIN_SAMPLES:
                continue
            obj = _objective(train, bench)
            scores[name] = -1e9 if obj is None else float(obj)
        if "current_price_mix" not in scores:
            continue
        winner = max(scores, key=scores.get)
        test_s = _slice(series[winner], year, year)
        test_c = _slice(series["current_price_mix"], year, year)
        common = test_s.index.intersection(test_c.index)
        if len(common) < 60:
            continue
        folds.append({"year": year, "winner": winner, "train_objective": round(scores[winner], 4)})
        parts_s.append(test_s.loc[common])
        parts_c.append(test_c.loc[common])
    if not parts_s:
        return {"error": "no_folds"}
    stitched, current = pd.concat(parts_s), pd.concat(parts_c)
    return {
        "folds": folds,
        "stitched": _row(stitched, bench),
        "current": _row(current, bench),
    }


def decide(series: dict, bench: pd.Series, wf: dict) -> dict:
    """2018년까지로 규칙을 고르고 2019-2026은 한 번만 본다."""
    pre = {}
    for name, s in series.items():
        obj = _objective(_slice(s, None, HOLDOUT_START - 1), bench)
        pre[name] = None if obj is None else float(obj)
    ranked = sorted(pre, key=lambda n: pre[n] if pre[n] is not None else -1e18, reverse=True)
    winner = ranked[0]
    held_s = _slice(series[winner], HOLDOUT_START, None)
    held_c = _slice(series["current_price_mix"], HOLDOUT_START, None)
    common = held_s.index.intersection(held_c.index)
    held_row = _row(held_s.loc[common], bench)
    current_row = _row(held_c.loc[common], bench)
    fixed_ok = winner != "current_price_mix" and should_adopt(
        {"excess": held_row["excess"], "mdd": held_row["mdd"], "n": held_row["n"]},
        {"excess": current_row["excess"], "mdd": current_row["mdd"], "n": current_row["n"]},
        held_row["bench_mdd"], min_samples=MIN_SAMPLES)
    wf_ok = not wf.get("error") and should_adopt(
        {"excess": wf["stitched"]["excess"], "mdd": wf["stitched"]["mdd"], "n": wf["stitched"]["n"]},
        {"excess": wf["current"]["excess"], "mdd": wf["current"]["mdd"], "n": wf["current"]["n"]},
        wf["stitched"]["bench_mdd"], min_samples=MIN_SAMPLES)
    adopt = bool(fixed_ok and wf_ok)
    holdout_all = {}
    pre_rows = {}
    for name, s in series.items():
        pre_rows[name] = _row(_slice(s, None, HOLDOUT_START - 1), bench)
        holdout_all[name] = _row(_slice(s, HOLDOUT_START, None), bench)
    return {
        "verdict": "GO" if adopt else "NO-GO",
        "winner": winner if adopt else None,
        "preholdout_winner": winner,
        "preholdout_objective": pre[winner],
        "preholdout": pre_rows,
        "holdout": held_row,
        "holdout_all": holdout_all,
        "holdout_current": current_row,
        "fixed_rule_passes": bool(fixed_ok),
        "walk_forward_passes": bool(wf_ok),
        "note": (
            "교체 후보는 2018년 이전 1위가 2019-2026 should_adopt 를 통과하고, "
            "워크포워드 연결 수익도 현재 믹스를 통과할 때만. 아니면 라이브 불변."
        ),
    }


def advantage_report(series: dict, bench: pd.Series) -> dict:
    """한국 장점 앙상블. 이 함수만 지수 낙폭 +3%p 를 허용한다."""
    from ml.kr_advantage import owner_adopts
    from ml.x_strategies import KR_ADVANTAGE_SLEEVES
    missing = [name for name in KR_ADVANTAGE_SLEEVES if series.get(name) is None or series[name].empty]
    if missing:
        return {"replace_live": False, "error": f"장점 슬리브 부재: {missing}"}
    blend, weights = blend_sleeves(
        {name: series[name] for name in KR_ADVANTAGE_SLEEVES}, bench,
        buy_bps=BUY_BPS, sell_bps=SELL_BPS,
    )
    if blend.empty:
        return {"replace_live": False, "error": "앙상블 구간이 없다"}
    held_c = series["current_price_mix"].loc[blend.index.intersection(series["current_price_mix"].index)]
    common = blend.index.intersection(held_c.index)
    common = common[common >= HOLDOUT_START]
    if len(common) < MIN_SAMPLES:
        return {"replace_live": False, "error": f"홀드아웃 구간이 짧다 ({len(common)})"}
    held_row = _row(blend.loc[common], bench)
    current_row = _row(held_c.loc[common], bench)
    adopt = owner_adopts(
        held_row["mdd"], held_row["bench_mdd"],
        held_row["excess"], current_row["excess"],
        held_row["n"], MIN_SAMPLES,
    )
    mean_w = {} if weights.empty else {k: round(float(v), 4) for k, v in weights.mean().items()}
    return {
        "sleeves": list(KR_ADVANTAGE_SLEEVES),
        "menu_frozen_from": "2001-2018 objective>0, distinct role. 2019+ was not used to pick the menu.",
        "full": _row(blend, bench),
        "holdout": held_row,
        "holdout_current": current_row,
        "mean_weight": mean_w,
        "cash_fraction": 0.0 if weights.empty else round(float((weights.sum(axis=1) <= 0).mean()), 4),
        "replace_live": bool(adopt),
        "note": (
            "소유자 2026-11-12: 이 앙상블만 지수 낙폭 +3%p 까지 허용. "
            "2019-2026 초과가 현재 가격축보다 커야 한다. "
            "목록은 2018년 이전에 고정. 라이브 적용은 crons/kiwoom_mock_track.py."
        ),
    }



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=2001)
    ap.add_argument("--end", type=int, default=datetime.now().year)
    args = ap.parse_args()
    log.info("패널 %d-%d", args.start, args.end)
    panels = build_panels(args.start, args.end)
    if panels is None:
        log.error("패널 없음")
        return 1
    frames = build_frames(panels)
    log.info("피처 월 %d", len(frames))
    ran = run_all(frames, panels)
    wf = walk_forward(ran["series"], ran["bench"])
    decision = decide(ran["series"], ran["bench"], wf)
    ensemble = advantage_report(ran["series"], ran["bench"])
    payload = {
        "asof": datetime.now().isoformat(timespec="seconds"),
        "universe_n": UNIVERSE_N,
        "top_k": TOP_K,
        "costs_bps": {"buy": BUY_BPS, "sell": SELL_BPS},
        "benchmark": "point-in-time cap-weight top N (kr_policy_backtest.cap_benchmark)",
        "bench_label": "cap-weight top N",
        "objective": "compounded excess - MDD penalty; adopt via reward.should_adopt",
        "weights": ran["weights"],
        "marcap_result": RESULT_PATH,
        "full": ran["full"],
        "walk_forward": wf,
        "decision": decision,
        "advantage_ensemble": ensemble,
        "catalog": {row["id"]: row for row in TESTED},
        "untested": list(UNTESTED),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("저장 %s verdict=%s ensemble_replace=%s", OUT_PATH, decision["verdict"], ensemble.get("replace_live"))
    print(json.dumps({"decision": decision["verdict"], "ensemble": ensemble}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
