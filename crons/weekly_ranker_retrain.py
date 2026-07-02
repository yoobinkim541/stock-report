#!/usr/bin/env python3
"""weekly_ranker_retrain.py — 랭커 주간 재학습 (Purged WF 검증 포함)

daily_ranking은 캐시 모델만 로드하므로 정기 재학습 경로가 없으면 모델이 노화된다.
매주 재학습 + purged walk-forward로 정직한 OOS IC를 측정해 텔레그램 보고.

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
        from ml.ranker import train_ranker, adopt_if_better, walk_forward_backtest

        ds = build_ml_dataset(mode="nasdaq100", days=756, forward_days=20)
        result = train_ranker(ds)
        adopted, champ_ic = adopt_if_better(result, dataset=ds)   # 챔피언/챌린저(동일창 재평가) — 퇴보 시 기존 유지

        wf = walk_forward_backtest(ds, n_folds=4)
        mean_ic = wf.get("mean_ic")
        icir    = wf.get("icir")
        fold_str = " / ".join(f"{x:+.3f}" for x in wf.get("fold_ics", []))

        adopt_str = ("채택 ✅" if adopted else f"보류 ⏸️ (챔피언 IC {champ_ic:+.3f} 유지)")
        msg = "\n".join([
            "🔄 랭커 주간 재학습 (Purged WF · 챔피언/챌린저)",
            "━━━━━━━━━━━━━━",
            f"분할 OOS IC: {result.oos_ic:+.3f}  ICIR: {result.oos_icir:.2f}  → {adopt_str}",
            f"WF 평균 IC: {mean_ic:+.4f}  ICIR: {icir:.2f}" if mean_ic is not None else "WF: 데이터 부족",
            f"폴드별 IC: {fold_str}" if fold_str else "",
            f"상위10% 초과수익: {result.oos_top_decile_ret*100:+.1f}%",
            "⚠️ WF IC < 0.02 지속 시 DCA 틸트 비중 축소 검토",
        ])
        send_cron_telegram(msg)
        logger.info("재학습·보고 완료 (WF mean IC=%s)", mean_ic)
        return 0
    except Exception as e:
        logger.exception("주간 재학습 실패")
        send_cron_telegram(f"❌ 랭커 주간 재학습 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
