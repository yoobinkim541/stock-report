#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/earnings_move_predictor.py — 실적후 주가반응 예측 (Phase C / §G4) — ★강화학습 대상.

사용자 요청: "주가가 어떻게 움직일지 예측". 출력 = **기대 변동폭(범위) + 방향확률**(허위정밀 금지).

★정직: 실적후 *방향* 예측은 pre-earnings 정보로는 거의 무작위(가장 강한 동인=서프라이즈 자체가
미지). 따라서 G4 의 실질 가치는 **기대 절대변동폭**(변동성·과거반응·IV로 예측 가능)이고, 방향은
약한 확률로만 제공. IV 기대변동(options_snapshot)·예측 서프라이즈(G3)는 데이터 축적 후 피처로 강화.

event_features 순수(테스트). build_training_set 은 earnings_reaction(과거 반응)+가격 사용.
"""
from __future__ import annotations

import logging
import os
import pickle
import statistics
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.path.expanduser("~/reports/ml-cache/earnings_move_predictor.pkl"))

FEATURE_COLS = ["hist_avg_abs_move", "hist_drift_persist", "prior_surprise_mean",
                "mom_20d", "vol_20d", "beat_prob", "iv_expected_move"]


def event_features(prior_abs_moves: list, prior_drift_hits: list, prior_surprises: list,
                   mom_20d=None, vol_20d=None, beat_prob=None, iv_expected_move=None) -> dict:
    """실적 전 피처(룩어헤드 없음) — 과거 반응 통계 + 모멘텀/변동성 + (옵션)IV·G3 beat확률."""
    am = [m for m in (prior_abs_moves or []) if m is not None]
    sp = [s for s in (prior_surprises or []) if s is not None]
    dh = [h for h in (prior_drift_hits or []) if h is not None]
    return {
        "hist_avg_abs_move": round(statistics.mean(am), 4) if am else None,
        "hist_drift_persist": round(statistics.mean(dh), 3) if dh else None,
        "prior_surprise_mean": round(statistics.mean(sp), 3) if sp else None,
        "mom_20d": mom_20d,
        "vol_20d": vol_20d,
        "beat_prob": beat_prob,                # G3 예측(없으면 None)
        "iv_expected_move": iv_expected_move,  # options_snapshot(축적 후)
    }


def _price_feats(closes, event_date):
    """lib.price_utils.window_feats 위임 (행위 동일)."""
    from lib.price_utils import window_feats
    return window_feats(closes, event_date)


def build_training_set(tickers: list[str], *, min_prior: int = 3):
    """earnings_reaction 과거 반응 → (rows, mag_labels, dir_labels, meta). 시간순.

    mag_label=|reaction_1d|, dir_label=1 if reaction_1d>0.
    """
    from reports import earnings_reaction as er
    from lib.price_utils import fetch_closes

    rows, mag, dirn, meta = [], [], [], []
    for tk in tickers:
        closes = fetch_closes(tk)
        reactions = er.post_earnings_reactions(tk, prices=closes) if closes is not None else []
        reactions = [r for r in reactions if r.get("reaction_1d") is not None]
        if len(reactions) <= min_prior:
            continue
        for i in range(min_prior, len(reactions)):
            ev = reactions[i]
            prior = reactions[:i]
            abs_moves = [abs(p["reaction_1d"]) for p in prior]
            surprises = [p.get("surprise_pct") for p in prior]
            drift_hits = [1 if (p.get("surprise_pct") is not None and p.get("drift_5d") is not None
                                and (p["surprise_pct"] > 0) == (p["drift_5d"] > 0)) else 0
                          for p in prior if p.get("surprise_pct") is not None and p.get("drift_5d") is not None]
            mom, vol = _price_feats(closes, ev["date"])
            rows.append({"features": event_features(abs_moves, drift_hits, surprises, mom, vol)})
            mag.append(abs(ev["reaction_1d"]))
            dirn.append(1 if ev["reaction_1d"] > 0 else 0)
            meta.append({"ticker": tk, "date": ev["date"]})
    order = sorted(range(len(rows)), key=lambda i: meta[i]["date"])
    return ([rows[i] for i in order], [mag[i] for i in order],
            [dirn[i] for i in order], [meta[i] for i in order])


def _matrix(rows):
    return [[r["features"].get(c) if r["features"].get(c) is not None else float("nan")
             for c in FEATURE_COLS] for r in rows]


def train(rows: list[dict], mag_labels: list[float], dir_labels: list[int], *, time_split: float = 0.7) -> dict:
    """기대변동폭 회귀(MAE vs 나이브) + 방향 분류(AUC), 시간순 OOS. 표본 부족 시 보류."""
    n = len(rows)
    if n < 100:
        return {"mag_model": None, "dir_model": None, "n": n, "reason": f"표본 부족(n={n}) — 보류"}
    try:
        import numpy as np
        from lightgbm import LGBMRegressor, LGBMClassifier
        from sklearn.metrics import roc_auc_score, mean_absolute_error
    except Exception as e:
        return {"mag_model": None, "dir_model": None, "n": n, "reason": f"라이브러리 없음: {e}"}
    X = np.array(_matrix(rows), float)
    ym, yd = np.array(mag_labels, float), np.array(dir_labels, int)
    s = int(n * time_split)
    # 변동폭 회귀
    reg = LGBMRegressor(n_estimators=150, learning_rate=0.05, num_leaves=15, min_child_samples=15, verbose=-1)
    reg.fit(X[:s], ym[:s])
    mae = float(mean_absolute_error(ym[s:], reg.predict(X[s:])))
    naive = float(mean_absolute_error(ym[s:], np.full(len(ym[s:]), ym[:s].mean())))   # 평균예측 베이스라인
    # 방향 분류(약한 신호 — 정직)
    dir_auc = None
    dir_model = None
    if yd[:s].sum() >= 8 and yd[s:].sum() >= 3 and len(set(yd[s:].tolist())) >= 2:
        clf = LGBMClassifier(n_estimators=120, learning_rate=0.05, num_leaves=15, min_child_samples=15, verbose=-1)
        clf.fit(X[:s], yd[:s])
        dir_auc = round(float(roc_auc_score(yd[s:], clf.predict_proba(X[s:])[:, 1])), 3)
        dir_model = clf
    return {"mag_model": reg, "dir_model": dir_model, "n": n,
            "mag_mae": round(mae, 4), "mag_mae_naive": round(naive, 4),
            "mag_skill": round(1 - mae / naive, 3) if naive > 0 else None,
            "dir_auc": dir_auc,
            "reason": (f"학습 완료 — 변동폭 MAE {mae:.3f} vs 나이브 {naive:.3f}"
                       f"(skill {round(1-mae/naive,3) if naive>0 else 'na'}), 방향 AUC {dir_auc}")}


def predict(res: dict, rows: list[dict]) -> list[dict]:
    """[{expected_abs_move, p_up}] — mag_model 없으면 빈 예측(None)."""
    out = [{"expected_abs_move": None, "p_up": 0.5} for _ in rows]
    if not rows or not res or res.get("mag_model") is None:
        return out
    try:
        import numpy as np
        X = np.array(_matrix(rows), float)
        mags = res["mag_model"].predict(X)
        ups = res["dir_model"].predict_proba(X)[:, 1] if res.get("dir_model") is not None else [0.5] * len(rows)
        for i in range(len(rows)):
            out[i] = {"expected_abs_move": round(float(mags[i]), 4), "p_up": round(float(ups[i]), 3)}
    except Exception as e:
        logger.warning("주가반응 예측 실패: %s", e)
    return out


# ── 모델 영속화 + 단일종목 추론(라이브 /earnings 배선) ──────────────────────────

def save_model(res: dict, path: Path = MODEL_PATH) -> None:
    if not res or res.get("mag_model") is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"mag_model": res["mag_model"], "dir_model": res.get("dir_model")}, f)
    except Exception as e:
        logger.warning("earnings_move 저장 실패: %s", e)


def load_model(path: Path = MODEL_PATH):
    try:
        if path.exists():
            with open(path, "rb") as f:
                return pickle.load(f)
    except Exception as e:
        logger.warning("earnings_move 로드 실패: %s", e)
    return None


def features_now(ticker: str, *, today: str | None = None, beat_prob=None) -> dict:
    """다음 실적 직전 피처 1행 — 과거 반응 통계 + 최근 모멘텀/변동성 + (G3)beat확률."""
    import datetime as _dt
    from reports import earnings_reaction as er
    from lib.price_utils import fetch_closes, window_feats
    closes = fetch_closes(ticker)
    reactions = er.post_earnings_reactions(ticker, prices=closes) if closes is not None else []
    reactions = [r for r in reactions if r.get("reaction_1d") is not None]
    abs_moves = [abs(r["reaction_1d"]) for r in reactions]
    surprises = [r.get("surprise_pct") for r in reactions]
    drift_hits = [1 if (r.get("surprise_pct") is not None and r.get("drift_5d") is not None
                        and (r["surprise_pct"] > 0) == (r["drift_5d"] > 0)) else 0
                  for r in reactions if r.get("surprise_pct") is not None and r.get("drift_5d") is not None]
    mom = vol = None
    if closes is not None:
        mom, vol = window_feats(closes, today or _dt.date.today().isoformat())
    return {"features": event_features(abs_moves, drift_hits, surprises, mom, vol, beat_prob=beat_prob)}


def predict_for_ticker(ticker: str, res=None, *, today: str | None = None, beat_prob=None):
    """다음 실적 {expected_abs_move, p_up} — 모델 캐시 로드. 없으면 None."""
    res = res if res is not None else load_model()
    if not res or res.get("mag_model") is None:
        return None
    try:
        return predict(res, [features_now(ticker, today=today, beat_prob=beat_prob)])[0]
    except Exception as e:
        logger.debug("move predict_for_ticker 실패 %s: %s", ticker, e)
        return None
