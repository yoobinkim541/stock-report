#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/deletion_risk.py — 부실 퇴출 사전예측 모델 (Phase A / §D) — ★강화학습 대상.

사용자 요청: "퇴출될 것 같은 기업을 미리 예측 → 회피 + 강화학습으로 발전".

데이터(이 환경): pykrx 무응답 → **marcap 파생 피처만**(시총순위·순위추세·시총추세·모멘텀·
유동성(거래대금)추세·top-N 경계근접). 라벨 = FDR 부실 상폐(distress; M&A·자진 제외) in 향후 K개월.
정직: marcap-only 피처라 재무·수급 피처는 없음 → 예측력 제한적일 수 있음. 백테스트로 엣지 검증
(엣지 없으면 회피만 보수적으로). RL = 예측을 원장에 기록 → 실제 상폐 발생 시 채점·OOS 게이트.

피처 엔지니어링(build_features)은 순수 함수(무네트워크 테스트). 학습셋 조립(build_training_set)은
marcap 로딩. 모델 = LightGBM 이진분류(P(부실퇴출)).
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

FEATURE_COLS = [
    "log_marcap", "rank", "rank_chg_6m", "rank_chg_12m",
    "marcap_chg_6m", "ret_6m", "ret_12m",
    "amount_chg_6m", "log_amount", "near_boundary",
]
BOUNDARY_RANK = 300        # top-300 밖 근접 = 편출/상폐 위험 구간
DEFAULT_HORIZON_M = 12     # 향후 K개월 내 부실퇴출 라벨


def _safe_log(x):
    try:
        x = float(x)
        return math.log(x) if x > 0 else None
    except (TypeError, ValueError):
        return None


def build_features(series: list[dict]) -> list[dict]:
    """한 종목의 월별 시계열(오래된→최신, 각 {date, marcap, rank, close, amount}) → 시점별 피처.

    각 시점 t 피처는 t 이하 데이터만 사용(룩어헤드 없음). 6/12개월 = 6/12 표본(월별 가정).
    반환: [{date, features:{...}}] (충분한 과거가 있는 시점만).
    """
    out = []
    n = len(series)
    for i in range(n):
        row = series[i]
        mc, rank = row.get("marcap"), row.get("rank")
        if mc is None or rank is None:
            continue
        feat = {
            "log_marcap": _safe_log(mc),
            "rank": float(rank),
            "near_boundary": 1.0 if float(rank) >= BOUNDARY_RANK else max(0.0, float(rank) / BOUNDARY_RANK),
            "log_amount": _safe_log(row.get("amount")),
            "rank_chg_6m": None, "rank_chg_12m": None,
            "marcap_chg_6m": None, "ret_6m": None, "ret_12m": None, "amount_chg_6m": None,
        }
        if i >= 6:
            p = series[i - 6]
            if p.get("rank") is not None:
                feat["rank_chg_6m"] = float(rank) - float(p["rank"])      # +면 순위 악화(시총 축소)
            if p.get("marcap"):
                feat["marcap_chg_6m"] = mc / p["marcap"] - 1.0
            if p.get("close") and row.get("close"):
                feat["ret_6m"] = row["close"] / p["close"] - 1.0
            if p.get("amount"):
                feat["amount_chg_6m"] = (row.get("amount", 0) or 0) / p["amount"] - 1.0 if p["amount"] else None
        if i >= 12:
            p = series[i - 12]
            if p.get("rank") is not None:
                feat["rank_chg_12m"] = float(rank) - float(p["rank"])
            if p.get("close") and row.get("close"):
                feat["ret_12m"] = row["close"] / p["close"] - 1.0
        out.append({"date": row.get("date"), "features": feat})
    return out


def label_distress(code: str, date: str, distress_map: dict, *, horizon_m: int = DEFAULT_HORIZON_M) -> int:
    """code 가 date 이후 horizon_m 개월 내 부실 상폐되면 1, 아니면 0.

    distress_map = {code(6자리): {"date": "YYYY-MM-DD", ...}} (kr_market_data.distress_delistings).
    """
    rec = distress_map.get(code)
    if not rec or not rec.get("date"):
        return 0
    try:
        import pandas as pd
        d0 = pd.Timestamp(date)
        dd = pd.Timestamp(rec["date"])
        return 1 if d0 < dd <= d0 + pd.DateOffset(months=horizon_m) else 0
    except Exception:
        return 0


def _to_matrix(rows: list[dict]):
    """[{features:{...}}] → X(결측 nan). lib.ml_utils.rows_to_matrix 위임."""
    from lib.ml_utils import rows_to_matrix
    return rows_to_matrix(rows, FEATURE_COLS)


def train_deletion_model(rows: list[dict], labels: list[int], *, time_split: float = 0.7,
                         horizon_m: int = DEFAULT_HORIZON_M):
    """LightGBM 이진분류 학습 + 시간순 OOS(AUC·precision@상위10%). rows 는 시간순 정렬 가정.

    반환 {"model", "oos_auc", "oos_prec_top", "n", "n_pos", "feature_importance"}.
    표본/양성 부족 시 model=None, 사유 포함.
    """
    n = len(rows)
    n_pos = sum(labels)
    if n < 200 or n_pos < 20:
        return {"model": None, "n": n, "n_pos": n_pos,
                "reason": f"표본 부족(n={n}, pos={n_pos}) — 학습 보류(콜드스타트)"}
    try:
        import numpy as np
        from lightgbm import LGBMClassifier
        from sklearn.metrics import roc_auc_score
    except Exception as e:
        return {"model": None, "n": n, "n_pos": n_pos, "reason": f"라이브러리 없음: {e}"}

    import pandas as pd
    X = np.array(_to_matrix(rows), dtype=float)
    y = np.array(labels, dtype=int)
    split = int(n * time_split)
    # 라벨 호라이즌 퍼지 — split 이전 horizon_m 개월 학습표본 제외: 그 표본의 12M 부실라벨이
    # test 구간 상폐 이벤트로 결정돼 OOS AUC 를 낙관 편향시킨다(감사 확정). 운영 rows 는 시간순·"date" 보유.
    dates = pd.to_datetime([r.get("date") for r in rows], errors="coerce")
    if bool(dates.notna().all()):
        split_date = dates[split]
        purge_start = split_date - pd.DateOffset(months=horizon_m)
        tr_keep = np.asarray(dates[:split] < purge_start)
    else:
        tr_keep = np.ones(split, dtype=bool)   # date 없는 합성/단위테스트 경로 → 퍼지 생략
    Xtr, ytr = X[:split][tr_keep], y[:split][tr_keep]
    Xte, yte = X[split:], y[split:]
    if ytr.sum() < 10 or yte.sum() < 3 or len(set(yte.tolist())) < 2:
        return {"model": None, "n": n, "n_pos": n_pos,
                "reason": f"분할 후 양성 부족(train+={int(ytr.sum())}, test+={int(yte.sum())}) — 보류"}

    scale = max(1.0, (len(ytr) - ytr.sum()) / max(1, ytr.sum()))   # 불균형 보정
    clf = LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15,
                         min_child_samples=20, scale_pos_weight=scale, verbose=-1)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    auc = float(roc_auc_score(yte, proba))
    k = max(1, int(len(yte) * 0.10))
    top_idx = np.argsort(-proba)[:k]
    prec_top = float(yte[top_idx].mean())
    imp = dict(zip(FEATURE_COLS, [int(v) for v in clf.feature_importances_]))
    return {"model": clf, "oos_auc": round(auc, 3), "oos_prec_top": round(prec_top, 3),
            "n": n, "n_pos": int(n_pos), "feature_importance": imp,
            "reason": f"학습 완료 — OOS AUC {auc:.3f}, precision@top10% {prec_top:.3f}"}


def build_training_set(start_year: int, end_year: int, *, market: str = "KOSPI",
                       horizon_m: int = DEFAULT_HORIZON_M, train_universe_n: int = 2000):
    """marcap 다년 → 월별 종목 패널 → 피처+부실라벨 학습셋(시간순). (rows, labels, meta) 반환.

    train_universe_n 은 학습 시 포함할 시총 상위 폭 — 부실 상폐는 대개 소형주라 폭을 넓혀(기본 사실상
    전종목) 양성 라벨 확보. 예측은 별도 유니버스(top-200)에 적용. 무거운 marcap 로딩.
    """
    import pandas as pd
    from providers import kr_market_data as km
    frames = []
    for y in range(start_year, end_year + 1):
        df = km._marcap_year(y)
        if df is None:
            continue
        if market and "Market" in df.columns:
            df = df[df["Market"] == market]
        sub = df[["Code", "Date", "Close", "Marcap", "Amount"]].copy()
        sub["Date"] = pd.to_datetime(sub["Date"])
        sub["ym"] = sub["Date"].dt.to_period("M")
        monthly = sub.sort_values("Date").groupby(["Code", "ym"], as_index=False).last()
        frames.append(monthly)
    if not frames:
        return [], [], []
    panel = pd.concat(frames, ignore_index=True)
    panel["rank"] = panel.groupby("ym")["Marcap"].rank(ascending=False, method="first")
    ever = panel.loc[panel["rank"] <= train_universe_n, "Code"].unique()
    panel = panel[panel["Code"].isin(ever)]
    distress = km.distress_delistings()
    rows, labels, meta = [], [], []
    for code, g in panel.groupby("Code"):
        g = g.sort_values("ym")
        series = [{"date": str(r["ym"].to_timestamp().date()), "marcap": r["Marcap"],
                   "rank": r["rank"], "close": r["Close"], "amount": r["Amount"]}
                  for _, r in g.iterrows()]
        for f in build_features(series):
            rows.append(f)
            labels.append(label_distress(code, f["date"], distress, horizon_m=horizon_m))
            meta.append({"code": code, "date": f["date"]})
    order = sorted(range(len(rows)), key=lambda i: meta[i]["date"])
    return [rows[i] for i in order], [labels[i] for i in order], [meta[i] for i in order]


def predict_risk(model, rows: list[dict]) -> list[float]:
    """현재 시점 피처행들 → P(부실퇴출). model None 이면 전부 0.0(회피 안 함)."""
    if model is None or not rows:
        return [0.0] * len(rows)
    try:
        import numpy as np
        X = np.array(_to_matrix(rows), dtype=float)
        return [float(p) for p in model.predict_proba(X)[:, 1]]
    except Exception as e:
        logger.warning("위험 예측 실패: %s", e)
        return [0.0] * len(rows)
