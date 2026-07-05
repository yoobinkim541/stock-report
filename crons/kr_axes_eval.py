#!/usr/bin/env python3
"""kr_axes_eval.py — KR 선택정책 가격축 ★게이트 주간 재검증 + 현재 권고 shadow.

backtest/kr_policy_backtest.run() 으로 25년 무생존편향·순비용·워크포워드를 재평가하고,
트레일링 5년 ★목적함수 최적의 매핑 가능 축 조합(current_recommendation)을 산출한다.
ADAPTIVE_KR_AXES_ENABLED=true 면 권고를 kr_policy_axes_shadow.json 에 기록 →
kr_policy.load_params() 가 **모의(paper) 선택에만** 게이트 반영(클램프·상한·stale 무시).

정직 규율: verdict 는 있는 그대로(현재 OBSERVE — DSR 미달, 엣지 단정 불가) 텔레그램 공개.
안전: 평가·표시·shadow 전용 — 실계좌 주문 경로 0·DCA/배분 불변. off(기본)면 평가·알림만.
크론 (토 05:30 UTC = 14:30 KST):  30 5 * * 6 cd <repo> && uv run python crons/kr_axes_eval.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/kr_policy_axes_shadow.json"))

from lib.cron_common import send_cron_telegram


def _save_shadow(rec: dict, verdict_code: str) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
               "chosen": rec.get("chosen"), "policy_weights": rec.get("policy_weights"),
               "train_obj": rec.get("train_obj"), "window": rec.get("window"),
               "verdict_code": verdict_code,
               "_meta": {"note": "kr_axes_eval 주간 재검증 — 모의 선택 전용·클램프·상한 적용"}}
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


def build_message(res: dict, *, enabled: bool, shadow_written: bool) -> str:
    """평가 결과 → 텔레그램 요약 (순수 — 테스트 가능)."""
    v = res.get("verdict") or {}
    rec = res.get("recommendation")
    lines = ["🇰🇷 KR 선택정책 가격축 ★게이트 (25년 무생존편향·순비용·워크포워드)",
             "━━━━━━━━━━━━━━",
             v.get("label", "판정 없음")]
    oos, bench = v.get("oos") or {}, v.get("bench") or {}
    if oos and bench:
        lines.append(f"OOS 연결: 전략 {oos.get('cagr', 0)*100:+.1f}%/년 vs 지수 {bench.get('cagr', 0)*100:+.1f}%"
                     f" · MDD {oos.get('mdd', 0)*100:.0f}%/{bench.get('mdd', 0)*100:.0f}%")
        lines.append(f"DSR {v.get('dsr')} · PBO {v.get('pbo')} (관문 ≥0.95 / <0.5)")
    if rec:
        pw = rec.get("policy_weights") or {}
        w_str = " ".join(f"{k[2:]}={v:.2f}" for k, v in sorted(pw.items()) if v > 0)
        lines.append(f"📌 현재 권고 축: {rec.get('chosen')} (트레일링 {rec.get('window')})")
        lines.append(f"   → 가중 {w_str or '—'}")
    else:
        lines.append("📌 권고 없음 (데이터 부족)")
    if enabled:
        lines.append("shadow 반영: " + ("✅ 기록(모의 선택 전용)" if shadow_written else "⚠️ 기록 실패/권고 없음"))
    else:
        lines.append("shadow off (ADAPTIVE_KR_AXES_ENABLED=false) — 평가·표시만")

    # 🛡️ 레짐 방어 오버레이 (표시·추적 전용 — 방어 verdict)
    ro = res.get("regime_overlay") or {}
    if ro and not ro.get("error"):
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"🛡️ 레짐 방어 오버레이: {ro.get('code')}")
        lines.append(f"MDD {ro['overlay']['mdd']*100:.0f}% (순공격比 {ro['mdd_vs_offense_pp']:+.0f}%p) ·"
                     f" 약세해방어 {ro.get('bear_defend_years')} · DSR {ro.get('dsr')}")
        lines.append("→ 수익 엔진 아님·낙폭 방어용 · 초과수익 통계 미달(위기집중·whipsaw)")

    # 💸 비용·회전율 (OOS 검증된 실행 권고)
    cs = res.get("cost_sensitivity") or {}
    if cs and not cs.get("error"):
        cur = cs.get("current") or {}
        oos = cs.get("oos") or {}
        reco = oos.get("live_reco") or {}
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"💸 회전율 비용: 현재(월간) 드래그 {cur.get('drag_pp')}%p/년 · OOS {oos.get('verdict')}")
        lines.append(f"   반기>월간 {int((oos.get('year_win_rate') or 0)*100)}%·gross보존 {oos.get('gross_preserved')}·타축확인 {oos.get('cross_axis_confirmed')}")
        if reco.get("min_hold_days"):
            live = "✅ 적용 중" if os.getenv("KR_MOCK_MIN_HOLD_DAYS", "0") != "0" else "off(KR_MOCK_MIN_HOLD_DAYS)"
            lines.append(f"→ 권고 최소보유 {reco['min_hold_days']}일 (~{reco['expected_drag_save_pp']}%p 절감·모의 한정) · {live}")
        else:
            lines.append("→ 견고 미확인 — 현행 유지(과적합 회피)")

    lines.append("⚠️ 검증상 OBSERVE = 엣지 단정 불가 · 모의 한정 · 실계좌 자동집행 0")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== kr_axes_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        from kr_policy_backtest import run
        res = run(start_year=2001)
    except Exception as e:
        logger.warning("게이트 실행 실패: %s", e)
        return 0
    if res.get("error"):
        logger.info("게이트 데이터 부족: %s", res["error"])
        return 0

    enabled = os.getenv("ADAPTIVE_KR_AXES_ENABLED", "false").lower() == "true"
    rec = res.get("recommendation")
    shadow_written = False
    if enabled and rec:
        try:
            _save_shadow(rec, (res.get("verdict") or {}).get("code", ""))
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
