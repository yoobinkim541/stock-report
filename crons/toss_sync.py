#!/usr/bin/env python3
"""toss_sync.py — 토스증권 잔고 확인/동기화 (읽기전용 API — 주문 경로 0).

기본 동작 = **확인 모드**: 계좌·보유 조회 → 현 portfolio_snapshot 과 차이를 텔레그램/
콘솔로 보고만 한다 (연결 검증·드리프트 감시). 스냅샷 실제 갱신은 단일 apply 소스 원칙:

    OVERSEAS_SYNC_SOURCE=toss  일 때만 USD 보유를 overseas_general 에 반영
    (kiwoom 해외 동기화와 이중 writer 충돌 방지 — lib/overseas_snapshot 강제)
    DOMESTIC_SYNC_SOURCE=toss  일 때만 KRW 보유(+예수금)를 domestic 에 반영
    (kiwoom 국내 동기화와 이중 writer 충돌 방지 — lib/domestic_snapshot 강제.
    국내주식을 토스로 보유하고 kiwoom 국내계좌는 비어있는 경우를 위해 2026-07-25 추가)

지정 안 하면 항상 보고만.

사용:
    uv run python crons/toss_sync.py --check     # 연결 확인 (계좌·보유·예수금 요약 출력만)
    uv run python crons/toss_sync.py             # 확인+diff 보고, *_SYNC_SOURCE=toss 면 반영
크론 (평일 22:40 UTC = 07:40 KST — 미장 마감 후·일일 리포트 23:00 전):
    40 22 * * 1-5 cd <repo> && uv run python crons/toss_sync.py >> /tmp/toss_sync.log 2>&1
환경변수: TOSS_API_KEY / TOSS_API_SECRET (corp.tossinvest.com/ko/open-api 발급) / TOSS_ACCOUNT_SEQ(선택)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def to_snapshot_rows(norm: list[dict]) -> list[dict]:
    """toss_api.normalize_holdings 출력(US·USD만) → overseas_general.holdings_usd 행 (순수)."""
    rows = []
    for h in norm:
        if h.get("market") != "US" or h.get("currency") != "USD":
            continue
        shares, avg, last = h["shares"], h["avg"], h["last"]
        rows.append({
            "name": h["name"], "ticker": h["symbol"], "shares": shares,
            "avg_price_usd": avg, "current_price_usd": last,
            "cost_usd": round(shares * avg, 4),
            "value_usd": h.get("value") or round(shares * last, 4),
            "pnl_usd": h.get("pnl") or round((last - avg) * shares, 4),
            "return_pct": h.get("return_pct", 0.0),
        })
    return rows


def to_domestic_rows(norm: list[dict]) -> list[dict]:
    """toss_api.normalize_holdings 출력(KR·KRW만) → domestic.holdings 행 (순수)."""
    rows = []
    for h in norm:
        if h.get("market") != "KR" or h.get("currency") != "KRW":
            continue
        shares, avg, last = h["shares"], h["avg"], h["last"]
        rows.append({
            "ticker": h["symbol"], "name": h["name"], "shares": shares,
            "avg_price": avg, "current_price": last,
            "pnl_krw": h.get("pnl") or round((last - avg) * shares, 4),
            "return_pct": h.get("return_pct", 0.0),
        })
    return rows


def _notify(msg: str) -> None:
    token = os.getenv("STOCK_BOT_TOKEN")
    if not token:
        return
    try:
        import notify
        notify.send_telegram(msg, token=token, chat_id=os.getenv("STOCK_BOT_CHAT_ID", "5771238245"))
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="연결 확인만 (보고·미반영)")
    args = parser.parse_args()

    from providers import toss_api
    from lib import overseas_snapshot as osnap
    from lib import domestic_snapshot as dsnap

    if not os.getenv("TOSS_API_KEY") or not os.getenv("TOSS_API_SECRET"):
        logger.error(".env 에 TOSS_API_KEY / TOSS_API_SECRET 없음 — corp.tossinvest.com/ko/open-api 발급")
        return 0

    accts = toss_api.accounts()
    if not accts:
        logger.error("토스 계좌 조회 실패 — 키/허용 IP 확인 필요")
        _notify("⚠️ 토스증권 API 연결 실패 — 키·허용 IP 확인 필요 (계좌 조회 불가)")
        return 0
    logger.info("토스 계좌 %d개: %s", len(accts),
                ", ".join(f"{a.get('accountType')}#{a.get('accountSeq')}" for a in accts))
    # 계좌 seq 를 명시적으로 넘겨서 holdings_raw 내부의 default_account_seq() 재조회를 생략
    # (2026-07-24: 미지정 시 /api/v1/accounts 를 다시 불러 같은 run 안에서 연속 호출 →
    # 429 유발 사례 있었음).
    account_seq = next((a.get("accountSeq") for a in accts if a.get("accountType") == "BROKERAGE"),
                       accts[0].get("accountSeq"))

    raw = toss_api.holdings_raw(account_seq=account_seq)
    if raw is None:
        logger.error("토스 보유 조회 실패")
        _notify("⚠️ 토스증권 잔고 조회 실패 (계좌 식별/권한 확인)")
        return 0
    norm = toss_api.normalize_holdings(raw)
    us_rows = to_snapshot_rows(norm)
    kr_rows = to_domestic_rows(norm)
    logger.info("토스 보유: US %d·KR %d 종목", len(us_rows), len(kr_rows))
    cash_krw = toss_api.buying_power("KRW", account_seq=account_seq)
    logger.info("토스 예수금(KRW): %s", f"{cash_krw:,.0f}" if cash_krw is not None else "조회 실패")

    if args.check:
        print(f"✅ 토스 연결 OK — 계좌 {len(accts)}개 · US {len(us_rows)}종목 · KR {len(kr_rows)}종목"
              f" · 예수금 {'₩' + format(cash_krw, ',.0f') if cash_krw is not None else '조회 실패'}")
        for r in us_rows:
            print(f"  {r['ticker']:6s} {r['shares']:>10g}주  평단 ${r['avg_price_usd']:,.2f}  {r['return_pct']:+.1f}%")
        for r in kr_rows:
            print(f"  {r['ticker']:6s} {r['shares']:>10g}주  평단 ₩{r['avg_price']:,.0f}  {r['return_pct']:+.1f}%")
        return 0

    us_diff = osnap.diff_holdings(osnap.load_current_overseas(), us_rows)
    kr_diff = osnap.diff_holdings(dsnap.load_current_domestic(), kr_rows)   # diff_holdings 는 순수(소스 무관)
    lines = [f"🔗 토스증권 잔고 (US {len(us_rows)}·KR {len(kr_rows)}종목)"]
    if cash_krw is not None:
        lines.append(f"  예수금 ₩{cash_krw:,.0f}")
    if us_diff or kr_diff:
        lines.append("스냅샷과 차이:")
        lines.extend((us_diff + kr_diff)[:12])
    else:
        lines.append("스냅샷과 일치 ✅")

    if osnap.can_apply("toss"):
        if us_rows:
            summary = osnap.update_overseas_holdings(us_rows, source="toss")
            lines.append("→ overseas_general 반영 완료 (OVERSEAS_SYNC_SOURCE=toss)")
            logger.info("스냅샷 반영(해외):\n%s", summary)
        else:
            lines.append("→ US 보유 0건 — 스냅샷 미변경(안전)")
    elif us_diff:
        lines.append("→ 해외 보고만 (반영하려면 OVERSEAS_SYNC_SOURCE=toss)")

    if dsnap.can_apply("toss"):
        summary = dsnap.update_domestic_holdings(kr_rows, source="toss", cash_krw=cash_krw)
        lines.append("→ domestic 반영 완료 (DOMESTIC_SYNC_SOURCE=toss)")
        logger.info("스냅샷 반영(국내):\n%s", summary)
    elif kr_diff:
        lines.append("→ 국내 보고만 (반영하려면 DOMESTIC_SYNC_SOURCE=toss)")

    msg = "\n".join(lines)
    logger.info("%s", msg)
    if us_diff or kr_diff or osnap.can_apply("toss") or dsnap.can_apply("toss"):
        _notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
