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


def _trend_empty(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "stock_code": dart.stock_code(ticker),
        "market_type": "kr",
        "source": "DART",
        "trends": {
            "rev_yoy": None,
            "net_margin": None,
            "net_margin_chg": None,
            "debt_to_assets": None,
            "debt_to_assets_chg": None,
            "is_loss": None,
            "n_years": 0,
        },
        "rows": [],
        "confidence": "missing",
        "error": None,
    }


def _financial_row(year: int | None, financials: dict) -> dict:
    return {
        "year": year,
        "revenue": financials.get("revenue"),
        "operating_income": financials.get("operating_income"),
        "net_income": financials.get("net_income"),
        "equity": financials.get("equity"),
        "assets": financials.get("assets"),
        "liabilities": financials.get("liabilities"),
        "fs_div": financials.get("fs_div"),
        "fs_nm": financials.get("fs_nm"),
    }


def _margin(row: dict) -> float | None:
    return _safe_div(row.get("net_income"), row.get("revenue"))


def _debt_to_assets(row: dict) -> float | None:
    return _safe_div(row.get("liabilities"), row.get("assets"))


def financial_trends(
    ticker: str,
    *,
    years: int = 4,
    base_year: int | None = None,
    financial_rows: list[dict] | None = None,
) -> dict:
    """국내 종목 DART 연간 재무 추세. dashboard.views.financials와 같은 trends shape."""
    out = _trend_empty(ticker)
    code = dart.stock_code(ticker)
    if not code:
        out["error"] = "KR 종목 아님"
        return out

    rows: list[dict] = []
    errors: list[str] = []
    if financial_rows is not None:
        rows = [dict(r) for r in financial_rows if r]
    else:
        base = base_year or _infer_recent_report_year()
        for y in range(base - max(1, years) + 1, base + 1):
            src = dart.major_financials(ticker, year=y)
            if src.get("error"):
                errors.append(f"{y}: {src.get('error')}")
                continue
            fin = src.get("financials") or {}
            if not any(fin.get(k) is not None for k in ("revenue", "net_income", "assets", "liabilities")):
                continue
            rows.append(_financial_row(src.get("year", y), fin))

    rows = sorted(rows, key=lambda r: int(r.get("year") or 0))
    out["rows"] = rows
    tr = out["trends"]
    tr["n_years"] = len(rows)
    if not rows:
        out["error"] = "; ".join(errors[-2:]) if errors else "DART 재무 데이터 없음"
        return out

    rev_rows = [r for r in rows if r.get("revenue")]
    if len(rev_rows) >= 2:
        tr["rev_yoy"] = round(rev_rows[-1]["revenue"] / rev_rows[-2]["revenue"] - 1.0, 4)

    margin_rows = [r for r in rows if _margin(r) is not None]
    if margin_rows:
        tr["net_margin"] = round(_margin(margin_rows[-1]), 4)
        tr["is_loss"] = bool((margin_rows[-1].get("net_income") or 0) < 0)
        if len(margin_rows) >= 2:
            tr["net_margin_chg"] = round(_margin(margin_rows[-1]) - _margin(margin_rows[-2]), 4)

    debt_rows = [r for r in rows if _debt_to_assets(r) is not None]
    if debt_rows:
        tr["debt_to_assets"] = round(_debt_to_assets(debt_rows[-1]), 4)
        if len(debt_rows) >= 2:
            tr["debt_to_assets_chg"] = round(_debt_to_assets(debt_rows[-1]) - _debt_to_assets(debt_rows[-2]), 4)

    latest = rows[-1]
    out["fiscal_year"] = latest.get("year")
    out["fs_div"] = latest.get("fs_div")
    out["fs_nm"] = latest.get("fs_nm")
    out["confidence"] = "high" if len(rows) >= 2 else "partial"
    if errors:
        out["warnings"] = errors[-2:]
    return out
