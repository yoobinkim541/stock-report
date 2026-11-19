#!/usr/bin/env python3
"""weekly_ranker_retrain.py — 랭커 주간 재학습 (Purged WF 검증 포함)

daily_ranking은 캐시 모델만 로드하므로 정기 재학습 경로가 없으면 모델이 노화된다.
매주 purged walk-forward 로 게이트 증거(IC·비용 차감 상위10%·MDD·보정·clean·기준선)를 만들고
라벨이 확정된 전 구간으로 실전 모델을 적합한다. 게이트를 통과할 때만 채택, 결과는 텔레그램 보고.

크론 (토요일 03:00 UTC = 12:00 KST — 주말, 시장 휴장):
    0 3 * * 6 uv run python crons/weekly_ranker_retrain.py >> /tmp/weekly_ranker.log 2>&1
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
    logger.info("=== weekly_ranker_retrain 시작 ===")
    try:
        from ml.data_pipeline import build_ml_dataset
        from ml.ranker import (
            adopt_if_better,
            evaluate_ranker_backend,
            format_backend_evaluation,
            format_validation_lines,
            train_ranker,
        )

        ds = build_ml_dataset(mode="nasdaq100", days=756, forward_days=20)
        result = train_ranker(ds, n_folds=4)
        adopted, champ_ic = adopt_if_better(result)
        mean_ic = result.meta["validation_metrics"].get("mean_ic")
        xgb_line = ""
        if os.getenv("RANKER_XGB_CHALLENGER_ENABLED", "1").lower() not in {"0", "false", "no", "off"}:
            xgb_eval = evaluate_ranker_backend(ds, backend="xgboost", use_ranker=False, n_folds=3)
            xgb_line = format_backend_evaluation(xgb_eval, champion_wf_ic=mean_ic)

        try:
            from ml.adaptive.ledger import Ledger
            from reports.recommendation_health import collect, format_report
            recommendation_health = format_report(collect(outcomes=Ledger("entry_signals").read_outcomes()))
        except Exception as exc:
            recommendation_health = f"Recommendation health: unavailable ({type(exc).__name__})"

        champion_text = f"{champ_ic:+.3f}" if champ_ic is not None else "n/a"
        adopt_str = ("채택 ✅" if adopted else f"보류 shadow (챔피언 IC {champion_text} 유지)")
        msg = "\n".join(x for x in [
            "🔄 랭커 주간 재학습 (Purged WF · 챔피언/챌린저)",
            "━━━━━━━━━━━━━━",
            f"→ {adopt_str}",
            *format_validation_lines(result),
            xgb_line,
            recommendation_health,
            "⚠️ WF IC < 0.02 지속 시 DCA 틸트 비중 축소 검토",
        ] if x)
        send_cron_telegram(msg)
        logger.info("재학습·보고 완료 (WF mean IC=%s)", mean_ic)
        return 0
    except Exception as e:
        logger.exception("주간 재학습 실패")
        send_cron_telegram(f"❌ 랭커 주간 재학습 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
