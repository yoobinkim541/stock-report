#!/usr/bin/env python3
"""kis_quote.py — 한국투자증권(KIS) **실계좌 시세 전용·읽기전용** REST 어댑터.

목적: 실시간 현재가·10단계 호가·체결 거래량을 KIS 실전 시세 서버에서 수신(yfinance 대체용).
KR 국내는 무료 실시간, 美 해외는 '해외 실시간시세 신청' 시 실시간·미신청 시 지연(graceful).

안전 경계 (절대 불변):
  - **읽기전용**: 주문 URL·주문 TR·주문 함수가 이 파일에 존재하지 않는다(구조적 보장 + grep 테스트).
    허용 POST 는 인증(토큰 발급)뿐. 모든 시세 조회는 GET.
  - 실전 시세 도메인(openapi.koreainvestment.com:9443) 하드락 — _assert_quote_url 통과해야만 호출.
  - REALTIME_ENABLED=true 아니면 동작 안 함(opt-in). 실 앱키(KOREA_API_KEY/SECRET) 없으면 fail-closed.
  - 실계좌 자동집행과 무관 — 이 파일은 '가격 숫자'만 제공. 집행은 paper(mock)/수동(불변).

env:
  REALTIME_ENABLED   "true" 여야 동작 (기본 off)
  KOREA_API_KEY      실전 앱키 (모의 KOREA_MOCK_API_KEY 와 별개 — 실시간 시세는 실전 키)
  KOREA_API_SECRET   실전 시크릿
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_QUOTE_BASE = "https://openapi.koreainvestment.com:9443"   # ★ 하드락 — 실전 시세 전용·읽기전용
_TOKEN_URL = "/oauth2/tokenP"
_KR_PRICE_URL = "/uapi/domestic-stock/v1/quotations/inquire-price"
_KR_ASKING_URL = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
_OVRS_PRICE_URL = "/uapi/overseas-price/v1/quotations/price"
_KR_VOLRANK_URL = "/uapi/domestic-stock/v1/quotations/volume-rank"

# 시세 조회 TR (읽기전용). 응답 필드명은 라이브 스모크 전 확정 금지.
_TR_KR_PRICE = "FHKST01010100"    # 국내 현재가
_TR_KR_ASKING = "FHKST01010200"   # 국내 호가+예상체결 (10단계)
_TR_OVRS_PRICE = "HHDFS00000300"  # 해외 현재가 (S6서 동작 확인된 TR)
_TR_KR_VOLRANK = "FHPST01710000"  # 국내 거래량/거래대금 순위 (단기 스캐너용 — 조회 전용)

_TOKEN_FILE = os.path.expanduser("~/.cache/kis_quote_token.json")
_token_cache: dict = {"token": None, "exp": 0.0}

# 美 종목 → 해외 현재가 EXCD (kis_mock 와 동일 규칙)
_US_EXCH = {
    "ORCL": "NYS", "UNH": "NYS", "SAP": "NYS",
    "SGOV": "AMS", "SPMO": "AMS",
    "QLD": "AMS", "TQQQ": "NAS", "SQQQ": "NAS",
    "SOXL": "AMS", "SSO": "AMS", "SOXS": "AMS",
    "NVDL": "NAS", "NVD": "NAS", "TSLL": "NAS", "AAPU": "NAS",
    "AMZU": "NAS", "GGLL": "NAS", "MSFU": "NAS", "METU": "NAS",
    "CONL": "NAS", "PLTU": "NAS", "MSTU": "NAS",
}


# ── 설정 ──────────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return os.getenv("REALTIME_ENABLED", "false").lower() == "true"


def _key() -> str | None:
    return os.getenv("KOREA_API_KEY")        # 실전 앱키만 (모의 fallback 없음 — 실시간은 실전)


def _secret() -> str | None:
    return os.getenv("KOREA_API_SECRET")


def _us_excd(ticker: str) -> str:
    return _US_EXCH.get(ticker.upper().replace(".", ""), "NAS")


# ── 안전 가드 ─────────────────────────────────────────────────────────────────

def _assert_quote_url(url: str) -> None:
    """실전 시세 도메인 외 호출 원천 차단."""
    if not url.startswith(_QUOTE_BASE + "/"):
        raise RuntimeError(f"[안전차단] 시세 도메인 외 호출 시도: {url}")


# ── 순수 파서 (무네트워크·폐형해 테스트 대상) ─────────────────────────────────

def _f(d: dict, key: str) -> float:
    try:
        return float(str(d.get(key, "") or "0").replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


def parse_kr_price(output: dict) -> dict:
    """국내 현재가 output → {price, volume}. stck_prpr=현재가·acml_vol=누적거래량."""
    return {"price": _f(output, "stck_prpr") or None, "volume": _f(output, "acml_vol")}


def parse_kr_orderbook(output: dict, depth: int = 10) -> dict:
    """국내 호가 output1 → 10단계 매수/매도 호가. askp/bidp{n}=가격·askp_rsqn/bidp_rsqn{n}=잔량."""
    asks, bids = [], []
    for i in range(1, depth + 1):
        ap, aq = _f(output, f"askp{i}"), _f(output, f"askp_rsqn{i}")
        bp, bq = _f(output, f"bidp{i}"), _f(output, f"bidp_rsqn{i}")
        if ap > 0:
            asks.append((ap, aq))
        if bp > 0:
            bids.append((bp, bq))
    return {
        "asks": asks, "bids": bids,
        "best_ask": asks[0][0] if asks else None,
        "best_bid": bids[0][0] if bids else None,
    }


def parse_overseas_price(output: dict) -> dict:
    """해외 현재가 output → {price, volume}. last=현재가·tvol=거래량."""
    last = output.get("last")
    price = _f(output, "last") if last not in (None, "", "0") else None
    return {"price": price, "volume": _f(output, "tvol")}


def parse_volume_rank(rows: list[dict]) -> list[dict]:
    """거래량 순위 output → [{code, name, price, chg_pct, volume, turnover}].

    mksc_shrn_iscd=단축코드·hts_kor_isnm=종목명·stck_prpr=현재가·prdy_ctrt=등락률%·
    acml_vol=누적거래량·acml_tr_pbmn=누적거래대금(원).
    """
    out = []
    for r in rows or []:
        code = str(r.get("mksc_shrn_iscd") or "").strip()
        if not code:
            continue
        out.append({"code": code, "name": str(r.get("hts_kor_isnm") or "").strip(),
                    "price": _f(r, "stck_prpr"), "chg_pct": _f(r, "prdy_ctrt"),
                    "volume": _f(r, "acml_vol"), "turnover": _f(r, "acml_tr_pbmn")})
    return out


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
        logger.debug("시세 토큰 디스크 저장 실패(무시): %s", e)


def _get_token() -> str | None:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["exp"] - 60:
        return _token_cache["token"]
    disk = _load_token_disk(now)
    if disk:
        return disk
    if not _key() or not _secret():
        logger.error("KIS 실전 앱키 없음 (KOREA_API_KEY/SECRET) — 시세 fail-closed")
        return None
    url = _QUOTE_BASE + _TOKEN_URL
    _assert_quote_url(url)
    try:
        r = requests.post(url, json={"grant_type": "client_credentials",
                                     "appkey": _key(), "appsecret": _secret()},
                          timeout=15, allow_redirects=False)
        r.raise_for_status()
        j = r.json()
        tok = j.get("access_token")
        if not tok:
            logger.error("KIS 시세 토큰 응답에 access_token 없음")
            return None
        exp = now + float(j.get("expires_in", 86400)) - 120
    except Exception as e:
        logger.error("KIS 시세 토큰 발급 실패: %s", e)
        return None
    _token_cache.update(token=tok, exp=exp)
    _save_token_disk(tok, exp)
    return tok


def _headers(tr_id: str) -> dict | None:
    tok = _get_token()
    if not tok:
        return None
    return {"content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {tok}", "appkey": _key(), "appsecret": _secret(),
            "tr_id": tr_id, "custtype": "P"}


# 간이 서킷브레이커 — KIS REST 장애 시 대시보드 8초 프래그먼트가 매 틱 ~수십초 블로킹되는 것 방지.
# full-실패 시 잠깐 개방(즉시 None → 소비자 yfinance 폴백), 쿨다운 후 자동 재시도(self-heal). 표시경로 한정.
_CB = {"open_until": 0.0}
_CB_COOLDOWN = 45.0   # 개방 유지 초


def _http_get(url: str, headers: dict, params: dict, *, retries: int = 2) -> dict | None:
    """시세 GET 단일 통로 — 도메인 하드락 + 간헐 500 0.5s 재시도 + 서킷브레이커. 실패 None. (GET 전용 — 주문 POST 없음.)"""
    _assert_quote_url(url)
    if time.time() < _CB["open_until"]:
        return None   # 서킷 개방 중 — 즉시 폴백(반복 프리즈 회피)
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)
            r.raise_for_status()
            _CB["open_until"] = 0.0   # 성공 → 서킷 닫기
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5)
    _CB["open_until"] = time.time() + _CB_COOLDOWN   # full-실패 → 개방(이후 호출 즉시 폴백·self-heal)
    logger.warning("KIS 시세 GET 실패 [%s] (재시도 %d) — 서킷 %.0fs 개방: %s",
                   url.rsplit("/", 1)[-1], retries, _CB_COOLDOWN, last_err)
    return None


# ── 공개 API (읽기전용) ───────────────────────────────────────────────────────

def get_quote(symbol: str, *, market: str = "KR") -> dict | None:
    """현재가+거래량. 반환 {price, volume, ts, source} 또는 None."""
    if not is_enabled():
        return None
    if market.upper() == "KR":
        h = _headers(_TR_KR_PRICE)
        if not h:
            return None
        j = _http_get(_QUOTE_BASE + _KR_PRICE_URL, h,
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
        if not j:
            return None
        p = parse_kr_price(j.get("output") or {})
    else:
        h = _headers(_TR_OVRS_PRICE)
        if not h:
            return None
        j = _http_get(_QUOTE_BASE + _OVRS_PRICE_URL, h,
                      {"AUTH": "", "EXCD": _us_excd(symbol), "SYMB": symbol.upper()})
        if not j:
            return None
        p = parse_overseas_price(j.get("output") or {})
    if not p.get("price"):
        return None
    return {**p, "ts": time.time(), "source": "kis_rest"}


def get_orderbook(symbol: str, *, market: str = "KR", depth: int = 10) -> dict | None:
    """10단계 호가. 반환 {bids, asks, best_bid, best_ask, ts, source} 또는 None.

    KR 국내만 확정. 美 호가 TR 은 라이브 스모크 전 미확정 → None(graceful).
    """
    if not is_enabled():
        return None
    if market.upper() != "KR":
        return None   # 美 호가 TR 미확정 — 스모크 후 보강
    h = _headers(_TR_KR_ASKING)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _KR_ASKING_URL, h,
                  {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
    if not j:
        return None
    ob = parse_kr_orderbook(j.get("output1") or {}, depth=depth)
    if ob["best_bid"] is None and ob["best_ask"] is None:
        return None
    return {**ob, "ts": time.time(), "source": "kis_rest"}


def get_volume(symbol: str, *, market: str = "KR") -> dict | None:
    q = get_quote(symbol, market=market)
    if not q:
        return None
    return {"volume": q.get("volume"), "ts": q["ts"]}


def get_snapshot(symbol: str, *, market: str = "KR") -> dict | None:
    """현재가+거래량+호가(KR) 통합. 부분 성공 허용."""
    q = get_quote(symbol, market=market)
    if not q:
        return None
    ob = get_orderbook(symbol, market=market) or {}
    return {**q, "bids": ob.get("bids"), "asks": ob.get("asks"),
            "best_bid": ob.get("best_bid"), "best_ask": ob.get("best_ask")}


def volume_rank_kr() -> list[dict] | None:
    """국내 거래대금 순위 상위 (단기 스캐너용·조회 전용 GET). 실패/비활성 None.

    FID_BLNG_CLS_CODE=3 → 거래금액순 정렬. 응답 필드 단위(acml_tr_pbmn=원)는
    라이브 스모크로 확정 — 실패 시 스캐너가 정적 유니버스로 폴백하므로 graceful.
    """
    if not is_enabled():
        return None
    h = _headers(_TR_KR_VOLRANK)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _KR_VOLRANK_URL, h, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "3", "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})
    if not j:
        return None
    rows = parse_volume_rank(j.get("output") or [])
    return rows or None
