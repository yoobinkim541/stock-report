#!/usr/bin/env python3
"""factor_premium_eval.py — Tier4 팩터 프리미엄 틸트 ★게이트 주간 재검증.

backtest/factor_premium_backtest.run_all() 로 롱온리 팩터 ETF(밸류·소형·퀄리티·모멘텀·최소변동) vs SPY
초과수익을 DSR 다중검정·PBO·약세슬라이스로 재평가. GO 팩터만 shadow(ADAPTIVE_FACTOR_TILT_ENABLED 시).
프리미엄 쇠퇴(밸류/사이즈 등)는 이름 명시 정직 공개. 밸류 부활 등 미래 변화 포착용 주간 모니터.

안전: 평가·표시·shadow 전용. 배분/leverage_state/DCA 무변경·자동집행 0. 모멘텀=SPMO 기보유 중복틸트 주의.
크론 (토 04:30 UTC = 13:30 KST):  30 4 * * 6 cd <repo> && uv run python crons/factor_premium_eval.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/factor_tilt_shadow.json"))


def _save_shadow(go: list, nogo: list, note: str) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"go_factors": go, "nogo_factors": nogo, "verdict": "GO",
               "_meta": {"at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "note": note}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== factor_premium_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from factor_premium_backtest import run_all
        out = run_all()
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0
    if out.get("error"):
        logger.info("게이트 데이터 부족: %s", out["error"])
        return 0

    v = out["verdict"]
    enabled = os.getenv("ADAPTIVE_FACTOR_TILT_ENABLED", "false").lower() == "true"
    lines = ["📐 팩터 프리미엄 ★게이트 (롱온리 ETF vs SPY · DSR 다중검정)", "━━━━━━━━━━━━━━"]
    for f in out.get("factors", []):
        lines.append(f"  {f['factor']}({f['etf']}): 초과SR {f['excess_sharpe']} · PSR {f['psr_excess']} · DSR {f['dsr']}")
    lines.append(f"  판정: {v}")
    if v == "GO" and out.get("go_factors"):
        if enabled:
            _save_shadow(out["go_factors"], out["nogo_factors"], out["note"])
            lines.append(f"  → 보상 팩터 {', '.join(out['go_factors'])} shadow 기록 (/risk 표시)")
        else:
            lines.append(f"  → 보상 팩터 {', '.join(out['go_factors'])} (shadow OFF — ADAPTIVE_FACTOR_TILT_ENABLED 시 /risk 표시)")
        lines.append("  ⚠️ 모멘텀=SPMO 기보유 중복틸트 주의 · 실계좌 수동(자동집행 없음)")
    else:
        lines.append(f"  → {out.get('note', '')}")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
