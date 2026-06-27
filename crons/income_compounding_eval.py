#!/usr/bin/env python3
"""income_compounding_eval.py — Tier5 인컴 복리 재투자 ★게이트 주간 재검증.

backtest/income_compounding_backtest.run_all() 로 커버드콜 인컴(QYLD 프록시) 재투자 vs 총수익(QQQ)을
세전/세후·재투자vs현금비축으로 재평가. 인컴 엔진이 세후 총수익 우위(GO·희귀)면 shadow 기록
(ADAPTIVE_INCOME_ENGINE_ENABLED 시). 평시 NO-GO 공개는 일일리포트 QQQI 섹션(상시).

안전: 평가·표시·shadow 전용. 배분/leverage_state/DCA 무변경·자동집행 0. 실계좌 수동.
크론 (토 05:00 UTC = 14:00 KST):  0 5 * * 6 cd <repo> && uv run python crons/income_compounding_eval.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/income_engine_shadow.json"))


def _save_shadow(out: dict) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"verdict": out["verdict"], "_meta": {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "aftertax_cagr_gap": out.get("aftertax_cagr_gap"),
        "reinvest_vs_hoard_gap_pct": out.get("reinvest_vs_hoard_gap_pct"), "note": out.get("note")}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== income_compounding_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from income_compounding_backtest import run_all
        out = run_all()
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0
    if out.get("error"):
        logger.info("게이트 데이터 부족: %s", out["error"])
        return 0

    enabled = os.getenv("ADAPTIVE_INCOME_ENGINE_ENABLED", "false").lower() == "true"
    lines = [
        "💰 인컴 복리 ★게이트 (커버드콜 인컴 vs 총수익)", "━━━━━━━━━━━━━━",
        f"  QYLD 재투자 {out['qyld_reinvest']['cagr']}% vs QQQ {out['qqq_total_return']['cagr']}% "
        f"(세후 CAGR 격차 {out.get('aftertax_cagr_gap')}%p)",
        f"  재투자 vs 현금비축: +{out.get('reinvest_vs_hoard_gap_pct')}% (재투자 우월)",
        f"  판정: {out['verdict']}",
    ]
    if out["verdict"] == "GO" and enabled:
        _save_shadow(out)
        lines.append("  → 세후 우위 — shadow 기록 (/risk 표시)")
    lines.append(f"  → {out.get('note', '')}")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
