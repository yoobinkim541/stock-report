#!/usr/bin/env python3
"""
kiwoom_mock_report.py — 국내 모의 페이퍼트레이딩 일일 현황 보고.

★목표 가시화: 누적 초과수익(vs KOSPI) + MDD(전략 vs 지수). 1순위 아웃퍼폼, 2순위 MDD≤지수.
보유 평가·손익·NAV + 최근 편입/퇴출 사유(불변 원장에서)도 함께. build_report()는 크론·/mock 공용.

크론 (평일 06:40 UTC = 15:40 KST, 장 마감 직후):
    40 6 * * 1-5 cd <repo> && flock -n /tmp/kiwoom_mock_report.lock uv run python crons/kiwoom_mock_report.py
"""
from __future__ import annotations

import logging
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
SEED_KRW = float(os.getenv("KIWOOM_MOCK_SEED", "10000000"))
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _snapshots() -> list[dict]:
    try:
        import store
        return [r for r in store.all("kr_mock_history")
                if r.get("kind") == "snapshot" and r.get("nav") is not None]
    except Exception as e:
        logger.warning("히스토리 조회 실패: %s", e)
        return []


def _recent_decisions() -> tuple[list[dict], str | None]:
    """가장 최근 집행일의 편입/퇴출 결정(사유 포함)."""
    try:
        from ml.adaptive import Ledger
        decs = Ledger("kr_mock").read_decisions()
    except Exception as e:
        logger.warning("결정 원장 조회 실패: %s", e)
        return [], None
    if not decs:
        return [], None
    last_date = max(d.get("date", "") for d in decs)
    recent = [d for d in decs if d.get("date") == last_date and d.get("side") in ("편입", "퇴출")]
    return recent, last_date


def build_report() -> str:
    """모의 현황 리포트 문자열(크론·/mock 공용). read-only."""
    bal = kiwoom_mock.get_balance()
    today = datetime.now(KST)
    hdr = f"🧪 [모의] 국내 페이퍼트레이딩 현황\n📅 {today.strftime('%Y-%m-%d')} ({_WEEKDAY_KR[today.weekday()]}) {today.strftime('%H:%M')} KST"
    if not bal.get("ok"):
        return hdr + "\n━━━━━━━━━━━━━━━━━━━\n  ⚠️ 잔고 조회 실패 — 모의 연결/모의투자 신청 확인 필요"

    positions = bal["positions"]
    cash = bal["cash_krw"]
    pos_value = bal["pos_value"] or 0.0
    nav = bal["nav"]
    if nav is None:
        nav = pos_value + (cash or 0.0) or SEED_KRW

    # NAV 시계열 → 인셉션·전일·전략 MDD
    snaps = _snapshots()
    inception_nav = float(snaps[0]["nav"]) if snaps else SEED_KRW
    inception_date = str(snaps[0]["date"])[:10] if snaps else today.strftime("%Y-%m-%d")
    today_d = today.strftime("%Y-%m-%d")
    prev = [s for s in snaps if str(s["date"])[:10] < today_d]
    prev_nav = float(prev[-1]["nav"]) if prev else inception_nav

    from ml.adaptive import reward as _reward
    nav_series = [float(s["nav"]) for s in snaps] + [float(nav)]
    strat_mdd = _reward.max_drawdown(nav_series) * 100.0
    cum_ret = (nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0
    day_ret = (nav / prev_nav - 1.0) * 100.0 if prev_nav else 0.0

    # KOSPI 벤치 (인셉션~오늘)
    try:
        from providers import market_data
        kospi = market_data.fetch_kospi_stats(inception_date)
    except Exception as e:
        logger.warning("KOSPI 통계 실패: %s", e)
        kospi = {"return_pct": None, "mdd": None}
    k_ret, k_mdd = kospi.get("return_pct"), kospi.get("mdd")
    k_mdd_pct = k_mdd * 100.0 if k_mdd is not None else None

    def _sign(x):
        return "▲" if x > 0 else ("▼" if x < 0 else "─")

    lines = [hdr, "━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"  NAV    ₩{nav:,.0f}   전일 {_sign(day_ret)}{abs(day_ret):.2f}%")
    if k_ret is not None:
        excess = cum_ret - k_ret
        lines.append(f"  누적   {_sign(cum_ret)}{abs(cum_ret):.2f}%  "
                     f"(KOSPI {_sign(k_ret)}{abs(k_ret):.2f}% · 초과 {excess:+.2f}%p)")
    else:
        lines.append(f"  누적   {_sign(cum_ret)}{abs(cum_ret):.2f}%  (KOSPI N/A)")
    if k_mdd_pct is not None:
        ok_mdd = "✅" if strat_mdd <= k_mdd_pct else "⚠️"
        lines.append(f"  MDD    전략 {strat_mdd:.1f}% vs 지수 {k_mdd_pct:.1f}% {ok_mdd}")
    else:
        lines.append(f"  MDD    전략 {strat_mdd:.1f}%")
    if cash is not None:
        lines.append(f"  예수금 ₩{cash:,.0f}")

    # 보유 종목 P&L
    held = {c: p for c, p in positions.items() if int(p.get("shares", 0) or 0) > 0}
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"  보유 {len(held)}종목")
    total_pnl = 0.0
    for code, p in sorted(held.items(), key=lambda kv: -(kv[1].get("value", 0) or 0)):
        ret = p.get("return_pct", 0) or 0
        total_pnl += p.get("pnl", 0) or 0
        lines.append(f"  {code} {p.get('name','')[:8]} {int(p['shares'])}주 "
                     f"@{p.get('avg_price',0):,.0f}→{p.get('cur_price',0):,.0f} "
                     f"{_sign(ret)}{abs(ret):.1f}% ₩{p.get('value',0):,.0f}")
    if not held:
        lines.append("  (보유 없음 — 현금 100%)")
    else:
        cost = pos_value - total_pnl
        pnl_ret = (total_pnl / cost * 100.0) if cost else 0.0
        lines.append(f"  ─── 평가손익 {_sign(total_pnl)}₩{abs(total_pnl):,.0f} ({pnl_ret:+.1f}%)")

    # 최근 편입/퇴출 사유
    recent, last_date = _recent_decisions()
    if recent:
        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append(f"  최근 편입/퇴출 ({last_date})")
        for d in recent:
            icon = "📥" if d.get("side") == "편입" else "📤"
            rr = (d.get("rationale") or {}).get("one_line_reason", "")
            lines.append(f"  {icon} {d.get('side')} {d.get('code')} {d.get('action','')} — {rr}")

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("  ⚠️ 모의투자 — 실거래 아님")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== kiwoom_mock_report 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kiwoom_mock.is_enabled():
        logger.info("KIWOOM_MOCK_ENABLED 아님 — 현황 보고 생략")
        return 0

    text = build_report()

    # EOD NAV 스냅샷 적재(종가 NAV 시계열 — 전일대비는 이전 날짜 기준이라 동일자 중복 무해)
    try:
        bal = kiwoom_mock.get_balance()
        if bal.get("ok"):
            import store
            store.append("kr_mock_history", {
                "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot", "at": "eod",
                "nav": bal.get("nav"), "cash": bal.get("cash_krw"),
                "positions": len([p for p in bal["positions"].values() if int(p.get("shares", 0) or 0) > 0]),
            })
    except Exception as e:
        logger.warning("EOD 스냅샷 실패: %s", e)

    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)
    logger.info("현황 보고 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
