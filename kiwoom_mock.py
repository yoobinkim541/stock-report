#!/usr/bin/env python3
"""
kiwoom_mock.py — 키움 모의투자(국내주식) 어댑터. **모의 도메인 하드락.**

안전 경계 (절대 불변):
  - 모든 요청은 mockapi.kiwoom.com(_MOCK_BASE)으로만 — 실전 api.kiwoom.com 경로 없음.
  - 단일 통로 _post()/_get_token()이 _assert_mock_url() 가드를 통과해야만 호출.
  - 실거래 주문 함수 미제공 — 여기서 만드는 주문은 전부 모의계좌 대상.
  - is_enabled()(KIWOOM_MOCK_ENABLED=true)가 아니면 루프가 호출 자체를 안 함.

키움 사실:
  - 주문은 앱키에 귀속된 계좌로 체결되므로 주문 바디에 계좌번호 불필요(kt10000/kt10001).
  - 앱키는 계좌 공용 — 모의투자 신청만 돼 있으면 KIWOOM_API_KEY 그대로 모의 도메인에서 동작.

env:
  KIWOOM_MOCK_ENABLED      "true" 여야 동작 (기본 off — 신청 전 오작동 방지)
  KIWOOM_MOCK_API_KEY      (없으면 KIWOOM_API_KEY 재사용)
  KIWOOM_MOCK_API_SECRET   (없으면 KIWOOM_API_SECRET 재사용)
  KIWOOM_MOCK_ACCOUNT_NO   모의 계좌번호 (표시·로깅용, 주문 바디엔 불필요)
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_MOCK_BASE  = "https://mockapi.kiwoom.com"   # ★ 하드락 — 변경 금지
_ORDER_URL  = "/api/dostk/ordr"
_ACNT_URL   = "/api/dostk/acnt"
_TOKEN_URL  = "/oauth2/token"

_token_cache: dict = {"token": None, "exp": 0.0}


# ── 설정 ──────────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return os.getenv("KIWOOM_MOCK_ENABLED", "false").lower() == "true"


def _key() -> str | None:
    return os.getenv("KIWOOM_MOCK_API_KEY") or os.getenv("KIWOOM_API_KEY")


def _secret() -> str | None:
    return os.getenv("KIWOOM_MOCK_API_SECRET") or os.getenv("KIWOOM_API_SECRET")


def account_no() -> str:
    return os.getenv("KIWOOM_MOCK_ACCOUNT_NO", "")


# ── 안전 가드 ─────────────────────────────────────────────────────────────────

def _assert_mock_url(url: str) -> None:
    """모의 도메인 외 호출 원천 차단 — 실전 주문 사고 방지."""
    if not url.startswith(_MOCK_BASE + "/"):
        raise RuntimeError(f"[안전차단] 모의 도메인 외 호출 시도: {url}")


# ── 숫자 파싱 ─────────────────────────────────────────────────────────────────

def _num(item: dict, key: str) -> float:
    raw = item.get(key, "") or ""
    try:
        return float(str(raw).replace(",", "").replace("%", "").strip() or "0")
    except ValueError:
        return 0.0


def _first_num(d: dict, keys: list[str]):
    for k in keys:
        if k in d and str(d[k]).strip() not in ("", "0"):
            return _num(d, k)
    return None


# ── 토큰 ──────────────────────────────────────────────────────────────────────

def _get_token() -> str | None:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["exp"] - 30:
        return _token_cache["token"]
    if not _key() or not _secret():
        logger.error("모의 앱키 없음 (KIWOOM_MOCK_API_KEY 또는 KIWOOM_API_KEY)")
        return None
    url = _MOCK_BASE + _TOKEN_URL
    _assert_mock_url(url)
    try:
        r = requests.post(
            url,
            json={"grant_type": "client_credentials", "appkey": _key(), "secretkey": _secret()},
            timeout=15,
            allow_redirects=False,   # 리다이렉트로 실전 도메인 유출 차단
        )
        r.raise_for_status()
        j = r.json()
        tok = j.get("token") or j.get("access_token")
        if not tok:
            logger.error("모의 토큰 응답에 token 없음")
            return None
        # 만료: 키움은 expires_dt(YYYYMMDDHHMMSS) 반환. 없으면 expires_in, 둘 다 없으면 1h.
        exp = now + 3600
        if j.get("expires_dt"):
            from datetime import datetime as _dt
            exp = _dt.strptime(str(j["expires_dt"]), "%Y%m%d%H%M%S").timestamp() - 60
        elif j.get("expires_in"):
            exp = now + float(j["expires_in"]) - 60
    except Exception as e:
        logger.error("모의 토큰 발급 실패: %s", e)   # fail-closed: 파싱 오류 포함 None 반환
        return None
    _token_cache.update(token=tok, exp=exp)
    return tok


# ── 공통 POST ─────────────────────────────────────────────────────────────────

def _post(path: str, api_id: str, body: dict) -> dict | None:
    tok = _get_token()
    if not tok:
        return None
    url = _MOCK_BASE + path
    _assert_mock_url(url)
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "Authorization": f"Bearer {tok}",
        "api-id": api_id,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=15, allow_redirects=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("모의 API 실패 [%s]: %s", api_id, e)
        return None


# ── 잔고 ──────────────────────────────────────────────────────────────────────

def get_balance() -> dict:
    """모의계좌 잔고.

    반환:
      {
        "ok": bool,                 # 조회 성공 여부 (False면 호출부는 매수 보류해야 함)
        "positions": {code: {name, shares, avg_price, cur_price, value, pnl, return_pct}},
        "pos_value": float,         # 보유 평가액 합 (kt00018 종목 리스트 — 신뢰 가능)
        "cash_krw": float | None,   # 예수금/주문가능현금 (요약필드 — 미확인 시 None)
        "nav": float | None,        # 순자산 (= pos_value + cash, 또는 추정예탁자산 요약필드)
        "raw": dict | None,
      }

    주의: 현금/NAV 요약 필드명은 라이브 모의응답으로 확정 필요 — 미확인 시 None 을
    반환하고 응답 키를 로깅한다(호출부가 보수적으로 동작하도록).
    """
    res = _post(_ACNT_URL, "kt00018", {"qry_tp": "2", "dmst_stex_tp": "KRX"})
    if not res or res.get("return_code", -1) != 0:
        logger.warning("모의 잔고 조회 실패: %s", (res or {}).get("return_msg", "no response"))
        return {"ok": False, "positions": {}, "pos_value": 0.0,
                "cash_krw": None, "nav": None, "raw": res}

    positions = {}
    for it in res.get("acnt_evlt_remn_indv_tot", []):
        code = (it.get("stk_cd") or "").strip().lstrip("A")
        if not code:
            continue
        positions[code] = {
            "name":       (it.get("stk_nm") or "").strip(),
            "shares":     _num(it, "rmnd_qty"),
            "avg_price":  _num(it, "pur_pric"),
            "cur_price":  _num(it, "cur_prc"),
            "value":      _num(it, "evlt_amt"),
            "pnl":        _num(it, "evltv_prft"),
            "return_pct": _num(it, "prft_rt"),
        }
    pos_value = sum(p["value"] for p in positions.values())
    # kt00018 확정 필드(2026-06 모의 검증): prsm_dpst_aset_amt = 추정예탁자산(NAV = 현금+평가),
    # tot_evlt_amt = 총평가금액(보유). **순수 예수금 필드는 없음** → 현금 = NAV - 보유평가액.
    nav  = _first_num(res, ["prsm_dpst_aset_amt", "tot_est_amt"])
    cash = _first_num(res, ["entr", "ord_alow_amt", "dnca_tot_amt", "prvs_rcdl_excc_amt"])
    if cash is None and nav is not None:
        cash = max(0.0, nav - pos_value)
    elif nav is None and cash is not None:
        nav = pos_value + cash
    if nav is None and cash is None:
        logger.warning("모의 NAV/현금 필드 미확인 — 응답 키: %s", list(res.keys()))
    return {"ok": True, "positions": positions, "pos_value": pos_value,
            "cash_krw": cash, "nav": nav, "raw": res}


# ── 주문 ──────────────────────────────────────────────────────────────────────

def place_order(code: str, qty: int, side: str, price: int | None = None) -> dict:
    """모의 주문 집행. side: 'buy'|'sell'. price=None → 시장가.

    반환: {ok: bool, ord_no: str|None, msg: str, raw: dict|None}
    """
    code = code.replace(".KS", "").replace(".KQ", "").lstrip("A")
    qty = int(qty)
    if qty <= 0:
        return {"ok": False, "ord_no": None, "msg": "수량 0 이하", "raw": None}
    if side not in ("buy", "sell"):
        return {"ok": False, "ord_no": None, "msg": f"잘못된 side: {side}", "raw": None}

    api_id  = "kt10000" if side == "buy" else "kt10001"   # 매수/매도
    trde_tp = "3" if price is None else "0"                # 3=시장가, 0=보통(지정가)
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": code,
        "ord_qty": str(qty),
        "ord_uv": "" if price is None else str(int(price)),
        "trde_tp": trde_tp,
        "cond_uv": "",
    }
    res = _post(_ORDER_URL, api_id, body)
    if not res:
        return {"ok": False, "ord_no": None, "msg": "요청 실패", "raw": None}
    ok = res.get("return_code", -1) == 0
    return {"ok": ok, "ord_no": res.get("ord_no"), "msg": res.get("return_msg", ""), "raw": res}
