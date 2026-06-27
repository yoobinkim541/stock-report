#!/usr/bin/env python3
"""
longterm_adaptive_eval.py — 장기 타점(바벨/레버리지) ★목적함수 라이브 평가 (Phase 3).

장기 전략은 단일 실현경로뿐이라 틸트의 반사실 학습이 어렵다. 대신 portfolio_history
(실현 NAV)로 **사용자 #1 목표를 라이브로 측정**한다: 지수(QQQ) 대비 아웃퍼폼 + MDD ≤ 지수.
악화(언더퍼폼 AND MDD>지수)가 지속되면 **보수적(레버리지 축소 방향만) shadow 권고**를 남긴다.

안전: 순수 평가 + shadow. 라이브 DCA/레버리지 advice 무변경(레버리지 최적화는 기존
leverage_optimizer 가 담당). shadow 는 ADAPTIVE_LONGTERM_ENABLED=true 일 때만, 그리고
lev_scale ≤ 1.0(증액 불가·축소만) — 자동 변경은 *더 안전한 방향*으로만.

크론 (토 04:00 UTC = 13:00 KST — 주말):
    0 4 * * 6 cd <repo> && uv run python crons/longterm_adaptive_eval.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/longterm_policy_shadow.json"))
WINDOW = 90               # 트레일링 평가 표본수(≈일별 3개월)
MIN_SAMPLES = 30          # 평가 최소 표본


def scorecard(records: list[dict], window: int = WINDOW) -> dict | None:
    """전략(total_usd) vs 지수(qqq_price) ★목적함수 스코어카드 — 최근 window 표본."""
    from ml.adaptive import reward as _reward
    rows = [r for r in records if r.get("total_usd") and r.get("qqq_price")]
    if len(rows) < MIN_SAMPLES:
        return None
    rows = rows[-window:]
    nav = [float(r["total_usd"]) for r in rows]
    idx = [float(r["qqq_price"]) for r in rows]
    strat_ret = (nav[-1] / nav[0] - 1.0) * 100.0
    qqq_ret = (idx[-1] / idx[0] - 1.0) * 100.0
    strat_mdd = _reward.max_drawdown(nav) * 100.0
    qqq_mdd = _reward.max_drawdown(idx) * 100.0
    excess = strat_ret - qqq_ret
    meets = (excess > 0) and (strat_mdd <= qqq_mdd)      # ★ 아웃퍼폼 + MDD≤지수
    return {"n": len(rows), "strat_ret": round(strat_ret, 2), "qqq_ret": round(qqq_ret, 2),
            "excess": round(excess, 2), "strat_mdd": round(strat_mdd, 2), "qqq_mdd": round(qqq_mdd, 2),
            "meets": meets}


def _conservative_scale(sc: dict) -> float:
    """MDD 가 지수를 초과하면 그 비율만큼 레버리지 축소 권고(0.5~1.0)."""
    if sc["strat_mdd"] <= sc["qqq_mdd"] or sc["strat_mdd"] <= 0:
        return 1.0
    return max(0.5, min(1.0, sc["qqq_mdd"] / sc["strat_mdd"]))


def _save_shadow(scale: float, sc: dict) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"lev_scale": round(scale, 3), "_meta": {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), **sc}}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== longterm_adaptive_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    try:
        import store
        records = store.all("portfolio_history")
    except Exception as e:
        logger.warning("portfolio_history 조회 실패: %s", e)
        return 0

    sc = scorecard(records)
    if sc is None:
        logger.info("표본 부족 — 평가 보류")
        return 0

    enabled = os.getenv("ADAPTIVE_LONGTERM_ENABLED", "false").lower() == "true"
    mark = "✅ 목표 충족" if sc["meets"] else "⚠️ 목표 미달"
    lines = [
        "📈 장기 전략 ★목표 스코어카드 (vs QQQ)",
        "━━━━━━━━━━━━━━",
        f"  최근 {sc['n']}표본",
        f"  수익  전략 {sc['strat_ret']:+.1f}% vs QQQ {sc['qqq_ret']:+.1f}%  (초과 {sc['excess']:+.1f}%p)",
        f"  MDD   전략 {sc['strat_mdd']:.1f}% vs QQQ {sc['qqq_mdd']:.1f}%",
        f"  {mark} (1순위 아웃퍼폼 + MDD≤지수)",
    ]
    # 악화 지속 시 보수적 shadow 권고(축소만) — 옵트인 시에만 라이브 의미
    if not sc["meets"]:
        scale = _conservative_scale(sc)
        if scale < 1.0:
            if enabled:
                _save_shadow(scale, sc)
                lines.append(f"  → 보수적 권고: 레버리지 ×{scale:.2f} (shadow 기록)")
            else:
                lines.append(f"  → 보수적 권고 가능: 레버리지 ×{scale:.2f} (shadow OFF — 미기록)")
    lines.append("  ⚠️ 평가·권고만 — 라이브 자동 변경 없음(레버리지 최적화는 기존 경로)")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
