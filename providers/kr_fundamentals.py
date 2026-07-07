#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 펀더멘털 밸류에이션 (DART 재무제표 + marcap 시총).

yfinance 의 .KS/.KQ 밸류에이션 필드는 자주 비어 있어 국내주식 PER/PBR/ROE/EPS가
불안정하다. 이 모듈은 DART 주요계정(연결 우선)과 FinanceData/marcap 시총·주식수를
결합해 국내 종목의 기본 밸류에이션을 계산한다. 네트워크 실패는 graceful(None)로 둔다.
"""
from __future__ import annotations

from datetime import date, timedelta

from providers import dart
from providers import kr_market_data as km


def _empty(ticker: str) -> dict:
    return {
        "per": None,
        "forward_pe": None,
        "pbr": None,
        "psr": None,
        "roe": None,
        "eps_ttm": None,
        "eps_fwd": None,
        "div_yield": None,
        "div_yield_5y_avg": None,
        "payout": None,
        "div_growth_1y": None,
        "div_growth_3y": None,
        "market_type": "kr",
        "source": "DART+marcap",
        "ticker": ticker,
        "stock_code": dart.stock_code(ticker),
        "confidence": "missing",
        "error": None,
    }


def _safe_div(num, den):
    try:
        n = float(num)
        d = float(den)
        if d == 0 or d != d:
            return None
        return n / d
    except (TypeError, ValueError):
        return None


def _latest_marcap_row(code: str, asof: str | None = None, *, market: str | None = None):
    """marcap_asof에서 code 행 하나. 실패 시 None."""
    asof = asof or date.today().isoformat()
    market = market or ""
    snap = km.marcap_asof(asof, market=market)
    if snap is None or len(snap) == 0:
        return None
    try:
        sub = snap[snap["Code"].map(km.norm_code) == km.norm_code(code)]
        if len(sub):
            return sub.iloc[0].to_dict()
    except Exception:
        return None
    return None


def _marcap_values(row: dict | None) -> dict:
    row = row or {}

    def f(key):
        try:
            v = row.get(key)
            return float(v) if v is not None and v == v else None
        except (TypeError, ValueError):
            return None

    market_cap = f("Marcap")
    close = f("Close")
    shares = f("Stocks")
    if shares is None and market_cap and close:
        shares = market_cap / close
    return {
        "market_cap": market_cap,
        "close": close,
        "shares": shares,
        "asof": str(row.get("Date"))[:10] if row.get("Date") is not None else None,
        "name": row.get("Name"),
        "market": row.get("Market"),
    }


def _infer_recent_report_year(today: date | None = None) -> int:
    """최근 사업보고서 기준 연도 추정. 3월 말 전에는 전전년도 사업보고서가 더 안전."""
    today = today or date.today()
    return today.year - 2 if today < date(today.year, 3, 31) else today.year - 1


def valuation_metrics(
    ticker: str,
    *,
    year: int | None = None,
    asof: str | None = None,
    financials: dict | None = None,
    marcap_row: dict | None = None,
) -> dict:
    """국내 종목 PER/PBR/PSR/ROE/EPS. DART·marcap 실패 시 기존 shape 유지."""
    out = _empty(ticker)
    code = dart.stock_code(ticker)
    if not code:
        out["error"] = "KR 종목 아님"
        return out

    if financials is None:
        year = year or _infer_recent_report_year()
        src = dart.major_financials(ticker, year=year)
        if src.get("error"):
            out.update({
                "fiscal_year": src.get("year", year),
                "reprt_code": src.get("reprt_code"),
                "error": src.get("error"),
            })
            return out
        financials = src.get("financials") or {}
        out.update({
            "fiscal_year": src.get("year", year),
            "reprt_code": src.get("reprt_code"),
            "fs_div": financials.get("fs_div"),
            "fs_nm": financials.get("fs_nm"),
        })
    else:
        out["fiscal_year"] = year
        out["fs_div"] = financials.get("fs_div")
        out["fs_nm"] = financials.get("fs_nm")

    if marcap_row is None:
        mkt = "KOSDAQ" if str(ticker).upper().endswith(".KQ") else ""
        marcap_row = _latest_marcap_row(code, asof=asof, market=mkt)
    mv = _marcap_values(marcap_row)
    out.update(mv)

    market_cap = mv.get("market_cap")
    shares = mv.get("shares")
    revenue = financials.get("revenue")
    net_income = financials.get("net_income")
    equity = financials.get("equity")
    eps = financials.get("eps")

    if eps is None and net_income is not None and shares:
        eps = _safe_div(net_income, shares)
    bps = _safe_div(equity, shares) if equity is not None and shares else None

    out.update({
        "revenue": revenue,
        "operating_income": financials.get("operating_income"),
        "net_income": net_income,
        "equity": equity,
        "assets": financials.get("assets"),
        "liabilities": financials.get("liabilities"),
        "eps_ttm": eps,
        "bps": bps,
        "per": _safe_div(market_cap, net_income) if net_income and net_income > 0 else None,
        "pbr": _safe_div(market_cap, equity) if equity and equity > 0 else None,
        "psr": _safe_div(market_cap, revenue) if revenue and revenue > 0 else None,
        "roe": _safe_div(net_income, equity) if equity and equity > 0 else None,
        "per_status": "loss" if net_income is not None and net_income <= 0 else None,
    })

    if market_cap and financials:
        out["confidence"] = "high" if out.get("fs_div") == "CFS" else "medium"
    elif financials:
        out["confidence"] = "partial"
    return out


def recent_annual_metrics(ticker: str, *, asof: str | None = None, lookback_years: int = 3) -> dict:
    """최근 사업보고서가 아직 없을 때 전년도들을 순차 조회하는 편의 함수."""
    base = _infer_recent_report_year()
    last = None
    for y in range(base, base - max(1, lookback_years), -1):
        last = valuation_metrics(ticker, year=y, asof=asof)
        if not last.get("error") and last.get("confidence") != "missing":
            return last
    return last or _empty(ticker)
