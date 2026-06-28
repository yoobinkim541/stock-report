#!/usr/bin/env python3
"""us_mock_report.py — 미국 모의 페이퍼트레이딩 일일 현황 + 로직 평가 스코어카드. kiwoom_mock_report 해외판.

★목표 가시화: 누적 초과수익(vs QQQ) + MDD(전략 vs 지수) + **로직 평가 스코어카드**(편입/퇴출 적중률·실현 IC).
= 사용자 요청 "로직이 맞게 작용하는지 평가". build_report()는 크론·/usmock 공용. read-only.

★정직: 선택 무엣지면 적중률 ~50%·IC ~0 으로 그대로 표시(과대광고 0).
크론 (평일 21:30 UTC = 미장 마감 후):
    30 21 * * 1-5 cd <repo> && flock -n /tmp/us_mock_report.lock uv run python crons/us_mock_report.py
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
import fmt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SEED_USD = float(os.getenv("KOREA_MOCK_SEED", "100000"))
_WD = ["월", "화", "수", "목", "금", "토", "일"]


def _pearson(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


# ── 평가 스코어카드 (순수 — 테스트 핵심) ──────────────────────────────────────

def compute_scorecard(rows: list[dict]) -> dict:
    """결정⋈결과 → 편입/퇴출 적중률 + 실현 IC(정책점수↔초과수익). '로직이 맞게 작용하는지' 정량."""
    buy = [r for r in rows if r.get("side") in ("편입", "증액") and r.get("correct") is not None]
    sell = [r for r in rows if r.get("side") in ("퇴출", "감액") and r.get("correct") is not None]

    def hit(rs):
        return round(sum(1 for r in rs if r.get("correct")) / len(rs) * 100.0, 1) if rs else None

    pairs = [(r.get("policy_score"), r.get("fwd_excess")) for r in buy
             if r.get("policy_score") is not None and r.get("fwd_excess") is not None]
    ic = round(_pearson([a for a, _ in pairs], [b for _, b in pairs]), 3) if len(pairs) >= 3 else None
    return {"buy_hit": hit(buy), "sell_hit": hit(sell), "ic": ic,
            "n_buy": len(buy), "n_sell": len(sell)}


def _scorecard_rows() -> list[dict]:
    try:
        from ml.adaptive import Ledger
        return Ledger("us_mock").training_set()
    except Exception as e:
        logger.warning("스코어카드 원장 조회 실패: %s", e)
        return []


def _snapshots() -> list[dict]:
    try:
        import store
        return [r for r in store.all("us_mock_history")
                if r.get("kind") == "snapshot" and r.get("nav") is not None]
    except Exception as e:
        logger.warning("히스토리 조회 실패: %s", e)
        return []


def _recent_decisions():
    try:
        from ml.adaptive import Ledger
        decs = Ledger("us_mock").read_decisions()
    except Exception as e:
        logger.warning("결정 원장 조회 실패: %s", e)
        return [], None
    if not decs:
        return [], None
    last = max(d.get("date", "") for d in decs)
    return [d for d in decs if d.get("date") == last and d.get("side") in ("편입", "퇴출")], last


def build_report(html: bool = False) -> str:
    """US 모의 현황 + 스코어카드 (크론·/paper us 공용). read-only. html=True 면 텔레그램 굵게."""
    _B = fmt.b if html else (lambda x: x)
    bal = kis_mock.get_balance()
    today = datetime.now(KST)
    hdr = (f"🧪 [모의] 미국 페이퍼트레이딩 현황 (KIS 해외)\n"
           f"📅 {today.strftime('%Y-%m-%d')} ({_WD[today.weekday()]}) {today.strftime('%H:%M')} KST")
    if not bal.get("ok"):
        return hdr + "\n━━━━━━━━━━━━━━━\n  ⚠️ 잔고 조회 실패 — KOREA_MOCK_ENABLED·계좌#·모의 연결 확인"

    positions, cash = bal["positions"], bal["cash_usd"]
    pos_value = bal["pos_value"] or 0.0
    nav = bal["nav"] or (pos_value + (cash or 0.0)) or SEED_USD

    snaps = _snapshots()
    inception_nav = float(snaps[0]["nav"]) if snaps else SEED_USD
    inception_date = str(snaps[0]["date"])[:10] if snaps else today.strftime("%Y-%m-%d")
    today_d = today.strftime("%Y-%m-%d")
    prev = [s for s in snaps if str(s["date"])[:10] < today_d]
    prev_nav = float(prev[-1]["nav"]) if prev else inception_nav

    from ml.adaptive import reward as _reward
    strat_mdd = _reward.max_drawdown([float(s["nav"]) for s in snaps] + [float(nav)]) * 100.0
    cum_ret = (nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0
    day_ret = (nav / prev_nav - 1.0) * 100.0 if prev_nav else 0.0

    try:
        from providers import market_data
        bm = market_data.fetch_kospi_stats(inception_date, symbol="QQQ")   # US 벤치마크
    except Exception as e:
        logger.warning("QQQ 통계 실패: %s", e)
        bm = {"return_pct": None, "mdd": None}
    q_ret = bm.get("return_pct")
    q_mdd_pct = bm["mdd"] * 100.0 if bm.get("mdd") is not None else None

    # 한눈 스코어카드 1줄 (NAV·누적·vs QQQ) — KR 리포트와 대칭
    excess = (cum_ret - q_ret) if q_ret is not None else None
    lines = [hdr, fmt.headline(
        f"📊 {_B(fmt.money(nav, abbrev=True))}", f"누적 {_B(fmt.pct(cum_ret))}",
        (f"QQQ대비 {_B(fmt.pct(excess) + 'p')} {'✅' if excess >= 0 else '⚠️'}" if excess is not None else None))]
    lines.append(fmt.sep())
    lines.append(f"NAV {fmt.money(nav)}  전일 {fmt.spct(day_ret, 2)}")
    if q_ret is not None:
        lines.append(f"누적 {fmt.spct(cum_ret, 2)}  (QQQ {fmt.spct(q_ret, 2)})")
    if q_mdd_pct is not None:
        ok = "✅" if strat_mdd <= q_mdd_pct else "⚠️지수보다 깊음"
        lines.append(f"MDD(최대낙폭) 전략 {strat_mdd:.1f}% / 지수 {q_mdd_pct:.1f}% {ok}")
    if cash is not None:
        lines.append(f"현금 {fmt.money(cash)}")

    # 보유 종목 — 2줄(종목·등락·평가액 / 수량·단가), 등락%·평가손익 합계 (KR과 대칭)
    held = {c: p for c, p in positions.items() if int(p.get("shares", 0) or 0) > 0}
    lines.append(fmt.sep(f"보유 {len(held)}종목"))
    total_pnl = 0.0
    for sym, p in sorted(held.items(), key=lambda kv: -(kv[1].get("value", 0) or 0)):
        avg = p.get("avg_price", 0) or 0
        cur = p.get("cur_price", 0) or 0
        sh  = int(p["shares"])
        val = p.get("value", 0) or 0
        ret = (cur - avg) / avg * 100 if avg > 0 else 0.0
        total_pnl += (cur - avg) * sh
        lines.append(f"{sym} {fmt.spct(ret)}  {fmt.money(val)}")
        lines.append(f"  {sh}주 · {avg:,.2f}→{cur:,.2f}")
    if not held:
        lines.append("(보유 없음 — 현금 100%)")
    else:
        cost = pos_value - total_pnl
        pnl_ret = (total_pnl / cost * 100.0) if cost else 0.0
        sm = ("+" if total_pnl >= 0 else "-") + fmt.money(abs(total_pnl))
        lines.append(f"─ 평가손익 {fmt.spct(pnl_ret)} ({sm})")

    # ★로직 평가 스코어카드 — 무엣지 기준선 대비 판정 라벨 인라인
    sc = compute_scorecard(_scorecard_rows())
    lines.append(fmt.sep("📊 로직 평가"))
    if sc["n_buy"] or sc["n_sell"]:
        def _verdict(hit):
            return "약한 엣지" if (hit is not None and hit >= 55) else "무엣지 수준"
        if sc["buy_hit"] is not None:
            lines.append(f"편입 적중률 {sc['buy_hit']}% (n={sc['n_buy']}) — {_verdict(sc['buy_hit'])}")
        if sc["sell_hit"] is not None:
            lines.append(f"퇴출 적중률 {sc['sell_hit']}% (n={sc['n_sell']}) — {_verdict(sc['sell_hit'])}")
        if sc["ic"] is not None:
            ic_v = "변별력 있음" if abs(sc["ic"]) >= 0.05 else "≈0(무변별)"
            lines.append(f"실현 IC {fmt.signed(sc['ic'], 2)} — {ic_v}")
        lines.append("※ IC=예측↔초과수익 상관 · 무엣지면 적중률 ~50%·IC ~0 (정직)")
    else:
        lines.append("성숙 결정 없음 — 평가 대기(horizon 경과 후)")

    recent, last = _recent_decisions()
    if recent:
        lines.append(fmt.sep(f"최근 편입/퇴출 ({last})"))
        for d in recent:
            icon = "📥" if d.get("side") == "편입" else "📤"
            rr = (d.get("rationale") or {}).get("one_line_reason", "")
            lines.append(f"{icon} {d.get('side')} {d.get('ticker')} — {rr}")

    lines.append(fmt.sep())
    lines.append("⚠️ 모의투자 — 실거래 아님")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== us_mock_report 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kis_mock.is_enabled():
        logger.info("KOREA_MOCK_ENABLED 아님 — 현황 보고 생략")
        return 0
    text = build_report()
    try:
        bal = kis_mock.get_balance()
        if bal.get("ok"):
            import store
            store.append("us_mock_history", {
                "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot", "at": "eod",
                "nav": bal.get("nav"), "cash": bal.get("cash_usd"),
                "positions": len([p for p in bal["positions"].values() if int(p.get("shares", 0) or 0) > 0])})
    except Exception as e:
        logger.warning("EOD 스냅샷 실패: %s", e)
    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
