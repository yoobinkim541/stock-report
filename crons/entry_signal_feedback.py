#!/usr/bin/env python3
"""entry_signal_feedback.py — 진입 후보 추천 사후성과 백필/회고.

매일 실행해 단기·스윙 horizon이 성숙한 추천 후보의 실제 성과를 outcome 원장에 추가한다.
추천 스냅샷은 telegram_bot.notify_entry_signals / /entry 명령에서 적재한다.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from lib.cron_common import send_cron_telegram
from ml import entry_feedback

KST = timezone(timedelta(hours=9))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== entry_signal_feedback 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    added = entry_feedback.backfill_outcomes()
    summary_horizons = ("30m", "1h", "4h", "1d", "3d", 20, 60)
    summaries = {h: entry_feedback.summarize_feedback(horizon=h) for h in summary_horizons}
    s20, s60 = summaries[20], summaries[60]
    adjust = entry_feedback.learn_feedback_adjustments(horizon=20)
    if adjust.get("challenger") is not None:
        # OOS 평가까지 간 시도만 이력에 남긴다 — 다음 학습의 다중시도 보정(시도 지문·미반올림 Sharpe) 근거.
        from ml.adaptive import evolution
        evidence = adjust.get("evidence") or {}
        evolution.record_learning(entry_feedback.TRIAL_SURFACE, {
            "date": datetime.now(KST).strftime("%Y-%m-%d"), "adopted": bool(adjust.get("adopted")),
            "reason": adjust.get("reason"),
            "excess_challenger": adjust["challenger"].get("excess"),
            "excess_champion": (adjust.get("champion") or {}).get("excess"),
            "n_oos": adjust.get("oos_n"),
            "n_oos_independent": adjust["challenger"].get("n_independent"),
            "sharpe_challenger": evidence.get("sharpe_pp"),
            "psr_challenger": evidence.get("psr"),
            "n_trials": evidence.get("n_trials"),
            "trial_key": evidence.get("trial_key"),
            "trial_sharpe": evidence.get("trial_sharpe"),
            "candidate_params": adjust.get("adjustments"),
        })
    compact = " · ".join(f"{h} n={summaries[h].get('n', 0)}" for h in summary_horizons)
    logger.info(
        "백필 %d건 · %s · 보정학습 adopted=%s (%s)",
        added, compact, adjust.get("adopted"), adjust.get("reason"),
    )

    if added or os.getenv("ENTRY_FEEDBACK_ALWAYS_SEND", "false").lower() == "true":
        send_cron_telegram("\n".join([
            "🧪 진입 후보 사후검증",
            f"신규 성숙 outcome: {added}건",
            " · ".join(f"{h} 표본 {summaries[h].get('n', 0)}건" for h in summary_horizons),
            entry_feedback.format_feedback_summary(s20),
            entry_feedback.format_feedback_summary(s60),
            f"점수 보정학습: {'채택' if adjust.get('adopted') else '대기'} — {adjust.get('reason')}",
            "⚠️ 정보형 추천 검증 — 실거래 주문 아님",
        ]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
