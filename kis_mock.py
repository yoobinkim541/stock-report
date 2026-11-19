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

import fcntl
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
_PSAMOUNT_URL = "/uapi/overseas-stock/v1/trading/inquire-psamount"
_PRICE_URL = "/uapi/overseas-price/v1/quotations/price"

# 모의 해외 TR_ID — ★S6 라이브 스모크서 잔고·현재가·present-balance 확정(2026-06-27). 주문은 미확정.
_TR_BUY = "VTTT1002U"     # 미국 매수(모의) — 라이브 미확정
_TR_SELL = "VTTT1001U"    # 미국 매도(모의) — 라이브 미확정
_TR_BALANCE = "VTTS3012R"  # 해외 잔고(모의) — ✅ 라이브 확정 (포지션·P&L)
_TR_PSAMOUNT = "VTTS3007R"  # 해외 매수가능금액(모의) — ✅ 2026-11-19 라이브 확인 (USD 현금·환율)
_TR_PRICE = "HHDFS00000300"  # 해외 현재가(모의/실전 공용) — ✅ 라이브 확정 (레이트리밋 有→재시도)

_TOKEN_FILE = os.path.expanduser("~/.cache/kis_mock_token.json")
_token_cache: dict = {"token": None, "exp": 0.0}

# 모의 게이트웨이는 초당 호출 한도를 넘기면 HTTP 500 + msg_cd EGW00201("초당 거래건수를 초과하였습니다")을
# 준다(2026-11-19 연속 GET 8회 실측: 200 은 2회). 주문마다 hashkey·잔고 GET·주문 POST 가 1초 안에 몰려
# QQQ·SGOV 주문이 거부됐다. 모든 모의 호출은 _send 한 통로로 프로세스 간 간격을 둔다.
# 간격 실측(현재가 GET 연속): 0.55s 8회 중 3회·0.75s 10회 중 1회 EGW00201, 1.0s 10회 0회.
_RATE_LIMIT_CODE = "EGW00201"
_RATE_LIMIT_RETRIES = 3
_MIN_INTERVAL_S = float(os.getenv("KIS_MOCK_MIN_INTERVAL_S", "1.0"))
_PACE_FILE = os.path.expanduser("~/.cache/kis_mock_pace")

# 종목 → 해외거래소코드(주문 OVRS_EXCG_CD). 미상은 NASD 기본 — get_price 가 다른 거래소에서 찾으면
# _EXCH_FOUND 에 기억해 덮는다. 2026-11-19 KIS 모의 현재가로 전수 확인(US 유니버스·레버리지 ETF).
_TICKER_EXCH = {
    "ORCL": "NYSE", "UNH": "NYSE", "SAP": "NYSE", "CRM": "NYSE",
    "SGOV": "NYSE", "SPMO": "AMEX", "GLD": "AMEX",
    # Leveraged ETFs. NASDAQ listed funds use NASD; NYSE Arca funds use KIS AMEX code.
    "QLD": "AMEX", "TQQQ": "NASD", "SQQQ": "NASD",
    "SOXL": "AMEX", "SSO": "AMEX", "SOXS": "AMEX",
    "NVDL": "NASD", "NVD": "NASD", "TSLL": "NASD", "AAPU": "NASD",
    "AMZU": "NASD", "GGLL": "NASD", "MSFU": "NASD", "METU": "NASD",
    "CONL": "NASD", "PLTU": "NASD", "MSTU": "AMEX",
}
_EXCH_FOUND: dict[str, str] = {}
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
    key = ticker.upper().replace(".", "")
    return _EXCH_FOUND.get(key) or _TICKER_EXCH.get(key, "NASD")


# ── 안전 가드 ─────────────────────────────────────────────────────────────────

def _assert_mock_url(url: str) -> None:
    """모의 도메인 외 호출 원천 차단 — 실전 주문 사고 방지."""
    if not url.startswith(_MOCK_BASE + "/"):
        raise RuntimeError(f"[안전차단] 모의 도메인 외 호출 시도: {url}")


# ── HTTP 단일 통로 (간격·초당 한도) ──────────────────────────────────────────

def _pace() -> None:
    """직전 모의 호출과 _MIN_INTERVAL_S 간격 — us_mock_track·intraday·report 가 같은 앱키를 공유하므로 파일 락."""
    if _MIN_INTERVAL_S <= 0:
        return
    os.makedirs(os.path.dirname(_PACE_FILE), exist_ok=True)
    with open(_PACE_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            last = float(f.read().strip() or 0)
        except ValueError:
            last = 0.0
        wait = last + _MIN_INTERVAL_S - time.time()
        if wait > 0:
            time.sleep(min(wait, _MIN_INTERVAL_S))
        f.seek(0)
        f.truncate()
        f.write(repr(time.time()))


def _rate_limited(resp) -> bool:
    if getattr(resp, "status_code", None) != 500:
        return False
    try:
        return (resp.json() or {}).get("msg_cd") == _RATE_LIMIT_CODE
    except ValueError:
        return False


def _send(method: str, url: str, **kwargs):
    """모의 HTTP 단일 통로 — 도메인 하드락 + 호출 간격 + EGW00201 재전송.

    EGW00201 은 게이트웨이가 처리 전에 거절한 응답이라 주문 POST 를 다시 보내도 중복체결이 없다.
    그 밖의 HTTP 오류는 응답 본문(msg_cd·msg1)을 붙여 예외로 올린다.
    """
    _assert_mock_url(url)
    call = requests.post if method == "POST" else requests.get
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        _pace()
        r = call(url, timeout=15, allow_redirects=False, **kwargs)
        if not _rate_limited(r) or attempt == _RATE_LIMIT_RETRIES:
            break
        logger.warning("KIS 모의 초당 한도(EGW00201) [%s] — 재전송 %d/%d",
                       url.rsplit("/", 1)[-1], attempt + 1, _RATE_LIMIT_RETRIES)
        time.sleep(1.0)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e} · body={r.text[:200]}", response=r) from None
    return r


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
    try:
        r = _send("POST", url, json={"grant_type": "client_credentials",
                                     "appkey": _key(), "appsecret": _secret()})
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
    try:
        r = _send("POST", url, headers={"appkey": _key(), "appsecret": _secret(),
                                        "content-type": "application/json"}, json=body)
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
    """모의 GET — _send(간격·EGW00201 재전송) 위에 그 밖의 간헐 오류 0.5s 간격 재시도. 실패 시 None.

    ★S6 확정: openapivts(모의) 서버는 price·balance·present-balance 전반에 간헐 500 → 재시도 필수.
    (주문 POST 는 EGW00201 외 재시도 금지 — 중복체결 위험. GET 조회만.)
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            return _send("GET", url, headers=headers, params=params).json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5)
    logger.warning("KIS 모의 GET 실패 [%s] (재시도 %d회): %s", url.rsplit("/", 1)[-1], retries, last_err)
    return None


# ── 현재가 ────────────────────────────────────────────────────────────────────

def _quote_last(symbol: str, excd: str, retries: int) -> float | None:
    h = _headers(_TR_PRICE)
    if not h:
        return None
    j = _http_get(_MOCK_BASE + _PRICE_URL, h,
                  {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()}, retries=retries)
    if not j:
        return None
    last = (j.get("output") or {}).get("last")
    return float(last) if last not in (None, "", "0") else None


def get_price(symbol: str, excd: str | None = None, *, retries: int = 2) -> float | None:
    """해외 현재가. ★S6 확정: 모의 quote 엔드포인트는 간헐 500(레이트리밋) → _http_get 재시도.

    다른 거래소 코드로 물으면 KIS 는 rt_cd 0 + 빈 last 를 준다. excd 를 지정하지 않았고 매핑
    거래소에서 빈 값이면 나머지 거래소를 찾아보고, 찾은 거래소를 기억해 주문에도 쓴다
    (2026-11-19: SGOV·GLD 매핑 오류로 현재가 None·주문 거래소 오류).
    """
    if excd:
        return _quote_last(symbol, excd, retries)
    mapped = exchange_of(symbol)
    price = _quote_last(symbol, _PRICE_EXCD.get(mapped, "NAS"), retries)
    if price is not None:
        _EXCH_FOUND.setdefault(symbol.upper().replace(".", ""), mapped)
        return price
    for order_code, quote_code in _PRICE_EXCD.items():
        if order_code == mapped:
            continue
        price = _quote_last(symbol, quote_code, retries)
        if price is not None:
            logger.warning("KIS 거래소 매핑 보정: %s %s → %s", symbol.upper(), mapped, order_code)
            _EXCH_FOUND[symbol.upper().replace(".", "")] = order_code
            return price
    return None


# ── 잔고 ──────────────────────────────────────────────────────────────────────

def usd_cash() -> dict:
    """해외 매수가능금액(VTTS3007R) — USD 현금 실측. ✅ 2026-11-19 라이브 확인.

    반환 {ok, cash_usd, integrated_usd, fx, msg}:
      cash_usd        USD 주문가능금액 + 매도재사용가능금액 — NAV·매수 상한의 현금
      integrated_usd  원화 통합증거금까지 포함한 주문가능 USD(frcr_ord_psbl_amt1) — 참고용
      fx              환율(exrt)
    체결기준현재잔고(VTRP6504R)는 00시대에 '모의투자 서비스가 지연'·환율 미고시로 자주 실패해
    더 쓰지 않는다. 원화는 NAV·매수에 넣지 않는다(USD 기준 NAV — KOREA_MOCK_SEED 와 같은 통화).
    """
    blank = {"ok": False, "cash_usd": None, "integrated_usd": None, "fx": None, "msg": None}
    cano, prdt = account()
    if not cano:
        return blank
    h = _headers(_TR_PSAMOUNT)
    if not h:
        return blank
    # 종목·단가는 조회 형식상 필수 — 금액 필드는 종목과 무관(USD 계좌 단위)
    params = {"CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": "NASD",
              "OVRS_ORD_UNPR": "1", "ITEM_CD": "QQQ"}
    res = _http_get(_MOCK_BASE + _PSAMOUNT_URL, h, params)
    if res is None:
        return blank
    if str(res.get("rt_cd", "")) != "0":
        return {**blank, "msg": str(res.get("msg1") or "").strip()}
    o = res.get("output") or {}
    # ovrs_ord_psbl_amt 가 매도재사용분을 포함하는지 문서상 불명 → 둘 중 큰 값(중복 합산 없음)
    cash = max(_f(o, "ovrs_ord_psbl_amt"), _f(o, "ord_psbl_frcr_amt") + _f(o, "sll_ruse_psbl_amt"))
    return {"ok": True, "cash_usd": cash, "integrated_usd": _f(o, "frcr_ord_psbl_amt1") or None,
            "fx": _f(o, "exrt") or None, "msg": None}


def get_balance() -> dict:
    """해외 모의계좌 잔고 — USD 기준.

    반환: {ok, positions{sym:{shares,avg_price,cur_price,value,pnl}}, pos_value, cash_usd, nav,
           integrated_usd, fx, raw}
    - KIS 는 업무 오류(예: '모의투자 신청내역을 확인하세요')를 HTTP 200 + rt_cd≠0 으로 돌려준다
      → ok=False(msg 포함). 빈 output1 을 '보유 없음'으로 오인하지 않는다.
    - NAV = 보유평가액 + USD 현금(usd_cash). 현금 조회가 실패하면 nav=None — 보유평가액만으로
      NAV 를 채우면 현금이 빠진 반쪽 NAV 가 된다.
    - 잔고 조회의 OVRS_EXCG_CD=NASD 는 모의에서 미국 전체(NYSE·AMEX 보유 포함 — 라이브 확인).
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
    if str(res.get("rt_cd", "0")) != "0":
        msg = str(res.get("msg1") or "").strip()
        logger.warning("KIS 모의 잔고 거부 rt_cd=%s: %s", res.get("rt_cd"), msg)
        return {"ok": False, "positions": {}, "pos_value": 0.0, "cash_usd": None, "nav": None,
                "msg": msg, "raw": res}
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
    uc = usd_cash()
    cash = uc.get("cash_usd")
    nav = (pos_value + cash) if cash is not None else None
    if nav is None:
        logger.warning("KIS 모의 USD 현금 미확인(%s) — NAV 결측(보유평가액 $%s 만으로 채우지 않음)",
                       uc.get("msg") or "응답 없음", f"{pos_value:,.0f}")
    elif not cash and (uc.get("integrated_usd") or 0) > 1000:
        logger.warning("KIS 모의 USD 예수금 0 — 원화 통합증거금($%s)은 NAV·매수에 쓰지 않는다",
                       f"{uc['integrated_usd']:,.0f}")
    return {"ok": True, "positions": positions, "pos_value": pos_value,
            "cash_usd": cash, "nav": nav, "integrated_usd": uc.get("integrated_usd"),
            "fx": uc.get("fx"), "raw": res}


def _f(d: dict, key: str) -> float:
    try:
        return float(str(d.get(key, "") or "0").replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


# ── 주문 ──────────────────────────────────────────────────────────────────────

def _held_shares(symbol: str) -> float | None:
    """체결확인용 단일 종목 보유수량 — 잔고조회 자체가 실패하면 None(비교 불가)."""
    bal = get_balance()
    if not bal.get("ok"):
        return None
    return (bal.get("positions") or {}).get(symbol.upper(), {}).get("shares")


def _post_order_once(url: str, headers: dict, body: dict) -> dict | None:
    try:
        return _send("POST", url, headers=headers, json=body).json()
    except Exception as e:
        logger.error("KIS 모의 주문 실패: %s", e)
        return None


def place_order(symbol: str, qty: int, side: str, price: float,
                excd: str | None = None) -> dict:
    """해외 모의 주문(정수주·지정가). 계좌# 미설정/수량0 → fail-closed.

    주문 POST 는 원칙적으로 재시도 금지(응답 유실 시 재시도하면 중복체결 위험) — 단, 500 등으로
    응답을 못 받으면 재시도 전에 잔고 조회(GET·재시도 안전)로 실제 체결 여부를 먼저 확인한다:
    체결 확인되면 재시도 없이 성공 반환, 미체결 확인되면 그때만 1회 재시도, 확인 자체가
    안 되면(잔고조회도 실패) 재시도하지 않고 실패 반환(체결 여부 모르는 채 재시도 금지).

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

    if not excd:
        key = symbol.upper().replace(".", "")
        if key not in _TICKER_EXCH and key not in _EXCH_FOUND:
            get_price(symbol)          # 미매핑 종목 — 현재가로 거래소를 찾아 _EXCH_FOUND 에 기억
        excd = exchange_of(symbol)
    body = build_order_body(cano, prdt, excd, symbol, qty, price)
    h = _headers(_order_tr_id(side), extra={"hashkey": _hashkey(body) or ""})
    if not h:
        return {"ok": False, "ord_no": None, "msg": "토큰 없음", "raw": None}
    url = _MOCK_BASE + _ORDER_URL
    _assert_mock_url(url)

    before = _held_shares(symbol)
    res = _post_order_once(url, h, body)
    if res is None:
        after = _held_shares(symbol)
        if before is not None and after is not None and after != before:
            logger.warning("KIS 모의 주문 [%s %s] 응답 유실이었지만 실체결 확인(%s→%s) — 재시도 생략",
                           side, symbol, before, after)
            return {"ok": True, "ord_no": None, "msg": "체결 확인됨(주문 응답 유실)", "raw": None}
        if before is None or after is None:
            return {"ok": False, "ord_no": None, "msg": "요청 실패(체결여부 확인불가 — 재시도 안 함)",
                    "raw": None}
        logger.info("KIS 모의 주문 [%s %s] 미체결 확인 — 1회 재시도", side, symbol)
        res = _post_order_once(url, h, body)
        if res is None:
            return {"ok": False, "ord_no": None, "msg": "요청 실패(재시도 후에도)", "raw": None}
    ok = str(res.get("rt_cd", "")) == "0"
    return {"ok": ok, "ord_no": (res.get("output") or {}).get("ODNO"),
            "msg": res.get("msg1", ""), "raw": res}
