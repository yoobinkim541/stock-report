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
from lib import mock_llm_execution as llm_exec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
HORIZON = 20             # 거래일(보상 성숙 기준)
MIN_SAMPLES = 40         # 채택 최소 표본(미만이면 콜드스타트 유지)
MAX_POS = int(os.getenv("KR_MOCK_MAX_POS", "5"))   # 배치 바스켓 크기(eval 일치용)
_FEATS = ["ranker", "fund", "signal", "conf", "mom", "mom12", "hi52", "lowvol", "news"]


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

    - **측정 축**(표본 ≥5): weight = max(0, pearson) — 무신호면 0.
    - **미측정 축**(원장에 아직 없음 — 예: 신규 가격 축): 0 — 데이터 축적 전까지 가중 금지.
      (미측정 축에 DEFAULT 프라이어를 주면, 측정 축이 전부 무신호일 때 정규화가 미측정
      축으로 전량 쏠리는 함정 — 신규 축 추가 시 실제로 발생.)
    - 측정 축 양의 상관 합 0 이면 DEFAULT 가중 전체 폴백 — 전부-0 가중이 채택돼
      랭킹이 유니버스 순서로 붕괴하는 것 방지(#12).
    """
    from ml import kr_policy
    from ml.adaptive.learner import NEW_AXIS_MIN_PAIRS, robust_axis_weight
    _NEW_AXES = {"mom12", "hi52", "lowvol", "news"}  # 원장 축적 초기 축 — 강화 게이트(E)
    measured = {}
    for f in _FEATS:
        pairs = [(r["features"].get(f), r.get("fwd_excess"))
                 for r in train_rows
                 if r.get("features") and r["features"].get(f) is not None and r.get("fwd_excess") is not None]
        new = f in _NEW_AXES
        w = robust_axis_weight(pairs, min_pairs=NEW_AXIS_MIN_PAIRS if new else 5, stability=new)
        if w is not None:
            measured[f] = w
    total = sum(measured.values())
    if total <= 1e-9:
        # 신호 없음(또는 전축 미측정) → 합리적 기본 혼합 유지(붕괴 방지)
        return {f"w_{f}": kr_policy.DEFAULT_POLICY[f"w_{f}"] for f in _FEATS}
    return {f"w_{f}": round(measured.get(f, 0.0) / total, 4) for f in _FEATS}


def eval_policy(oos_rows: list[dict], params: dict, max_positions: int = 5) -> dict:
    """OOS 평가 = **배치와 동일한 결정일별 바스켓**(그날 정책점수 상위 max_positions 균등).

    excess = 평가일 바스켓 평균 초과수익의 평균, n = 평가일 수(최소 표본 게이트 단위).
    series = 실제 라벨 종료일 기준 비중첩 코호트 초과수익(n_independent = len(series)),
    mdd/index_mdd = 그 코호트를 차례로 보유한 일별 mark-to-market 곡선의 MDD(보유 경로 반영)
    (ml.adaptive.oos_eval.dated_topk_eval).
    """
    from ml import kr_policy
    from ml.adaptive import oos_eval
    return oos_eval.dated_topk_eval(
        oos_rows, lambda r: kr_policy.score(r.get("features", {}), params), max_positions)


# ── 보상 백필 ─────────────────────────────────────────────────────────────────

def _holding_window(ticker: str, start_date: str, horizon: int) -> dict | None:
    """보유기간 outcome 필드(수익·MDD·실제 진입/종료 거래일·일별 경로). 미성숙/실패 시 None.

    종목·지수를 **공통 날짜로 정렬**해 동일 구간으로 평가(거래정지·결측에 의한 종료일 어긋남 방지).
    MDD = 보유 horizon 구간의 peak-to-trough 낙폭(양수) → 지수 MDD 와 동일 단위로 비교 가능.
    """
    try:
        from providers import market_data
        from ml.data_pipeline import KR_BENCHMARK
        from ml.adaptive import oos_eval
        from ohlc_utils import align_common_index
        # 성숙 판정에 필요한 마지막 거래일(≈ 결정일 + horizon 거래일, 주말 여유 포함)
        import pandas as _pd
        _need = _pd.Timestamp(start_date) + _pd.Timedelta(days=int(horizon * 1.6) + 5)
        s = market_data.load_close_series_upto(ticker, _need)
        k = market_data.load_close_series_upto(KR_BENCHMARK, _need)
        if s is None or k is None:
            return None
        # 공통 거래일 정렬 — tz-aware/naive 혼재 흡수(종목 naive vs ^KS11 Asia/Seoul).
        # 예전엔 intersection 이 0건이 돼 성숙이 무기한 지연됐다(감사 2026-08-21).
        s, k = align_common_index(s, k, start=start_date)
        if s is None or k is None:
            return None
        return oos_eval.holding_window(s, k, horizon)               # 미성숙이면 None
    except Exception as e:
        logger.warning("가격 조회 실패 %s: %s", ticker, e)
        return None


def _default_price_fn(ticker: str, start_date: str, horizon: int):
    """보유기간 성과 — (종목수익, 지수수익, 종목MDD, 지수MDD). 미성숙/실패 시 None (LLM shadow 백필용)."""
    w = _holding_window(ticker, start_date, horizon)
    return None if w is None else (w["stock_ret"], w["idx_ret"], w["stock_mdd"], w["idx_mdd"])


def backfill_outcomes(ledger, *, horizon: int = HORIZON, window_fn=None,
                      sides: tuple[str, ...] = ("편입", "증액")) -> int:
    """미성숙 결정의 실현 초과수익 + 보유기간 MDD·경로를 outcomes 에 추가. 추가 건수 반환.

    window_fn(ticker, date, horizon) -> _holding_window 형식 dict | None.
    sides: 평가 대상 side. 기본은 라이브 매수(편입/증액). 랭킹 섀도 표면은 ("관측",) 로
    호출한다(선택편향 없는 IC 측정용 — lib/rank_shadow.py).
    """
    window_fn = window_fn or _holding_window
    added = 0
    for d in ledger.pending():
        # 매수(또는 섀도 관측) 결정만 보상 평가 대상(선택 정책 학습 신호)
        if d.get("side") not in sides:
            continue
        if d.get("ok") is False:   # ★미집행(주문실패) 결정 제외 — 팬텀 트레이드 오염 방지(US S6 미러·감사 확정)
            continue
        w = window_fn(d.get("ticker", ""), d.get("date", ""), horizon)
        if w is None:
            continue   # 아직 미성숙 → 다음 회차
        stock_ret, idx_ret = w["stock_ret"], w["idx_ret"]
        from ml.adaptive import costs
        gross = stock_ret - idx_ret
        # 매수(편입/증액) 결정만 평가 → 왕복 거래비용(수수료+증권거래세) 차감한 net 보상
        fwd_excess = gross - costs.round_trip_frac("KR")
        ledger.log_outcome({
            "decision_id": d["id"], "horizon": horizon,
            "matured_at": datetime.now(KST).strftime("%Y-%m-%d"),
            "entry_date": w["entry_date"], "exit_date": w["exit_date"], "path": w["path"],
            "stock_ret": round(stock_ret, 5), "index_ret": round(idx_ret, 5),
            "fwd_excess": round(fwd_excess, 5), "gross_excess": round(gross, 5),
            "fwd_mdd": round(w["stock_mdd"], 5), "idx_fwd_mdd": round(w["idx_mdd"], 5),
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
    # ★랭킹 섀도 — 주문 안 된 후보 포함 전 구간 성숙(선택편향 없는 IC 측정용).
    # 라이브 원장·정책 채택 게이트와 완전 분리 — 측정 전용.
    try:
        from ml.adaptive import evolution as _evo
        shadow_ledger = Ledger("kr_mock_shadow")
        shadow_added = backfill_outcomes(shadow_ledger, sides=("관측",))
        shadow_snap = _evo.snapshot(shadow_ledger.training_set())
        logger.info("랭킹 섀도 백필: %d건 성숙 · n=%s IC=%s CI=%s",
                    shadow_added, shadow_snap.get("n"), shadow_snap.get("realized_ic"),
                    shadow_snap.get("ic_ci"))
    except Exception as e:
        shadow_added, shadow_snap = 0, {}
        logger.warning("랭킹 섀도 백필 실패(무시): %s", e)
    llm_ledger = Ledger("kr_mock_llm_shadow")
    llm_added = llm_exec.backfill_shadow_outcomes(
        llm_ledger, market="KR", horizons_=llm_exec.horizons(), price_fn=_default_price_fn)
    llm_summary = llm_exec.summarize_shadow(
        llm_exec.shadow_training_set(llm_ledger), horizon=llm_exec.report_horizon())
    logger.info("LLM shadow 백필: %d건 성숙 · %s", llm_added, llm_exec.summary_line(llm_summary))

    rows = ledger.training_set()
    from ml.adaptive import evolution
    snap = evolution.snapshot(rows)
    if len(rows) < MIN_SAMPLES:
        evolution.record_learning("kr_mock", {
            "date": datetime.now(KST).strftime("%Y-%m-%d"), "adopted": False,
            "reason": "콜드스타트 (표본 미달)",
            "llm_shadow_added": llm_added,
            "llm_shadow_n": llm_summary.get("n"),
            "llm_shadow_hit_rate": llm_summary.get("hit_rate"),
            "llm_shadow_avg_delta": llm_summary.get("avg_delta"),
            "rank_shadow_added": shadow_added,
            "rank_shadow_n": shadow_snap.get("n"),
            "rank_shadow_ic": shadow_snap.get("realized_ic"),
            "rank_shadow_ic_ci": shadow_snap.get("ic_ci"),
            **snap})
        msg = (
            f"🇰🇷 KR 정책 학습 — 표본 {len(rows)}/{MIN_SAMPLES} 미달, 콜드스타트 유지(보류)\n"
            f"🧠 LLM shadow: {llm_exec.summary_line(llm_summary)}"
        )
        logger.info(msg)
        send_cron_telegram(msg)
        return 0

    # 지수 MDD 기준은 eval_policy 가 챌린저와 **같은 보유 날짜·같은 비중**의 지수 곡선으로 낸다
    # (index_mdd). 보유 경로가 없어 산출 불가면 위험 증거 없음 → 보류(기본값으로 대체하지 않음).
    from ml.adaptive import learner
    out = learner.refit_and_adopt(
        rows, kr_policy.get_policy(), fit_policy,
        lambda oos, params: eval_policy(oos, params, MAX_POS),    # 배치 바스켓과 동일
        min_samples=MIN_SAMPLES, embargo=HORIZON,
        trial_log=learner.trial_history("kr_mock"))
    logger.info("재학습 결과: %s", out["reason"])
    evolution.record_learning("kr_mock", {
        "date": datetime.now(KST).strftime("%Y-%m-%d"), "adopted": bool(out.get("adopted")),
        "reason": out.get("reason"),
        "excess_challenger": (out.get("challenger") or {}).get("excess"),
        "excess_champion": (out.get("champion") or {}).get("excess"),
        "mdd_challenger": (out.get("challenger") or {}).get("mdd"),
        "index_mdd_oos": out.get("risk_index_mdd"),
        "mdd_basis": (out.get("challenger") or {}).get("mdd_basis"),
        "n_oos": (out.get("challenger") or {}).get("n"),
        "n_oos_candidates": (out.get("challenger") or {}).get("n_candidates"),
        "n_oos_selected": (out.get("challenger") or {}).get("n_selected"),
        "n_oos_independent": (out.get("challenger") or {}).get("n_independent"),
        "n_oos_unresolved": (out.get("challenger") or {}).get("n_unresolved_days"),
        "sharpe_challenger": (out.get("evidence") or {}).get("sharpe_pp"),
        "psr_challenger": (out.get("evidence") or {}).get("psr"),
        "n_trials": (out.get("evidence") or {}).get("n_trials"),
        "trial_key": (out.get("evidence") or {}).get("trial_key"),
        "trial_sharpe": (out.get("evidence") or {}).get("trial_sharpe"),
        "candidate_params": out.get("candidate_params"),
        "llm_shadow_added": llm_added,
        "llm_shadow_n": llm_summary.get("n"),
        "llm_shadow_hit_rate": llm_summary.get("hit_rate"),
        "llm_shadow_avg_delta": llm_summary.get("avg_delta"),
        "rank_shadow_added": shadow_added,
        "rank_shadow_n": shadow_snap.get("n"),
        "rank_shadow_ic": shadow_snap.get("realized_ic"),
        "rank_shadow_ic_ci": shadow_snap.get("ic_ci"),
        **snap})
    send_cron_telegram(
        f"🇰🇷 KR 정책 강화 (표본 {len(rows)})\n{out['reason']}\n"
        f"🧠 LLM shadow: {llm_exec.summary_line(llm_summary)}\n"
        "⚠️ 모의 정책 — 실거래 미반영")
    return 0


if __name__ == "__main__":
    sys.exit(main())
