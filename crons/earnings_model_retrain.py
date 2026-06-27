#!/usr/bin/env python3
"""earnings_model_retrain.py — 어닝 예측(G3 서프라이즈·G4 주가반응) 주간 재학습 + 캐시 (Phase C 배선).

/earnings 가 라이브 예측을 쓰려면 모델이 캐시돼 있어야 한다. 주간 재학습 → OOS 평가 → pickle 캐시.
표본/엣지 부족 시 저장 안 함(보류 — /earnings 는 예측 생략하고 밸류에이션만 표시).

크론 (토 03:50 UTC = 12:50 KST — 주말, 美 랭커 재학습 직후):
    50 3 * * 6 cd <repo> && uv run python crons/earnings_model_retrain.py >> /tmp/earnings_model_retrain.log 2>&1
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _send(text: str) -> None:
    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception:
        pass


def main() -> int:
    logger.info("=== earnings_model_retrain 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    from ml.data_pipeline import US_TOP50
    from ml.entry_analyzer import PORTFOLIO_STOCKS
    from ml import earnings_predictor as g3
    from ml import earnings_move_predictor as g4

    tickers = list(dict.fromkeys(list(PORTFOLIO_STOCKS) + list(US_TOP50)))
    lines = ["📈 어닝 예측 모델 재학습 (Phase C)", "━━━━━━━━━━━━━━"]

    # 품질 게이트: 엣지 없는 모델은 캐시 안 함(/earnings 가 무근거 예측 노출 방지).
    G3_MIN_AUC = 0.52      # 랜덤(0.5) 초과
    G4_MIN_SKILL = 0.02    # 나이브 평균예측 대비 우위

    try:
        rows, labels, _ = g3.build_training_set(tickers)
        r3 = g3.train(rows, labels)
        auc = r3.get("oos_auc")
        if r3.get("model") is not None and auc is not None and auc > G3_MIN_AUC:
            g3.save_model(r3["model"])
            lines.append(f"G3 서프라이즈: 채택 ✅ {r3['reason']}")
        else:
            lines.append(f"G3 서프라이즈: 보류 ⏸️ (엣지 미달) {r3.get('reason')}")
    except Exception as e:
        logger.exception("G3 재학습 실패")
        lines.append(f"G3 서프라이즈: 실패 — {e}")

    try:
        rows4, mag, dirn, _ = g4.build_training_set(tickers)
        r4 = g4.train(rows4, mag, dirn)
        skill = r4.get("mag_skill")
        if r4.get("mag_model") is not None and skill is not None and skill > G4_MIN_SKILL:
            g4.save_model(r4)
            lines.append(f"G4 주가반응: 채택 ✅ {r4['reason']}")
        else:
            lines.append(f"G4 주가반응: 보류 ⏸️ (변동폭 엣지 미달) {r4.get('reason')}")
    except Exception as e:
        logger.exception("G4 재학습 실패")
        lines.append(f"G4 주가반응: 실패 — {e}")

    lines.append("⚠️ 어닝 방향예측은 본질적 한계 — 변동폭·서프라이즈 확률만(정보형)")
    msg = "\n".join(lines)
    logger.info(msg)
    _send(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
