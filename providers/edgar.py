#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/edgar.py — SEC EDGAR 재무 데이터층 (Phase B / §A·D).

★핵심: EDGAR companyfacts 는 **상장폐지 기업의 과거 재무도 보존** → 생존편향 없는 재무악화 피처
(매출·이익·부채 추세)를 무료로 확보(XBRL ~2010+). 美 퇴출예측의 핵심 피처원.

서버 실측(2026.06): `www.sec.gov/files/company_tickers.json`(ticker→CIK) + `data.sec.gov/api/xbrl/
companyfacts/CIK##########.json` 모두 동작(User-Agent 헤더 필수). 캐시 + graceful.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache/edgar"))
_CIK_TTL_H = 24 * 7
_FACTS_TTL_H = 24 * 7

_REVENUE_CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
_NI_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
_ASSET_CONCEPTS = ["Assets"]
_LIAB_CONCEPTS = ["Liabilities"]


def _get(url: str) -> bytes:
    from lib.http_utils import http_get, EDGAR_UA      # SEC 준수 UA(연락처)
    return http_get(url, timeout=30, ua=EDGAR_UA)


def _cik_map() -> dict:
    """{TICKER: CIK(10자리)} — company_tickers.json 캐시. 실패 시 {}."""
    try:
        from lib.file_cache import is_fresh
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / "cik_map.json"
        if not is_fresh(p, _CIK_TTL_H):
            raw = _get("https://www.sec.gov/files/company_tickers.json")
            p.write_bytes(raw)
        data = json.loads(p.read_text(encoding="utf-8"))
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
    except Exception as e:
        logger.warning("EDGAR CIK 맵 실패: %s", e)
        return {}


def companyfacts(ticker: str, *, cik: str | None = None) -> dict | None:
    """ticker(또는 cik) 의 companyfacts JSON. 캐시. 실패 시 None."""
    try:
        cik = cik or _cik_map().get((ticker or "").upper())
        if not cik:
            return None
        from lib.file_cache import is_fresh
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"cf_{cik}.json"
        if not is_fresh(p, _FACTS_TTL_H):
            raw = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
            p.write_bytes(raw)
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("companyfacts 실패 %s: %s", ticker, e)
        return None


def _annual_series(cf: dict, concepts: list[str], asof: str | None, *, agg: str = "first") -> list[tuple]:
    """us-gaap 연간(FY) 값 [(end, val)] 시간순 — asof 이하 종료기간만(무룩어헤드).

    agg='first': 첫 매칭 concept 만(단일 항목 — 순이익/자산/부채).
    agg='max'  : 전 concept 종합해 end 별 **최댓값**(총매출 — 'Revenues'가 부분값/세그먼트일 때 방지 +
                 연도별 concept 전환(2018 ASC606) 호환).
    """
    facts = (cf or {}).get("facts", {}).get("us-gaap", {})

    def _iter(c):
        usd = (facts.get(c) or {}).get("units", {}).get("USD") or []
        for it in usd:
            if it.get("fp") != "FY" or it.get("form") not in ("10-K", "10-K/A", "20-F"):
                continue
            end, val = it.get("end"), it.get("val")
            if end is None or val is None or (asof and end > str(asof)[:10]):
                continue
            yield end, float(val)

    if agg == "max":
        by_end = {}
        for c in concepts:
            for end, val in _iter(c):
                by_end[end] = max(by_end.get(end, float("-inf")), val)
        return sorted(by_end.items())
    for c in concepts:
        seen = {end: val for end, val in _iter(c)}
        if seen:
            return sorted(seen.items())
    return []


def _val_at(series: list[tuple], end: str, *, tol_days: int = 20):
    """series 에서 end 와 ±tol_days 내 가장 가까운 값(회계연도 52/53주 드리프트 흡수). 없으면 None."""
    import datetime as _dt
    try:
        e = _dt.date.fromisoformat(end)
    except Exception:
        return None
    best, bd = None, tol_days + 1
    for en, v in series:
        try:
            d = abs((_dt.date.fromisoformat(en) - e).days)
        except Exception:
            continue
        if d < bd:
            bd, best = d, v
    return best if bd <= tol_days else None


def fundamental_trends(ticker: str, *, asof: str | None = None, cf: dict | None = None) -> dict:
    """재무악화 피처(무룩어헤드) — 매출 YoY·순마진·순마진추세·부채비율·부채추세·이익적자플래그.

    cf 주입 시 무네트워크(테스트). 결측은 None(graceful).
    """
    out = {"rev_yoy": None, "net_margin": None, "net_margin_chg": None,
           "debt_to_assets": None, "debt_to_assets_chg": None, "is_loss": None, "n_years": 0}
    cf = cf if cf is not None else companyfacts(ticker)
    if not cf:
        return out
    rev = _annual_series(cf, _REVENUE_CONCEPTS, asof, agg="max")    # 총매출(부분값 방지)
    ni = _annual_series(cf, _NI_CONCEPTS, asof)
    assets = _annual_series(cf, _ASSET_CONCEPTS, asof)
    liab = _annual_series(cf, _LIAB_CONCEPTS, asof)
    out["n_years"] = len(rev)
    if len(rev) >= 2 and rev[-2][1]:
        out["rev_yoy"] = round(rev[-1][1] / rev[-2][1] - 1.0, 4)
    # 순마진: 이익 end 에 ±일 내 매출을 매칭(회계연도 드리프트 흡수)
    if ni and rev:
        out["is_loss"] = bool(ni[-1][1] < 0)
        rev_now = _val_at(rev, ni[-1][0])
        if rev_now:
            out["net_margin"] = round(ni[-1][1] / rev_now, 4)
        if len(ni) >= 2 and out["net_margin"] is not None:
            rev_prev = _val_at(rev, ni[-2][0])
            if rev_prev:
                out["net_margin_chg"] = round(out["net_margin"] - ni[-2][1] / rev_prev, 4)
    a_by, l_by = dict(assets), dict(liab)
    common = sorted(set(a_by) & set(l_by))
    if common:
        le = common[-1]
        if a_by[le]:
            out["debt_to_assets"] = round(l_by[le] / a_by[le], 4)
        if len(common) >= 2:
            pe = common[-2]
            if a_by[le] and a_by[pe]:
                out["debt_to_assets_chg"] = round(l_by[le] / a_by[le] - l_by[pe] / a_by[pe], 4)
    return out
