#!/usr/bin/env python3
"""kis_mock.py — 한국투자증권(KIS) 해외주식 모의투자 어댑터. **모의 도메인 하드락.**

안전 경계 (절대 불변 — kiwoom_mock 과 동일 원칙):
  - 모든 요청은 openapivts.koreainvestment.com(_MOCK_BASE)으로만 — 실전 openapi... 경로 없음.
  - 단일 통로 _request()/_get_token()이 _assert_mock_url() 가드를 통과해야만 호출.
  - 실거래 주문 함수 미제공 — 여기서 만드는 주문은 전부 모의계좌 대상.
  - is_enabled()(KOREA_MOCK_ENABLED=true)가 아니면 루프가 호출 자체를 안 함.
  - 계좌번호(CANO+ACNT_PRDT_CD) 미설정 시 fail-closed(HTTP 호출 0).

KIS 사실:
  - 주문/잔고 바디에 CANO(8자리)+ACNT_PRDT_CD(2자리) 필수 (키움과 달리 앱키 추론 불가).
  - 토큰(/oauth2/tokenP) 유효 ~24h, **발급 레이트리밋** → 디스크 영속(~/.cache)로 재사용.
  - 해외주식 = 정수주만(분수 없음), 지정가(ORD_DVSN 00) 기준.
  - TR_ID·응답 필드명은 라이브 스모크 전 확정 금지 (미확인 시 graceful).

env:
  KOREA_MOCK_ENABLED      "true" 여야 동작 (기본 off)
  KOREA_MOCK_API_KEY      (없으면 KOREA_API_KEY 재사용)
  KOREA_MOCK_API_SECRET   (없으면 KOREA_API_SECRET 재사용)
  KOREA_MOCK_ACCOUNT_NO   모의 계좌 "CANO-PRDT" (예 "50012345-01"). 미설정 시 주문/잔고 차단.
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_MOCK_BASE = "https://openapivts.koreainvestment.com:29443"   # ★ 하드락 — 변경 금지 (모의)
_TOKEN_URL = "/oauth2/tokenP"
_HASHKEY_URL = "/uapi/hashkey"
_ORDER_URL = "/uapi/overseas-stock/v1/trading/order"
_BALANCE_URL = "/uapi/overseas-stock/v1/trading/inquire-balance"
_PRESENT_BALANCE_URL = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
_PRICE_URL = "/uapi/overseas-price/v1/quotations/price"

# 모의 해외 TR_ID — ★S6 라이브 스모크서 잔고·현재가·present-balance 확정(2026-06-27). 주문은 미확정.
_TR_BUY = "VTTT1002U"     # 미국 매수(모의) — 라이브 미확정
_TR_SELL = "VTTT1001U"    # 미국 매도(모의) — 라이브 미확정
_TR_BALANCE = "VTTS3012R"  # 해외 잔고(모의) — ✅ 라이브 확정 (포지션·P&L)
_TR_PRESENT = "VTRP6504R"  # 해외 체결기준현재잔고(모의) — ✅ 라이브 확정 (예수금·NAV·환율)
_TR_PRICE = "HHDFS00000300"  # 해외 현재가(모의/실전 공용) — ✅ 라이브 확정 (레이트리밋 有→재시도)

_TOKEN_FILE = os.path.expanduser("~/.cache/kis_mock_token.json")
_token_cache: dict = {"token": None, "exp": 0.0}

# 종목 → 해외거래소코드(주문 OVRS_EXCG_CD). 미상은 NASD 기본. (스모크서 보강)
_TICKER_EXCH = {"ORCL": "NYSE", "UNH": "NYSE", "SAP": "NYSE", "SGOV": "AMEX", "SPMO": "AMEX"}
_PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}   # 주문코드 → 현재가 EXCD


# ── 설정 ──────────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return os.getenv("KOREA_MOCK_ENABLED", "false").lower() == "true"


def _key() -> str | None:
    return os.getenv("KOREA_MOCK_API_KEY") or os.getenv("KOREA_API_KEY")


def _secret() -> str | None:
    return os.getenv("KOREA_MOCK_API_SECRET") or os.getenv("KOREA_API_SECRET")


def _parse_account(raw: str | None) -> tuple[str | None, str | None]:
    """'CANO-PRDT' 또는 'CANO' → (CANO, ACNT_PRDT_CD). 미설정/형식오류 → (None,None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if "-" in raw:
        cano, prdt = raw.split("-", 1)
        return (cano.strip() or None), (prdt.strip() or "01")
    return raw, "01"   # 상품코드 미지정 시 종합 01 기본


def account() -> tuple[str | None, str | None]:
    return _parse_account(os.getenv("KOREA_MOCK_ACCOUNT_NO"))


def exchange_of(ticker: str) -> str:
    """종목 → 주문 OVRS_EXCG_CD (기본 NASD)."""
    return _TICKER_EXCH.get(ticker.upper().replace(".", ""), "NASD")


# ── 안전 가드 ─────────────────────────────────────────────────────────────────

def _assert_mock_url(url: str) -> None:
    """모의 도메인 외 호출 원천 차단 — 실전 주문 사고 방지."""
    if not url.startswith(_MOCK_BASE + "/"):
        raise RuntimeError(f"[안전차단] 모의 도메인 외 호출 시도: {url}")


# ── 순수 빌더 (테스트 폐형해) ─────────────────────────────────────────────────

def _order_tr_id(side: str) -> str:
    return _TR_BUY if side == "buy" else _TR_SELL


def build_order_body(cano: str, prdt: str, excd: str, symbol: str,
                     qty: int, price: float) -> dict:
    """해외주식 주문 바디 — 정수주·지정가(ORD_DVSN 00). 순수함수."""
    return {
        "CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": excd,
        "PDNO": symbol.upper(), "ORD_QTY": str(int(qty)),
        "OVRS_ORD_UNPR": f"{float(price):.4f}",
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",
    }


# ── 토큰 (디스크 영속) ────────────────────────────────────────────────────────

def _load_token_disk(now: float) -> str | None:
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("token") and now < float(d.get("exp", 0)) - 60:
            _token_cache.update(token=d["token"], exp=float(d["exp"]))
            return d["token"]
    except Exception:
        pass
    return None


def _save_token_disk(token: str, exp: float) -> None:
    try:
        import safe_io
        safe_io.atomic_write_json(_TOKEN_FILE, {"token": token, "exp": exp})
    except Exception as e:
        logger.debug("토큰 디스크 저장 실패(무시): %s", e)


def _get_token() -> str | None:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["exp"] - 60:
        return _token_cache["token"]
    disk = _load_token_disk(now)         # 발급 레이트리밋 회피 — 디스크 우선
    if disk:
        return disk
    if not _key() or not _secret():
        logger.error("KIS 모의 앱키 없음 (KOREA_MOCK_API_KEY/KOREA_API_KEY)")
        return None
    url = _MOCK_BASE + _TOKEN_URL
    _assert_mock_url(url)
    try:
        r = requests.post(url, json={"grant_type": "client_credentials",
                                     "appkey": _key(), "appsecret": _secret()},
                          timeout=15, allow_redirects=False)
        r.raise_for_status()
        j = r.json()
        tok = j.get("access_token")
        if not tok:
            logger.error("KIS 모의 토큰 응답에 access_token 없음")
            return None
        exp = now + float(j.get("expires_in", 86400)) - 120
    except Exception as e:
        logger.error("KIS 모의 토큰 발급 실패: %s", e)
        return None
    _token_cache.update(token=tok, exp=exp)
    _save_token_disk(tok, exp)
    return tok


def _hashkey(body: dict) -> str | None:
    url = _MOCK_BASE + _HASHKEY_URL
    _assert_mock_url(url)
    try:
        r = requests.post(url, headers={"appkey": _key(), "appsecret": _secret(),
                                        "content-type": "application/json"},
                          json=body, timeout=10, allow_redirects=False)
        r.raise_for_status()
        return r.json().get("HASH")
    except Exception as e:
        logger.debug("hashkey 실패(무시): %s", e)
        return None


def _headers(tr_id: str, extra: dict | None = None) -> dict | None:
    tok = _get_token()
    if not tok:
        return None
    h = {"content-type": "application/json; charset=utf-8",
         "authorization": f"Bearer {tok}", "appkey": _key(), "appsecret": _secret(),
         "tr_id": tr_id, "custtype": "P"}
    if extra:
        h.update(extra)
    return h


def _http_get(url: str, headers: dict, params: dict, *, retries: int = 2) -> dict | None:
    """모의 GET 단일 통로 — 도메인 하드락 + 간헐 500(레이트리밋) 0.5s 간격 재시도. 실패 시 None.

    ★S6 확정: openapivts(모의) 서버는 price·balance·present-balance 전반에 간헐 500 → 재시도 필수.
    (주문 POST 는 재시도 금지 — 중복체결 위험. GET 조회만.)
    """
    _assert_mock_url(url)
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15, allow_redirects=False)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5)
    logger.warning("KIS 모의 GET 실패 [%s] (재시도 %d회): %s", url.rsplit("/", 1)[-1], retries, last_err)
    return None


# ── 현재가 ────────────────────────────────────────────────────────────────────

def get_price(symbol: str, excd: str | None = None, *, retries: int = 2) -> float | None:
    """해외 현재가. ★S6 확정: 모의 quote 엔드포인트는 간헐 500(레이트리밋) → _http_get 재시도."""
    excd = excd or _PRICE_EXCD.get(exchange_of(symbol), "NAS")
    h = _headers(_TR_PRICE)
    if not h:
        return None
    j = _http_get(_MOCK_BASE + _PRICE_URL, h,
                  {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()}, retries=retries)
    if not j:
        return None
    last = (j.get("output") or {}).get("last")
    return float(last) if last not in (None, "", "0") else None


# ── 잔고 ──────────────────────────────────────────────────────────────────────

def present_balance() -> dict:
    """해외 체결기준현재잔고(VTRP6504R) — 외화 주문가능액·NAV·환율. ★S6 라이브 확정 필드.

    반환 {ok, cash_usd, nav_usd, krw_asset, fx, pnl, raw}:
      cash_usd  외화 주문가능금액(output3.frcr_use_psbl_amt) — 환전/통합증거금-USD 전이면 0
      krw_asset 총자산(output3.tot_asst_amt) — 모의계좌 KRW 시드
      nav_usd   tot_asst_amt / fx (USD 환산 총자산) — fx 있을 때만
      fx        USD 환율(output2 USD행.frst_bltn_exrt)
    """
    blank = {"ok": False, "cash_usd": None, "nav_usd": None, "krw_asset": None,
             "fx": None, "pnl": None, "raw": None}
    cano, prdt = account()
    if not cano:
        logger.error("KOREA_MOCK_ACCOUNT_NO 미설정 — present-balance 차단(fail-closed)")
        return blank
    h = _headers(_TR_PRESENT)
    if not h:
        return blank
    params = {"CANO": cano, "ACNT_PRDT_CD": prdt, "WCRC_FRCR_DVSN_CD": "02",
              "NATN_CD": "000", "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"}
    res = _http_get(_MOCK_BASE + _PRESENT_BALANCE_URL, h, params)
    if res is None:
        return blank
    o3 = res.get("output3") or {}
    cash_usd = _f(o3, "frcr_use_psbl_amt")
    krw_asset = _f(o3, "tot_asst_amt")
    pnl = _f(o3, "tot_evlu_pfls_amt")
    fx = None
    for row in res.get("output2") or []:
        if (row.get("crcy_cd") or "").upper() == "USD":
            fx = _f(row, "frst_bltn_exrt") or None
            if not cash_usd:
                cash_usd = _f(row, "frcr_dncl_amt_2")   # USD 예수금 폴백
            break
    nav_usd = (krw_asset / fx) if (krw_asset and fx) else None
    return {"ok": str(res.get("rt_cd", "")) == "0", "cash_usd": cash_usd or None,
            "nav_usd": nav_usd, "krw_asset": krw_asset or None, "fx": fx,
            "pnl": pnl, "raw": res}


def get_balance() -> dict:
    """해외 모의계좌 잔고. 필드명은 라이브 스모크 전 미확정 → graceful(None+키 로깅).

    반환: {ok, positions{sym:{shares,avg_price,cur_price,value,pnl}}, pos_value, cash_usd, nav, raw}
    """
    cano, prdt = account()
    if not cano:
        logger.error("KOREA_MOCK_ACCOUNT_NO 미설정 — 잔고조회 차단(fail-closed)")
        return {"ok": False, "positions": {}, "pos_value": 0.0, "cash_usd": None, "nav": None, "raw": None}
    h = _headers(_TR_BALANCE)
    if not h:
        return {"ok": False, "positions": {}, "pos_value": 0.0, "cash_usd": None, "nav": None, "raw": None}
    params = {"CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": "NASD",
              "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""}
    res = _http_get(_MOCK_BASE + _BALANCE_URL, h, params)
    if res is None:
        return {"ok": False, "positions": {}, "pos_value": 0.0, "cash_usd": None, "nav": None, "raw": None}
    positions = {}
    for it in res.get("output1", []) or []:
        sym = (it.get("ovrs_pdno") or "").strip().upper()
        if not sym:
            continue
        positions[sym] = {
            "shares": _f(it, "ovrs_cblc_qty"), "avg_price": _f(it, "pchs_avg_pric"),
            "cur_price": _f(it, "now_pric2"), "value": _f(it, "ovrs_stck_evlu_amt"),
            "pnl": _f(it, "frcr_evlu_pfls_amt"),
        }
    pos_value = sum(p["value"] for p in positions.values())
    # NAV·현금은 present-balance(VTRP6504R)에서 — ★S6 확정. VTTS3012R output2 는 P&L 위주라 부정확.
    pb = present_balance()
    cash = pb.get("cash_usd")
    nav = pb.get("nav_usd")
    if nav is None:
        nav = (pos_value + cash) if cash is not None else (pos_value or None)
    if cash is None and nav is not None:
        cash = max(0.0, nav - pos_value)
    return {"ok": True, "positions": positions, "pos_value": pos_value,
            "cash_usd": cash, "nav": nav, "krw_asset": pb.get("krw_asset"),
            "fx": pb.get("fx"), "raw": res}


def _f(d: dict, key: str) -> float:
    try:
        return float(str(d.get(key, "") or "0").replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


# ── 주문 ──────────────────────────────────────────────────────────────────────

def place_order(symbol: str, qty: int, side: str, price: float,
                excd: str | None = None) -> dict:
    """해외 모의 주문(정수주·지정가). 계좌# 미설정/수량0 → fail-closed.

    반환: {ok, ord_no, msg, raw}
    """
    cano, prdt = account()
    if not cano:
        return {"ok": False, "ord_no": None, "msg": "계좌# 미설정(fail-closed)", "raw": None}
    qty = int(qty)
    if qty <= 0:
        return {"ok": False, "ord_no": None, "msg": "수량 0 이하", "raw": None}
    if side not in ("buy", "sell"):
        return {"ok": False, "ord_no": None, "msg": f"잘못된 side: {side}", "raw": None}
    if not price or price <= 0:
        return {"ok": False, "ord_no": None, "msg": "지정가 필요(해외 정수주)", "raw": None}

    excd = excd or exchange_of(symbol)
    body = build_order_body(cano, prdt, excd, symbol, qty, price)
    h = _headers(_order_tr_id(side), extra={"hashkey": _hashkey(body) or ""})
    if not h:
        return {"ok": False, "ord_no": None, "msg": "토큰 없음", "raw": None}
    url = _MOCK_BASE + _ORDER_URL
    _assert_mock_url(url)
    try:
        r = requests.post(url, headers=h, json=body, timeout=15, allow_redirects=False)
        r.raise_for_status()
        res = r.json()
    except Exception as e:
        logger.error("KIS 모의 주문 실패 [%s %s]: %s", side, symbol, e)
        return {"ok": False, "ord_no": None, "msg": "요청 실패", "raw": None}
    ok = str(res.get("rt_cd", "")) == "0"
    return {"ok": ok, "ord_no": (res.get("output") or {}).get("ODNO"),
            "msg": res.get("msg1", ""), "raw": res}
