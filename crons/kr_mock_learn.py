#!/usr/bin/env python3
"""
kr_mock_learn.py — KR 모의 정책 강화 루프 (보상 백필 + ★목적함수 재학습).

흐름:
  1) 보상 백필: 불변 원장의 미성숙 결정 중 horizon(20거래일) 경과분에 대해 실현 초과수익
     (종목수익 − KOSPI수익)을 계산해 kr_outcomes.jsonl 에 *추가*(결정 줄은 불변).
  2) 재학습: 결정⋈결과로 정책 가중치를 walk-forward 재적합 → ★목적함수 OOS 게이트
     (아웃퍼폼 최우선·MDD≤지수) 통과 시만 policy_kr_mock.json 채택. 표본 부족 시 보류.

크론 (토요일 02:00 UTC = 11:00 KST — 주말):
    0 2 * * 6 cd <repo> && flock -n /tmp/kr_mock_learn.lock uv run python crons/kr_mock_learn.py
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

import kiwoom_mock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
HORIZON = 20             # 거래일(보상 성숙 기준)
MIN_SAMPLES = 40         # 채택 최소 표본(미만이면 콜드스타트 유지)
MAX_POS = int(os.getenv("KR_MOCK_MAX_POS", "5"))   # 배치 바스켓 크기(eval 일치용)
_FEATS = ["ranker", "fund", "signal", "conf", "mom"]


# ── 통계 ──────────────────────────────────────────────────────────────────────

def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


# ── fit / eval (★목적함수) ────────────────────────────────────────────────────

def fit_policy(train_rows: list[dict]) -> dict:
    """피처별 (피처값 ↔ 실현 초과수익) 양의 상관에 비례한 가중치 적합.

    전 피처 무상관(양의 상관 합 0)이면 DEFAULT 가중으로 폴백 — 전부-0 가중이 채택돼
    랭킹이 유니버스 순서로 붕괴하는 것 방지(#12).
    """
    from ml import kr_policy
    weights = {}
    for f in _FEATS:
        pairs = [(r["features"].get(f), r.get("fwd_excess"))
                 for r in train_rows
                 if r.get("features") and r["features"].get(f) is not None and r.get("fwd_excess") is not None]
        if len(pairs) < 5:
            weights[f] = kr_policy.DEFAULT_POLICY.get(f"w_{f}", 0.1)
        else:
            weights[f] = max(0.0, _pearson([a for a, _ in pairs], [b for _, b in pairs]))
    total = sum(weights.values())
    if total <= 1e-9:
        # 신호 없음 → 합리적 기본 혼합 유지(붕괴 방지)
        return {f"w_{f}": kr_policy.DEFAULT_POLICY[f"w_{f}"] for f in _FEATS}
    return {f"w_{f}": round(weights[f] / total, 4) for f in _FEATS}


def eval_policy(oos_rows: list[dict], params: dict, max_positions: int = 5) -> dict:
    """OOS 평가 = **배치와 동일한 바스켓**(정책점수 상위 max_positions) 의 평균 초과수익 +
    보유기간 MDD(선택 종목 평균 peak-to-trough 낙폭 — 지수 MDD 와 동일 단위로 비교 가능).

    plan_rebalance 의 선택(top max_positions 균등)과 일치시켜 '학습 목적 = 실제 운용 목적'.
    """
    scored = []
    for r in oos_rows:
        if r.get("fwd_excess") is None:
            continue
        from ml import kr_policy
        sc = kr_policy.score(r.get("features", {}), params)
        scored.append((sc, r))
    if not scored:
        return {"excess": 0.0, "mdd": 1.0, "n": 0}
    scored.sort(key=lambda x: -x[0])
    sel = [r for _, r in scored[:max(1, max_positions)]]
    excess = sum(r["fwd_excess"] for r in sel) / len(sel)
    # 실제 낙폭: 선택 종목 보유기간 MDD 평균(없으면 음의 초과수익 크기로 보수적 대체)
    mdds = [r["fwd_mdd"] for r in sel if r.get("fwd_mdd") is not None]
    if mdds:
        mdd = sum(mdds) / len(mdds)
    else:
        negs = [r["fwd_excess"] for r in sel if r["fwd_excess"] < 0]
        mdd = abs(sum(negs) / len(negs)) if negs else 0.0
    return {"excess": round(excess, 4), "mdd": round(mdd, 4), "n": len(scored)}


# ── 보상 백필 ─────────────────────────────────────────────────────────────────

def _default_price_fn(ticker: str, start_date: str, horizon: int):
    """보유기간 성과 — (종목수익, 지수수익, 종목MDD, 지수MDD). 미성숙/실패 시 None.

    종목·지수를 **공통 날짜로 정렬**해 동일 구간으로 평가(거래정지·결측에 의한 종료일 어긋남 방지).
    MDD = 보유 horizon 구간의 peak-to-trough 낙폭(양수) → 지수 MDD 와 동일 단위로 비교 가능.
    """
    try:
        import yfinance as yf
        import pandas as pd
        from ml.data_pipeline import KR_BENCHMARK
        from ml.adaptive import reward as _reward
        start = pd.Timestamp(start_date)
        end = start + pd.Timedelta(days=horizon * 2 + 10)
        data = yf.download([ticker, KR_BENCHMARK], start=start.strftime("%Y-%m-%d"),
                           end=end.strftime("%Y-%m-%d"), progress=False, group_by="ticker")
        try:
            s = data[ticker]["Close"].dropna()
            k = data[KR_BENCHMARK]["Close"].dropna()
        except Exception:
            return None
        common = s.index.intersection(k.index)          # 공통 거래일로 정렬
        s, k = s.reindex(common), k.reindex(common)
        if len(common) <= horizon:
            return None                                   # 미성숙
        s_win, k_win = s.iloc[:horizon + 1], k.iloc[:horizon + 1]
        stock_ret = float(s_win.iloc[-1]) / float(s_win.iloc[0]) - 1.0
        idx_ret = float(k_win.iloc[-1]) / float(k_win.iloc[0]) - 1.0
        stock_mdd = _reward.max_drawdown([float(x) for x in s_win.values])
        idx_mdd = _reward.max_drawdown([float(x) for x in k_win.values])
        return stock_ret, idx_ret, stock_mdd, idx_mdd
    except Exception as e:
        logger.warning("가격 조회 실패 %s: %s", ticker, e)
        return None


def backfill_outcomes(ledger, *, horizon: int = HORIZON, price_fn=None) -> int:
    """미성숙 결정의 실현 초과수익 + 보유기간 MDD 를 outcomes 에 추가. 추가 건수 반환."""
    price_fn = price_fn or _default_price_fn
    added = 0
    for d in ledger.pending():
        # 편입/증액(매수) 결정만 보상 평가 대상(선택 정책 학습 신호)
        if d.get("side") not in ("편입", "증액"):
            continue
        res = price_fn(d.get("ticker", ""), d.get("date", ""), horizon)
        if res is None:
            continue   # 아직 미성숙 → 다음 회차
        stock_ret, idx_ret, stock_mdd, idx_mdd = res
        fwd_excess = stock_ret - idx_ret
        ledger.log_outcome({
            "decision_id": d["id"], "horizon": horizon,
            "matured_at": datetime.now(KST).strftime("%Y-%m-%d"),
            "stock_ret": round(stock_ret, 5), "index_ret": round(idx_ret, 5),
            "fwd_excess": round(fwd_excess, 5),
            "fwd_mdd": round(stock_mdd, 5), "idx_fwd_mdd": round(idx_mdd, 5),
            "success": bool(fwd_excess > 0),
        })
        added += 1
    return added


# ── 진입점 ────────────────────────────────────────────────────────────────────

from lib.cron_common import send_cron_telegram


def main() -> int:
    logger.info("=== kr_mock_learn 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kiwoom_mock.is_enabled():
        logger.info("KIWOOM_MOCK_ENABLED 아님 — 학습 생략")
        return 0

    from ml.adaptive import Ledger
    from ml import kr_policy
    ledger = Ledger("kr_mock")

    added = backfill_outcomes(ledger)
    logger.info("보상 백필: %d건 성숙", added)

    rows = ledger.training_set()
    if len(rows) < MIN_SAMPLES:
        msg = f"🇰🇷 KR 정책 학습 — 표본 {len(rows)}/{MIN_SAMPLES} 미달, 콜드스타트 유지(보류)"
        logger.info(msg)
        send_cron_telegram(msg)
        return 0

    # 지수 MDD 기준 — 결정들의 *보유기간 지수 MDD 평균*(eval 의 종목 MDD 와 동일 단위로 비교).
    idx_mdds = [r["idx_fwd_mdd"] for r in rows if r.get("idx_fwd_mdd") is not None]
    if idx_mdds:
        index_mdd = sum(idx_mdds) / len(idx_mdds)
    else:
        index_mdd = 0.20
        logger.warning("보유기간 지수 MDD 없음 — 기본 0.20")

    from ml.adaptive import learner
    out = learner.refit_and_adopt(
        rows, kr_policy.get_policy(), fit_policy,
        lambda oos, params: eval_policy(oos, params, MAX_POS),    # 배치 바스켓과 동일
        index_mdd=index_mdd, min_samples=MIN_SAMPLES, embargo=HORIZON)
    logger.info("재학습 결과: %s", out["reason"])
    send_cron_telegram(f"🇰🇷 KR 정책 강화 (표본 {len(rows)})\n{out['reason']}\n⚠️ 모의 정책 — 실거래 미반영")
    return 0


if __name__ == "__main__":
    sys.exit(main())
