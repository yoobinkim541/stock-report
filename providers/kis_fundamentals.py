#!/usr/bin/env python3
"""kis_fundamentals.py — 한국투자증권(KIS) **실계좌 종목정보·순위분석 전용·읽기전용** REST 어댑터.

목적: yfinance 에 없는 KR 전용 데이터 — 증권사별 투자의견(목표주가 포함)·신용잔고
일별추이·공매도 일별추이·재무비율(성장성/ROE/EPS/BPS/유보율/부채비율).

providers.kis_quote 와 같은 실전 앱키·토큰 캐시를 공유한다(감사 후속) — 페이지 하나에서
시세+종목정보를 함께 조회할 때 토큰 발급이 중복되지 않도록. 두 모듈은 "같은 KIS 실계좌
어댑터 계열"이라는 전제로 kis_quote 의 언더스코어 헬퍼(_get_token/_headers/_http_get/
_QUOTE_BASE)를 직접 재사용 — 이 파일 자체엔 별도 토큰 로직을 두지 않는다.

안전 경계 (kis_quote 와 동일):
  - **읽기전용**: 주문 URL·주문 TR·주문 함수가 이 파일에 존재하지 않는다(구조적 보장 + grep 테스트).
  - REALTIME_ENABLED=true 아니면 동작 안 함(opt-in). 실 앱키 없으면 fail-closed.
  - 실계좌 자동집행과 무관 — 조회 전용.
"""
from __future__ import annotations

import logging

from providers.kis_quote import (
    _headers,
    _http_get,
    _maybe_f,
    _QUOTE_BASE,
    is_enabled,
)

logger = logging.getLogger(__name__)

_OPINION_URL = "/uapi/domestic-stock/v1/quotations/invest-opinion"
_CREDIT_URL = "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
_SHORT_URL = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
_FINRATIO_URL = "/uapi/domestic-stock/v1/finance/financial-ratio"

# 종목정보/순위분석 조회 TR (읽기전용). 라이브 스모크로 확인된 필드명(실계좌 크리덴셜).
_TR_OPINION = "FHKST663300C0"    # 국내주식 종목투자의견 (증권사별 목표주가·의견)
_TR_CREDIT = "FHPST04760000"     # 국내주식 신용잔고 일별추이
_TR_SHORT = "FHPST04830000"      # 국내주식 공매도 일별추이
_TR_FINRATIO = "FHKST66430300"   # 국내주식 재무비율


# ── 순수 파서 (무네트워크·라이브 확인 필드명 기반) ─────────────────────────────

def parse_broker_opinions(rows: list[dict]) -> list[dict]:
    """종목투자의견 output → [{date, broker, opinion, prev_opinion, target_price,
    price_at_opinion, deviation_pct}]. mbcr_name=증권사명·hts_goal_prc=목표주가·
    dprt=괴리율(목표가 대비 당시 종가, %)."""
    out = []
    for r in rows or []:
        broker = str(r.get("mbcr_name") or "").strip()
        date = str(r.get("stck_bsop_date") or "").strip()
        if not broker or not date:
            continue
        out.append({
            "date": date,
            "broker": broker,
            "opinion": str(r.get("invt_opnn") or "").strip(),
            "prev_opinion": str(r.get("rgbf_invt_opnn") or "").strip(),
            "target_price": _maybe_f(r, "hts_goal_prc"),
            "price_at_opinion": _maybe_f(r, "stck_prdy_clpr"),
            "deviation_pct": _maybe_f(r, "dprt"),
        })
    return out


def parse_credit_balance(rows: list[dict]) -> list[dict]:
    """신용잔고 일별추이 output → [{date, price, loan_balance_shares,
    loan_balance_amount, loan_balance_rate_pct}]. whol_loan_rmnd_stcn=융자잔고
    주식수·whol_loan_rmnd_amt=잔고금액(천원)·whol_loan_rmnd_rate=잔고비율%."""
    out = []
    for r in rows or []:
        date = str(r.get("deal_date") or "").strip()
        if not date:
            continue
        out.append({
            "date": date,
            "price": _maybe_f(r, "stck_prpr"),
            "loan_balance_shares": _maybe_f(r, "whol_loan_rmnd_stcn"),
            "loan_balance_amount": _maybe_f(r, "whol_loan_rmnd_amt"),
            "loan_balance_rate_pct": _maybe_f(r, "whol_loan_rmnd_rate"),
        })
    return out


def parse_short_sale(rows: list[dict]) -> list[dict]:
    """공매도 일별추이 output2 → [{date, price, short_qty, short_ratio_pct,
    cumulative_short_ratio_pct}]. ssts_cntg_qty=공매도 체결수량·ssts_vol_rlim=
    거래량 대비 당일 공매도비중%·acml_ssts_cntg_qty_rlim=누적 비중%."""
    out = []
    for r in rows or []:
        date = str(r.get("stck_bsop_date") or "").strip()
        if not date:
            continue
        out.append({
            "date": date,
            "price": _maybe_f(r, "stck_clpr"),
            "short_qty": _maybe_f(r, "ssts_cntg_qty"),
            "short_ratio_pct": _maybe_f(r, "ssts_vol_rlim"),
            "cumulative_short_ratio_pct": _maybe_f(r, "acml_ssts_cntg_qty_rlim"),
        })
    return out


def parse_financial_ratios(rows: list[dict]) -> list[dict]:
    """재무비율 output → [{period, revenue_growth_pct, op_income_growth_pct,
    net_income_growth_pct, roe_pct, eps, bps, reserve_ratio_pct, debt_ratio_pct}].
    stac_yymm=결산연월(YYYYMM)·grs=매출증가율·roe_val=ROE·rsrv_rate=유보율·
    lblt_rate=부채비율."""
    out = []
    for r in rows or []:
        period = str(r.get("stac_yymm") or "").strip()
        if not period:
            continue
        out.append({
            "period": period,
            "revenue_growth_pct": _maybe_f(r, "grs"),
            "op_income_growth_pct": _maybe_f(r, "bsop_prfi_inrt"),
            "net_income_growth_pct": _maybe_f(r, "ntin_inrt"),
            "roe_pct": _maybe_f(r, "roe_val"),
            "eps": _maybe_f(r, "eps"),
            "bps": _maybe_f(r, "bps"),
            "reserve_ratio_pct": _maybe_f(r, "rsrv_rate"),
            "debt_ratio_pct": _maybe_f(r, "lblt_rate"),
        })
    return out


# ── 공개 API (읽기전용) ───────────────────────────────────────────────────────

def broker_opinions(code: str, *, date_from: str, date_to: str) -> list[dict] | None:
    """증권사별 투자의견(목표주가 포함) — date_from/date_to 는 YYYYMMDD. 실패/비활성 None."""
    if not is_enabled():
        return None
    h = _headers(_TR_OPINION)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _OPINION_URL, h, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633",
        "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": date_from, "FID_INPUT_DATE_2": date_to,
    })
    if not j:
        return None
    return parse_broker_opinions(j.get("output") or []) or None


def credit_balance_trend(code: str, *, date: str) -> list[dict] | None:
    """신용잔고 일별추이 — date 는 YYYYMMDD(결제일자). 실패/비활성 None."""
    if not is_enabled():
        return None
    h = _headers(_TR_CREDIT)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _CREDIT_URL, h, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": date,
    })
    if not j:
        return None
    return parse_credit_balance(j.get("output") or []) or None


def short_sale_trend(code: str, *, date_from: str, date_to: str) -> list[dict] | None:
    """공매도 일별추이 — date_from/date_to 는 YYYYMMDD. 실패/비활성 None."""
    if not is_enabled():
        return None
    h = _headers(_TR_SHORT)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _SHORT_URL, h, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": date_from, "FID_INPUT_DATE_2": date_to,
    })
    if not j:
        return None
    return parse_short_sale(j.get("output2") or []) or None


def financial_ratios(code: str, *, annual: bool = True) -> list[dict] | None:
    """재무비율(성장성/ROE/EPS/BPS/유보율/부채비율) — annual=False 면 분기. 실패/비활성 None."""
    if not is_enabled():
        return None
    h = _headers(_TR_FINRATIO)
    if not h:
        return None
    j = _http_get(_QUOTE_BASE + _FINRATIO_URL, h, {
        "fid_div_cls_code": "0" if annual else "1",
        "fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
    })
    if not j:
        return None
    return parse_financial_ratios(j.get("output") or []) or None
