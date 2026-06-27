#!/usr/bin/env python3
"""leverage_structural_eval.py — Tier3 구조적 레버리지 ★게이트 라이브 재검증 (주간).

backtest/leverage_structural_backtest.run_all() 로 SPY+QQQ 양 프록시 × 레버리지 그리드의
낙폭예산(50%)·DSR·PBO 게이트를 재평가. GO 시 권고 레버리지를 shadow 기록(ADAPTIVE_LEVERAGE_ENABLED 시만).

안전(증액 방향이라 축소보다 위험):
- 평가·표시·shadow 전용. 라이브 배분/leverage_state/DCA 무변경. **실계좌 레버리지 증액은 사람이**(자동집행 0).
- 폭락엔 *디리스크 서킷브레이커* 전제(증액 아님 — 폭락 물타기는 파산). 갭리스크 잔존.
- shadow 는 옵트인(ADAPTIVE_LEVERAGE_ENABLED=true) 시에만, reco ≤ 1.5 클램프.

크론 (토 04:15 UTC = 13:15 KST):
    15 4 * * 6 cd <repo> && uv run python crons/leverage_structural_eval.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/structural_leverage_shadow.json"))
LEV_CLAMP = 1.5


def _save_shadow(reco_L: float, results: dict, note: str) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    spy = results.get("SPY", {}).get("baseline_1.0x", {})
    payload = {"reco_lev": round(min(reco_L, LEV_CLAMP), 2), "verdict": "GO",
               "_meta": {"at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                         "spy_baseline_mdd": spy.get("mdd"), "note": note}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== leverage_structural_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from leverage_structural_backtest import run_all
        out = run_all()
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0

    v = out["verdict"]
    enabled = os.getenv("ADAPTIVE_LEVERAGE_ENABLED", "false").lower() == "true"
    lines = ["🏗 구조적 레버리지 ★게이트 (SPY+QQQ · 낙폭예산 50%)", "━━━━━━━━━━━━━━"]
    for p, res in out.get("results", {}).items():
        b = res.get("baseline_1.0x", {})
        lines.append(f"  {p}: baseline MDD {b.get('mdd')}% · best L* {res.get('best_L')}")
    lines.append(f"  판정: {v['verdict']}")
    if v["verdict"] == "GO" and v.get("reco_L"):
        reco = min(v["reco_L"], LEV_CLAMP)
        if enabled:
            _save_shadow(v["reco_L"], out.get("results", {}), v["note"])
            lines.append(f"  → 권고 구조적 레버리지 ×{reco:.2f} (shadow 기록·표시용)")
        else:
            lines.append(f"  → 권고 ×{reco:.2f} (shadow OFF — /risk 미표시; ADAPTIVE_LEVERAGE_ENABLED=true 시 기록)")
        lines.append("  ⚠️ 폭락 디리스크 서킷브레이커 전제·갭리스크 잔존·실계좌 수동(자동집행 없음)")
    else:
        lines.append(f"  → {v['note']}")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
