#!/usr/bin/env python3
"""us_axes_eval.py — US 선택정책 가격축 ★게이트 주간 재검증 + 현재 권고 shadow. kr_axes_eval 미국판.

backtest/us_policy_backtest.run() 으로 S&P500 시점 멤버십 마스킹·순비용 워크포워드를 재평가
(yfinance 필요 — 서버 전용·오프라인이면 조용히 skip). ADAPTIVE_US_AXES_ENABLED=true 면 권고를
us_policy_axes_shadow.json 에 기록 → us_policy.load_params() 가 **모의 선택에만** 게이트 반영.

정직 규율: 상폐종목 가격 부재(커버리지)로 GO 강등 가능 — verdict·커버리지 있는 그대로 공개.
안전: 평가·표시·shadow 전용 — 실계좌 주문 경로 0. off(기본)면 평가·알림만.
크론 (토 05:45 UTC = 14:45 KST):  45 5 * * 6 cd <repo> && uv run python crons/us_axes_eval.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/us_policy_axes_shadow.json"))

from lib.cron_common import send_cron_telegram


def _save_shadow(rec: dict, verdict_code: str, coverage) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
               "chosen": rec.get("chosen"), "policy_weights": rec.get("policy_weights"),
               "train_obj": rec.get("train_obj"), "window": rec.get("window"),
               "verdict_code": verdict_code, "coverage": coverage,
               "_meta": {"note": "us_axes_eval 주간 재검증 — 모의 선택 전용·클램프·상한 적용"}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


def build_message(res: dict, *, enabled: bool, shadow_written: bool) -> str:
    """평가 결과 → 텔레그램 요약 (순수 — 테스트 가능)."""
    v = res.get("verdict") or {}
    rec = res.get("recommendation")
    lines = ["🇺🇸 US 선택정책 가격축 ★게이트 (S&P500 시점멤버십·순비용·워크포워드)",
             "━━━━━━━━━━━━━━",
             v.get("label", "판정 없음")]
    oos, bench = v.get("oos") or {}, v.get("bench") or {}
    if oos and bench:
        lines.append(f"OOS 연결: 전략 {oos.get('cagr', 0)*100:+.1f}%/년 vs QQQ {bench.get('cagr', 0)*100:+.1f}%"
                     f" · MDD {oos.get('mdd', 0)*100:.0f}%/{bench.get('mdd', 0)*100:.0f}%")
        lines.append(f"DSR {v.get('dsr')} · PBO {v.get('pbo')} · 커버리지 {(res.get('coverage') or 0)*100:.0f}%"
                     f" (상폐 누락=상방편향 가능)")
    if rec:
        pw = rec.get("policy_weights") or {}
        w_str = " ".join(f"{k[2:]}={val:.2f}" for k, val in sorted(pw.items()) if val > 0)
        lines.append(f"📌 현재 권고 축: {rec.get('chosen')} (트레일링 {rec.get('window')})")
        lines.append(f"   → 가중 {w_str or '—'}")
    else:
        lines.append("📌 권고 없음 (데이터 부족)")
    if enabled:
        lines.append("shadow 반영: " + ("✅ 기록(모의 선택 전용)" if shadow_written else "⚠️ 기록 실패/권고 없음"))
    else:
        lines.append("shadow off (ADAPTIVE_US_AXES_ENABLED=false) — 평가·표시만")
    lines.append("⚠️ KR 보다 약한 검증(상폐 가격 부재) · 모의 한정 · 실계좌 자동집행 0")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== us_axes_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from us_policy_backtest import run
        res = run()
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0
    if res.get("error"):
        logger.info("게이트 실행 불가(오프라인/데이터 부족): %s", res["error"])
        return 0

    enabled = os.getenv("ADAPTIVE_US_AXES_ENABLED", "false").lower() == "true"
    rec = res.get("recommendation")
    shadow_written = False
    if enabled and rec:
        try:
            _save_shadow(rec, (res.get("verdict") or {}).get("code", ""), res.get("coverage"))
            shadow_written = True
            logger.info("shadow 기록: %s → %s", rec.get("chosen"), SHADOW_PATH)
        except Exception as e:
            logger.warning("shadow 기록 실패: %s", e)

    send_cron_telegram(build_message(res, enabled=enabled, shadow_written=shadow_written))
    logger.info("완료 — verdict %s · 권고 %s", (res.get("verdict") or {}).get("code"),
                (rec or {}).get("chosen"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
