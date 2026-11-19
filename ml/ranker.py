"""ml/ranker.py — GBDT 기반 종목 선택 모델

목표: QQQ 대비 초과수익률(excess return) 예측 → 종목 랭킹 생성

공개 API:
  train_ranker(dataset, n_folds)      → RankerResult (WF 검증 증거 + 전 구간 실전 적합)
  rank_today(mode, top_n)             → pd.DataFrame (랭킹 + 점수)
  RankerResult                        → 모델 + OOS 성능 + feature importance
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fmt

import numpy as np
import pandas as pd

from ml import gbdt_adapters as gbdt
from ml.recommendation_validation import recommendation_gate, _finite

logger = logging.getLogger(__name__)

MODEL_CACHE = Path.home() / "reports" / "ml-cache" / "ranker_model.pkl"
# 게이트 기준선 — 6개월 가격 모멘텀 단일 팩터. 같은 WF 행·같은 북·같은 보정으로 채점한다.
RANKER_BASELINE_FEATURE = "mom_126d"
# 편도 수수료+슬리피지. 리밸런스마다 교체 비율 × 2 × 이 값을 비용으로 차감한다.
RANKER_COST_ONE_WAY = 0.001


# ── 생존편향 페널티 ────────────────────────────────────────────────────────────

def _survivorship_penalty(feats: pd.Series) -> float:
    """52주 고점 근처 + 강한 모멘텀 종목에 페널티 적용.

    근거: NASDAQ100 현재 구성종목 중 최근 편입된 종목은
         최고점 근처에서 편입되는 경향이 있어 모델이 과낙관할 수 있음.

    조건 (둘 다 충족 시):
      - dist_52w_high > -0.05 (52주 고점 5% 이내)
      - mom_125d > 0.30 (125일 수익률 30% 이상)

    Returns:
      0.85 — 페널티 적용 (15% 점수 감소)
      1.00 — 정상
    """
    dist_high = float(feats.get("dist_52w_high", -0.10))
    mom_125d  = float(feats.get("mom_125d", 0.0))
    if dist_high > -0.05 and mom_125d > 0.30:
        return 0.85
    return 1.0


# ── 결과 컨테이너 ─────────────────────────────────────────────────────────────

@dataclass
class RankerResult:
    model:              object
    feature_names:      list[str]
    train_end_date:     str
    oos_ic:             float          # information coefficient (rank corr)
    oos_icir:           float          # IC / std(IC)  — 월별 IC의 안정성
    oos_top_decile_ret: float          # 상위 10% 평균 실현 초과수익
    oos_hit_rate:       float          # 상위 10분위 픽의 양수 초과수익 적중률(모델 성능)
    feature_importance: pd.Series
    meta:               dict = field(default_factory=dict)


def ranker_freshness(result: RankerResult, now: datetime | None = None) -> dict:
    """90-day training/artifact age ceiling; missing or future dates fail closed."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    meta = getattr(result, "meta", {}) or {}
    reasons = []

    def age(value, key):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            days = (now - parsed).total_seconds() / 86400
            if days < 0 or days > 90:
                reasons.append(key)
            return days
        except (TypeError, ValueError):
            reasons.append(key)
            return None

    train_end = getattr(result, "train_end_date", None)
    training_age = age(train_end, "training_stale_or_unknown")
    model_age = age(meta.get("trained_at"), "model_stale_or_unknown")
    stale = bool(reasons)
    ic = getattr(result, "oos_ic", None)
    top = getattr(result, "oos_top_decile_ret", None)
    if not _finite(ic) or ic < 0:
        reasons.append("negative_or_missing_oos_ic")
    if not _finite(top) or top <= 0:
        reasons.append("nonpositive_or_missing_oos_excess")
    return {"train_end_date": train_end, "model_age_days": model_age,
            "training_age_days": training_age, "max_age_days": 90,
            "oos_ic": ic if _finite(ic) else None,
            "oos_top_decile_ret": top if _finite(top) else None,
            "data_coverage": meta.get("data_coverage"), "stale": stale,
            "shadow_only": bool(reasons), "reasons": reasons}


def ranker_gate(result: RankerResult) -> dict:
    """Recompute eligibility from evidence, never trust a cached passed flag."""
    meta = getattr(result, "meta", {}) or {}
    gate = recommendation_gate(meta.get("validation_metrics"), meta.get("validation_baseline"))
    freshness = ranker_freshness(result)
    gate["reasons"].extend(freshness["reasons"])
    gate["passed"] = not gate["reasons"]
    gate["shadow_only"] = not gate["passed"]
    gate["freshness"] = freshness
    return gate


def format_validation_lines(result: RankerResult) -> list[str]:
    """주간 재학습 보고용 — 게이트 증거·같은 행 기준선·미통과 사유."""
    gate = ranker_gate(result)
    metrics = result.meta.get("validation_metrics") or {}
    base = result.meta.get("validation_baseline") or {}
    fresh = gate["freshness"]

    def num(value, digits: int = 4) -> str:
        return f"{float(value):+.{digits}f}" if _finite(value) else "n/a"

    def pct(value) -> str:
        return f"{float(value) * 100:+.2f}%" if _finite(value) else "n/a"

    folds = " / ".join(f"{x:+.3f}" for x in metrics.get("fold_ics", [])) or "n/a"
    return [
        f"WF IC {num(metrics.get('mean_ic'))} (폴드 {folds}) · 기준선 {base.get('feature', 'n/a')} {num(base.get('mean_ic'))}",
        f"상위10% 북(20일): 초과 {pct(metrics.get('top_decile_excess'))} · 비용 {pct(metrics.get('transaction_cost'))}"
        f" · MDD {num(metrics.get('mdd'), 3)} (기준선 {num(base.get('mdd'), 3)})",
        f"Calibration: Brier {num(metrics.get('brier'))} / 기준선 {num(base.get('brier'))}"
        f" · ECE {num(metrics.get('ece'))} / 기준선 {num(base.get('ece'))}",
        f"Clean: IC {num(metrics.get('clean_mean_ic'))} · 순초과 {pct(metrics.get('clean_top_decile_excess'))}"
        f" · {metrics.get('clean_rows', 'n/a')}행",
        f"Freshness: train_end={fresh['train_end_date']} · age_days={fresh['model_age_days']}"
        f" · data coverage: {fresh['data_coverage']}",
        f"Gate: {'통과' if gate['passed'] else 'shadow'} · reasons: {', '.join(gate['reasons']) or 'none'}",
    ]


# ── 학습 ──────────────────────────────────────────────────────────────────────

def _make_ranker_labels(excess: np.ndarray, dates: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    """LGBMRanker용 rank label(0~3 버킷) + group array 생성."""
    df = pd.DataFrame({"excess": excess, "date": dates})
    # 날짜별 4분위 버킷 (0=하위, 3=상위)
    df["label"] = df.groupby("date")["excess"].transform(
        lambda x: pd.qcut(x.rank(method="first"), q=4, labels=[0, 1, 2, 3])
    ).astype(int)
    # stable 정렬 필수 — _fit_gbdt 의 X 재정렬(np.argsort kind="stable")과
    # 동일 날짜 내 행 순서가 일치해야 라벨-피처 정렬이 깨지지 않음
    df = df.sort_values("date", kind="stable")
    groups = df.groupby("date", sort=True).size().values
    return df["label"].values, groups


def _fit_gbdt(X: np.ndarray, y: np.ndarray, dates: pd.Index, feat_names: list[str],
              backend: str, use_ranker: bool):
    """GBDT ranker(일자 그룹 라벨)/regressor 한 번 적합."""
    if use_ranker:
        labels, groups = _make_ranker_labels(y, dates)
        order = np.argsort(dates, kind="stable")        # 라벨·그룹과 같은 날짜순
        model = gbdt.make_ranker(backend=backend, random_state=42)
        gbdt.fit_ranker_model(model, X[order], labels, groups, feat_names, backend)
    else:
        model = gbdt.make_regressor(backend=backend, random_state=42)
        gbdt.fit_regressor_model(model, X, y, feat_names, backend)
    return model


def _clean_flags(dataset: dict, index: pd.MultiIndex) -> np.ndarray | None:
    """원천 스냅샷과 그 일자 봉이 모두 complete 인 (일자, 종목) 행만 True.

    스냅샷이 없으면 None — 품질 증거가 없으니 clean 지표도 만들지 않는다(게이트 fail-closed).
    """
    snapshots = dataset["features"].attrs.get("data_snapshots")
    if not snapshots:
        return None
    complete = {t for t, snap in snapshots.items()
                if str((snap or {}).get("quality", "")).lower() == "complete"}
    bad_bars = {(t, _naive_day(stamp.get("timestamp")))
                for t, snap in snapshots.items()
                for stamp in (snap or {}).get("data_stamps", [])
                if str(stamp.get("quality", "")).lower() != "complete"}
    return np.array([t in complete and (t, _naive_day(d)) not in bad_bars
                     for d, t in zip(index.get_level_values("date"),
                                     index.get_level_values("ticker"))], dtype=bool)


def _decile_book(frame: pd.DataFrame, col: str, horizon: int) -> dict:
    """비중첩 리밸런스(horizon 거래일 간격) 상위 10% 동일가중 북.

    수익은 벤치마크 대비 초과수익, 비용은 교체 비율 × 왕복 RANKER_COST_ONE_WAY.
    MDD 는 비용 차감 초과수익 누적 곡선 기준(0~1).
    """
    gross, costs, hits, prev = [], [], [], None
    for _, fold in frame.groupby("fold", sort=True):
        fold = fold[np.isfinite(fold[col])]
        for day in sorted(fold["date"].unique())[::max(1, horizon)]:
            book = fold[fold["date"] == day]
            if len(book) < 2:
                continue
            k = max(1, int(np.ceil(len(book) * 0.1)))
            top = book.nlargest(k, col)
            pick = set(top["ticker"])
            gross.append(float(top["y"].mean()))
            hits.append(float((top["y"] > 0).mean()))
            turnover = 1.0 if prev is None else 1.0 - len(pick & prev) / k
            costs.append(2.0 * RANKER_COST_ONE_WAY * turnover)
            prev = pick
    if not gross:
        return {}
    net = np.asarray(gross) - np.asarray(costs)
    equity = np.cumprod(np.maximum(1.0 + net, 0.0))
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return {"top_decile_excess": float(np.mean(gross)),
            "transaction_cost": float(np.mean(costs)),
            "net_top_decile_excess": float(np.mean(net)),
            "mdd": float(np.max(1.0 - equity / peak)),
            "top_decile_hit_rate": float(np.mean(hits)),
            "n_rebalances": len(gross)}


def _oos_calibration(frame: pd.DataFrame, col: str) -> dict:
    """P(초과수익>0) 보정 — 앞선 폴드 OOS 의 일자별 백분위→적중 로지스틱을 다음 폴드에 적용.

    첫 폴드는 앞선 OOS 가 없어 채점하지 않는다. 표본·클래스 부족이면 invalid(None) 그대로.
    """
    from sklearn.linear_model import LogisticRegression
    from ml.recommendation_validation import validate_calibration

    data = frame[np.isfinite(frame[col])].copy()
    data["pct"] = data.groupby("date")[col].rank(pct=True) - 0.5
    data["hit"] = (data["y"] > 0).astype(int)
    ys, probs = [], []
    for fold in sorted(data["fold"].unique())[1:]:
        hist, cur = data[data["fold"] < fold], data[data["fold"] == fold]
        if hist["hit"].nunique() < 2:
            return validate_calibration([], [])
        fitted = LogisticRegression().fit(hist[["pct"]].to_numpy(), hist["hit"].to_numpy())
        probs.extend(fitted.predict_proba(cur[["pct"]].to_numpy())[:, 1].tolist())
        ys.extend(cur["hit"].tolist())
    return validate_calibration(ys, probs)


def _fold_mean_ic(frame: pd.DataFrame, col: str) -> float | None:
    ics = []
    for _, fold in frame.groupby("fold", sort=True):
        fold = fold[np.isfinite(fold[col])]
        monthly = _monthly_ic(fold[col].to_numpy(), fold["y"].to_numpy(), pd.DatetimeIndex(fold["date"]))
        if len(monthly):
            ics.append(float(monthly.mean()))
    return float(np.mean(ics)) if ics else None


def _wf_evidence(frame: pd.DataFrame, horizon: int) -> tuple[dict, dict]:
    """WF OOS 행으로 게이트 증거(모델)와 같은 행의 단일 팩터 기준선을 만든다."""
    book = _decile_book(frame, "pred", horizon)
    calibration = _oos_calibration(frame, "pred")
    evidence = {**book, "brier": calibration["brier"], "ece": calibration["ece"],
                "calibration_samples": calibration["n_samples"],
                "cost_one_way": RANKER_COST_ONE_WAY,
                "clean_mean_ic": None, "clean_top_decile_excess": None}
    clean = frame["clean"]
    if clean.map(lambda v: isinstance(v, (bool, np.bool_))).all():
        clean_rows = frame[clean.astype(bool)]
        evidence["clean_rows"] = len(clean_rows)
        if not clean_rows.empty:
            evidence["clean_mean_ic"] = _fold_mean_ic(clean_rows, "pred")
            evidence["clean_top_decile_excess"] = _decile_book(
                clean_rows, "pred", horizon).get("net_top_decile_excess")

    baseline: dict = {"feature": RANKER_BASELINE_FEATURE}
    if np.isfinite(frame["baseline"]).any():
        base_book = _decile_book(frame, "baseline", horizon)
        base_cal = _oos_calibration(frame, "baseline")
        baseline.update(mean_ic=_fold_mean_ic(frame, "baseline"), mdd=base_book.get("mdd"),
                        brier=base_cal["brier"], ece=base_cal["ece"],
                        top_decile_excess=base_book.get("top_decile_excess"),
                        transaction_cost=base_book.get("transaction_cost"))
    return evidence, baseline


def train_ranker(
    dataset: dict,
    *,
    n_folds: int = 4,
    use_ranker: bool = True,
    backend: str = "lightgbm",
) -> RankerResult:
    """Purged walk-forward 로 검증 증거를 만들고, 라벨이 확정된 전 구간으로 실전 모델을 적합.

    검증 수치(oos_*, validation_metrics, validation_baseline)는 모두 WF OOS 에서 온다.
    실전 모델은 같은 설정으로 전 구간을 학습해 train_end_date 가 마지막 라벨일이 된다
    (70% 홀드아웃 분할 모델은 학습 종료가 약 11개월 전이라 90일 신선도를 구조적으로 넘는다).

    Args:
        dataset:     build_ml_dataset() 반환값
        n_folds:     walk-forward 폴드 수
        use_ranker:  True=ranker(lambdarank/pairwise), False=regressor
        backend:     lightgbm(기본 챔피언) 또는 xgboost(챌린저)
    """
    backend = gbdt.normalize_backend(backend)
    features: pd.DataFrame = dataset["features"]
    excess:   pd.Series    = dataset["excess"]
    if features.empty:
        raise ValueError("피처 데이터가 비어있습니다")

    wf = walk_forward_backtest(dataset, n_folds=n_folds, backend=backend, use_ranker=use_ranker)
    baseline = wf.pop("baseline", None) or {}

    X = features.values.astype(float)
    y = excess.values.astype(float)
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
    train_dates = features[valid].index.get_level_values("date")
    feat_names = list(features.columns)
    model = _fit_gbdt(X[valid], y[valid], train_dates, feat_names, backend, use_ranker)

    def metric(key: str) -> float:
        value = wf.get(key)
        return float(value) if _finite(value) else float("nan")

    logger.info(
        "실전 적합 %d행(~%s) | WF IC=%.3f top10%%=%.2f%% 비용=%.2f%%",
        int(valid.sum()), train_dates.max().date(), metric("mean_ic"),
        metric("top_decile_excess") * 100, metric("transaction_cost") * 100,
    )
    return RankerResult(
        model=model,
        feature_names=feat_names,
        train_end_date=str(train_dates.max().date()),
        oos_ic=metric("mean_ic"),
        oos_icir=metric("icir"),
        oos_top_decile_ret=metric("top_decile_excess"),
        oos_hit_rate=metric("top_decile_hit_rate"),
        feature_importance=gbdt.feature_importance(model, feat_names),
        meta={
            "backend": backend,
            "backend_label": gbdt.backend_label(backend),
            "model_type": type(model).__name__,
            "use_ranker": bool(use_ranker),
            "fit_scope": "all_labeled",
            "n_train": int(valid.sum()),
            "n_tickers": features.index.get_level_values("ticker").nunique(),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "data_coverage": {"train_valid_rows": int(valid.sum()),
                              "train_total_rows": len(valid)},
            "validation_metrics": wf,
            "validation_baseline": baseline,
        },
    )


def _monthly_ic(preds: np.ndarray, actuals: np.ndarray, dates: pd.Index) -> pd.Series:
    """월별 rank IC (Spearman correlation) 계산."""
    try:
        from scipy.stats import spearmanr
    except ImportError:
        spearmanr = None

    df = pd.DataFrame({"pred": preds, "actual": actuals, "date": dates})
    df["ym"] = df["date"].dt.to_period("M")

    ics = []
    for _, g in df.groupby("ym"):
        if len(g) < 5:
            continue
        if spearmanr is not None:
            corr, _ = spearmanr(g["pred"], g["actual"])
        else:
            corr = g["pred"].rank(method="average").corr(g["actual"].rank(method="average"))
        if np.isfinite(corr):
            ics.append(corr)
    return pd.Series(ics)


# ── 저장 / 로드 ───────────────────────────────────────────────────────────────

def walk_forward_backtest(
    dataset: dict,
    n_folds: int = 4,
    min_train_months: int = 12,
    embargo: int | None = None,
    backend: str = "lightgbm",
    use_ranker: bool = True,
) -> dict:
    """롤링 Walk-forward 백테스트 — 폴드별 독립 학습 + OOS 평가 (Purged).

    각 폴드: expanding window 학습 → 다음 기간 OOS 평가.
    embargo (기본 forward_days): test 시작 직전 N거래일을 학습에서 제외해
    forward 레이블의 test 구간 누수를 차단.

    Returns:
        fold_ics       — 폴드별 월평균 IC
        fold_top10_rets — 폴드별 상위10분위 평균 수익(풀링 진단값)
        mean_ic        — 전체 평균 IC
        std_ic         — IC 표준편차
        icir           — mean_ic / std_ic
        n_folds        — 실행된 폴드 수
        top_decile_excess / transaction_cost / mdd — 비중첩 리밸런스 상위 10% 북(기간당)
        brier / ece    — 앞선 폴드 OOS 로 보정한 P(초과수익>0)
        clean_mean_ic / clean_top_decile_excess — 스냅샷 complete 행만, 초과수익은 비용 차감
        baseline       — 같은 행에서 RANKER_BASELINE_FEATURE 단일 팩터의 같은 지표
    """
    backend = gbdt.normalize_backend(backend)
    features: pd.DataFrame = dataset["features"]
    excess:   pd.Series    = dataset["excess"]

    dates = features.index.get_level_values("date")
    unique_dates = sorted(dates.unique())
    total_months = (unique_dates[-1] - unique_dates[0]).days // 30

    # 최소 훈련 기간 확보 후 폴드 분할
    min_train_days = min_train_months * 21
    usable = [d for d in unique_dates if (d - unique_dates[0]).days >= min_train_days]
    if len(usable) < n_folds * 21:
        return {"backend": backend, "mean_ic": None, "std_ic": None, "icir": None,
                "n_folds": 0, "fold_ics": [], "fold_top10_rets": []}

    fold_size = len(usable) // n_folds
    fold_ics: list[float] = []
    fold_top10: list[float] = []
    oos_frames: list[pd.DataFrame] = []

    if embargo is None:
        embargo = int(dataset.get("meta", {}).get("forward_days", 20))

    for fold in range(n_folds):
        test_start = usable[fold * fold_size]
        test_end   = usable[min((fold + 1) * fold_size, len(usable)) - 1]

        # Purge: test 시작 전 embargo 거래일은 학습 제외 (레이블 중첩 누수 방지)
        ts_pos     = unique_dates.index(test_start)
        purge_date = unique_dates[max(ts_pos - embargo, 0)]

        train_mask = dates < purge_date
        test_mask  = (dates >= test_start) & (dates <= test_end)

        X_tr = features[train_mask].values.astype(float)
        y_tr = excess[train_mask].values.astype(float)
        X_te = features[test_mask].values.astype(float)
        y_te = excess[test_mask].values.astype(float)

        valid_tr = np.isfinite(X_tr).all(axis=1) & np.isfinite(y_tr)
        valid_te = np.isfinite(X_te).all(axis=1) & np.isfinite(y_te)
        X_tr, y_tr = X_tr[valid_tr], y_tr[valid_tr]
        X_te, y_te = X_te[valid_te], y_te[valid_te]

        # 피처 웜업(52주 롤링 등) NaN 제거 후 기준으로 표본 확인 — 초기 폴드 빈 학습셋 방지
        if len(X_tr) < 500 or len(X_te) < 100:
            continue

        model = _fit_gbdt(X_tr, y_tr, features[train_mask][valid_tr].index.get_level_values("date"),
                          list(features.columns), backend, use_ranker)
        preds = np.asarray(model.predict(X_te)).ravel()

        test_index = features[test_mask][valid_te].index
        test_dates_fold = test_index.get_level_values("date")
        monthly_ics = _monthly_ic(preds, y_te, test_dates_fold)
        if len(monthly_ics):
            fold_ics.append(float(monthly_ics.mean()))
            baseline_col = (features[test_mask][valid_te][RANKER_BASELINE_FEATURE].to_numpy(dtype=float)
                            if RANKER_BASELINE_FEATURE in features.columns else np.full(len(preds), np.nan))
            oos_frames.append(pd.DataFrame({
                "fold": fold, "date": test_dates_fold,
                "ticker": test_index.get_level_values("ticker"),
                "pred": preds, "baseline": baseline_col, "y": y_te,
                "clean": _clean_flags(dataset, test_index),
            }))

        top_mask = preds >= np.percentile(preds, 90)
        if top_mask.any():
            fold_top10.append(float(y_te[top_mask].mean()))

    if not fold_ics:
        return {"backend": backend, "mean_ic": None, "std_ic": None, "icir": None,
                "n_folds": 0, "fold_ics": [], "fold_top10_rets": []}

    ics   = np.array(fold_ics)
    mean  = float(ics.mean())
    std   = float(ics.std()) if len(ics) > 1 else 0.0
    icir  = mean / std if std > 0 else 0.0

    logger.info(
        "Walk-forward %s %d폴드: mean_IC=%.3f  std=%.3f  ICIR=%.2f",
        gbdt.backend_label(backend), len(fold_ics), mean, std, icir,
    )
    horizon = int(dataset.get("meta", {}).get("forward_days", 20))
    evidence, baseline = _wf_evidence(pd.concat(oos_frames, ignore_index=True), horizon)
    return {
        "backend":        backend,
        "mean_ic":        mean,
        "std_ic":         std,
        "icir":           icir,
        "n_folds":        len(fold_ics),
        "fold_ics":       fold_ics,
        "fold_top10_rets": fold_top10,
        **evidence,
        "baseline":       baseline,
        "purged": embargo >= max(1, horizon),
        "embargo": embargo,
        "use_ranker": use_ranker,
    }


def evaluate_ranker_backend(
    dataset: dict,
    *,
    backend: str = "xgboost",
    use_ranker: bool = False,
    n_folds: int = 4,
) -> dict:
    """Evaluate a backend as a shadow challenger without saving/adopting it."""
    backend = gbdt.normalize_backend(backend)
    if not gbdt.backend_available(backend):
        return {
            "backend": backend,
            "backend_label": gbdt.backend_label(backend),
            "available": False,
            "error": f"{gbdt.backend_label(backend)} 미설치",
        }
    try:
        result = train_ranker(dataset, n_folds=n_folds, use_ranker=use_ranker, backend=backend)
        wf = result.meta["validation_metrics"]
        gate = ranker_gate(result)
        return {
            "backend": backend,
            "backend_label": gbdt.backend_label(backend),
            "available": True,
            "use_ranker": bool(use_ranker),
            "model_type": type(result.model).__name__,
            "oos_ic": result.oos_ic,
            "oos_icir": result.oos_icir,
            "oos_top_decile_ret": result.oos_top_decile_ret,
            "oos_hit_rate": result.oos_hit_rate,
            "wf_mean_ic": wf.get("mean_ic"),
            "wf_icir": wf.get("icir"),
            "wf_n_folds": wf.get("n_folds", 0),
            "wf_fold_ics": wf.get("fold_ics", []),
            "gate": gate,
            "freshness": gate["freshness"],
            "shadow_only": not gate["passed"],
        }
    except (gbdt.BackendUnavailable, ImportError) as e:
        return {
            "backend": backend,
            "backend_label": gbdt.backend_label(backend),
            "available": False,
            "error": str(e),
        }


def compare_ranker_backends(
    dataset: dict,
    *,
    backends: tuple[str, ...] = ("lightgbm", "xgboost"),
    use_ranker: bool = False,
    n_folds: int = 4,
    improvement_tol: float = 0.01,
) -> dict:
    """Compare installed GBDT backends on the same dataset without adoption."""
    evaluations = [
        evaluate_ranker_backend(
            dataset,
            backend=backend,
            use_ranker=use_ranker,
            n_folds=n_folds,
        )
        for backend in backends
    ]
    successful = [ev for ev in evaluations if ev.get("available")]

    def score(ev: dict) -> float | None:
        wf_score = ev.get("wf_mean_ic")
        return float(wf_score) if wf_score is not None else ev.get("oos_ic")

    best = None
    for ev in successful:
        ev_score = score(ev)
        if ev_score is None:
            continue
        if best is None or ev_score > score(best):
            best = ev

    champion = next((ev for ev in evaluations if ev.get("backend") == gbdt.normalize_backend(backends[0])), None)
    best_score = score(best) if best else None
    champion_score = score(champion) if champion and champion.get("available") else None
    adopt_candidate = (
        best is not None
        and champion is not None
        and best.get("backend") != champion.get("backend")
        and best_score is not None
        and champion_score is not None
        and best_score >= champion_score + improvement_tol
        and best.get("gate", {}).get("passed") is True
    )

    return {
        "results": evaluations,
        "best_backend": best.get("backend") if best else None,
        "champion_backend": champion.get("backend") if champion else gbdt.normalize_backend(backends[0]),
        "adopt_candidate": bool(adopt_candidate),
        "improvement_tol": improvement_tol,
    }


def format_backend_evaluation(evaluation: dict, champion_wf_ic: float | None = None, tol: float = 0.01) -> str:
    """Compact Korean status line for cron reports."""
    label = evaluation.get("backend_label") or gbdt.backend_label(evaluation.get("backend"))
    if not evaluation.get("available"):
        return f"{label} challenger: 보류 ({evaluation.get('error', '평가 불가')})"

    def fmt_num(value, digits: int = 3) -> str:
        return "n/a" if value is None else f"{float(value):+.{digits}f}"

    wf = evaluation.get("wf_mean_ic")
    suffix = "shadow 유지"
    if champion_wf_ic is not None and wf is not None and float(wf) >= float(champion_wf_ic) + tol:
        suffix = "후보 우위 관찰"
    return (
        f"{label} challenger: OOS IC {fmt_num(evaluation.get('oos_ic'))} · "
        f"WF IC {fmt_num(wf, 4)} · {suffix}"
    )


def _blocks_challenger(champ: RankerResult | None, result: RankerResult, tol: float) -> bool:
    """게이트를 통과한 챔피언만 비교 기준이 된다 — 노화·증거 없는 챔피언은 교체 대상.

    oos_ic 는 양쪽 모두 각자 채택 시점 WF OOS 평균이다. 챔피언을 현재 데이터로 재채점하면
    전 구간 적합 모델에는 학습 표본 재채점이 되어 IC 가 부풀므로 쓰지 않는다.
    """
    if champ is None or not ranker_gate(champ)["passed"]:
        return False
    return result.oos_ic < champ.oos_ic - tol


def save_ranker(result: RankerResult, path: Path = MODEL_CACHE, *, tol: float = .01) -> bool:
    if not ranker_gate(result)["passed"]:
        logger.info("Ranker cache unchanged: validation requires shadow mode")
        return False
    champ = load_ranker(path)
    if champ is None and (path.exists() or path.is_symlink()):
        logger.warning("Ranker cache unchanged: existing artifact could not be loaded")
        return False
    if _blocks_challenger(champ, result, tol):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    from ml._safe_cache import harden_cache_dir
    harden_cache_dir(path.parent)  # 0700 best-effort — 타 사용자 모델 주입 방지
    path.write_bytes(pickle.dumps(result))
    logger.info("모델 저장: %s", path)
    return True


def load_ranker(path: Path = MODEL_CACHE) -> Optional[RankerResult]:
    if not path.exists():
        return None
    # 안전 로더: 심링크·소유자 검증 후 역직렬화(실패 시 None=모델 미스→재학습)
    from ml._safe_cache import safe_unpickle
    return safe_unpickle(path)


def adopt_if_better(result: RankerResult, path: Path = MODEL_CACHE, *,
                    tol: float = 0.01) -> tuple[bool, float | None]:
    """챔피언/챌린저 채택 게이트 — 게이트 통과 + 통과 챔피언보다 WF IC 가 tol 이상 나쁘지 않을 때 저장.

    반환: (채택 여부, 비교에 쓴 챔피언 OOS IC | None).
    """
    champ = load_ranker(path)
    champ_ic = getattr(champ, "oos_ic", None)
    if not ranker_gate(result)["passed"]:
        return False, champ_ic
    if _blocks_challenger(champ, result, tol):
        logger.info("랭커 재학습 보류 — WF IC %.3f < 챔피언 %.3f → 기존 유지", result.oos_ic, champ_ic)
        return False, champ_ic
    return save_ranker(result, path, tol=tol) is not False, champ_ic


# ── 오늘의 랭킹 생성 ──────────────────────────────────────────────────────────

# 횡단면 관측일 허용 시차. 주말·휴장(약 5일)은 같은 세션으로 보고,
# 그 이전 관측은 타 종목 최신일과 섞지 않는다. 벽시계 '오늘'과 비교하지 않는다.
RANK_OBS_MAX_LAG_DAYS = 5


def _naive_day(value) -> pd.Timestamp | None:
    """거래일 비교용 naive 자정. ohlc_utils.to_naive_days 와 같은 벽시계 규칙."""
    try:
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.tz_localize(None).normalize()
    except (TypeError, ValueError, OverflowError):
        return None


def _finite_score(value) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if np.isfinite(score) else None


def _ticker_key(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    key = str(value).strip()
    return key or None


def _ranking_session_date(dates: list[pd.Timestamp]) -> pd.Timestamp | None:
    """다수가 공유하는 가장 늦은 관측일.

    한 종목만 가진 미래 스탬프는 세션을 끌어올리지 않는다. 공유일이 없으면
    (유니버스가 흩어진 퇴화 경우) 가장 늦은 관측일을 세션으로 본다.
    """
    counts: dict[pd.Timestamp, int] = {}
    for day in dates:
        if day is None:
            continue
        counts[day] = counts.get(day, 0) + 1
    if not counts:
        return None
    shared = [day for day, n in counts.items() if n >= 2]
    return max(shared or counts)


def _observation_is_current(
    obs: pd.Timestamp | None,
    session: pd.Timestamp | None,
    *,
    max_lag_days: int = RANK_OBS_MAX_LAG_DAYS,
) -> bool:
    if obs is None or session is None:
        return False
    if obs > session:
        return False
    return int((session - obs).days) <= max_lag_days


def _select_ranking_rows(
    rows: list[dict],
    *,
    session: pd.Timestamp | None = None,
    max_lag_days: int = RANK_OBS_MAX_LAG_DAYS,
) -> list[dict]:
    """스테일·중복·비유한 예측을 랭킹 전에 제거.

    티커 중복은 더 최신 관측을 남긴다(같은 날이면 먼저 온 유한 점수).
    """
    prepared: list[dict] = []
    for row in rows:
        ticker = _ticker_key(row.get("ticker"))
        score = _finite_score(row.get("score"))
        obs = _naive_day(row.get("obs_date"))
        if ticker is None or score is None or obs is None:
            continue
        item = {**row, "ticker": ticker, "score": score, "obs_date": obs.date().isoformat()}
        item["_obs"] = obs
        prepared.append(item)
    if session is None:
        session = _ranking_session_date([row["_obs"] for row in prepared])
    best: dict[str, dict] = {}
    for row in prepared:
        if not _observation_is_current(row["_obs"], session, max_lag_days=max_lag_days):
            continue
        ticker = row["ticker"]
        prev = best.get(ticker)
        if prev is None:
            best[ticker] = row
            continue
        if row["_obs"] > prev["_obs"]:
            best[ticker] = row
    return [{k: v for k, v in row.items() if k != "_obs"} for row in best.values()]


def _scores_from_frame(df: pd.DataFrame | None) -> dict[str, float]:
    """랭킹 프레임 → 티커별 유한 점수. 정렬된 첫 행을 유지하고 중복·비유한 값은 버린다."""
    out: dict[str, float] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for _, row in df.iterrows():
        ticker = _ticker_key(row.get("ticker"))
        score = _finite_score(row.get("score"))
        if ticker is None or score is None or ticker in out:
            continue
        out[ticker] = score
    return out


def _close_on(df: pd.DataFrame, label) -> float | None:
    """피처 관측일과 같은 봉의 종가. 더 늦은 종가로 옛 피처를 포장하지 않는다."""
    if df is None or "Close" not in getattr(df, "columns", []):
        return None
    close = pd.to_numeric(df["Close"], errors="coerce")
    try:
        px = close.loc[label]
    except KeyError:
        return None
    if isinstance(px, pd.Series):
        px = px.dropna()
        if px.empty:
            return None
        px = px.iloc[-1]
    return _finite_score(px)



def rank_today(
    mode: str = "nasdaq100",
    top_n: int = 15,
    retrain: bool = False,
    benchmark_ticker: str = "QQQ",
    cache_path: Path = MODEL_CACHE,
) -> pd.DataFrame:
    """현재 종목 랭킹 생성.

    Args:
        mode:    fetch_universe 모드
        top_n:   상위 N개 반환
        retrain: True면 기존 캐시 무시하고 재학습
        benchmark_ticker: 초과수익·베타 기준 지수(미국 QQQ / 한국 ^KS11). KR 모델 재사용용.
        cache_path: 모델 캐시 경로(KR 모델은 별도 경로).

    Returns:
        DataFrame (ticker, score, rank, obs_date, features...)
        관측일은 벤치마크 최신 유효 종가 기준 −5일~당일만 사용한다. 벤치마크가
        없으면 후보들의 공통 관측일로 대체한다. 비유한 점수·중복 티커는 제외한다.
    """
    from ml.data_pipeline import build_ml_dataset, fetch_prices, build_stock_features, build_fear_greed_proxy

    # 모델 로드 또는 학습
    result = None if retrain else load_ranker(cache_path)
    if result is None:
        logger.info("모델 없음 — 신규 학습 시작 (bench=%s)", benchmark_ticker)
        ds = build_ml_dataset(mode=mode, days=756, forward_days=20, benchmark_ticker=benchmark_ticker)
        result = train_ranker(ds)
        adopted, _ = adopt_if_better(result, cache_path)
        if not adopted:
            result = load_ranker(cache_path)

    if result is None or not ranker_gate(result)["passed"]:
        logger.info("Ranker production scoring disabled: shadow-only evidence")
        return pd.DataFrame(columns=["ticker", "score", "rank"])

    # 오늘 데이터로 예측
    from ml.data_pipeline import fetch_universe, PORTFOLIO_TICKERS
    tickers = fetch_universe(mode)

    prices = fetch_prices(tickers + [benchmark_ticker, "QQQ", "SPY", "^VIX", "HYG", "LQD", "IEF", "TLT"], days=300)
    fg = build_fear_greed_proxy(days=300)
    vix_df = prices.get("^VIX")
    market_feat = fg.to_frame("fg_score")
    if vix_df is not None:
        market_feat["vix"] = vix_df["Close"]
    market_feat = market_feat.ffill()

    bench_df = prices.get(benchmark_ticker)
    qqq_close = bench_df.get("Close") if bench_df is not None else None
    if qqq_close is not None:
        # 학습과 동일하게 지수 다중TF RSI(일/주/월) 시장공통 피처 주입
        try:
            from ml.data_pipeline import index_multitf_rsi
            market_feat = market_feat.join(index_multitf_rsi(qqq_close), how="left").ffill()
        except Exception as e:
            logger.warning("지수 다중TF RSI(추론) 생성 실패: %s", e)

    rows = []
    seen: set[str] = set()
    for ticker in tickers:
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            df = prices.get(ticker)
            if df is None or len(df) < 60:
                continue
            feat = build_stock_features(ticker, df, market_feat, qqq_close=qqq_close)
            if feat is None or getattr(feat, "empty", True):
                continue
            # inf 는 dropna 가 남긴다. 비유한 피처 행을 빼고 남은 완결 행 중 가장 늦은 관측을 쓴다.
            feat_clean = feat.reindex(columns=result.feature_names).replace([np.inf, -np.inf], np.nan).dropna()
            if feat_clean.empty:
                continue
            if not feat_clean.index.is_monotonic_increasing:
                feat_clean = feat_clean.sort_index()
            today_feat = feat_clean.iloc[-1]
            score = _finite_score(result.model.predict(today_feat.to_frame().T)[0])
            if score is None:
                continue
            obs = _naive_day(today_feat.name)
            price = _close_on(df, today_feat.name)
            if obs is None or price is None or price <= 0:
                continue
            # 생존편향 플래그: 52주 고점 근처 + 강한 모멘텀 = 편입 이후 고점 가능성.
            # 감산은 DataFrame 단계에서 횡단면 스케일로 — 곱셈(×0.85)은 lambdarank 음수 점수에서
            # 오히려 값을 키워(부스트) 페널티 대상을 위로 올리는 부호버그였다(감사 확정).
            surv_flag = _survivorship_penalty(today_feat) < 1.0
            try:
                from ml.technical_rating import compute_technical_rating
                tr = compute_technical_rating(df.loc[:today_feat.name])
                tech_rating = tr["summary"]["rating"] if tr else None
            except Exception:
                tech_rating = None
            rows.append({
                "ticker": ticker,
                "score": score,
                "surv_flag": surv_flag,
                "price": price,
                "tech_rating": tech_rating,
                **today_feat.to_dict(),
                "obs_date": obs.date().isoformat(),
            })
        except Exception as e:
            logger.warning("랭킹 관측 스킵 %s: %s", ticker, e)
            continue

    session = None
    if qqq_close is not None:
        bench_px = pd.to_numeric(qqq_close, errors="coerce")
        valid = bench_px.index[np.isfinite(bench_px) & (bench_px > 0)]
        if len(valid):
            session = _naive_day(valid.max())
    kept = _select_ranking_rows(rows, session=session)
    if len(kept) != len(rows):
        logger.info(
            "랭킹 관측 제외 %d/%d (세션 불일치·중복·비유한 점수)",
            len(rows) - len(kept), len(rows),
        )
    if not kept:
        return pd.DataFrame()

    ranking = (
        pd.DataFrame(kept)
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )

    # 생존편향 페널티 — 횡단면 스케일로 감산(점수 부호와 무관하게 항상 하향). 곱셈 부호버그 대체.
    # LGBMRanker 는 점수 스케일이 임의이므로 ±0.15σ, 회귀는 예측수익 단위(0.005)로 환산(펀더멘털 틸트와 동일).
    try:
        s_std = float(ranking["score"].std(ddof=0))
        is_rank_model = gbdt.is_ranker_model(result.model)
        surv_unit = (0.15 * s_std if is_rank_model else 0.005) if (np.isfinite(s_std) and s_std > 0) else 0.0
        flags = ranking.get("surv_flag")
        if surv_unit > 0 and flags is not None:
            pen = flags.fillna(False).astype(bool)
            ranking["surv_penalty"] = pen.map(lambda f: round(-surv_unit, 4) if f else 0.0)
            ranking.loc[pen, "score"] = ranking.loc[pen, "score"] - surv_unit
            ranking = ranking.sort_values("score", ascending=False).reset_index(drop=True)
        else:
            ranking["surv_penalty"] = 0.0
    except Exception as e:
        logger.warning("생존편향 페널티(횡단면) 적용 실패: %s", e)

    # 펀더멘털 틸트: 상위 후보(top_n×2)에 한해 재무 점수(0~100)를 점수에 가산
    # — 50점 중립. 회귀 모델은 예측수익 단위(S급≈+0.4%p), LGBMRanker는 lambdarank
    #   점수 스케일이 임의이므로 횡단면 표준편차 기준(±0.15σ)으로 환산.
    try:
        cand = ranking.head(top_n * 2).copy()
        fund = _fundamental_scores(cand["ticker"].tolist())
        if fund:
            is_rank_model = gbdt.is_ranker_model(result.model)
            if is_rank_model:
                s_std = float(cand["score"].std(ddof=0))
                unit  = 0.15 * s_std if np.isfinite(s_std) and s_std > 0 else 0.0
            else:
                unit = 0.005
            fund_scores = pd.to_numeric(cand["ticker"].map(fund), errors="coerce")
            cand["fund_score"] = fund_scores.where(fund_scores.between(0, 100))
            adj = (cand["fund_score"].fillna(50) - 50) / 50 * unit
            cand["score"] = cand["score"] + adj
            ranking = pd.concat([cand, ranking.iloc[len(cand):]]) \
                        .sort_values("score", ascending=False).reset_index(drop=True)
    except Exception as e:
        logger.warning("펀더멘털 틸트 실패 — 모델 점수만 사용: %s", e)

    ranking["rank"] = range(1, len(ranking) + 1)
    return ranking.head(top_n)


def scores_by_ticker(tickers: list[str] | None = None, *, mode: str = "nasdaq100",
                     benchmark_ticker: str = "QQQ") -> dict[str, float]:
    """티커→모델점수 매핑 (US 선택정책 `ranker` 축 주입용). 실패 시 빈 dict.

    ml/kr_ranker.kr_scores_by_ticker 의 미국판. crons/us_mock_track.py 가
    `hasattr(ranker, "scores_by_ticker")` 로 찾는 **정확한 이름** — 이 함수가 없던
    동안(감사 2026-08-21 발견) US 정책의 최대 가중치 축(w_ranker=0.40)이 단 한 번도
    주입되지 않았고, hasattr 가드라 예외·로그조차 없이 조용히 죽어 있었다.

    tickers 를 주면 그 티커만 필터링해 반환(랭킹은 충분히 넉넉하게 요청).
    """
    try:
        want = [str(t) for t in (tickers or []) if t]
        top_n = max(len(want) * 2, 50) if want else 50
        df = rank_today(mode=mode, top_n=top_n, benchmark_ticker=benchmark_ticker)
        out = _scores_from_frame(df)
        if want:
            keep = set(want)
            out = {k: v for k, v in out.items() if k in keep}
        return out
    except Exception as e:
        logger.warning("US 랭킹 점수 산출 실패: %s", e)
        return {}


FUND_CACHE = Path.home() / "reports" / "ml-cache" / "fundamental_scores.json"


def _fundamental_scores(tickers: list[str], max_age_days: int = 7) -> dict[str, float]:
    """펀더멘털 점수 (0~100) — 7일 파일 캐시, 미보유 종목만 신규 채점."""
    import time
    cache: dict = {}
    try:
        if FUND_CACHE.exists():
            raw = json.loads(FUND_CACHE.read_text())
            if time.time() - raw.get("ts", 0) < max_age_days * 86400:
                cache = raw.get("scores", {})
    except Exception:
        cache = {}

    missing = [t for t in tickers if t not in cache]
    if missing:
        from reports.fundamental_score import score_ticker
        for t in missing:
            try:
                r = score_ticker(t)
                # ETF·조회 실패 등 채점 불가(sections 없음)는 중립 50 처리
                cache[t] = float(r["total_score"]) if r.get("sections") else 50.0
            except Exception:
                cache[t] = 50.0
        try:
            FUND_CACHE.parent.mkdir(parents=True, exist_ok=True)
            FUND_CACHE.write_text(json.dumps({"ts": time.time(), "scores": cache}))
        except Exception:
            pass
    return {t: cache[t] for t in tickers if t in cache}


# ── 텔레그램용 포맷 ───────────────────────────────────────────────────────────

def _ranking_reasons(row: pd.Series) -> str:
    """랭킹 상위 종목의 추천 이유 — 주요 피처 해석."""
    reasons = []
    tech = row.get("tech_rating")
    if isinstance(tech, str) and tech:
        reasons.append(f"기술등급 {tech}")
    ex_mom = row.get("excess_mom_60d")
    if ex_mom is not None and not pd.isna(ex_mom) and ex_mom > 0:
        reasons.append(f"QQQ 대비 +{ex_mom*100:.1f}% (60d)")
    rsi = row.get("rsi_14")
    if rsi is not None and not pd.isna(rsi):
        if rsi < 40:
            reasons.append(f"RSI {rsi:.0f} 과매도권")
        elif rsi > 70:
            reasons.append(f"RSI {rsi:.0f} 과열 주의")
    vs_high = row.get("close_vs_52w_high")
    if vs_high is not None and not pd.isna(vs_high) and vs_high < 0.90:
        reasons.append(f"52주 고점 -{(1-vs_high)*100:.0f}%")
    fund = row.get("fund_score")
    if fund is not None and not pd.isna(fund) and fund != 50:
        reasons.append(f"펀더멘털 {fund:.0f}점")
    return " · ".join(reasons[:3])


def format_ranking_report(ranking: pd.DataFrame, result: RankerResult, detail_top: int = 5) -> str:
    """텔레그램 발송용 랭킹 리포트 포맷.

    상위 detail_top개는 ATR 기반 매매 가이드(권장 매수·목표·손절) 포함.
    """
    # Ranker(lambdarank/pairwise) 점수는 임의 스케일 — %수익률로 표시하면 오해 유발
    is_rank_model = gbdt.is_ranker_model(result.model)
    meta = result.meta or {}
    backend = meta.get("backend") or gbdt.model_backend_name(result.model)
    backend_label = gbdt.backend_label(backend)
    max_abs = float(ranking["score"].abs().max()) if len(ranking) else 1.0

    ic_grade = ("낮음" if abs(result.oos_ic) < 0.03
                else "보통" if abs(result.oos_ic) < 0.06 else "양호")
    lines = [
        f"📈 종목 랭킹 ({backend_label} · QQQ 초과수익 기준)",
        fmt.SEP,
        f"모델 신뢰도 {ic_grade} (OOS IC {result.oos_ic:+.3f}) · 학습 ~{result.train_end_date}",
        fmt.SEP,
    ]
    if is_rank_model:
        lines.append("※ 점수 = 상대순위(스케일 임의 · %수익 아님)")
    for _, row in ranking.iterrows():
        if is_rank_model:
            score_bar = "█" * max(1, min(int(abs(row["score"]) / max_abs * 8), 8)) if max_abs > 0 else ""
            score_str = f"점수 {row['score']:+.3f}"
        else:
            score_bar = "█" * min(int(abs(row["score"]) * 500), 8)
            sign = "+" if row["score"] >= 0 else "-"
            score_str = f"{sign}{abs(row['score'])*100:.2f}%"
        lines.append(f"  {row['rank']:>2}. {row['ticker']:<6}  {score_str}  {score_bar}")
        if row.get("obs_date"):
            lines[-1] += f"  ({row['obs_date']} 종가)"

        # 상위 종목 매매 가이드: ATR(14) 배수 — 목표 +2×ATR / 손절 -1.5×ATR / 매수 -0.5×ATR~현재가
        price = row.get("price")
        atr   = row.get("atr_14")
        if (row["rank"] <= detail_top and price is not None and not pd.isna(price)
                and atr is not None and not pd.isna(atr) and atr > 0):
            lo = price - 1.5 * atr
            hi = price + 2.0 * atr
            # 무엣지 정보 — 처방(목표/손절) 대신 ATR 통계 참고범위
            lines.append(f"      ${price:.2f}  ·  ATR 참고범위 ${lo:.2f}~${hi:.2f}")
            reason = _ranking_reasons(row)
            if reason:
                lines.append(f"      💡 {reason}")

    lines += [
        fmt.SEP,
        "⚠️ 생존편향 — 현재 살아남은 종목만(상폐 제외) · 참고용",
    ]
    return "\n".join(lines)


# ── CLI 진입점 (봇 .venv subprocess 용) ─────────────────────────────────────────
# 봇은 hermes venv(lightgbm 없음)라 /signals rank 를 프로젝트 .venv 의
# `python -m ml.ranker` subprocess 로 실행 → stdout(리포트)만 회수. (hermes venv 불변)
if __name__ == "__main__":
    import argparse
    import sys
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="종목 랭킹 리포트 — stdout 으로 출력")
    ap.add_argument("--mode", default="nasdaq100")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--retrain", action="store_true")
    a = ap.parse_args()

    ranking = rank_today(mode=a.mode, top_n=a.top, retrain=a.retrain)
    result = load_ranker()
    if ranking is None or ranking.empty or result is None:
        print("__RANK_EMPTY__")
        sys.exit(2)
    print(format_ranking_report(ranking, result))
