#!/usr/bin/env python3
"""us_mock_learn.py — US 모의 정책 강화 루프 (보상 백필 + ★목적함수 OOS 재학습). kr_mock_learn 해외판.

흐름:
  1) 보상 백필: 불변 원장의 미성숙 결정(horizon 20거래일 경과)에 실현 초과수익(종목−QQQ) +
     **side-aware 정답 플래그**(편입/증액: 초과>0 이면 적중 · 퇴출/감액: 초과<0 이면 잘 뺌) → us_outcomes.jsonl 추가.
  2) 재학습: 편입측 결정⋈결과로 us_policy 가중치 walk-forward 재적합 → ★목적함수 OOS 게이트 +
     챔피언-챌린저(held-out 우위 시만 policy_us_mock.json 채택). 표본 부족 시 콜드스타트 유지.

★정직: 선택 무엣지(6티어)면 게이트가 채택 거부 → 정책 불변(열화 0). 스코어카드(S5)가 정답률 그대로 공개.
크론 (토 03:00 UTC):  0 3 * * 6 cd <repo> && flock -n /tmp/us_mock_learn.lock uv run python crons/us_mock_learn.py
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import kis_mock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
HORIZON = 20
MIN_SAMPLES = 40
MAX_POS = int(os.getenv("US_MOCK_MAX_POS", "5"))
BENCHMARK = "QQQ"
_FEATS = ["ranker", "value", "quality", "mom", "conf"]
_BUY = ("편입", "증액")


def _pearson(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def _buy_rows(rows):
    return [r for r in rows if r.get("side") in _BUY]


def fit_policy(train_rows: list[dict]) -> dict:
    """편입측 (피처값 ↔ 실현 초과수익) 양의 상관 비례 가중치. 무신호면 DEFAULT 폴백(붕괴 방지)."""
    from ml import us_policy
    rows = _buy_rows(train_rows)
    weights = {}
    for f in _FEATS:
        pairs = [(r["features"].get(f), r.get("fwd_excess")) for r in rows
                 if r.get("features") and r["features"].get(f) is not None and r.get("fwd_excess") is not None]
        if len(pairs) < 5:
            weights[f] = us_policy.DEFAULT_POLICY.get(f"w_{f}", 0.1)
        else:
            weights[f] = max(0.0, _pearson([a for a, _ in pairs], [b for _, b in pairs]))
    total = sum(weights.values())
    if total <= 1e-9:
        return {f"w_{f}": us_policy.DEFAULT_POLICY[f"w_{f}"] for f in _FEATS}
    return {f"w_{f}": round(weights[f] / total, 4) for f in _FEATS}


def eval_policy(oos_rows: list[dict], params: dict, max_positions: int = 5) -> dict:
    """OOS = 정책점수 상위 max_positions 바스켓(편입측)의 평균 초과수익 + 보유기간 MDD."""
    from ml import us_policy
    scored = [(us_policy.score(r.get("features", {}), params), r)
              for r in _buy_rows(oos_rows) if r.get("fwd_excess") is not None]
    if not scored:
        return {"excess": 0.0, "mdd": 1.0, "n": 0}
    scored.sort(key=lambda x: -x[0])
    sel = [r for _, r in scored[:max(1, max_positions)]]
    excess = sum(r["fwd_excess"] for r in sel) / len(sel)
    mdds = [r["fwd_mdd"] for r in sel if r.get("fwd_mdd") is not None]
    if mdds:
        mdd = sum(mdds) / len(mdds)
    else:
        negs = [r["fwd_excess"] for r in sel if r["fwd_excess"] < 0]
        mdd = abs(sum(negs) / len(negs)) if negs else 0.0
    return {"excess": round(excess, 4), "mdd": round(mdd, 4), "n": len(scored)}


def _default_price_fn(ticker: str, start_date: str, horizon: int):
    """보유기간 (종목수익, QQQ수익, 종목MDD, QQQ MDD). 미성숙/실패 → None. 공통 거래일 정렬."""
    try:
        import yfinance as yf
        import pandas as pd
        from ml.adaptive import reward as _reward
        start = pd.Timestamp(start_date)
        end = start + pd.Timedelta(days=horizon * 2 + 10)
        data = yf.download([ticker, BENCHMARK], start=start.strftime("%Y-%m-%d"),
                           end=end.strftime("%Y-%m-%d"), progress=False, group_by="ticker")
        try:
            s = data[ticker]["Close"].dropna()
            b = data[BENCHMARK]["Close"].dropna()
        except Exception:
            return None
        common = s.index.intersection(b.index)
        s, b = s.reindex(common), b.reindex(common)
        if len(common) <= horizon:
            return None
        sw, bw = s.iloc[:horizon + 1], b.iloc[:horizon + 1]
        return (float(sw.iloc[-1]) / float(sw.iloc[0]) - 1.0,
                float(bw.iloc[-1]) / float(bw.iloc[0]) - 1.0,
                _reward.max_drawdown([float(x) for x in sw.values]),
                _reward.max_drawdown([float(x) for x in bw.values]))
    except Exception as e:
        logger.warning("가격 조회 실패 %s: %s", ticker, e)
        return None


def backfill_outcomes(ledger, *, horizon: int = HORIZON, price_fn=None) -> int:
    """미성숙 결정의 실현 초과수익 + side-aware 정답 플래그를 outcomes 에 추가. 추가 건수 반환."""
    price_fn = price_fn or _default_price_fn
    added = 0
    for d in ledger.pending():
        side = d.get("side")
        if side not in ("편입", "증액", "퇴출", "감액"):
            continue
        if d.get("ok") is False:   # ★미집행(주문실패) 결정은 학습 제외 — 팬텀 트레이드 오염 방지(S6)
            continue
        res = price_fn(d.get("ticker", ""), d.get("date", ""), horizon)
        if res is None:
            continue
        stock_ret, idx_ret, stock_mdd, idx_mdd = res
        from ml.adaptive import costs
        gross = stock_ret - idx_ret
        is_buy = side in _BUY
        # 매수 결정 보상은 왕복 거래비용 차감(net) — 정책이 비용 넘는 엣지만 학습. 매도는 gross(회피 판단).
        net = (gross - costs.round_trip_frac("US")) if is_buy else gross
        correct = (net > 0) if is_buy else (net < 0)   # 퇴출: 미달이면 잘 뺀 것
        ledger.log_outcome({
            "decision_id": d["id"], "side": side, "horizon": horizon,
            "matured_at": datetime.now(KST).strftime("%Y-%m-%d"),
            "stock_ret": round(stock_ret, 5), "index_ret": round(idx_ret, 5),
            "fwd_excess": round(net, 5), "gross_excess": round(gross, 5),
            "fwd_mdd": round(stock_mdd, 5),
            "idx_fwd_mdd": round(idx_mdd, 5), "correct": bool(correct), "success": bool(correct),
        })
        added += 1
    return added


from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== us_mock_learn 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kis_mock.is_enabled():
        logger.info("KOREA_MOCK_ENABLED 아님 — 학습 생략")
        return 0
    from ml.adaptive import Ledger, learner
    from ml import us_policy
    ledger = Ledger("us_mock")

    added = backfill_outcomes(ledger)
    logger.info("보상 백필: %d건 성숙", added)
    rows = ledger.training_set()
    buy = _buy_rows(rows)
    from ml.adaptive import evolution
    snap = evolution.snapshot(rows)
    if len(buy) < MIN_SAMPLES:
        evolution.record_learning("us_mock", {
            "date": datetime.now(KST).strftime("%Y-%m-%d"), "adopted": False,
            "reason": "콜드스타트 (표본 미달)", **snap})
        msg = f"🇺🇸 US 정책 학습 — 편입표본 {len(buy)}/{MIN_SAMPLES} 미달, 콜드스타트 유지(보류)"
        logger.info(msg)
        send_cron_telegram(msg)
        return 0

    idx_mdds = [r["idx_fwd_mdd"] for r in buy if r.get("idx_fwd_mdd") is not None]
    index_mdd = (sum(idx_mdds) / len(idx_mdds)) if idx_mdds else 0.20
    out = learner.refit_and_adopt(
        rows, us_policy.get_policy(), fit_policy,
        lambda oos, params: eval_policy(oos, params, MAX_POS),
        index_mdd=index_mdd, min_samples=MIN_SAMPLES, embargo=HORIZON)
    logger.info("재학습 결과: %s", out["reason"])
    evolution.record_learning("us_mock", {
        "date": datetime.now(KST).strftime("%Y-%m-%d"), "adopted": bool(out.get("adopted")),
        "reason": out.get("reason"),
        "excess_challenger": (out.get("challenger") or {}).get("excess"),
        "excess_champion": (out.get("champion") or {}).get("excess"),
        "mdd_challenger": (out.get("challenger") or {}).get("mdd"),
        "n_oos": (out.get("challenger") or {}).get("n"),
        "candidate_params": out.get("candidate_params"), **snap})
    send_cron_telegram(f"🇺🇸 US 정책 강화 (편입표본 {len(buy)})\n{out['reason']}\n⚠️ 모의 정책 — 실거래 미반영")
    return 0


if __name__ == "__main__":
    sys.exit(main())
