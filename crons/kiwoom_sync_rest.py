#!/usr/bin/env python3
"""
kiwoom_sync_rest.py — 키움 REST API 국내주식 잔고 → portfolio_snapshot.json 동기화

크론 등록 (매일 08:35 KST = 23:35 UTC):
    35 23 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python kiwoom_sync_rest.py

환경변수 (.env):
    KIWOOM_API_KEY    — openapi.kiwoom.com 에서 발급
    KIWOOM_API_SECRET — openapi.kiwoom.com 에서 발급
    KIWOOM_ACCOUNT_NO — 국내주식 계좌번호 (선택, 다계좌 구분 시)
"""

import os
import json
import shutil
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_DIR    = os.getenv("STOCK_REPORT_PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTFOLIO_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")


def _shadow_to_store(snap: dict):
    """portfolio_snapshot 을 store 로 best-effort 그림자 동기화 (라이브 키움 동기화 비차단)."""
    try:
        import sys
        if PROJECT_DIR not in sys.path:
            sys.path.insert(0, PROJECT_DIR)
        import store
        store.shadow_doc("portfolio_snapshot", snap)
    except Exception as e:
        logger.warning("store 그림자 동기화 실패: %s", e)

# 텔레그램 알림
_BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
_CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")


def _notify(msg: str):
    if not _BOT_TOKEN:
        return
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import notify
    notify.send_telegram(msg, token=_BOT_TOKEN, chat_id=_CHAT_ID)


def fetch_domestic_balance() -> list[dict] | None:
    """키움 REST API로 국내주식 보유잔고 조회. **실패→None(알림 대상)**, 정상·보유없음→[]."""
    try:
        import requests as _req
        from kiwoom_rest_api.auth.token import TokenManager
    except ImportError:
        logger.error("kiwoom-rest-api 미설치: uv pip install kiwoom-rest-api")
        return None

    if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
        logger.error(".env에 KIWOOM_API_KEY / KIWOOM_API_SECRET 없음")
        return None

    try:
        tok = TokenManager().access_token
        if not tok:
            logger.error("키움 토큰 발급 실패 — API Key/Secret 확인 필요")
            return None

        resp = _req.post(
            "https://api.kiwoom.com/api/dostk/acnt",
            headers={
                "content-type": "application/json;charset=UTF-8",
                "Authorization": f"Bearer {tok}",
                "api-id": "kt00018",
            },
            json={"qry_tp": "2", "dmst_stex_tp": "KRX"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.error("키움 API 호출 실패: %s", e)
        return None

    if result.get("return_code", -1) != 0:
        logger.error("API 오류: %s", result.get("return_msg", "unknown"))
        return None

    def _num(item: dict, key: str) -> float:
        """숫자 문자열 → float (쉼표·퍼센트 제거, 빈값=0)."""
        raw = item.get(key, "") or ""
        return float(raw.replace(",", "").replace("%", "").strip() or "0")

    holdings = []
    for item in result.get("acnt_evlt_remn_indv_tot", []):
        ticker = item.get("stk_cd", "").strip()
        if not ticker:
            continue

        holdings.append({
            "ticker":            ticker,
            "name":              item.get("stk_nm", "").strip(),
            "shares":            _num(item, "rmnd_qty"),
            "avg_price_krw":     _num(item, "pur_pric"),
            "current_price_krw": _num(item, "cur_prc"),
            "cost_krw":          _num(item, "pur_amt"),
            "value_krw":         _num(item, "evlt_amt"),
            "pnl_krw":           _num(item, "evltv_prft"),
            "return_pct":        _num(item, "prft_rt"),
        })

    logger.info("잔고 조회 완료: %d개 종목", len(holdings))
    return holdings


def update_portfolio(holdings: list[dict]) -> str:
    """portfolio_snapshot.json의 domestic 섹션 업데이트.

    교차 프로세스 쓰기 락으로 portfolio_sync_server·holding_manager 와 동시 쓰기 시
    lost update 를 방지하고, atomic write 로 torn read 를 막는다 (read-modify-write 통째 보호).
    """
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import safe_io
    from lib import trade_events

    trade_recs = []
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        shutil.copy2(PORTFOLIO_PATH, PORTFOLIO_PATH + ".bak")
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)

        # 기존 국내주식 딕셔너리 (ticker → entry)
        existing = {
            h["ticker"]: h
            for h in snap.get("domestic", {}).get("holdings", [])
        }
        had_prior_sync = bool(snap.get("last_domestic_sync"))
        for h in holdings:
            old = existing.get(h["ticker"]) or {}
            old_shares = float(old.get("shares", 0) or 0)
            new_shares = float(h.get("shares", 0) or 0)
            delta = round(new_shares - old_shares, 6)
            if had_prior_sync and abs(delta) > 1e-8:
                side = "buy" if delta > 0 else "sell"
                trade_recs.append({
                    "ticker": h["ticker"],
                    "side": side,
                    "qty": abs(delta),
                    "price": h.get("avg_price_krw") if side == "buy" else h.get("current_price_krw"),
                    "avg_price": h.get("avg_price_krw"),
                    "account": os.getenv("KIWOOM_ACCOUNT_NO", "domestic"),
                    "source": "kiwoom_sync",
                    "market": "KR",
                    "currency": "KRW",
                    "confirmed": True,
                    "note": "키움 잔고 동기화 수량 변화",
                })
            existing[h["ticker"]] = h

        snap.setdefault("domestic", {})["holdings"] = list(existing.values())
        snap["last_domestic_sync"] = datetime.now().isoformat()

        safe_io.atomic_write_json(PORTFOLIO_PATH, snap)

    _shadow_to_store(snap)
    for rec in trade_recs:
        trade_events.record_trade(**rec)
    lines = [f"  {h['ticker']} {h['name']} {h['shares']}주  {h['return_pct']:+.1f}%" for h in holdings]
    return "\n".join(lines)


def _touch_sync_timestamp():
    """국내 종목 0개여도 sync 실행 시각 기록 (파일 mtime 갱신)."""
    if not os.path.exists(PORTFOLIO_PATH):
        return
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import safe_io
    # 교차 프로세스 lost update 방지 — 무락 직접쓰기는 동시 holding_manager/sync_server 갱신을
    # 되돌릴 수 있어 read-modify-write 를 통째로 file_write_lock + atomic_write 로 보호(감사 확정).
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        snap["last_domestic_sync"] = datetime.now().isoformat()
        safe_io.atomic_write_json(PORTFOLIO_PATH, snap)
    _shadow_to_store(snap)


# ── 해외주식(미국) — 키움 REST 해외 지원 (read-only 잔고·주문 경로 0) ──────────

def _parse_us_balance(result: dict) -> list[dict]:
    """ust21070(미국주식 원장잔고확인) 응답 → overseas_general.holdings_usd 행 (순수).

    필드: result_list[{stk_cd, frgn_stk_nm, qty, frgn_stk_book_uv(장부단가),
    now_pric(현재가), evlt_amt(평가금액), pl_amt(손익금액), crnc_code}]. USD 만 채택.
    """
    def _num(item, key):
        raw = item.get(key, "")
        try:
            return float(str(raw).replace(",", "").replace("%", "").strip() or "0")
        except (TypeError, ValueError):
            return 0.0

    rows = []
    for item in (result or {}).get("result_list", []) or []:
        ticker = str(item.get("stk_cd", "") or "").strip().upper()
        if not ticker:
            continue
        crnc = str(item.get("crnc_code", "") or "").upper()
        if crnc and crnc != "USD":                       # 미국(USD) 잔고만 — 타통화 제외
            continue
        shares = _num(item, "qty") or _num(item, "poss_qty")
        avg = _num(item, "frgn_stk_book_uv")
        cur = _num(item, "now_pric")
        value = _num(item, "evlt_amt") or round(shares * cur, 4)
        pnl = _num(item, "pl_amt") or round((cur - avg) * shares, 4)
        rows.append({
            "name": str(item.get("frgn_stk_nm", "") or ticker).strip(),
            "ticker": ticker, "shares": shares,
            "avg_price_usd": avg, "current_price_usd": cur,
            "cost_usd": round(shares * avg, 4), "value_usd": value, "pnl_usd": pnl,
            "return_pct": round((cur / avg - 1) * 100, 2) if avg > 0 and cur > 0 else 0.0,
        })
    return rows


def fetch_us_balance() -> list[dict] | None:
    """키움 REST 해외주식 잔고(ust21070·/api/us/acnt) — 실패→None, 정상·보유없음→[].

    국내(kt00018)와 동일 토큰·도메인(read-only 조회 전용) — 주문 TR 없음.
    """
    try:
        import requests as _req
        from kiwoom_rest_api.auth.token import TokenManager
    except ImportError:
        logger.error("kiwoom-rest-api 미설치")
        return None
    if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
        return None
    try:
        tok = TokenManager().access_token
        if not tok:
            return None
        resp = _req.post(
            "https://api.kiwoom.com/api/us/acnt",
            headers={
                "content-type": "application/json;charset=UTF-8",
                "Authorization": f"Bearer {tok}",
                "api-id": "ust21070",
            },
            json={"stex_tp": "", "stk_cd": ""},          # 전체 거래소·전체 종목
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.error("키움 해외 잔고 API 실패: %s", e)
        return None
    if result.get("return_code", -1) != 0:
        logger.error("키움 해외 API 오류: %s", result.get("return_msg", "unknown"))
        return None
    rows = _parse_us_balance(result)
    logger.info("키움 해외 잔고: %d개 종목", len(rows))
    return rows


def sync_us_balance() -> None:
    """해외 잔고 확인 + (OVERSEAS_SYNC_SOURCE=kiwoom 일 때만) 스냅샷 반영.

    기본은 diff 보고만 — 토스 동기화와 이중 writer 충돌 방지(lib/overseas_snapshot 단일
    apply 소스 원칙). 해외 계좌 미개설/기능 미가입이면 API 오류 → 조용히 스킵(로그만).
    """
    rows = fetch_us_balance()
    if rows is None:
        logger.info("키움 해외 잔고 조회 불가 — 스킵 (해외 미개설/키 문제면 정상)")
        return
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    from lib import overseas_snapshot as osnap

    diff = osnap.diff_holdings(osnap.load_current_overseas(), rows)
    if osnap.can_apply("kiwoom"):
        if rows:
            summary = osnap.update_overseas_holdings(rows, source="kiwoom")
            total = sum(h.get("value_usd", 0) for h in rows)
            _notify("📊 키움 해외주식 동기화 (OVERSEAS_SYNC_SOURCE=kiwoom)\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{summary}\n\n  총평가  ${total:,.2f}")
        else:
            logger.info("키움 해외 보유 0건 — 스냅샷 미변경(안전)")
    elif diff:
        _notify("🔗 키움 해외 잔고 — 스냅샷과 차이 (보고만·반영하려면 OVERSEAS_SYNC_SOURCE=kiwoom)\n"
                + "\n".join(diff[:12]))
    else:
        logger.info("키움 해외 잔고 %d종목 — 스냅샷과 일치", len(rows))


def main():
    logger.info("키움 국내주식 동기화 시작")

    # 해외주식(미국) — 키움 REST 해외 지원 확인·동기화 (실패는 격리 — 국내 흐름 불변)
    try:
        sync_us_balance()
    except Exception as e:
        logger.warning("키움 해외 동기화 실패(격리): %s", e)

    holdings = fetch_domestic_balance()
    if holdings is None:                       # API/키/토큰/연결 실패 — 조용히 묻히지 않게 알림
        _notify("⚠️ 키움 국내주식 동기화 실패\n  API 키·토큰·연결 확인 필요 (KR 잔고 미갱신)")
        logger.error("동기화 실패 — 텔레그램 알림 발송")
        return
    if not holdings:                            # 정상 조회·국내 보유 없음 (실패 아님 → 알림 X)
        logger.warning("국내 보유 종목 없음 — 스킵")
        try:
            _touch_sync_timestamp()
            logger.info("last_domestic_sync 타임스탬프 갱신 완료")
        except Exception as e:
            logger.warning("타임스탬프 갱신 실패: %s", e)
        return

    summary = update_portfolio(holdings)
    logger.info("업데이트 완료:\n%s", summary)

    total_value = sum(h.get("value_krw", 0) for h in holdings)
    _notify(
        f"📊 키움 국내주식 동기화\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{summary}\n\n"
        f"  총평가  ₩{total_value:,.0f}"
    )


if __name__ == "__main__":
    main()
