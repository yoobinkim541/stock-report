"""providers/toss_api.py — 토스증권 Open API **읽기전용** 어댑터 (잔고·시세·환율).

공식 스펙(openapi.tossinvest.com, Open API v1.2+) 기준:
  - 인증: OAuth2 Client Credentials — POST /oauth2/token (form-urlencoded) →
    Authorization: Bearer. 토큰은 디스크 영속(만료 60s 마진 재발급 — 발급 낭비 방지).
  - 계좌: GET /api/v1/accounts → [{accountNo, accountSeq, accountType}]
  - 잔고: GET /api/v1/holdings (헤더 X-Tossinvest-Account: accountSeq) →
    {items: [{symbol, name, quantity, averagePurchasePrice, lastPrice, currency, ...}]}
  - 시세: GET /api/v1/prices?symbols=..., 환율: GET /api/v1/exchange-rate

안전 (CLAUDE.md 자동매매 금지 규율):
  - **읽기전용** — 주문/정정/취소/조건주문 API 경로가 이 코드에 존재하지 않는다
    (tests/test_toss_api.py 가 소스 grep 으로 강제). 시세·잔고 *숫자*만 제공.
  - 도메인 하드락: openapi.tossinvest.com 외 호출 불가 (_assert_url).
  - 키 미설정 시 fail-closed(None/[]) — 어떤 경로도 예외로 상위를 깨지 않는다.

환경변수: TOSS_API_KEY(client_id) / TOSS_API_SECRET(client_secret) / TOSS_ACCOUNT_SEQ(선택).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.tossinvest.com"          # 하드락 — 이 도메인 외 호출 금지
TOKEN_CACHE = Path(os.path.expanduser("~/.cache/toss_token.json"))
TIMEOUT = 15


def _assert_url(url: str) -> str:
    if not url.startswith(BASE_URL + "/"):
        raise ValueError(f"토스 API 도메인 밖 호출 차단: {url[:60]}")
    return url


def _keys() -> tuple[str, str] | None:
    k, s = os.getenv("TOSS_API_KEY", "").strip(), os.getenv("TOSS_API_SECRET", "").strip()
    return (k, s) if k and s else None


def get_token(force: bool = False) -> str | None:
    """액세스 토큰 (디스크 캐시·만료 60s 마진 재발급). 키 없음/실패 → None (fail-closed)."""
    keys = _keys()
    if not keys:
        return None
    if not force:
        try:
            if TOKEN_CACHE.exists():
                tok = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
                if tok.get("access_token") and time.time() < float(tok.get("expires_at", 0)) - 60:
                    return tok["access_token"]
        except Exception:
            pass
    import requests
    try:
        resp = requests.post(
            _assert_url(f"{BASE_URL}/oauth2/token"),
            data={"grant_type": "client_credentials",
                  "client_id": keys[0], "client_secret": keys[1]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            logger.error("토스 토큰 응답에 access_token 없음")
            return None
        try:
            TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = TOKEN_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "access_token": token,
                "expires_at": time.time() + float(data.get("expires_in", 3600)),
            }), encoding="utf-8")
            os.replace(tmp, TOKEN_CACHE)
        except Exception:
            pass
        return token
    except Exception as e:
        logger.error("토스 토큰 발급 실패: %s", e)
        return None


def _get(path: str, *, params: dict | None = None, account_seq=None):
    """GET 헬퍼 — Bearer + (계좌 API 면) X-Tossinvest-Account. 실패 → None."""
    token = get_token()
    if not token:
        return None
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)
    try:
        resp = requests.get(_assert_url(f"{BASE_URL}{path}"), params=params,
                            headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("토스 API %s 실패: %s", path, e)
        return None


def _result(payload):
    """공통 ApiResponse envelope → result (형식 밖 응답 graceful)."""
    if isinstance(payload, dict):
        return payload.get("result", payload)
    return payload


def accounts() -> list[dict]:
    """계좌 목록 — [{accountNo, accountSeq, accountType}]. 실패 → []."""
    res = _result(_get("/api/v1/accounts"))
    return res if isinstance(res, list) else []


def default_account_seq():
    """사용 계좌 accountSeq — env TOSS_ACCOUNT_SEQ 우선, 없으면 첫 BROKERAGE(종합매매)."""
    env = os.getenv("TOSS_ACCOUNT_SEQ", "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            logger.warning("TOSS_ACCOUNT_SEQ 형식 오류(%s) — 계좌 목록에서 선택", env)
    for a in accounts():
        if a.get("accountType") == "BROKERAGE" and a.get("accountSeq") is not None:
            return a["accountSeq"]
    return None


def holdings_raw(account_seq=None) -> dict | None:
    """보유 주식 원본(HoldingsOverview). 계좌/토큰 불가 → None."""
    seq = account_seq if account_seq is not None else default_account_seq()
    if seq is None:
        logger.error("토스 계좌 식별 불가 (TOSS_ACCOUNT_SEQ 미설정 + 계좌 목록 실패)")
        return None
    res = _result(_get("/api/v1/holdings", account_seq=seq))
    return res if isinstance(res, dict) else None


def _dec(v):
    """decimal-string("65000")/숫자 → float. 결측 → 0.0."""
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def normalize_holdings(overview: dict | None) -> list[dict]:
    """HoldingsOverview → [{symbol, name, shares, avg, last, currency, market, value, pnl}] (순수).

    market: 'US'(USD 티커) | 'KR'(6자리 숫자) — marketCountry 우선, 없으면 심볼 형태로 추정.
    """
    out = []
    for it in (overview or {}).get("items", []) or []:
        symbol = str(it.get("symbol") or "").strip()
        if not symbol:
            continue
        currency = str(it.get("currency") or "").upper() or ("KRW" if symbol.isdigit() else "USD")
        market = str(it.get("marketCountry") or "").upper()
        if market not in ("KR", "US"):
            market = "KR" if symbol.isdigit() else "US"
        shares = _dec(it.get("quantity"))
        avg = _dec(it.get("averagePurchasePrice"))
        last = _dec(it.get("lastPrice"))
        mv = it.get("marketValue")
        value = _dec(mv.get("amount") if isinstance(mv, dict) else mv)
        pl = it.get("profitLoss")
        pnl = _dec(pl.get("amount") if isinstance(pl, dict) else pl)
        out.append({
            "symbol": symbol,
            "name": str(it.get("name") or symbol),
            "shares": shares,
            "avg": avg,
            "last": last,
            "currency": currency,
            "market": market,
            "value": value or round(shares * last, 4),
            "pnl": pnl or round((last - avg) * shares, 4),
            "return_pct": round((last / avg - 1) * 100, 2) if avg > 0 and last > 0 else 0.0,
        })
    return out


def holdings(account_seq=None) -> list[dict]:
    """정규화 보유 목록. 실패 → [] (호출부는 빈값=조회실패로 취급하지 말 것 — raw None 구분)."""
    return normalize_holdings(holdings_raw(account_seq))


def prices(symbols: list[str]) -> dict[str, float]:
    """현재가 — {symbol: last}. 최대 200개. 실패 → {}."""
    if not symbols:
        return {}
    res = _result(_get("/api/v1/prices", params={"symbols": ",".join(symbols[:200])}))
    out = {}
    for row in res if isinstance(res, list) else []:
        sym, last = row.get("symbol"), _dec(row.get("lastPrice"))
        if sym and last > 0:
            out[str(sym)] = last
    return out


def exchange_rate(base: str = "USD", quote: str = "KRW"):
    """환율 — 실패 → None."""
    res = _result(_get("/api/v1/exchange-rate",
                       params={"baseCurrency": base, "quoteCurrency": quote}))
    if isinstance(res, dict):
        for k in ("rate", "exchangeRate", "price", "value"):
            v = _dec(res.get(k))
            if v > 0:
                return v
    return None
