#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fundamental_score.py — 100점 기준 재무 건강도 점수 모델
yfinance 데이터를 기반으로 한 한국어 스코어링 시스템

Score breakdown:
  A. 수익성 (Profitability):       30점
  B. 이익의 질 (Earnings Quality):  25점
  C. 재무 안정성 (Financial Stability): 20점
  D. 성장의 질 (Growth Quality):    15점
  E. 자본 배분 (Capital Allocation): 10점
  ----------------------------------------
  Total:                           100점

Grade mapping:
  S = 85-100, A = 75-84, B = 60-74, C = 45-59, D = 0-44
"""

import yfinance as yf
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────

def _safe(info, key, default=None):
    """Safely extract a value from ticker.info dict."""
    val = info.get(key, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val

def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default

def is_etf(info: dict) -> bool:
    """Return True if the ticker is an ETF or ETN based on quoteType."""
    quote_type = (info.get('quoteType') or '').upper()
    return quote_type in ('ETF', 'ETN')


def _past_3y_financials(ticker):
    """Return up to 4 years of annual financial data as a list of dicts."""
    try:
        fs = ticker.financials
        bs = ticker.balance_sheet
        cf = ticker.cashflow
    except Exception:
        return []
    if fs is None or fs.empty:
        return []
    records = []
    cols = fs.columns[:4]  # up to 4 years
    for col in cols:
        rec = {}
        for label, val in fs.get(col, {}).items():
            rec[label] = _safe_float(val)
        if bs is not None and not bs.empty:
            for label, val in bs.get(col, {}).items():
                rec[label] = _safe_float(val)
        if cf is not None and not cf.empty:
            for label, val in cf.get(col, {}).items():
                rec[label] = _safe_float(val)
        records.append(rec)
    return records


# ── section scorers ─────────────────────────────────────────────────────

def _score_A_profitability(ticker, info, fin_records):
    """A. 수익성 (30점)"""
    result = {"score": 0, "max": 30, "items": {}, "notes": []}
    items = result["items"]

    # 1. ROIC 양수 (7점)
    roe = _safe(info, "returnOnEquity") or _safe(info, "returnOnCapital")
    roe_val = _safe_float(roe)
    if roe_val is not None and roe_val > 0:
        items["ROIC_양수"] = {"score": 7, "max": 7, "note": f"ROE/ROIC = {roe_val:.2%} > 0"}
    else:
        items["ROIC_양수"] = {"score": 0, "max": 7, "note": f"ROE/ROIC = {roe_val} (음수 또는 데이터 없음)"}
        result["notes"].append("ROIC 양수 조건 불충족")

    # 2. ROIC > WACC (8점)
    # Estimate WACC: cost of equity ≈ risk-free (3.5%) + beta * ERP (5.5%)
    beta = _safe(info, "beta")
    if beta is not None and roe_val is not None:
        wacc_est = 0.035 + beta * 0.055
        if roe_val > wacc_est:
            items["ROIC_WACC"] = {"score": 8, "max": 8,
                                  "note": f"ROIC {roe_val:.2%} > WACC 추정 {wacc_est:.2%} (beta={beta:.2f})"}
        else:
            items["ROIC_WACC"] = {"score": 0, "max": 8,
                                  "note": f"ROIC {roe_val:.2%} ≤ WACC 추정 {wacc_est:.2%} (beta={beta:.2f})"}
            result["notes"].append("ROIC가 WACC 미만")
    else:
        items["ROIC_WACC"] = {"score": 0, "max": 8, "note": "ROE/ROIC 또는 beta 데이터 부족으로 평가 불가"}
        result["notes"].append("WACC 비교 불가")

    # 3. ROIC 3년 개선 (7점)
    if len(fin_records) >= 2:
        roic_vals = []
        for r in fin_records:
            v = r.get("returnOnEquity") or r.get("returnOnCapital")
            if v is not None:
                roic_vals.append(v)
        if len(roic_vals) >= 2 and roic_vals[-1] > roic_vals[0]:
            items["ROIC_개선"] = {"score": 7, "max": 7,
                                  "note": f"ROIC {roic_vals[0]:.2%} → {roic_vals[-1]:.2%} (개선)"}
        elif len(roic_vals) >= 2:
            items["ROIC_개선"] = {"score": 0, "max": 7,
                                  "note": f"ROIC {roic_vals[0]:.2%} → {roic_vals[-1]:.2%} (악화 또는 정체)"}
            result["notes"].append("ROIC 3년 추세 악화")
        else:
            items["ROIC_개선"] = {"score": 0, "max": 7, "note": "ROIC 시계열 데이터 부족"}
            result["notes"].append("ROIC 3년 데이터 불충분")
    else:
        items["ROIC_개선"] = {"score": 0, "max": 7, "note": "재무제표 데이터 부족"}
        result["notes"].append("ROIC 개선 평가 불가")

    # 4. 영업현금흐름 양수 (8점)
    ocf = _safe(info, "operatingCashFlow")
    ocf_val = _safe_float(ocf)
    if ocf_val is not None and ocf_val > 0:
        items["OCF_양수"] = {"score": 8, "max": 8, "note": f"영업현금흐름 = {ocf_val:,.0f} > 0"}
    else:
        if fin_records:
            for r in fin_records:
                ocf_r = r.get("Operating Cash Flow") or r.get("Total Cash From Operating Activities")
                if ocf_r is not None and ocf_r > 0:
                    items["OCF_양수"] = {"score": 8, "max": 8, "note": f"영업현금흐름(재무제표) = {ocf_r:,.0f} > 0"}
                    break
            else:
                items["OCF_양수"] = {"score": 0, "max": 8, "note": "영업현금흐름 음수 또는 데이터 없음"}
                result["notes"].append("영업현금흐름 음수")
        else:
            items["OCF_양수"] = {"score": 0, "max": 8, "note": "영업현금흐름 데이터 없음"}
            result["notes"].append("영업현금흐름 확인 불가")

    for k, v in items.items():
        result["score"] += v["score"]
    return result


def _score_B_earnings_quality(ticker, info, fin_records):
    """B. 이익의 질 (25점)"""
    result = {"score": 0, "max": 25, "items": {}, "notes": []}
    items = result["items"]

    # 1. 영업현금흐름 > 순이익 (7점)
    ocf = _safe(info, "operatingCashFlow")
    ni = _safe(info, "netIncomeToCommon")
    ocf_val = _safe_float(ocf)
    ni_val = _safe_float(ni)
    if ocf_val is not None and ni_val is not None:
        if ocf_val > ni_val:
            items["OCF_NI"] = {"score": 7, "max": 7,
                               "note": f"OCF {ocf_val:,.0f} > 순이익 {ni_val:,.0f}"}
        else:
            items["OCF_NI"] = {"score": 0, "max": 7,
                               "note": f"OCF {ocf_val:,.0f} ≤ 순이익 {ni_val:,.0f}"}
            result["notes"].append("영업현금흐름이 순이익보다 낮음")
    else:
        # fallback: try from financials
        if fin_records:
            r = fin_records[0]
            ocf_f = r.get("Operating Cash Flow") or r.get("Total Cash From Operating Activities")
            ni_f = r.get("Net Income") or r.get("Net Income Common Stockholders")
            if ocf_f and ni_f and ocf_f > ni_f:
                items["OCF_NI"] = {"score": 7, "max": 7,
                                   "note": f"OCF(FS) {ocf_f:,.0f} > 순이익(FS) {ni_f:,.0f}"}
            else:
                items["OCF_NI"] = {"score": 0, "max": 7, "note": "영업현금흐름/순이익 데이터 부족"}
                result["notes"].append("OCF/NI 비교 불가")
        else:
            items["OCF_NI"] = {"score": 0, "max": 7, "note": "데이터 부족"}
            result["notes"].append("OCF/NI 비교 불가")

    # 2. 매출채권 증가율 ≤ 매출 증가율 (6점)
    if len(fin_records) >= 2:
        r0, r1 = fin_records[0], fin_records[-1]
        rev0 = r0.get("Total Revenue") or r0.get("Revenue")
        rev1 = r1.get("Total Revenue") or r1.get("Revenue")
        ar0 = r0.get("Accounts Receivable") or r0.get("Receivables") or r0.get("Net Receivables")
        ar1 = r1.get("Accounts Receivable") or r1.get("Receivables") or r1.get("Net Receivables")
        if rev0 and rev1 and ar0 and ar1 and rev0 > 0 and rev1 > 0:
            rev_g = (rev1 - rev0) / rev0
            ar_g = (ar1 - ar0) / ar0
            if ar_g <= rev_g:
                items["AR_성장"] = {"score": 6, "max": 6,
                                   "note": f"매출채권 증가율 {ar_g:.2%} ≤ 매출 증가율 {rev_g:.2%}"}
            else:
                items["AR_성장"] = {"score": 0, "max": 6,
                                   "note": f"매출채권 증가율 {ar_g:.2%} > 매출 증가율 {rev_g:.2%} (위험)"}
                result["notes"].append("매출채권이 매출보다 빠르게 증가")
        else:
            items["AR_성장"] = {"score": 0, "max": 6, "note": "매출/매출채권 데이터 부족"}
            result["notes"].append("AR/매출 비교 불가")
    else:
        items["AR_성장"] = {"score": 0, "max": 6, "note": "2년 이상 재무 데이터 필요"}
        result["notes"].append("매출채권 분석 불가")

    # 3. 재고 증가율 ≤ 매출원가 증가율 (6점)
    if len(fin_records) >= 2:
        r0, r1 = fin_records[0], fin_records[-1]
        cogs0 = r0.get("Cost Of Revenue") or r0.get("Cost of Revenue")
        cogs1 = r1.get("Cost Of Revenue") or r1.get("Cost of Revenue")
        inv0 = r0.get("Inventory")
        inv1 = r1.get("Inventory")
        if cogs0 and cogs1 and inv0 is not None and inv1 is not None and cogs0 > 0:
            cogs_g = (cogs1 - cogs0) / cogs0
            inv_g = (inv1 - inv0) / inv0
            if inv_g <= cogs_g:
                items["재고_성장"] = {"score": 6, "max": 6,
                                   "note": f"재고 증가율 {inv_g:.2%} ≤ 매출원가 증가율 {cogs_g:.2%}"}
            else:
                items["재고_성장"] = {"score": 0, "max": 6,
                                   "note": f"재고 증가율 {inv_g:.2%} > 매출원가 증가율 {cogs_g:.2%} (위험)"}
                result["notes"].append("재고가 매출원가보다 빠르게 증가")
        else:
            items["재고_성장"] = {"score": 0, "max": 6, "note": "재고/매출원가 데이터 부족"}
            result["notes"].append("재고 분석 불가")
    else:
        items["재고_성장"] = {"score": 0, "max": 6, "note": "2년 이상 재무 데이터 필요"}
        result["notes"].append("재고 분석 불가")

    # 4. 일회성 이익 의존도 낮음 (6점) — approximated via net income vs operating income
    ni_check = _safe(info, "netIncomeToCommon")
    oi_check = _safe(info, "operatingIncome")
    ni_c = _safe_float(ni_check)
    oi_c = _safe_float(oi_check)
    if ni_c is not None and oi_c is not None and oi_c != 0:
        ratio = ni_c / oi_c
        if 0.7 <= ratio <= 1.5:
            items["일회성_이익"] = {"score": 6, "max": 6,
                                 "note": f"순이익/영업이익 = {ratio:.2f} (정상 범위)"}
        else:
            items["일회성_이익"] = {"score": 3, "max": 6,
                                 "note": f"순이익/영업이익 = {ratio:.2f} (일회성 이익 가능성)"}
            result["notes"].append("일회성 이익 의존 의심")
    elif fin_records:
        r = fin_records[0]
        ni_r = r.get("Net Income") or r.get("Net Income Common Stockholders")
        oi_r = r.get("Operating Income") or r.get("EBIT")
        if ni_r and oi_r and oi_r != 0:
            ratio = ni_r / oi_r
            if 0.7 <= ratio <= 1.5:
                items["일회성_이익"] = {"score": 6, "max": 6,
                                     "note": f"순이익/영업이익(FS) = {ratio:.2f} (정상)"}
            else:
                items["일회성_이익"] = {"score": 3, "max": 6,
                                     "note": f"순이익/영업이익(FS) = {ratio:.2f} (비정상)"}
                result["notes"].append("일회성 이익 의심")
        else:
            items["일회성_이익"] = {"score": 3, "max": 6, "note": "충분한 데이터 없음 — 중간 점수"}
            result["notes"].append("일회성 이익 평가 불가")
    else:
        items["일회성_이익"] = {"score": 3, "max": 6, "note": "데이터 부족 — 중간 점수"}
        result["notes"].append("일회성 이익 평가 불가")

    for k, v in items.items():
        result["score"] += v["score"]
    return result


def _score_C_financial_stability(ticker, info, fin_records):
    """C. 재무 안정성 (20점)"""
    result = {"score": 0, "max": 20, "items": {}, "notes": []}
    items = result["items"]

    # 1. 순부채/EBITDA 안정적 (7점)
    ndte = _safe(info, "netDebtToEBITDA")
    ndte_val = _safe_float(ndte)
    if ndte_val is not None:
        if ndte_val < 3.0:
            items["부채_EBITDA"] = {"score": 7, "max": 7,
                                   "note": f"순부채/EBITDA = {ndte_val:.2f} (안정, <3.0)"}
        elif ndte_val < 5.0:
            items["부채_EBITDA"] = {"score": 4, "max": 7,
                                   "note": f"순부채/EBITDA = {ndte_val:.2f} (주의, 3~5)"}
            result["notes"].append("부채 수준 주의 필요")
        else:
            items["부채_EBITDA"] = {"score": 0, "max": 7,
                                   "note": f"순부채/EBITDA = {ndte_val:.2f} (위험, >5)"}
            result["notes"].append("과다 부채 위험")
    else:
        # fallback: total debt / EBITDA estimate
        td = _safe(info, "totalDebt")
        ebitda = _safe(info, "ebitda")
        td_v = _safe_float(td)
        eb_v = _safe_float(ebitda)
        if td_v is not None and eb_v is not None and eb_v > 0:
            ratio = td_v / eb_v
            if ratio < 3.0:
                items["부채_EBITDA"] = {"score": 7, "max": 7,
                                       "note": f"총부채/EBITDA = {ratio:.2f} (안정)"}
            elif ratio < 5.0:
                items["부채_EBITDA"] = {"score": 4, "max": 7,
                                       "note": f"총부채/EBITDA = {ratio:.2f} (주의)"}
            else:
                items["부채_EBITDA"] = {"score": 0, "max": 7,
                                       "note": f"총부채/EBITDA = {ratio:.2f} (위험)"}
        else:
            items["부채_EBITDA"] = {"score": 0, "max": 7, "note": "부채/EBITDA 데이터 없음"}
            result["notes"].append("부채 분석 불가")

    # 2. 이자보상배율 충분 (6점)
    ic = _safe(info, "interestCoverage")
    ic_val = _safe_float(ic)
    if ic_val is not None:
        if ic_val > 3.0:
            items["이자보상"] = {"score": 6, "max": 6,
                               "note": f"이자보상배율 = {ic_val:.2f} (충분, >3)"}
        elif ic_val > 1.5:
            items["이자보상"] = {"score": 3, "max": 6,
                               "note": f"이자보상배율 = {ic_val:.2f} (최소, 1.5~3)"}
            result["notes"].append("이자보상배율 낮음")
        else:
            items["이자보상"] = {"score": 0, "max": 6,
                               "note": f"이자보상배율 = {ic_val:.2f} (위험)"}
            result["notes"].append("이자보상배율 부족")
    else:
        # fallback: operating income / interest expense
        oi = _safe(info, "operatingIncome")
        ie = _safe(info, "interestExpense")
        oi_v = _safe_float(oi)
        ie_v = _safe_float(ie)
        if oi_v and ie_v and ie_v != 0:
            ic_est = oi_v / ie_v
            if ic_est > 3:
                items["이자보상"] = {"score": 6, "max": 6,
                                   "note": f"영업이익/이자비용 = {ic_est:.2f} (충분)"}
            elif ic_est > 1.5:
                items["이자보상"] = {"score": 3, "max": 6,
                                   "note": f"영업이익/이자비용 = {ic_est:.2f} (최소)"}
            else:
                items["이자보상"] = {"score": 0, "max": 6,
                                   "note": f"영업이익/이자비용 = {ic_est:.2f} (위험)"}
        else:
            items["이자보상"] = {"score": 0, "max": 6, "note": "이자보상배율 데이터 없음"}
            result["notes"].append("이자보상배율 확인 불가")

    # 3. 유동비율 악화 없음 (4점)
    cr = _safe(info, "currentRatio")
    cr_val = _safe_float(cr)
    if cr_val is not None:
        if cr_val >= 1.5:
            items["유동비율"] = {"score": 4, "max": 4,
                               "note": f"유동비율 = {cr_val:.2f} (양호, ≥1.5)"}
        elif cr_val >= 1.0:
            items["유동비율"] = {"score": 2, "max": 4,
                               "note": f"유동비율 = {cr_val:.2f} (최소 기준, 1.0~1.5)"}
            result["notes"].append("유동비율 낮음")
        else:
            items["유동비율"] = {"score": 0, "max": 4,
                               "note": f"유동비율 = {cr_val:.2f} (위험, <1.0)"}
            result["notes"].append("유동비율 위험")
    else:
        items["유동비율"] = {"score": 0, "max": 4, "note": "유동비율 데이터 없음"}
        result["notes"].append("유동비율 확인 불가")

    # 4. 유상증자 과하지 않음 (3점)
    so = _safe(info, "sharesOutstanding")
    so_dil = _safe(info, "dilutedSharesOutstanding")
    so_v = _safe_float(so)
    so_dil_v = _safe_float(so_dil)
    if so_v and so_dil_v:
        dil_ratio = (so_dil_v - so_v) / so_v
        if dil_ratio < 0.05:
            items["유상증자"] = {"score": 3, "max": 3,
                               "note": f"희석률 {dil_ratio:.2%} (양호)"}
        else:
            items["유상증자"] = {"score": 1, "max": 3,
                               "note": f"희석률 {dil_ratio:.2%} (주의)"}
            result["notes"].append("주식 희석률 높음")
    elif len(fin_records) >= 2:
        so0 = fin_records[-1].get("Weighted Average Shares Outstanding")
        so1 = fin_records[0].get("Weighted Average Shares Outstanding")
        if so0 and so1 and so0 > 0:
            so_g = (so1 - so0) / so0
            if so_g < 0.05:
                items["유상증자"] = {"score": 3, "max": 3,
                                   "note": f"발행주식 증가율 {so_g:.2%} (양호)"}
            else:
                items["유상증자"] = {"score": 1, "max": 3,
                                   "note": f"발행주식 증가율 {so_g:.2%} (주의)"}
        else:
            items["유상증자"] = {"score": 2, "max": 3, "note": "데이터 부족 — 중간 점수"}
    else:
        items["유상증자"] = {"score": 2, "max": 3, "note": "데이터 부족 — 중간 점수"}

    for k, v in items.items():
        result["score"] += v["score"]
    return result


def _score_D_growth_quality(ticker, info, fin_records):
    """D. 성장의 질 (15점)"""
    result = {"score": 0, "max": 15, "items": {}, "notes": []}
    items = result["items"]

    # 1. 매출 3년 성장 (4점)
    rev_g = _safe(info, "revenueGrowth")
    rev_g_v = _safe_float(rev_g)
    if rev_g_v is not None:
        if rev_g_v > 0.05:
            items["매출성장"] = {"score": 4, "max": 4,
                               "note": f"매출 성장률 = {rev_g_v:.2%} (성장 중)"}
        elif rev_g_v > 0:
            items["매출성장"] = {"score": 2, "max": 4,
                               "note": f"매출 성장률 = {rev_g_v:.2%} (미미한 성장)"}
        else:
            items["매출성장"] = {"score": 0, "max": 4,
                               "note": f"매출 성장률 = {rev_g_v:.2%} (역성장)"}
            result["notes"].append("매출 역성장")
    elif len(fin_records) >= 2:
        r0 = fin_records[0].get("Total Revenue") or fin_records[0].get("Revenue")
        r1 = fin_records[-1].get("Total Revenue") or fin_records[-1].get("Revenue")
        if r0 and r1 and r0 > 0:
            cagr = (r1 / r0) ** (1 / max(len(fin_records) - 1, 1)) - 1
            if cagr > 0.05:
                items["매출성장"] = {"score": 4, "max": 4,
                                   "note": f"연평균 매출 성장률 = {cagr:.2%} (성장)"}
            elif cagr > 0:
                items["매출성장"] = {"score": 2, "max": 4,
                                   "note": f"연평균 매출 성장률 = {cagr:.2%} (미미)"}
            else:
                items["매출성장"] = {"score": 0, "max": 4,
                                   "note": f"연평균 매출 성장률 = {cagr:.2%} (역성장)"}
        else:
            items["매출성장"] = {"score": 0, "max": 4, "note": "매출 데이터 부족"}
    else:
        items["매출성장"] = {"score": 0, "max": 4, "note": "매출 데이터 부족"}

    # 2. 영업이익률 유지/개선 (4점)
    om = _safe(info, "operatingMargins")
    om_v = _safe_float(om)
    if om_v is not None:
        if om_v > 0.10:
            items["영업이익률"] = {"score": 4, "max": 4,
                                 "note": f"영업이익률 = {om_v:.2%} (양호, >10%)"}
        elif om_v > 0:
            items["영업이익률"] = {"score": 2, "max": 4,
                                 "note": f"영업이익률 = {om_v:.2%} (개선 필요)"}
        else:
            items["영업이익률"] = {"score": 0, "max": 4,
                                 "note": f"영업이익률 = {om_v:.2%} (적자)"}
            result["notes"].append("영업이익률 적자")
    elif len(fin_records) >= 2:
        om_vals = []
        for r in fin_records:
            rev = r.get("Total Revenue") or r.get("Revenue")
            oi = r.get("Operating Income") or r.get("EBIT")
            if rev and oi and rev > 0:
                om_vals.append(oi / rev)
        if om_vals:
            latest_om = om_vals[-1]
            if latest_om > 0.10:
                items["영업이익률"] = {"score": 4, "max": 4,
                                     "note": f"영업이익률(FS) = {latest_om:.2%} (양호)"}
            elif latest_om > 0:
                items["영업이익률"] = {"score": 2, "max": 4,
                                     "note": f"영업이익률(FS) = {latest_om:.2%}"}
            else:
                items["영업이익률"] = {"score": 0, "max": 4,
                                     "note": f"영업이익률(FS) = {latest_om:.2%} (적자)"}
        else:
            items["영업이익률"] = {"score": 0, "max": 4, "note": "영업이익률 데이터 부족"}
    else:
        items["영업이익률"] = {"score": 0, "max": 4, "note": "영업이익률 데이터 부족"}

    # 3. FCF 매출 성장과 함께 개선 (4점)
    fcf = _safe(info, "freeCashFlow")
    fcf_v = _safe_float(fcf)
    if fcf_v is not None:
        if fcf_v > 0 and rev_g_v is not None and rev_g_v > 0:
            items["FCF_개선"] = {"score": 4, "max": 4,
                                "note": f"FCF 양수({fcf_v:,.0f}) + 매출성장({rev_g_v:.2%})"}
        elif fcf_v > 0:
            items["FCF_개선"] = {"score": 2, "max": 4,
                                "note": f"FCF 양수({fcf_v:,.0f})지만 매출 정체"}
        else:
            items["FCF_개선"] = {"score": 0, "max": 4,
                                "note": f"FCF = {fcf_v:,.0f} (음수)"}
            result["notes"].append("FCF 음수")
    else:
        items["FCF_개선"] = {"score": 0, "max": 4, "note": "FCF 데이터 없음"}

    # 4. 성장이 일회성 효과 아님 (3점)
    if rev_g_v is not None and rev_g_v > 0.05 and om_v is not None and om_v > 0:
        items["일회성_성장"] = {"score": 3, "max": 3,
                             "note": "매출·영업이익률 동반 성장 (지속 가능)"}
    elif rev_g_v is not None and rev_g_v > 0:
        items["일회성_성장"] = {"score": 1, "max": 3,
                             "note": "성장 징후 있으나 확인 필요"}
        result["notes"].append("성장 지속성 확인 필요")
    else:
        items["일회성_성장"] = {"score": 0, "max": 3, "note": "데이터 부족 또는 역성장"}

    for k, v in items.items():
        result["score"] += v["score"]
    return result


def _score_E_capital_allocation(ticker, info, fin_records):
    """E. 자본 배분 (10점)"""
    result = {"score": 0, "max": 10, "items": {}, "notes": []}
    items = result["items"]

    # 1. 자사주 매입 합리적 (4점)
    buyback = _safe(info, "buybackShares")
    shares_out = _safe(info, "sharesOutstanding")
    bb_v = _safe_float(buyback)
    so_v = _safe_float(shares_out)
    if bb_v is not None and bb_v > 0:
        items["자사주매입"] = {"score": 4, "max": 4,
                             "note": f"자사주 매입 진행 중 ({bb_v:,.0f})"}
    elif bb_v is not None and bb_v <= 0:
        items["자사주매입"] = {"score": 0, "max": 4, "note": "자사주 매입 없음"}
    elif so_v is not None:
        # If shares outstanding decreasing, treat as buyback
        items["자사주매입"] = {"score": 2, "max": 4,
                             "note": "자사주 매입 데이터 없음 — 중간 점수"}
    else:
        items["자사주매입"] = {"score": 0, "max": 4, "note": "자사주 매입 데이터 없음"}

    # 2. 배당 FCF 범위 내 (3점)
    pr = _safe(info, "payoutRatio")
    pr_v = _safe_float(pr)
    fcf_v2 = _safe_float(_safe(info, "freeCashFlow"))
    div_v = _safe(info, "dividendRate")
    div_v2 = _safe_float(div_v)
    if pr_v is not None:
        if pr_v < 0.6:
            items["배당_FCF"] = {"score": 3, "max": 3,
                               "note": f"배당성향 = {pr_v:.2%} (안정, <60%)"}
        elif pr_v < 1.0:
            items["배당_FCF"] = {"score": 1, "max": 3,
                               "note": f"배당성향 = {pr_v:.2%} (다소 높음)"}
        else:
            items["배당_FCF"] = {"score": 0, "max": 3,
                               "note": f"배당성향 = {pr_v:.2%} (과도, >100%)"}
            result["notes"].append("배당성향 과도")
    elif fcf_v2 is not None and div_v2 is not None and fcf_v2 > 0:
        # rough check
        div_total = div_v2 * so_v if so_v else None
        if div_total and div_total < fcf_v2:
            items["배당_FCF"] = {"score": 3, "max": 3,
                               "note": "배당이 FCF 범위 내 (추정)"}
        else:
            items["배당_FCF"] = {"score": 1, "max": 3, "note": "배당/FCF 확인 불가 — 중간"}
    else:
        items["배당_FCF"] = {"score": 1, "max": 3, "note": "배당 데이터 부족 — 중간 점수"}

    # 3. 인수합병 무리하지 않음 (3점)
    # Approximate via goodwill / total assets ratio
    goodwill = _safe(info, "goodwill")
    tot_assets = _safe(info, "totalAssets")
    gw_v = _safe_float(goodwill)
    ta_v = _safe_float(tot_assets)
    if gw_v is not None and ta_v is not None and ta_v > 0:
        gw_ratio = gw_v / ta_v
        if gw_ratio < 0.2:
            items["M&A_리스크"] = {"score": 3, "max": 3,
                                 "note": f"영업권/자산 = {gw_ratio:.2%} (양호, <20%)"}
        elif gw_ratio < 0.4:
            items["M&A_리스크"] = {"score": 1, "max": 3,
                                 "note": f"영업권/자산 = {gw_ratio:.2%} (주의)"}
            result["notes"].append("영업권 비중 높음")
        else:
            items["M&A_리스크"] = {"score": 0, "max": 3,
                                 "note": f"영업권/자산 = {gw_ratio:.2%} (과도, >40%)"}
            result["notes"].append("M&A 과도 위험")
    else:
        items["M&A_리스크"] = {"score": 2, "max": 3, "note": "데이터 부족 — 중간 점수"}

    for k, v in items.items():
        result["score"] += v["score"]
    return result


# ── grade mapping ────────────────────────────────────────────────────────

def _grade(total_score):
    if total_score >= 85:
        return "S"
    elif total_score >= 75:
        return "A"
    elif total_score >= 60:
        return "B"
    elif total_score >= 45:
        return "C"
    else:
        return "D"


# ── public API ──────────────────────────────────────────────────────────

def score_ticker(ticker_symbol: str) -> dict:
    """
    Evaluate a single ticker's fundamental health score (0–100).

    Args:
        ticker_symbol: Stock ticker symbol (e.g. 'MSFT', 'AAPL')

    Returns:
        dict with keys:
          - ticker: str
          - total_score: int (0–100)
          - grade: str (S/A/B/C/D)
          - sections: dict with per-section breakdown
          - notes: list[str] of warnings/missing data
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
    except Exception as e:
        logger.error(f"Failed to fetch ticker {ticker_symbol}: {e}")
        return {
            "ticker": ticker_symbol,
            "total_score": 0,
            "grade": "D",
            "sections": {},
            "notes": [f"Ticker fetch failed: {e}"],
        }

    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        logger.warning(f"No market data for {ticker_symbol}, trying fallback...")

    if is_etf(info):
        return {
            "ticker": ticker_symbol,
            "total_score": 0,
            "grade": "N/A",
            "sections": {},
            "notes": ["ETF/ETN — 재무 점수 불필요"],
        }

    fin_records = _past_3y_financials(ticker)

    sections = {}
    all_notes = []

    a = _score_A_profitability(ticker, info, fin_records)
    sections["A_수익성"] = a
    all_notes.extend(a["notes"])

    b = _score_B_earnings_quality(ticker, info, fin_records)
    sections["B_이익의질"] = b
    all_notes.extend(b["notes"])

    c = _score_C_financial_stability(ticker, info, fin_records)
    sections["C_재무안정성"] = c
    all_notes.extend(c["notes"])

    d = _score_D_growth_quality(ticker, info, fin_records)
    sections["D_성장의질"] = d
    all_notes.extend(d["notes"])

    e = _score_E_capital_allocation(ticker, info, fin_records)
    sections["E_자본배분"] = e
    all_notes.extend(e["notes"])

    total = sum(sec["score"] for sec in sections.values())

    return {
        "ticker": ticker_symbol,
        "total_score": total,
        "grade": _grade(total),
        "sections": sections,
        "notes": all_notes,
    }


# ── standalone test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    result = score_ticker(sym)
    print(f"\n{'='*60}")
    print(f"  {result['ticker']} — 재무 건강도 점수: {result['total_score']}/100 (등급: {result['grade']})")
    print(f"{'='*60}")
    for sec_name, sec_data in result["sections"].items():
        print(f"\n  [{sec_name}] {sec_data['score']}/{sec_data['max']}점")
        for item_name, item_data in sec_data["items"].items():
            print(f"    {item_name}: {item_data['score']}/{item_data['max']} — {item_data['note']}")
    if result["notes"]:
        print(f"\n  ⚠ 주의 사항:")
        for n in result["notes"]:
            print(f"    - {n}")