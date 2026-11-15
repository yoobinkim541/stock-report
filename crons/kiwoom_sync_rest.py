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
import time
from datetime import datetime, timedelta
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


def _parse_us_deposit(result: dict) -> float | None:
    """ust21110(해외주식 예수금) 응답 → USD 주문가능금액 (순수). USD 행 없으면 None.

    result_list 항목 중 crnc_code=USD 의 fc_ord_alowa(외화주문가능금액) 채택 — 원시
    예수금(fc_entra)이 아니라 주문가능액을 쓰는 이유는 domestic(토스 buying-power)과
    같은 개념으로 맞추기 위함(2026-07-25, 라이브 조회로 fc_entra=fc_ord_alowa 동일값
    확인된 계좌도 있었지만 개념상 주문가능액이 더 안전한 표시).
    """
    for item in (result or {}).get("result_list", []) or []:
        if str(item.get("crnc_code", "")).upper() == "USD":
            try:
                return float(str(item.get("fc_ord_alowa", "") or "0").replace(",", "").strip())
            except (TypeError, ValueError):
                return None
    return None


def fetch_us_deposit() -> float | None:
    """키움 REST 해외 USD 예수금(ust21110·/api/us/acnt) — 실패 → None."""
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
                "api-id": "ust21110",
            },
            json={}, timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.error("키움 해외 예수금 API 실패: %s", e)
        return None
    if result.get("return_code", -1) != 0:
        logger.error("키움 해외 예수금 API 오류: %s", result.get("return_msg", "unknown"))
        return None
    return _parse_us_deposit(result)


_US_ACNT_URL = "https://api.kiwoom.com/api/us/acnt"
_US_TX_COLLECTION = "kiwoom_us_transactions"   # store 캐시 — 계좌 개설 이후 전체 거래내역(파싱본)
_US_TX_WINDOW_DAYS = 360                        # ust21100 은 조회기간 1년 이내만 허용
_US_TX_REFRESH_DAYS = 14                        # 증분 갱신 때 다시 받는 최근 구간
_US_TX_MAX_WINDOWS = 30
_US_TX_NO_DATA = "501724"                       # "관련자료가 없습니다" — 오류가 아니라 빈 구간
_US_SHARE_EPS = 1e-6


def _parse_us_transactions(result: dict) -> list[dict]:
    """ust21100(미국주식 거래내역) → 수량이 바뀌는 행 (순수). 입출금·배당·세금은 제외.

    실제 응답은 종류(deal_kind_nm)와 방향(rmrk_nm)이 따로 온다:
      매매/매수·매도          → book=whole,      move=trade
      소수점매매/매수·매도    → book=fractional, move=trade   (주식모으기 체결)
      입출고/대체온주전환출고 → book=fractional, move=convert (소수점 1주 → 온주)
      입출고/대체온주전환입고 → book=whole,      move=convert
      입출고/대체출고·대체입고 → move=transfer (book 은 reconstruct_us_positions 가 짝으로 판정)
    deal_no 는 날짜별 일련번호라 (date, deal_no) 가 고유 키다. 단가는 uv_exrt(USD),
    없으면 fc_deal_amt/deal_qty.
    """
    def _num(item, key):
        try:
            return float(str(item.get(key, "") or "0").replace(",", "").strip() or "0")
        except (TypeError, ValueError):
            return 0.0

    rows = []
    for item in (result or {}).get("result_list", []) or []:
        deal_kind = str(item.get("deal_kind_nm", "") or "").strip()
        remark = str(item.get("rmrk_nm", "") or "").strip()
        label = f"{deal_kind} {remark}".strip()
        ticker = str(item.get("stk_cd", "") or "").strip().upper()
        qty = _num(item, "deal_qty")
        if not ticker or qty <= 0:
            continue
        direction = remark or deal_kind           # 종류 "입출고" 에도 "출고" 가 들어 있어 방향은 적요로 본다
        if "대체" in direction:
            side = "sell" if "출고" in direction else ("buy" if "입고" in direction else None)
            move = "convert" if "온주전환" in direction else "transfer"
            book = ("fractional" if side == "sell" else "whole") if move == "convert" else ""
        else:
            side = "buy" if "매수" in direction else ("sell" if "매도" in direction else None)
            move = "trade"
            book = "fractional" if "소수점" in label or "모으기" in label else "whole"
        if not side:
            continue                              # 입출금·환전·배당 등 수량 변화 없는 행
        amount = abs(_num(item, "fc_deal_amt")) or abs(_num(item, "deal_amt"))
        price = _num(item, "uv_exrt") or (amount / qty if amount > 0 else 0.0)
        rows.append({
            "ticker": ticker, "side": side, "qty": qty,
            "price": round(price, 4) if price > 0 else None,
            "amount": round(amount or price * qty, 4),
            "date": str(item.get("deal_dt", "") or "").strip(),
            "deal_no": str(item.get("deal_no", "") or "").strip(),
            "kind": label, "name": str(item.get("stk_nm", "") or "").strip(),
            "book": book, "move": move,
        })
    return rows


def _us_tx_key(row: dict) -> tuple[str, str]:
    return str(row.get("date") or ""), str(row.get("deal_no") or "")


def _fetch_us_transaction_window(post, token: str, start: datetime, end: datetime) -> list[dict] | None:
    """한 조회 구간의 연속 페이지(cont-yn/next-key)를 모두 읽는다 — 실패 → None, 내역 없음 → []."""
    body = {"strt_dt": start.strftime("%Y%m%d"), "end_dt": end.strftime("%Y%m%d"),
            "tp": "0", "stex_tp": "", "stk_cd": "", "krw_repl_skip_yn": "Y"}  # tp=0 전체(매수·매도) — 빈값은 999999 "구분 입력" 거부
    rows: list[dict] = []
    next_key = ""
    for _ in range(2000):
        headers = {"content-type": "application/json;charset=UTF-8",
                   "Authorization": f"Bearer {token}", "api-id": "ust21100"}
        if next_key:
            headers.update({"cont-yn": "Y", "next-key": next_key})
        try:
            resp = post(_US_ACNT_URL, headers=headers, json=body, timeout=20)
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            logger.error("키움 해외 거래내역 API 실패: %s", e)
            return None
        if result.get("return_code", -1) != 0:
            message = str(result.get("return_msg", "unknown"))
            if _US_TX_NO_DATA in message:
                return rows
            logger.error("키움 해외 거래내역 오류: %s", message)
            return None
        rows.extend(_parse_us_transactions(result))
        next_key = str(resp.headers.get("next-key") or "").strip()
        if str(resp.headers.get("cont-yn") or "").upper() != "Y" or not next_key:
            return rows
        time.sleep(0.2)                           # 연속조회 호출 간격
    logger.error("키움 해외 거래내역 연속조회가 끝나지 않음 — 중단")
    return None


def fetch_us_transactions(days: int | None = 7) -> list[dict] | None:
    """키움 REST 해외 거래내역(ust21100) — 최근 N일, days=None 이면 전체 이력. 실패→None. read-only.

    전체 이력은 1년 이내 구간을 과거로 거슬러 가며 내역이 없는 구간이 나올 때까지 읽는다.
    """
    try:
        import requests as _req
        from kiwoom_rest_api.auth.token import TokenManager
    except ImportError:
        return None
    if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
        return None
    try:
        tok = TokenManager().access_token
    except Exception as e:
        logger.error("키움 토큰 발급 실패: %s", e)
        return None
    if not tok:
        return None

    end = datetime.now()
    if days is not None:
        span = min(max(int(days), 0), _US_TX_WINDOW_DAYS)
        return _fetch_us_transaction_window(_req.post, tok, end - timedelta(days=span), end)
    rows: list[dict] = []
    for _ in range(_US_TX_MAX_WINDOWS):
        start = end - timedelta(days=_US_TX_WINDOW_DAYS)
        chunk = _fetch_us_transaction_window(_req.post, tok, start, end)
        if chunk is None:
            return None
        if not chunk:
            break
        rows.extend(chunk)
        end = start - timedelta(days=1)
    return rows


def refresh_us_transaction_history(*, full: bool = False) -> list[dict] | None:
    """전체 해외 거래내역 캐시(store)를 갱신해 날짜순으로 돌려준다 — 실패 → None.

    캐시가 비었거나 full=True 면 전체 이력을, 아니면 마지막 캐시 날짜 14일 전부터만 다시 받아
    그 구간을 통째로 교체한다(브로커가 정정·취소한 행이 캐시에 남지 않도록).
    """
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import store

    cached = [] if full else store.all(_US_TX_COLLECTION)
    last = max((str(r.get("date") or "") for r in cached), default="")
    if len(last) == 8 and last.isdigit():
        since = datetime.strptime(last, "%Y%m%d") - timedelta(days=_US_TX_REFRESH_DAYS)
        days = (datetime.now() - since).days + 1
        if days > _US_TX_WINDOW_DAYS:
            cached, days = [], None
    else:
        cached, days = [], None
    fetched = fetch_us_transactions(days)
    if fetched is None:
        return None
    if days is not None:
        cutoff = since.strftime("%Y%m%d")
        fetched = [r for r in cached if str(r.get("date") or "") < cutoff] + fetched
    history = sorted({_us_tx_key(r): r for r in fetched}.values(), key=_us_tx_key)
    store.replace_all(_US_TX_COLLECTION, history)
    return history


def reconstruct_us_positions(history: list[dict]) -> dict[str, dict]:
    """전체 거래내역 → 종목별 {whole, fractional, fractional_cost, name, last_price} (순수).

    키움 잔고 조회(ust21070)는 온주만 돌려주므로 소수점 보유는 거래내역 누적으로 구한다.
    같은 날·같은 종목·같은 수량의 대체출고/대체입고 짝은 소수점→온주 전환(옛 표기)이고,
    짝이 없는 대체는 계좌 간 온주 이동으로 본다. 소수점 원가는 이동평균법이며, 거래번호가
    체결 순서가 아니라서(당일 매수분을 같은 날 앞 번호로 매도) 하루 안에서는 매수를 먼저 반영한다.
    """
    rows = sorted(history or [], key=lambda r: (str(r.get("date") or ""), r.get("side") == "sell",
                                                str(r.get("deal_no") or "")))
    pairs: dict[tuple, dict[str, int]] = {}
    for r in rows:
        if r.get("move") == "transfer":
            key = (r.get("date"), r.get("ticker"), r.get("qty"))
            pairs.setdefault(key, {"buy": 0, "sell": 0})[r["side"]] += 1
    convertible = {key: min(n["buy"], n["sell"]) for key, n in pairs.items()}
    used: dict[tuple, dict[str, int]] = {}

    positions: dict[str, dict] = {}
    for r in rows:
        ticker, side, qty = r.get("ticker"), r.get("side"), float(r.get("qty") or 0)
        if not ticker or side not in ("buy", "sell") or qty <= 0:
            continue
        pos = positions.setdefault(ticker, {"whole": 0.0, "fractional": 0.0,
                                            "fractional_cost": 0.0, "name": "", "last_price": None})
        if r.get("name"):
            pos["name"] = r["name"]
        book = r.get("book")
        if r.get("move") == "transfer":
            key = (r.get("date"), ticker, r.get("qty"))
            seen = used.setdefault(key, {"buy": 0, "sell": 0})
            seen[side] += 1
            paired = seen[side] <= convertible.get(key, 0)
            book = "fractional" if paired and side == "sell" else "whole"
        elif r.get("move") == "trade" and r.get("price"):
            pos["last_price"] = float(r["price"])
        if book == "fractional":
            if side == "buy":
                pos["fractional"] += qty
                pos["fractional_cost"] += float(r.get("amount") or 0)
            else:
                held = pos["fractional"]
                pos["fractional_cost"] -= pos["fractional_cost"] * min(qty / held, 1.0) if held > 0 else 0.0
                pos["fractional"] = held - qty
        else:
            pos["whole"] += qty if side == "buy" else -qty
    return positions


def whole_share_mismatches(positions: dict[str, dict], balance: list[dict]) -> list[str]:
    """거래내역으로 만든 온주 수량 vs 브로커 잔고(ust21070) 차이 (순수) — 빈 목록이면 일치.

    일치할 때만 거래내역이 완전하다고 보고 소수점 보유를 반영한다.
    """
    broker = {str(h.get("ticker", "")).upper(): float(h.get("shares", 0) or 0)
              for h in balance or [] if h.get("ticker")}
    lines = []
    for ticker in sorted(set(broker) | set(positions)):
        rebuilt = float((positions.get(ticker) or {}).get("whole", 0.0))
        actual = broker.get(ticker, 0.0)
        if abs(rebuilt - actual) > _US_SHARE_EPS:
            lines.append(f"{ticker}: 거래내역 {rebuilt:g}주 ≠ 잔고 {actual:g}주")
    return lines


def _us_quote_prices(tickers: list[str]) -> dict[str, float]:
    """잔고 조회에 없는(소수점만 보유한) 종목의 최근 종가 — 실패한 종목은 빠진다."""
    prices: dict[str, float] = {}
    if not tickers:
        return prices
    try:
        import yfinance as yf
        close = yf.download(tickers, period="5d", auto_adjust=False, progress=False)["Close"]
        for ticker in tickers:
            series = (close[ticker] if hasattr(close, "columns") else close).dropna()
            if not series.empty and float(series.iloc[-1]) > 0:
                prices[ticker] = float(series.iloc[-1])
    except Exception as e:
        logger.warning("소수점 종목 시세 조회 실패 — 마지막 체결가로 대체: %s", e)
    return prices


def fractional_holdings(positions: dict[str, dict], balance: list[dict],
                        quotes: dict[str, float] | None = None) -> list[dict]:
    """소수점 보유 행(overseas_fractional.holdings) — 현재가: 잔고 → 시세 → 마지막 체결가 (순수)."""
    broker = {str(h.get("ticker", "")).upper(): h for h in balance or [] if h.get("ticker")}
    rows = []
    for ticker in sorted(positions):
        pos = positions[ticker]
        shares = round(float(pos.get("fractional") or 0.0), 6)
        if shares <= _US_SHARE_EPS:
            continue
        cost = max(float(pos.get("fractional_cost") or 0.0), 0.0)
        avg = cost / shares
        held = broker.get(ticker) or {}
        price = (float(held.get("current_price_usd") or 0) or float((quotes or {}).get(ticker) or 0)
                 or float(pos.get("last_price") or 0) or avg)
        value = shares * price
        rows.append({
            "name": held.get("name") or pos.get("name") or ticker, "ticker": ticker,
            "shares": shares, "avg_price_usd": round(avg, 4), "current_price_usd": round(price, 4),
            "cost_usd": round(cost, 4), "value_usd": round(value, 4),
            "pnl_usd": round(value - cost, 4),
            "return_pct": round((value / cost - 1) * 100, 2) if cost > 0 else 0.0,
        })
    return rows


def _record_us_trades(history: list[dict]) -> int:
    """최근 체결을 trade_events 원장에 기록 — 새로 들어간 건수 (차트 ▲▼ 마커 원천).

    event_id=kiwoomus:날짜:거래번호 로 멱등. 전환·대체는 체결이 아니라 기록하지 않는다.
    소수점 매수는 crons/accumulate_daily.py 가 종가 기준으로 먼저 적어 두므로(manual_holding),
    같은 종목에 하루 이내의 그 기록이 있으면 건너뛴다 — 같은 모으기 체결에 ▲ 가 두 개 찍히지 않게.
    """
    from lib import trade_events

    last = max((str(r.get("date") or "") for r in history), default="")
    if len(last) != 8 or not last.isdigit():
        return 0
    cutoff = (datetime.strptime(last, "%Y%m%d") - timedelta(days=_US_TX_REFRESH_DAYS)).strftime("%Y%m%d")
    trades = trade_events.all_trades()
    known = {t.get("event_id") for t in trades}
    planned = {(str(t.get("ticker") or "").upper(), str(t.get("date") or "")[:10]) for t in trades
               if t.get("source") == "manual_holding" and t.get("side") == "buy"}
    added = 0
    for r in history:
        d = str(r.get("date") or "")
        event_id = f"kiwoomus:{d}:{r.get('deal_no')}"
        if r.get("move") != "trade" or d < cutoff or not r.get("deal_no") or event_id in known:
            continue
        if r.get("book") == "fractional" and r.get("side") == "buy":
            day = datetime.strptime(d, "%Y%m%d")
            if any((r["ticker"], (day + timedelta(days=n)).strftime("%Y-%m-%d")) in planned for n in (-1, 0, 1)):
                continue
        try:
            trade_events.record_trade(
                ticker=r["ticker"], side=r["side"], qty=r["qty"], price=r.get("price"),
                account=os.getenv("KIWOOM_ACCOUNT_NO", "kiwoom_us"), source="kiwoom_us_sync",
                market="US", currency="USD", timestamp=f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00",
                confirmed=True, broker_order_id=r["deal_no"], note=r.get("kind", ""),
                event_id=event_id)
            added += 1
        except Exception as e:
            logger.warning("해외 체결 기록 실패(1건 무시): %s", e)
    return added


def sync_us_fractional(history: list[dict], balance: list[dict]) -> bool:
    """거래내역 누적으로 소수점 보유(overseas_fractional)를 다시 만든다 — 반영했으면 True.

    온주 수량이 브로커 잔고와 맞지 않으면 전체 이력을 한 번 다시 받고, 그래도 다르면
    스냅샷을 건드리지 않고 알린다.
    """
    from lib import overseas_snapshot as osnap

    positions = reconstruct_us_positions(history)
    mismatches = whole_share_mismatches(positions, balance)
    if mismatches:
        refetched = refresh_us_transaction_history(full=True)
        if refetched is not None:
            positions = reconstruct_us_positions(refetched)
            mismatches = whole_share_mismatches(positions, balance)
    if mismatches:
        logger.error("해외 거래내역과 잔고 불일치 — 소수점 보유 미반영: %s", "; ".join(mismatches))
        _notify("⚠️ 키움 해외 소수점 보유 미반영 — 거래내역으로 만든 온주 수량이 잔고와 다릅니다\n"
                + "\n".join(mismatches[:12]))
        return False

    held = {str(h.get("ticker", "")).upper() for h in balance}
    fractional_only = [t for t, p in positions.items()
                       if p["fractional"] > _US_SHARE_EPS and t not in held]
    rows = fractional_holdings(positions, balance, _us_quote_prices(fractional_only))
    if not osnap.can_apply("kiwoom"):
        logger.info("키움 해외 소수점 %d종목 확인 — 반영하려면 OVERSEAS_SYNC_SOURCE=kiwoom", len(rows))
        return False
    changes = osnap.update_fractional_holdings(rows, source="kiwoom")
    logger.info("키움 해외 소수점 보유 %d종목 반영", len(rows))
    if changes:
        total = sum(h["value_usd"] for h in rows)
        _notify("🧩 키움 해외 소수점 보유 변경\n━━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(changes[:20]) + f"\n\n  소수점 평가 ${total:,.2f}")
    return True


def sync_us_transactions(balance: list[dict] | None = None) -> int:
    """해외 거래내역 → trade_events 원장 + 소수점 보유 재구성. 새로 기록한 체결 건수를 돌려준다.

    '주식모으기'(소수점매매) 체결과 매도도 원장에 남는다. balance(ust21070 결과)가 있으면
    거래내역 누적으로 소수점 보유를 다시 만들어 반영한다 — 잔고 조회는 온주만 주기 때문.
    """
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    history = refresh_us_transaction_history()
    if not history:
        return 0
    added = _record_us_trades(history)
    logger.info("키움 해외 체결 %d건 원장 반영 (전체 내역 %d행)", added, len(history))
    if balance is not None:
        sync_us_fractional(history, balance)
    return added


def sync_us_balance() -> list[dict] | None:
    """해외 잔고 확인 + (OVERSEAS_SYNC_SOURCE=kiwoom 일 때만) 스냅샷 반영. 조회한 잔고 행을 돌려준다.

    기본은 diff 보고만 — 토스 동기화와 이중 writer 충돌 방지(lib/overseas_snapshot 단일
    apply 소스 원칙). 해외 계좌 미개설/기능 미가입이면 API 오류 → 조용히 스킵(로그만) → None.
    """
    rows = fetch_us_balance()
    if rows is None:
        logger.info("키움 해외 잔고 조회 불가 — 스킵 (해외 미개설/키 문제면 정상)")
        return None
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    from lib import overseas_snapshot as osnap

    diff = osnap.diff_holdings(osnap.load_current_overseas(), rows)
    cash_usd = fetch_us_deposit()
    logger.info("키움 해외 예수금(USD): %s", f"{cash_usd:,.2f}" if cash_usd is not None else "조회 실패")
    if osnap.can_apply("kiwoom"):
        if rows:
            # 체결은 거래내역(sync_us_transactions)이 원장에 남긴다 — 수량 변화 추정 기록은 중복이라 끈다.
            summary = osnap.update_overseas_holdings(rows, source="kiwoom", cash_usd=cash_usd,
                                                     record_trades=False)
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
    return rows


def main():
    logger.info("키움 국내주식 동기화 시작")

    # 해외주식(미국) — 키움 REST 해외 지원 확인·동기화 (실패는 격리 — 국내 흐름 불변)
    us_balance = None
    try:
        us_balance = sync_us_balance()
    except Exception as e:
        logger.warning("키움 해외 동기화 실패(격리): %s", e)
    try:
        sync_us_transactions(us_balance)   # 체결 → trade_events, 거래내역 누적 → 소수점 보유
    except Exception as e:
        logger.warning("키움 해외 체결 동기화 실패(격리): %s", e)

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

    # DOMESTIC_SYNC_SOURCE 가 다른 소스(예: toss)로 명시돼 있으면 국내 스냅샷 쓰기는 건너뜀
    # (2026-07-25 — 토스로 국내주식 보유 시 이중 writer 충돌 방지. 미지정이면 기존처럼 kiwoom
    # 이 기본 — 여기서 처음 도입된 개념이라 opt-in 이 아니라 opt-out 으로 하위호환 유지).
    import sys
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    from lib import domestic_snapshot as dsnap
    other_source = dsnap.apply_source() not in ("", "kiwoom")
    if other_source:
        logger.info("DOMESTIC_SYNC_SOURCE=%s — 키움 국내 스냅샷 쓰기 생략(보고만)", dsnap.apply_source())
        try:
            _touch_sync_timestamp()
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


def check_us() -> int:
    """해외 계좌 연결 확인만 (읽기 조회·스냅샷/원장 미변경) — `--check-us`."""
    rows = fetch_us_balance()
    if rows is None:
        print("❌ 키움 해외 잔고 조회 실패 — 키/토큰/해외계좌 개설 여부 확인 (로그 참조)")
        return 1
    total = sum(h.get("value_usd", 0) for h in rows)
    print(f"✅ 키움 해외 연결 OK — 보유 {len(rows)}종목 · 총평가 ${total:,.2f}")
    for h in rows:
        print(f"  {h['ticker']:6s} {h.get('name', ''):20s} {h['shares']:>10g}주"
              f"  평단 ${h['avg_price_usd']:,.2f}  {h['return_pct']:+.1f}%")
    txs = fetch_us_transactions(days=7)
    if txs is None:
        print("⚠️ 해외 거래내역(ust21100) 조회 실패")
    else:
        print(f"최근 7일 체결 {len(txs)}건" + (" (주식모으기 체결 포함 시 거래종류명으로 표시)" if txs else ""))
        for t in txs[:10]:
            print(f"  {t['date']} {t['kind']} {t['ticker']} {t['qty']:g}주"
                  + (f" @${t['price']:,.2f}" if t.get("price") else ""))
    return 0


if __name__ == "__main__":
    import sys as _sys
    if "--check-us" in _sys.argv:
        _sys.exit(check_us())
    main()
