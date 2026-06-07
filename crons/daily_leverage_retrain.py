#!/usr/bin/env python3
"""
daily_leverage_retrain.py — 레버리지 ETF 모델 일일 재학습 + 신호 발송

매일 (22:15 UTC): LeverageModel 재학습 → 진입 신호 발송
매주 월요일    : Optuna 파라미터 재최적화 (주말 종가 반영)

크론:
    15 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python daily_leverage_retrain.py >> /tmp/daily_leverage.log 2>&1
"""
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID")


def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 미설정")
        return False
    import requests
    try:
        for i in range(0, len(text), 4000):
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text[i:i+4000]},
                timeout=15,
            ).raise_for_status()
        return True
    except Exception as e:
        logger.error("텔레그램 발송 실패: %s", e)
        return False


def _should_reoptimize() -> bool:
    """매주 월요일 OR 최적화 결과 없을 때 재최적화."""
    from datetime import datetime, timezone, timedelta
    from ml.leverage_optimizer import load_result, RESULTS_PATH
    if not RESULTS_PATH.exists():
        return True
    # 월요일 (weekday=0) = 재최적화
    if datetime.now(timezone(timedelta(hours=9))).weekday() == 0:
        return True
    return False


def main() -> int:
    logger.info("=== daily_leverage_retrain 시작 ===")

    # ── 주간 파라미터 재최적화 (월요일) ──────────────────────────────
    if _should_reoptimize():
        logger.info("주간 Optuna 파라미터 재최적화 시작...")
        try:
            from ml.leverage_optimizer import optimize_leverage, format_optimization_report
            from ml.data_pipeline import fetch_prices

            prices    = fetch_prices(["QQQ"], days=2520)
            qqq       = prices["QQQ"]["Close"]
            qqq_years = (qqq.index[-1] - qqq.index[0]).days / 365.25
            qqq_cagr  = float(qqq.iloc[-1] / qqq.iloc[0]) ** (1 / qqq_years) - 1

            opt_result = optimize_leverage(
                days=2520, n_optuna=200,
                train_months=18, test_months=6, step_months=3,
            )
            opt_report = format_optimization_report(opt_result, bm_qqq=qqq_cagr)
            _send(opt_report)
            logger.info(
                "Optuna 재최적화 완료 — CAGR %.1f%%  MDD %.1f%%  Calmar %.2f",
                opt_result.best_cagr * 100, opt_result.best_max_dd * 100, opt_result.best_calmar,
            )
        except Exception as e:
            logger.error("파라미터 재최적화 실패: %s", e)
            _send(f"⚠️ 레버리지 파라미터 재최적화 실패: {e}")

    # ── 일일 모델 재학습 + 신호 발송 ────────────────────────────────
    try:
        from ml.leverage_signal import get_entry_signal, format_leverage_report

        logger.info("LeverageModel 재학습 중...")
        sig    = get_entry_signal(retrain=True)
        report = format_leverage_report(sig)

        logger.info(
            "재학습 완료 — 낙폭 %.1f%% | %s",
            sig.current_drawdown * 100, sig.entry_advice[:25],
        )

        if _send(report):
            logger.info("레버리지 리포트 발송 완료")
        return 0

    except Exception as e:
        msg = f"❌ daily_leverage_retrain 오류: {e}"
        logger.error(msg)
        _send(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
