#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/earnings_predictor.py — 실적 서프라이즈 예측 (Phase C / §G3) — ★강화학습 대상.

사용자 요청: "실적발표 얼마 안 남았으면 실적을 예측". 타깃 = P(beat = 서프라이즈>0).

★정직: 어닝 *방향* 예측은 본질적으로 고난도. 문서화된 실제 엣지 = **추정치 리비전 모멘텀**
(`eps_revisions`)·**서프라이즈 지속성**(beat 종목이 또 beat). 단 리비전 모멘텀은 point-in-time
스냅샷(earnings_snapshots.jsonl, Phase 1에서 막 적재 시작)이 쌓여야 학습 가능 → 현재는 yfinance
과거 서프라이즈·모멘텀으로 학습 가능한 만큼만(엣지 약할 수 있음, 스냅샷 축적 후 리비전 피처로 강화).

event_features 는 순수(무네트워크 테스트). build_training_set 은 yfinance/earnings_data 사용.
"""
from __future__ import annotations

import logging
import os
import pickle
import statistics
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.path.expanduser("~/reports/ml-cache/earnings_predictor.pkl"))

FEATURE_COLS = ["prior_n", "prior_surprise_mean", "prior_surprise_std", "prior_beat_rate",
                "last_surprise", "mom_20d", "vol_20d", "revision_momentum"]


def event_features(prior_surprises: list, mom_20d=None, vol_20d=None, revision_momentum=None) -> dict:
    """한 실적 이벤트의 피처 — 직전(prior) 서프라이즈들 + 실적 전 모멘텀/변동성(룩어헤드 없음)."""
    ps = [s for s in (prior_surprises or []) if s is not None]
    n = len(ps)
    return {
        "prior_n": float(n),
        "prior_surprise_mean": round(statistics.mean(ps), 3) if n else None,
        "prior_surprise_std": round(statistics.pstdev(ps), 3) if n >= 2 else None,
        "prior_beat_rate": round(sum(1 for s in ps if s > 0) / n, 3) if n else None,
        "last_surprise": ps[-1] if n else None,
        "mom_20d": mom_20d,
        "vol_20d": vol_20d,
        "revision_momentum": revision_momentum,   # 스냅샷 축적 후 채워짐(현재 대개 None)
    }


def _price_feats(closes, event_date):
    """lib.price_utils.window_feats 위임 (행위 동일 — 실적일 직전 21거래일 모멘텀·변동성)."""
    from lib.price_utils import window_feats
    return window_feats(closes, event_date)


def build_training_set(tickers: list[str], *, min_prior: int = 3, limit: int = 20):
    """yfinance 과거 서프라이즈 + 가격 → (rows, labels, meta). label=beat(서프라이즈>0). 시간순.

    각 이벤트는 직전 서프라이즈 min_prior 개 이상일 때만 포함(워밍업).
    """
    from providers import earnings_data as ed
    from lib.price_utils import fetch_closes

    rows, labels, meta = [], [], []
    for tk in tickers:
        try:
            hist = ed.earnings_history(tk, limit=limit)        # 최신순
        except Exception:
            hist = []
        hist = [h for h in hist if h.get("surprise_pct") is not None]
        hist = sorted(hist, key=lambda h: h["date"])           # 시간순
        if len(hist) <= min_prior:
            continue
        closes = fetch_closes(tk)
        for i in range(min_prior, len(hist)):
            ev = hist[i]
            prior = [hist[j]["surprise_pct"] for j in range(i)]
            mom, vol = _price_feats(closes, ev["date"]) if closes is not None else (None, None)
            rows.append({"features": event_features(prior, mom, vol)})
            labels.append(1 if ev["surprise_pct"] > 0 else 0)
            meta.append({"ticker": tk, "date": ev["date"]})
    order = sorted(range(len(rows)), key=lambda i: meta[i]["date"])
    return [rows[i] for i in order], [labels[i] for i in order], [meta[i] for i in order]


def _matrix(rows):
    from lib.ml_utils import rows_to_matrix
    return rows_to_matrix(rows, FEATURE_COLS)


def train(rows: list[dict], labels: list[int], *, time_split: float = 0.7) -> dict:
    """LightGBM 이진분류(beat 예측) + 시간순 OOS AUC. 표본 부족 시 보류(콜드스타트)."""
    n, n_pos = len(rows), sum(labels)
    if n < 100 or n_pos < 15 or (n - n_pos) < 15:
        return {"model": None, "n": n, "n_pos": n_pos,
                "reason": f"표본 부족(n={n}, beat={n_pos}) — 보류"}
    try:
        import numpy as np
        from lightgbm import LGBMClassifier
        from sklearn.metrics import roc_auc_score
    except Exception as e:
        return {"model": None, "n": n, "reason": f"라이브러리 없음: {e}"}
    X, y = np.array(_matrix(rows), float), np.array(labels, int)
    s = int(n * time_split)
    if y[:s].sum() < 8 or y[s:].sum() < 3 or len(set(y[s:].tolist())) < 2:
        return {"model": None, "n": n, "n_pos": int(n_pos), "reason": "분할 후 클래스 부족 — 보류"}
    clf = LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=15,
                         min_child_samples=15, verbose=-1)
    clf.fit(X[:s], y[:s])
    auc = float(roc_auc_score(y[s:], clf.predict_proba(X[s:])[:, 1]))
    imp = dict(zip(FEATURE_COLS, [int(v) for v in clf.feature_importances_]))
    return {"model": clf, "oos_auc": round(auc, 3), "n": n, "n_pos": int(n_pos),
            "base_rate": round(n_pos / n, 3), "feature_importance": imp,
            "reason": f"학습 완료 — OOS AUC {auc:.3f} (beat base rate {n_pos/n:.2f})"}


def predict_beat(model, rows: list[dict]) -> list[float]:
    """P(beat) 예측. model None → base 0.5(중립)."""
    if model is None or not rows:
        return [0.5] * len(rows)
    try:
        import numpy as np
        return [float(p) for p in model.predict_proba(np.array(_matrix(rows), float))[:, 1]]
    except Exception:
        return [0.5] * len(rows)


# ── 모델 영속화 + 단일종목 추론(라이브 /earnings 배선) ──────────────────────────

def save_model(model, path: Path = MODEL_PATH) -> None:
    if model is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(model, f)
    except Exception as e:
        logger.warning("earnings_predictor 저장 실패: %s", e)


def load_model(path: Path = MODEL_PATH):
    try:
        if path.exists():
            from ml._safe_cache import safe_unpickle   # 심링크·소유자 검증(캐시 스왑 RCE 방어) — 타 ml 로더 공용
            return safe_unpickle(path)
    except Exception as e:
        logger.warning("earnings_predictor 로드 실패: %s", e)
    return None


def features_now(ticker: str, *, today: str | None = None) -> dict:
    """다음 실적 직전 시점 피처 1행 — 전체 과거 서프라이즈를 prior, 최근 모멘텀/변동성, 리비전 모멘텀."""
    import datetime as _dt
    from providers import earnings_data as ed
    hist = sorted([h for h in ed.earnings_history(ticker, limit=20) if h.get("surprise_pct") is not None],
                  key=lambda h: h["date"])
    prior = [h["surprise_pct"] for h in hist]
    mom = vol = rev = None
    from lib.price_utils import fetch_closes, window_feats
    c = fetch_closes(ticker, period="3mo")
    if c is not None:
        mom, vol = window_feats(c, today or _dt.date.today().isoformat())
    try:
        rev = ed.consensus(ticker).get("revision_momentum")
    except Exception:
        pass
    return {"features": event_features(prior, mom, vol, revision_momentum=rev)}


def predict_for_ticker(ticker: str, model=None, *, today: str | None = None):
    """다음 실적 P(beat) — 모델 캐시 로드. 모델/데이터 없으면 None."""
    model = model if model is not None else load_model()
    if model is None:
        return None
    try:
        return predict_beat(model, [features_now(ticker, today=today)])[0]
    except Exception as e:
        logger.debug("predict_for_ticker 실패 %s: %s", ticker, e)
        return None
