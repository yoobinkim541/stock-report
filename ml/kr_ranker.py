"""
kr_ranker.py — 한국주식 전용 ML ranker (KOSPI 대비 전방 초과수익 예측).

미국 `ml/ranker.py` 아키텍처(LGBMRanker·purged walk-forward·OOS IC·safe cache)를 그대로
재사용하되, 유니버스=KOSPI_TOP30, 벤치마크=KOSPI(^KS11), 모델 캐시는 별도 경로.
신규 학습 로직 없음 — ranker 의 일반화된 함수에 KR 파라미터만 주입.

★목적함수(아웃퍼폼 vs KOSPI)와 일관: 타깃이 KOSPI 대비 초과수익이므로 ranker 점수가
'코스피 이길 확률/크기' 신호가 된다. 콜드스타트=과거 5년, 이후 원장 누적분으로 주간 재학습.

KR 데이터 한계(조사): sector_id 희소(기본 0)·옵션/13F 없음 — 가격/기술/펀더멘털 위주로 동작.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ml import ranker as _ranker
from ml.data_pipeline import KR_BENCHMARK

logger = logging.getLogger(__name__)

KR_MODEL_CACHE = Path.home() / "reports" / "ml-cache" / "kr_ranker_model.pkl"
KR_MODE = "kr30"


def train_kr_ranker(days: int = 1260, forward_days: int = 20):
    """KR ranker 학습 + 캐시 저장. RankerResult 반환(OOS IC 포함)."""
    from ml.data_pipeline import build_ml_dataset
    ds = build_ml_dataset(mode=KR_MODE, days=days, forward_days=forward_days,
                          benchmark_ticker=KR_BENCHMARK)
    if not len(ds.get("features", [])):
        logger.warning("KR 데이터셋 비어있음 — 학습 생략")
        return None
    result = _ranker.train_ranker(ds)
    _ranker.save_ranker(result, KR_MODEL_CACHE)
    logger.info("KR ranker 학습 완료: OOS IC=%.3f ICIR=%.2f", result.oos_ic, result.oos_icir)
    return result


def load_kr_ranker():
    """캐시된 KR ranker 로드(safe_unpickle). 없으면 None."""
    return _ranker.load_ranker(KR_MODEL_CACHE)


def rank_kr_today(top_n: int = 30, retrain: bool = False):
    """오늘 KR 종목 랭킹(코스피 대비 초과수익 기대 순). 모델 없으면 콜드스타트 학습."""
    return _ranker.rank_today(mode=KR_MODE, top_n=top_n, retrain=retrain,
                              benchmark_ticker=KR_BENCHMARK, cache_path=KR_MODEL_CACHE)


def kr_scores_by_ticker(top_n: int = 30) -> dict[str, float]:
    """티커→모델점수 매핑(정책 점수 산출용). 실패 시 빈 dict."""
    try:
        df = rank_kr_today(top_n=top_n)
        if df is None or df.empty:
            return {}
        return {str(r["ticker"]): float(r["score"]) for _, r in df.iterrows()}
    except Exception as e:
        logger.warning("KR 랭킹 산출 실패: %s", e)
        return {}
