#!/usr/bin/env python3
"""
entry_adaptive_learn.py — 해외 단기진입 enter_threshold 의 라이브 outcome 적응 학습 (Phase 2).

기존 backtest/entry_calibration(월간, 과거 walk-forward)을 **라이브 신호 성과**로 보완.
signal_outcomes(자동 진입신호의 R-multiple 실현결과)에서 (점수 → R) 관계를 학습해
진입 임계값(enter_threshold)을 ★목적함수 OOS 게이트로 재추정한다.

안전: 결과는 **shadow 파일**(entry_score_params_adaptive.json)에만 기록한다. 라이브 진입
점수는 이를 `ADAPTIVE_ENTRY_ENABLED=true` 일 때만 반영(기본 off → 라이브 advice 불변).
표본 부족/미개선이면 보류. (signal_outcomes 는 피처가 없어 가중치까진 학습 불가 — 임계값만.)

크론 (월 1일 14:30 UTC — 기존 entry_calibration 직후):
    30 14 1 * * cd <repo> && uv run python crons/entry_adaptive_learn.py
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
SHADOW_PATH = Path(os.path.expanduser("~/reports/ml-cache/entry_score_params_adaptive.json"))
MIN_SAMPLES = 20                          # signal_outcomes 는 희소 → 보수적 최소 표본
THRESH_GRID = [round(0.45 + 0.05 * i, 2) for i in range(9)]   # 0.45~0.85
DOWNSIDE_CAP_R = 1.0                      # 진입군 평균 하방이 -1R 보다 나쁘면 채택 안 함(★MDD 제약)


def _samples() -> list[tuple[float, float]]:
    """signal_outcomes → [(score, r_multiple)] (score 있는 것만)."""
    try:
        import store
        rows = store.all("signal_outcomes")
    except Exception as e:
        logger.warning("signal_outcomes 조회 실패: %s", e)
        return []
    out = []
    for r in rows:
        sc, rm = r.get("score"), r.get("r_multiple")
        if sc is None or rm is None:
            continue
        try:
            out.append((float(sc), float(rm)))
        except (TypeError, ValueError):
            continue
    return out


def _eval_threshold(samples: list[tuple[float, float]], thr: float) -> dict:
    """thr 이상 점수로 '진입'한 신호들의 평균 R(excess) + 하방(mdd) + 건수(n)."""
    entered = [rm for sc, rm in samples if sc >= thr]
    if not entered:
        return {"excess": 0.0, "mdd": 1.0, "n": 0, "win_rate": 0.0}
    mean_r = sum(entered) / len(entered)
    negs = [r for r in entered if r < 0]
    mdd = abs(sum(negs) / len(negs)) if negs else 0.0      # 진입군 평균 손실(R) — DOWNSIDE_CAP_R 와 동일 단위
    win = sum(1 for r in entered if r > 0) / len(entered)
    return {"excess": round(mean_r, 3), "mdd": round(mdd, 3), "n": len(entered), "win_rate": round(win, 3)}


def _best_threshold(samples: list[tuple[float, float]]) -> float:
    """train 에서 진입군 평균 R 을 최대화하는 임계값(최소 진입건수 5 보장)."""
    best_thr, best_r = THRESH_GRID[0], float("-inf")
    for thr in THRESH_GRID:
        ev = _eval_threshold(samples, thr)
        if ev["n"] >= 5 and ev["excess"] > best_r:
            best_r, best_thr = ev["excess"], thr
    return best_thr


def learn(samples: list[tuple[float, float]]) -> dict:
    """★목적함수 OOS 게이트로 enter_threshold 학습. 반환 보고 dict."""
    from ml.adaptive import reward as _reward
    n = len(samples)
    if n < MIN_SAMPLES:
        return {"adopted": False, "reason": f"표본 {n}/{MIN_SAMPLES} 미달 — 보류(라이브 불변)", "n": n}

    # 날짜 정보 없음 → 순서(기록순) 기준 train/OOS 분할(앞 60% train)
    split = int(n * 0.6)
    train, oos = samples[:split], samples[split:]
    if len(train) < 5 or len(oos) < 5:
        return {"adopted": False, "reason": "분할 표본 부족 — 보류", "n": n}

    cand_thr = _best_threshold(train)
    chal = _eval_threshold(oos, cand_thr)

    # 현행 임계값(라이브) 대비 OOS 평가
    try:
        from ml.entry_analyzer import get_score_params
        cur_thr = float(get_score_params().get("enter_threshold", 0.62))
    except Exception:
        cur_thr = 0.62
    champ = _eval_threshold(oos, cur_thr)

    # ★목적함수: 진입군 평균 R>0(절대 아웃퍼폼) + 챔피언 초과 + 하방 ≤ 1R(MDD 제약) + OOS 표본 충분
    adopt = _reward.should_adopt(
        {"excess": chal["excess"], "mdd": chal["mdd"], "n": chal["n"]},
        {"excess": champ["excess"], "mdd": champ["mdd"], "n": champ["n"]},
        index_mdd=DOWNSIDE_CAP_R, min_samples=max(5, MIN_SAMPLES // 4))

    if adopt:
        _save_shadow(cand_thr, chal)
        reason = (f"채택(shadow) ✅ thr {cur_thr:.2f}→{cand_thr:.2f} · OOS 평균R {chal['excess']:+.2f}"
                  f">{champ['excess']:+.2f} · 승률 {chal['win_rate']*100:.0f}% · 하방 {chal['mdd']:.2f}R")
    else:
        reason = (f"보류 ⏸️ 후보 thr {cand_thr:.2f} · OOS 평균R {chal['excess']:+.2f} vs 현행 {champ['excess']:+.2f}"
                  f" (하방 {chal['mdd']:.2f}R, n={chal['n']})")
    return {"adopted": adopt, "reason": reason, "cand_thr": cand_thr, "n": n,
            "chal": chal, "champ": champ, "cur_thr": cur_thr}


def _save_shadow(thr: float, ev: dict) -> None:
    from ml.entry_analyzer import _clamp_score_params
    thr = _clamp_score_params({"enter_threshold": thr})["enter_threshold"]
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHADOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"enter_threshold": thr, "_meta": {
        "learned_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "oos_mean_r": ev["excess"], "oos_n": ev["n"], "win_rate": ev["win_rate"]}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHADOW_PATH)


def _send(text: str) -> None:
    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception:
        pass


def main() -> int:
    logger.info("=== entry_adaptive_learn 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    samples = _samples()
    out = learn(samples)
    logger.info("결과: %s", out["reason"])
    enabled = os.getenv("ADAPTIVE_ENTRY_ENABLED", "false").lower() == "true"
    _send("\n".join([
        "🎯 진입 임계값 적응 학습 (라이브 outcome)",
        out["reason"],
        f"shadow 반영: {'ON(라이브 적용)' if enabled else 'OFF(shadow만 — 라이브 불변)'}",
    ]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
