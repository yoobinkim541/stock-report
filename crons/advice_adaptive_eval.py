#!/usr/bin/env python3
"""
advice_adaptive_eval.py — 포트폴리오 advice(MetaAllocator) 적응 평가 (Phase 4, 최종).

기존 paper_track A/B(meta 배분 vs phase 규칙 배분)의 **실현 성과**로 meta 의 신뢰도를
★목적함수 챔피언/챌린저로 평가한다. meta(챌린저)가 rule(챔피언)을 실현수익에서 이기고
(아웃퍼폼) 하방이 rule 이하(MDD 제약)일 때만, DCA blend 를 meta 쪽으로 올리도록
**shadow 권고**(clamped 0~0.6). 기본 off → 평가·권고만, 라이브 blend 무변경.

meta/rule 은 사전 정의된 두 전략의 실현수익 비교라 학습 파라미터 과적합 위험이 없어
(train/OOS 분할 없이) 누적 성숙 표본 전체로 직접 비교한다.

안전: 순수 평가 + shadow. 라이브 DCA/advice 무변경(_phase_blend_factor 그대로).
크론 (토 04:30 UTC = 13:30 KST):
    30 4 * * 6 cd <repo> && uv run python crons/advice_adaptive_eval.py
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
TRACK_PATH = Path.home() / ".local" / "share" / "stock-report" / "paper_track.json"
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/advice_blend_shadow.json"))
MIN_SAMPLES = 15          # paper_track 은 희소 → 보수적 최소 표본
BLEND_MAX = 0.6           # blend 상한(기존 _phase_blend_factor 최대와 일치)


def _samples(track: dict, horizon: str = "5d") -> list[tuple[float, float]]:
    """성숙 항목 → [(ret_meta, ret_rule)]."""
    mk, rk = f"ret_meta_{horizon}", f"ret_rule_{horizon}"
    out = []
    for _, e in sorted(track.items()):
        if e.get(mk) is None or e.get(rk) is None:
            continue
        try:
            out.append((float(e[mk]), float(e[rk])))
        except (TypeError, ValueError):
            continue
    return out


def _downside(vals: list[float]) -> float:
    negs = [v for v in vals if v < 0]
    return abs(sum(negs) / len(negs)) if negs else 0.0


def evaluate(samples: list[tuple[float, float]]) -> dict:
    """meta(챌린저) vs rule(챔피언) ★목적함수 평가."""
    from ml.adaptive import reward as _reward
    n = len(samples)
    if n < MIN_SAMPLES:
        return {"adopt": False, "reason": f"표본 {n}/{MIN_SAMPLES} 미달 — 보류", "n": n}
    meta = [m for m, _ in samples]
    rule = [r for _, r in samples]
    excess = sum(m - r for m, r in samples) / n            # meta 의 rule 대비 평균 우위
    meta_dd, rule_dd = _downside(meta), _downside(rule)
    # 챌린저(meta) excess(=meta-rule 평균)>0 + 하방 ≤ rule + 표본 충분
    adopt = _reward.should_adopt({"excess": excess, "mdd": meta_dd, "n": n},
                                 {"excess": 0.0, "mdd": rule_dd, "n": n},
                                 index_mdd=rule_dd, min_samples=MIN_SAMPLES)
    meta_mean, rule_mean = sum(meta) / n, sum(rule) / n
    return {"adopt": adopt, "n": n, "excess": round(excess, 4),
            "meta_mean": round(meta_mean, 4), "rule_mean": round(rule_mean, 4),
            "meta_dd": round(meta_dd, 4), "rule_dd": round(rule_dd, 4),
            "reason": ("채택 가능 ✅ meta 우위" if adopt else "보류 ⏸️ meta 우위 불충분/하방 초과")}


def _recommended_blend(ev: dict) -> float:
    """meta 우위 크기에 비례한 blend 권고(0.3~0.6 범위, 보수적)."""
    edge = max(0.0, ev.get("excess", 0.0))
    return round(min(BLEND_MAX, 0.3 + edge * 5.0), 3)      # +1%p 우위당 +0.05


def _save_shadow(blend: float, ev: dict) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"blend": blend, "_meta": {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), **ev}}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== advice_adaptive_eval 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not TRACK_PATH.exists():
        logger.info("paper_track 없음 — 보류")
        return 0
    try:
        track = json.loads(TRACK_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("paper_track 로드 실패: %s", e)
        return 0

    ev = evaluate(_samples(track))
    enabled = os.getenv("ADAPTIVE_ADVICE_ENABLED", "false").lower() == "true"
    lines = ["🧭 포트폴리오 advice 적응 평가 (MetaAllocator A/B)", "━━━━━━━━━━━━━━"]
    if ev.get("n", 0) < MIN_SAMPLES:
        lines.append(f"  {ev['reason']}")
    else:
        lines += [
            f"  표본 {ev['n']}  meta {ev['meta_mean']*100:+.2f}% vs rule {ev['rule_mean']*100:+.2f}% (우위 {ev['excess']*100:+.2f}%p)",
            f"  하방 meta {ev['meta_dd']*100:.2f}% vs rule {ev['rule_dd']*100:.2f}%",
            f"  {ev['reason']}",
        ]
        if ev["adopt"]:
            blend = _recommended_blend(ev)
            if enabled:
                _save_shadow(blend, ev)
                lines.append(f"  → blend 권고 {blend:.2f} (shadow 기록)")
            else:
                lines.append(f"  → blend 권고 {blend:.2f} (shadow OFF — 미기록)")
    lines.append("  ⚠️ 평가·권고만 — 라이브 blend 무변경")
    logger.info(" / ".join(lines))
    send_cron_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
