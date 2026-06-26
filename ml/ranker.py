"""ml/ranker.py — LightGBM 기반 종목 선택 모델

목표: QQQ 대비 초과수익률(excess return) 예측 → 종목 랭킹 생성

공개 API:
  train_ranker(dataset, train_frac)   → RankerResult
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

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_CACHE = Path.home() / "reports" / "ml-cache" / "ranker_model.pkl"


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
    oos_hit_rate:       float          # 양수 초과수익 비율
    feature_importance: pd.Series
    meta:               dict = field(default_factory=dict)


# ── 학습 ──────────────────────────────────────────────────────────────────────

def _make_ranker_labels(excess: np.ndarray, dates: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    """LGBMRanker용 rank label(0~3 버킷) + group array 생성."""
    df = pd.DataFrame({"excess": excess, "date": dates})
    # 날짜별 4분위 버킷 (0=하위, 3=상위)
    df["label"] = df.groupby("date")["excess"].transform(
        lambda x: pd.qcut(x.rank(method="first"), q=4, labels=[0, 1, 2, 3])
    ).astype(int)
    # stable 정렬 필수 — train_ranker의 X_train 재정렬(np.argsort kind="stable")과
    # 동일 날짜 내 행 순서가 일치해야 라벨-피처 정렬이 깨지지 않음
    df = df.sort_values("date", kind="stable")
    groups = df.groupby("date", sort=True).size().values
    return df["label"].values, groups


def train_ranker(
    dataset: dict,
    train_frac: float = 0.7,
    use_ranker: bool = True,
) -> RankerResult:
    """시계열 분할로 LGBMRanker(기본) 또는 LGBMRegressor 학습, OOS 성능 평가.

    Args:
        dataset:     build_ml_dataset() 반환값
        train_frac:  학습 기간 비율 (나머지는 OOS 평가)
        use_ranker:  True=LGBMRanker(lambdarank), False=LGBMRegressor

    Returns:
        RankerResult
    """
    import lightgbm as lgb

    features: pd.DataFrame = dataset["features"]
    excess:   pd.Series    = dataset["excess"]

    if features.empty:
        raise ValueError("피처 데이터가 비어있습니다")

    # 날짜 기준 시계열 분할 (Purged: 분할 직전 embargo 거래일은 학습에서 제외 —
    # forward 레이블이 test 구간을 내다보는 데이터 누수 방지)
    embargo = int(dataset.get("meta", {}).get("forward_days", 20))
    dates = features.index.get_level_values("date")
    unique_dates = sorted(dates.unique())
    split_idx = int(len(unique_dates) * train_frac)
    split_date = unique_dates[split_idx]
    purge_date = unique_dates[max(split_idx - embargo, 0)]

    train_mask = dates < purge_date
    test_mask  = dates >= split_date

    X_train = features[train_mask].values.astype(float)
    y_train = excess[train_mask].values.astype(float)
    X_test  = features[test_mask].values.astype(float)
    y_test  = excess[test_mask].values.astype(float)

    # NaN 제거
    train_valid = np.isfinite(X_train).all(axis=1) & np.isfinite(y_train)
    test_valid  = np.isfinite(X_test).all(axis=1) & np.isfinite(y_test)
    X_train, y_train = X_train[train_valid], y_train[train_valid]
    X_test,  y_test  = X_test[test_valid],  y_test[test_valid]
    train_dates = features[train_mask][train_valid].index.get_level_values("date")

    logger.info("학습: %d행 | OOS: %d행 | 분할일: %s | 모델: %s",
                len(X_train), len(X_test), split_date.date(),
                "LGBMRanker" if use_ranker else "LGBMRegressor")

    feat_names = list(features.columns)

    if use_ranker:
        labels, groups = _make_ranker_labels(y_train, train_dates)
        # X_train도 날짜 순서로 재정렬
        date_order = np.argsort(train_dates, kind="stable")
        X_train_sorted = X_train[date_order]

        model = lgb.LGBMRanker(
            objective="lambdarank",
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            min_child_samples=5,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1,
            n_jobs=-1,
        )
        model.fit(X_train_sorted, labels, group=groups, feature_name=feat_names)
    else:
        model = lgb.LGBMRegressor(
            objective="regression",
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1,
            n_jobs=-1,
        )
        model.fit(X_train, y_train, feature_name=feat_names)

    # OOS 예측
    preds = model.predict(X_test)

    # 성능 지표 — 월별 IC (rank correlation)
    test_dates = features[test_mask][test_valid].index.get_level_values("date")
    ic_series = _monthly_ic(preds, y_test, test_dates)
    oos_ic   = float(ic_series.mean()) if len(ic_series) > 0 else 0.0
    oos_icir = float(ic_series.mean() / ic_series.std()) if len(ic_series) > 1 else 0.0

    # 상위 10분위 실현 초과수익
    top_mask = preds >= np.percentile(preds, 90)
    oos_top_decile_ret = float(y_test[top_mask].mean()) if top_mask.any() else 0.0

    # hit rate
    oos_hit_rate = float((y_test > 0).mean())

    # feature importance
    fi = pd.Series(model.feature_importances_, index=feat_names, name="importance")
    fi = fi.sort_values(ascending=False)

    logger.info(
        "OOS IC=%.3f  ICIR=%.2f  top10%%=%.2f%%  hit=%.1f%%",
        oos_ic, oos_icir, oos_top_decile_ret * 100, oos_hit_rate * 100,
    )

    return RankerResult(
        model=model,
        feature_names=feat_names,
        train_end_date=str(split_date.date()),
        oos_ic=oos_ic,
        oos_icir=oos_icir,
        oos_top_decile_ret=oos_top_decile_ret,
        oos_hit_rate=oos_hit_rate,
        feature_importance=fi,
        meta={
            "n_train": int(train_valid.sum()),
            "n_test":  int(test_valid.sum()),
            "n_tickers": features.index.get_level_values("ticker").nunique(),
            "trained_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _monthly_ic(preds: np.ndarray, actuals: np.ndarray, dates: pd.Index) -> pd.Series:
    """월별 rank IC (Spearman correlation) 계산."""
    from scipy.stats import spearmanr

    df = pd.DataFrame({"pred": preds, "actual": actuals, "date": dates})
    df["ym"] = df["date"].dt.to_period("M")

    ics = []
    for _, g in df.groupby("ym"):
        if len(g) < 5:
            continue
        corr, _ = spearmanr(g["pred"], g["actual"])
        if np.isfinite(corr):
            ics.append(corr)
    return pd.Series(ics)


# ── 저장 / 로드 ───────────────────────────────────────────────────────────────

def walk_forward_backtest(
    dataset: dict,
    n_folds: int = 4,
    min_train_months: int = 12,
    embargo: int | None = None,
) -> dict:
    """롤링 Walk-forward 백테스트 — 폴드별 독립 학습 + OOS 평가 (Purged).

    각 폴드: expanding window 학습 → 다음 기간 OOS 평가.
    embargo (기본 forward_days): test 시작 직전 N거래일을 학습에서 제외해
    forward 레이블의 test 구간 누수를 차단.

    Returns:
        fold_ics       — 폴드별 월평균 IC
        fold_top10_rets — 폴드별 상위10분위 평균 수익
        mean_ic        — 전체 평균 IC
        std_ic         — IC 표준편차
        icir           — mean_ic / std_ic
        n_folds        — 실행된 폴드 수
    """
    import lightgbm as lgb
    from scipy.stats import spearmanr

    features: pd.DataFrame = dataset["features"]
    excess:   pd.Series    = dataset["excess"]

    dates = features.index.get_level_values("date")
    unique_dates = sorted(dates.unique())
    total_months = (unique_dates[-1] - unique_dates[0]).days // 30

    # 최소 훈련 기간 확보 후 폴드 분할
    min_train_days = min_train_months * 21
    usable = [d for d in unique_dates if (d - unique_dates[0]).days >= min_train_days]
    if len(usable) < n_folds * 21:
        return {"mean_ic": None, "std_ic": None, "icir": None,
                "n_folds": 0, "fold_ics": [], "fold_top10_rets": []}

    fold_size = len(usable) // n_folds
    fold_ics: list[float] = []
    fold_top10: list[float] = []

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

        model = lgb.LGBMRegressor(
            n_estimators=200, num_leaves=31, learning_rate=0.05,
            min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.1, reg_lambda=0.1, random_state=42, verbose=-1, n_jobs=-1,
        )
        model.fit(X_tr, y_tr, feature_name=list(features.columns))
        preds = model.predict(features[test_mask][valid_te])

        test_dates_fold = features[test_mask][valid_te].index.get_level_values("date")
        monthly_ics = _monthly_ic(preds, y_te, test_dates_fold)
        if len(monthly_ics):
            fold_ics.append(float(monthly_ics.mean()))

        top_mask = preds >= np.percentile(preds, 90)
        if top_mask.any():
            fold_top10.append(float(y_te[top_mask].mean()))

    if not fold_ics:
        return {"mean_ic": None, "std_ic": None, "icir": None,
                "n_folds": 0, "fold_ics": [], "fold_top10_rets": []}

    ics   = np.array(fold_ics)
    mean  = float(ics.mean())
    std   = float(ics.std()) if len(ics) > 1 else 0.0
    icir  = mean / std if std > 0 else 0.0

    logger.info(
        "Walk-forward %d폴드: mean_IC=%.3f  std=%.3f  ICIR=%.2f",
        len(fold_ics), mean, std, icir,
    )
    return {
        "mean_ic":        mean,
        "std_ic":         std,
        "icir":           icir,
        "n_folds":        len(fold_ics),
        "fold_ics":       fold_ics,
        "fold_top10_rets": fold_top10,
    }


def save_ranker(result: RankerResult, path: Path = MODEL_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from ml._safe_cache import harden_cache_dir
    harden_cache_dir(path.parent)  # 0700 best-effort — 타 사용자 모델 주입 방지
    path.write_bytes(pickle.dumps(result))
    logger.info("모델 저장: %s", path)


def load_ranker(path: Path = MODEL_CACHE) -> Optional[RankerResult]:
    if not path.exists():
        return None
    # 안전 로더: 심링크·소유자 검증 후 역직렬화(실패 시 None=모델 미스→재학습)
    from ml._safe_cache import safe_unpickle
    return safe_unpickle(path)


# ── 오늘의 랭킹 생성 ──────────────────────────────────────────────────────────

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
        DataFrame (ticker, score, rank, features...)
    """
    from ml.data_pipeline import build_ml_dataset, fetch_prices, build_stock_features, build_fear_greed_proxy

    # 모델 로드 또는 학습
    result = None if retrain else load_ranker(cache_path)
    if result is None:
        logger.info("모델 없음 — 신규 학습 시작 (bench=%s)", benchmark_ticker)
        ds = build_ml_dataset(mode=mode, days=756, forward_days=20, benchmark_ticker=benchmark_ticker)
        result = train_ranker(ds)
        save_ranker(result, cache_path)

    # 오늘 데이터로 예측
    universe = result.meta.get("tickers", None)
    from ml.data_pipeline import fetch_universe, PORTFOLIO_TICKERS
    tickers = fetch_universe(mode)

    prices = fetch_prices(tickers + [benchmark_ticker, "QQQ", "SPY", "^VIX", "HYG", "LQD", "IEF", "TLT"], days=300)
    fg = build_fear_greed_proxy(days=300)
    import yfinance as yf
    vix_df = prices.get("^VIX")
    market_feat = fg.to_frame("fg_score")
    if vix_df is not None:
        market_feat["vix"] = vix_df["Close"]
    market_feat = market_feat.ffill()

    bench_df = prices.get(benchmark_ticker)
    qqq_close = bench_df.get("Close") if bench_df is not None else None

    rows = []
    for ticker in tickers:
        df = prices.get(ticker)
        if df is None or len(df) < 60:
            continue
        feat = build_stock_features(ticker, df, market_feat, qqq_close=qqq_close)
        if feat.empty:
            continue
        feat_clean = feat.dropna()
        if feat_clean.empty:
            continue
        today_feat = feat_clean.iloc[-1].reindex(result.feature_names)
        if today_feat.isna().any():
            continue
        score = float(result.model.predict(today_feat.to_frame().T)[0])
        # 생존편향 페널티: 52주 고점 근처 + 강한 모멘텀 = 편입 이후 고점 가능성
        penalty = _survivorship_penalty(today_feat)
        score   = score * penalty
        # TradingView식 기술등급 (참고 표시용 — 점수에는 미반영)
        try:
            from ml.technical_rating import compute_technical_rating
            tr = compute_technical_rating(df)
            tech_rating = tr["summary"]["rating"] if tr else None
        except Exception:
            tech_rating = None

        rows.append({"ticker": ticker, "score": score,
                     "surv_penalty": round(penalty, 3),
                     "price": float(df["Close"].dropna().iloc[-1]),
                     "tech_rating": tech_rating,
                     **today_feat.to_dict()})

    if not rows:
        return pd.DataFrame()

    ranking = (
        pd.DataFrame(rows)
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )

    # 펀더멘털 틸트: 상위 후보(top_n×2)에 한해 재무 점수(0~100)를 점수에 가산
    # — 50점 중립. 회귀 모델은 예측수익 단위(S급≈+0.4%p), LGBMRanker는 lambdarank
    #   점수 스케일이 임의이므로 횡단면 표준편차 기준(±0.15σ)으로 환산.
    try:
        cand = ranking.head(top_n * 2).copy()
        fund = _fundamental_scores(cand["ticker"].tolist())
        if fund:
            is_rank_model = type(result.model).__name__ == "LGBMRanker"
            if is_rank_model:
                s_std = float(cand["score"].std(ddof=0))
                unit  = 0.15 * s_std if np.isfinite(s_std) and s_std > 0 else 0.0
            else:
                unit = 0.005
            cand["fund_score"] = cand["ticker"].map(fund)
            adj = (cand["fund_score"].fillna(50) - 50) / 50 * unit
            cand["score"] = cand["score"] + adj
            ranking = pd.concat([cand, ranking.iloc[len(cand):]]) \
                        .sort_values("score", ascending=False).reset_index(drop=True)
    except Exception as e:
        logger.warning("펀더멘털 틸트 실패 — 모델 점수만 사용: %s", e)

    ranking["rank"] = range(1, len(ranking) + 1)
    return ranking.head(top_n)


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
    # LGBMRanker(lambdarank) 점수는 임의 스케일 — %수익률로 표시하면 오해 유발
    is_rank_model = type(result.model).__name__ == "LGBMRanker"
    max_abs = float(ranking["score"].abs().max()) if len(ranking) else 1.0

    lines = [
        "📈 종목 랭킹 (LightGBM, QQQ 초과수익 기준)",
        "━━━━━━━━━━━━━━",
        f"학습 기간: ~ {result.train_end_date}",
        f"OOS IC: {result.oos_ic:+.3f}  |  ICIR: {result.oos_icir:.2f}",
        f"상위10% 초과수익: {result.oos_top_decile_ret*100:+.1f}%",
        "━━━━━━━━━━━━━━",
    ]
    for _, row in ranking.iterrows():
        if is_rank_model:
            score_bar = "█" * max(1, min(int(abs(row["score"]) / max_abs * 8), 8)) if max_abs > 0 else ""
            score_str = f"점수 {row['score']:+.3f}"
        else:
            score_bar = "█" * min(int(abs(row["score"]) * 500), 8)
            sign = "+" if row["score"] >= 0 else "-"
            score_str = f"{sign}{abs(row['score'])*100:.2f}%"
        lines.append(f"  {row['rank']:>2}. {row['ticker']:<6}  {score_str}  {score_bar}")

        # 상위 종목 매매 가이드: ATR(14) 배수 — 목표 +2×ATR / 손절 -1.5×ATR / 매수 -0.5×ATR~현재가
        price = row.get("price")
        atr   = row.get("atr_14")
        if (row["rank"] <= detail_top and price is not None and not pd.isna(price)
                and atr is not None and not pd.isna(atr) and atr > 0):
            buy_lo = price - 0.5 * atr
            target = price + 2.0 * atr
            stop   = price - 1.5 * atr
            lines.append(f"      현재 ${price:.2f} | 매수 ${buy_lo:.2f}~${price:.2f} | 목표 ${target:.2f} | 손절 ${stop:.2f}")
            reason = _ranking_reasons(row)
            if reason:
                lines.append(f"      💡 {reason}")

    lines += [
        "━━━━━━━━━━━━━━",
        f"⚠️ survivorship bias 있음 (현재 구성종목 기준)",
    ]
    return "\n".join(lines)
