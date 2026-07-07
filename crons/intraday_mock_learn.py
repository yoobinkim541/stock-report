#!/usr/bin/env python3
"""
intraday_mock_learn.py — 단기 모의 정책 주간 학습 + ★게이트 평가 (kr/us_intraday).

일간 모의와 달리 **보상 백필 불필요** — 엔진이 청산 즉시 net-of-cost R(fwd_excess)을
기록한다. pending 잔존은 orphan(엔진이 수리) — 건수만 경고.

흐름 (시장별):
  1) 재학습: 원장(결정⋈결과) → 축별 (축점수↔실현 net R) 상관 적합(전 축 신규 취급·
     stability 게이트) → walk-forward OOS ★목적함수 채택 시만 policy_{kr,us}_intraday 갱신.
  2) ★게이트 평가 (판정·기록·통지만 — INTRADAY_SHADOW_ONLY 해제는 항상 수동):
     트레이드 ≥100 · 순비용 기대치 >0 · PSR(0 대비) ≥0.95 · θ 그리드 PBO <0.5 → GO.
     미달은 OBSERVE/NO-GO 정직 라벨 (Tier4~6 관례).

크론 (토 02:30 UTC — kr_mock_learn 02:00 직후·03:30 ranker 재학습과 분산):
    30 2 * * 6 cd <repo> && flock -n /tmp/intraday_mock_learn.lock uv run python crons/intraday_mock_learn.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MIN_SAMPLES = 100                      # 트레이드가 많아 통계 파워가 빨리 참 — 일간(40)보다 높게
RISK_FRAC = float(os.getenv("INTRADAY_RISK_PER_TRADE", "0.005"))
_BENCH = {"kr": "^KS11", "us": "QQQ"}
_THETA_GRID = (0.45, 0.50, 0.55, 0.60, 0.65)   # PBO 용 진입문턱 변형


def _trade_frac(r: dict) -> float | None:
    """실현 net R → 슬리브 분수 수익 (트레이드당 리스크 = RISK_FRAC 정의에 의해)."""
    v = r.get("fwd_excess")
    return None if v is None else float(v) * RISK_FRAC


# ── fit / eval ────────────────────────────────────────────────────────────────

def make_fit(market: str):
    from ml import intraday_policy as ip
    from ml.adaptive.learner import NEW_AXIS_MIN_PAIRS, robust_axis_weight

    def fit_policy(train_rows: list[dict]) -> dict:
        """축별 (축점수 ↔ 실현 net R) 상관 가중 — 전 축 신규 취급(stability 필수).

        무신호(전축 상관≤0)면 DEFAULT 가중 폴백 — 전부-0 가중 채택으로 선택이
        붕괴하는 것 방지 (kr_mock_learn #12 미러).
        """
        measured = {}
        for f in ip.AXES:
            pairs = [(r["features"].get(f), r.get("fwd_excess"))
                     for r in train_rows
                     if r.get("features") and r["features"].get(f) is not None
                     and r.get("fwd_excess") is not None]
            w = robust_axis_weight(pairs, min_pairs=NEW_AXIS_MIN_PAIRS, stability=True)
            if w is not None:
                measured[f] = w
        total = sum(measured.values())
        if total <= 1e-9:
            return {f"w_{f}": ip.DEFAULTS[market][f"w_{f}"] for f in ip.AXES}
        return {f"w_{f}": round(measured.get(f, 0.0) / total, 4) for f in ip.AXES}

    return fit_policy


def eval_policy(oos_rows: list[dict], params: dict, market: str) -> dict:
    """OOS 평가 = 후보 가중으로 θ_entry 통과했을 트레이드의 슬리브 분수 성과.

    excess = 평균 트레이드 분수수익, mdd = 그 트레이드열 복리 곡선의 MDD (지수 MDD 와 동일 단위).
    """
    from ml import intraday_policy as ip
    from ml.adaptive import reward
    full = {**ip.DEFAULTS[market], **(params or {})}
    theta = float(full.get("theta_entry", 0.55))
    sel = []
    for r in sorted(oos_rows, key=lambda x: (x.get("date", ""), x.get("id", ""))):
        f = _trade_frac(r)
        if f is None or not r.get("features"):
            continue
        if ip.score(r["features"], full, market) >= theta:
            sel.append(f)
    if not sel:
        return {"excess": 0.0, "mdd": 1.0, "n": 0}    # 아무것도 안 뽑는 정책 — 채택 불가 방향
    nav, navs = 1.0, [1.0]
    for f in sel:
        nav *= (1.0 + f)
        navs.append(nav)
    return {"excess": round(sum(sel) / len(sel), 6),
            "mdd": round(reward.max_drawdown(navs), 5), "n": len(sel)}


# ── ★게이트 (표시·판정 전용 — 집행 전환은 수동) ──────────────────────────────

def gate_eval(rows: list[dict], market: str) -> dict:
    """shadow/모의 트레이드 분포 → GO/OBSERVE/NO-GO/콜드스타트 verdict."""
    from ml import intraday_policy as ip
    fracs, scored = [], []
    news_n = 0
    for r in sorted(rows, key=lambda x: (x.get("date", ""), x.get("id", ""))):
        f = _trade_frac(r)
        if f is None:
            continue
        fracs.append(f)
        if r.get("features"):
            if r["features"].get("news") is not None:
                news_n += 1
            scored.append((ip.score(r["features"], None, market), f))
    n = len(fracs)
    out = {"n": n, "mean_net_r": None, "psr": None, "pbo": None,
           "news_axis_n": news_n, "verdict": "콜드스타트"}
    if n < 10:
        return out
    mean_r = sum(r.get("fwd_excess", 0) or 0 for r in rows if r.get("fwd_excess") is not None) / n
    out["mean_net_r"] = round(mean_r, 4)
    try:
        from ml.validation import _skew_kurt, pbo_cscv, probabilistic_sharpe_ratio, sharpe_ratio
        pp = sharpe_ratio(fracs)["pp"]
        skew, kurt = _skew_kurt(fracs)
        out["psr"] = round(probabilistic_sharpe_ratio(pp, 0.0, n, skew, kurt), 4)
        if len(scored) >= 20:
            matrix = [[f if sc >= th else 0.0 for th in _THETA_GRID] for sc, f in scored]
            pb = pbo_cscv(matrix, n_splits=min(10, len(scored) // 2 * 2))
            if pb:
                out["pbo"] = round(pb["pbo"], 3)
    except Exception as e:
        logger.warning("[%s] 게이트 통계 실패: %s", market, e)
    if n < MIN_SAMPLES:
        out["verdict"] = "콜드스타트"
    elif mean_r <= 0:
        out["verdict"] = "NO-GO"
    elif (out["psr"] or 0) >= 0.95 and (out["pbo"] is None or out["pbo"] < 0.5):
        out["verdict"] = "GO"
    else:
        out["verdict"] = "OBSERVE"
    return out


def _index_mdd(market: str, dates: list[str]) -> float:
    """OOS 기간 벤치마크 일봉 MDD (동일 분수 단위). 실패 시 보수 0.10."""
    try:
        import yfinance as yf
        from ml.adaptive import reward
        ds = sorted(d for d in dates if d)
        if not ds:
            return 0.10
        data = yf.download(_BENCH[market], start=ds[0], end=None, progress=False)
        closes = [float(x) for x in data["Close"].dropna().values]
        if len(closes) < 5:
            return 0.10
        return reward.max_drawdown(closes)
    except Exception as e:
        logger.warning("[%s] 벤치마크 MDD 실패(기본 0.10): %s", market, e)
        return 0.10


# ── 진입점 ────────────────────────────────────────────────────────────────────

def run_market(market: str) -> str:
    from ml import intraday_policy as ip
    from ml.adaptive import Ledger, evolution, learner
    flag = "🇰🇷" if market == "kr" else "🇺🇸"
    ledger = Ledger(f"{market}_intraday")
    rows = ledger.training_set()
    orphans = [d for d in ledger.pending() if d.get("side") == "단기진입" and d.get("ok") is not False]
    gate = gate_eval(rows, market)
    snap = evolution.snapshot(rows)
    date = datetime.now(KST).strftime("%Y-%m-%d")

    base_rec = {"date": date, "gate": gate, **snap}
    if len(rows) < MIN_SAMPLES:
        evolution.record_learning(f"{market}_intraday", {
            **base_rec, "adopted": False, "reason": f"콜드스타트 (표본 {len(rows)}/{MIN_SAMPLES})"})
        return (f"{flag} 단기 {market.upper()} — 표본 {len(rows)}/{MIN_SAMPLES} 콜드스타트"
                + (f" · ⚠️orphan {len(orphans)}" if orphans else ""))

    idx_mdd = _index_mdd(market, [r.get("date", "") for r in rows])
    out = learner.refit_and_adopt(
        rows, ip.get_policy(market), make_fit(market),
        lambda oos, params: eval_policy(oos, params, market),
        index_mdd=idx_mdd, min_samples=MIN_SAMPLES, embargo=1)   # 당일 청산 — 라벨 수평선 1일
    logger.info("[%s] 재학습: %s", market, out["reason"])
    evolution.record_learning(f"{market}_intraday", {
        **base_rec, "adopted": bool(out.get("adopted")), "reason": out.get("reason"),
        "excess_challenger": (out.get("challenger") or {}).get("excess"),
        "excess_champion": (out.get("champion") or {}).get("excess"),
        "mdd_challenger": (out.get("challenger") or {}).get("mdd"),
        "n_oos": (out.get("challenger") or {}).get("n"),
        "candidate_params": out.get("candidate_params")})
    g = gate
    gate_line = (f"게이트 {g['verdict']} — n {g['n']}·순R {g.get('mean_net_r')}"
                 f"·PSR {g.get('psr')}·PBO {g.get('pbo')}"
                 + (f"·news축 표본 {g['news_axis_n']}" if g.get("news_axis_n") is not None else ""))
    return (f"{flag} 단기 {market.upper()} (표본 {len(rows)})\n{out['reason']}\n{gate_line}"
            + (f"\n⚠️ orphan pending {len(orphans)}건" if orphans else ""))


def main() -> int:
    logger.info("=== intraday_mock_learn 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if os.getenv("INTRADAY_MOCK_ENABLED", "false").lower() != "true":
        logger.info("INTRADAY_MOCK_ENABLED 아님 — 학습 생략")
        return 0
    lines = []
    for market in ("kr", "us"):
        try:
            lines.append(run_market(market))
        except Exception as e:
            logger.exception("[%s] 학습 예외: %s", market, e)
            lines.append(f"⚠️ {market.upper()} 학습 예외: {e}")
    from lib.cron_common import send_cron_telegram
    send_cron_telegram("🕐 단기 정책 학습\n" + "\n".join(lines)
                       + "\n⚠️ 모의/shadow — 실거래 미반영·게이트 GO 여도 집행 전환은 수동")
    return 0


if __name__ == "__main__":
    sys.exit(main())
