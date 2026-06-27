#!/usr/bin/env python3
"""concentration_validated_eval.py — Tier6 검증된 집중 ★게이트 주간 재검증.

backtest/concentration_validated_backtest.run_all() 로 무스킬 랜덤 집중(K종목) vs 분산을
몬테카를로·DSR 다중검정으로 재평가. 집중이 분산을 이기면(GO·선택엣지 존재 의미) shadow
(ADAPTIVE_CONCENTRATION_DISPLAY_ENABLED 시). 평시 NO-GO + 과집중 분산권고는 일일/`/risk` 상시.

안전: 평가·표시·shadow 전용. 배분/leverage_state/DCA 무변경·자동집행 0. 검증된 집중=구조레버리지뿐.
크론 (토 05:15 UTC = 14:15 KST):  15 5 * * 6 cd <repo> && uv run python crons/concentration_validated_eval.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/concentration_validated_shadow.json"))


def _save_shadow(out: dict) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"verdict": out["verdict"], "_meta": {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "note": out.get("note")}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== concentration_validated_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from concentration_validated_backtest import run_all
        out = run_all()
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0
    if out.get("error"):
        logger.info("게이트 데이터 부족: %s", out["error"])
        return 0

    enabled = os.getenv("ADAPTIVE_CONCENTRATION_DISPLAY_ENABLED", "false").lower() == "true"
    lines = ["🎯 검증된 집중 ★게이트 (무스킬 랜덤집중 vs 분산 · 무생존편향 섹터ETF)", "━━━━━━━━━━━━━━",
             f"  분산 EW Sharpe {out['diversified_sharpe']} · PBO {out['pbo']}"]
    for K, r in out.get("by_K", {}).items():
        p = r["penalty"]
        lines.append(f"  집중 K={K}: 분산 이길확률 {round((1 - p['frac_worse']) * 100)}% · 중앙Δ {p['median_delta']}")
    lines.append(f"  판정: {out['verdict']}")
    if out["verdict"] == "GO" and enabled:
        _save_shadow(out)
        lines.append("  → 집중 우위 — shadow 기록 (희귀)")
    lines.append(f"  → {out.get('note', '')}")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
