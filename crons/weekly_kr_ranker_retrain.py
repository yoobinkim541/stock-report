#!/usr/bin/env python3
"""weekly_kr_ranker_retrain.py — 한국주식 ranker 주간 재학습 (Purged WF).

KR 모의 페이퍼트레이딩 정책의 가치모델(KOSPI 대비 초과수익 예측). 캐시 모델은 노화하므로
매주 purged walk-forward 게이트 증거 + 전 구간 실전 적합, 게이트 통과 시에만 채택.
(KR ranker 는 US ranker 아키텍처 재사용 — kr_ranker.py.)

크론 (토요일 03:30 UTC = 12:30 KST — 주말, 시장 휴장; US ranker 03:00 직후):
    30 3 * * 6 uv run python crons/weekly_kr_ranker_retrain.py >> /tmp/weekly_kr_ranker.log 2>&1
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.cron_common import send_cron_telegram

from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)




def main() -> int:
    logger.info("=== weekly_kr_ranker_retrain 시작 ===")
    try:
        from ml.data_pipeline import build_ml_dataset, KR_BENCHMARK
        from ml.ranker import (
            adopt_if_better,
            evaluate_ranker_backend,
            format_backend_evaluation,
            format_validation_lines,
            train_ranker,
        )
        from ml.kr_ranker import KR_MODE, KR_MODEL_CACHE

        ds = build_ml_dataset(mode=KR_MODE, days=756, forward_days=20, benchmark_ticker=KR_BENCHMARK)
        if not len(ds.get("features", [])):
            logger.warning("KR 데이터셋 비어있음 — 재학습 생략")
            return 0
        result = train_ranker(ds, n_folds=4)
        adopted, champ_ic = adopt_if_better(result, KR_MODEL_CACHE)
        mean_ic = result.meta["validation_metrics"].get("mean_ic")
        xgb_line = ""
        xgb_enabled = os.getenv(
            "KR_RANKER_XGB_CHALLENGER_ENABLED",
            os.getenv("RANKER_XGB_CHALLENGER_ENABLED", "1"),
        ).lower() not in {"0", "false", "no", "off"}
        if xgb_enabled:
            xgb_eval = evaluate_ranker_backend(ds, backend="xgboost", use_ranker=False, n_folds=3)
            xgb_line = format_backend_evaluation(xgb_eval, champion_wf_ic=mean_ic)

        champion_text = f"{champ_ic:+.3f}" if champ_ic is not None else "n/a"
        adopt_str = ("채택 ✅" if adopted else f"보류 ⏸️ (챔피언 IC {champion_text} 유지)")
        msg = "\n".join(x for x in [
            "🇰🇷 KR 랭커 주간 재학습 (Purged WF · vs KOSPI · 챔피언/챌린저)",
            "━━━━━━━━━━━━━━",
            f"→ {adopt_str}",
            *format_validation_lines(result),
            xgb_line,
            "⚠️ KR 데이터 한계(섹터/옵션/13F 부족) — IC 변동 클 수 있음",
        ] if x)
        logger.info(msg)
        send_cron_telegram(msg)
        return 0
    except Exception as e:
        logger.exception("KR 랭커 재학습 실패")
        send_cron_telegram(f"⚠️ KR 랭커 주간 재학습 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
