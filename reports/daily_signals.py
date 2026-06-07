#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_signals.py — 일일 신호 탐지 모듈
yfinance 데이터를 기반으로 포트폴리오 종목의 일일 변화 및 신호 감지
"""

import yfinance as yf
import numpy as np
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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


def _detect_price_signals(ticker):
    """Check 1d, 5d, 1mo price changes and volume patterns."""
    signals = {"price": {}, "volume": {}}
    try:
        hist = ticker.history(period="1mo")
        if hist is None or hist.empty:
            return signals

        closes = hist["Close"]
        volumes = hist["Volume"]

        if len(closes) < 2:
            return signals

        # Price changes
        current_price = _safe_float(closes.iloc[-1])
        if current_price is None:
            return signals

        prev_close = _safe_float(closes.iloc[-2])
        if prev_close and prev_close > 0:
            d1_change = (current_price - prev_close) / prev_close * 100
            signals["price"]["1d_change_pct"] = round(d1_change, 2)
        else:
            signals["price"]["1d_change_pct"] = 0

        if len(closes) >= 6:
            d5_price = _safe_float(closes.iloc[-6])
            if d5_price and d5_price > 0:
                d5_change = (current_price - d5_price) / d5_price * 100
                signals["price"]["5d_change_pct"] = round(d5_change, 2)
            else:
                signals["price"]["5d_change_pct"] = 0
        else:
            signals["price"]["5d_change_pct"] = 0

        if len(closes) >= 21:
            mo_price = _safe_float(closes.iloc[-21])
            if mo_price and mo_price > 0:
                mo_change = (current_price - mo_price) / mo_price * 100
                signals["price"]["1mo_change_pct"] = round(mo_change, 2)
            else:
                signals["price"]["1mo_change_pct"] = 0
        else:
            signals["price"]["1mo_change_pct"] = 0

        signals["price"]["current_price"] = current_price

        # Volume comparison
        if len(volumes) >= 21:
            today_vol = _safe_float(volumes.iloc[-1])
            avg_vol = _safe_float(volumes.iloc[-21:-1].mean())
            if today_vol and avg_vol and avg_vol > 0:
                vol_ratio = today_vol / avg_vol
                signals["volume"]["today"] = today_vol
                signals["volume"]["avg_20d"] = avg_vol
                signals["volume"]["ratio"] = round(vol_ratio, 2)
                signals["volume"]["spike"] = vol_ratio > 1.5
            else:
                signals["volume"]["ratio"] = 1.0
                signals["volume"]["spike"] = False

    except Exception as e:
        logger.warning(f"Price/volume data error: {e}")

    return signals


def _detect_analyst_signals(ticker, info):
    """Check analyst target vs current price."""
    signals = {"analyst": {}}
    try:
        target = info.get("targetMeanPrice")
        current = info.get("currentPrice") or info.get("regularMarketPrice")
        target_v = _safe_float(target)
        current_v = _safe_float(current)

        if target_v and current_v and current_v > 0:
            upside = (target_v - current_v) / current_v * 100
            signals["analyst"]["target_price"] = target_v
            signals["analyst"]["upside_pct"] = round(upside, 2)
            if upside > 15:
                signals["analyst"]["signal"] = "positive"
            elif upside > 5:
                signals["analyst"]["signal"] = "neutral_positive"
            elif upside > -5:
                signals["analyst"]["signal"] = "neutral"
            else:
                signals["analyst"]["signal"] = "negative"
        else:
            signals["analyst"]["signal"] = "unknown"
    except Exception as e:
        logger.warning(f"Analyst data error: {e}")
        signals["analyst"]["signal"] = "unknown"

    return signals


def _classify_news_item(item):
    """Classify a news item's sentiment based on keywords."""
    title = (item.get("title") or "").lower()
    summary = (item.get("summary") or "").lower()
    text = title + " " + summary

    # Positive keywords
    positive_kw = [
        "beat", "raised", "upgrade", "positive", "growth", "surge", "rally",
        "outperform", "buyback", "dividend", "record", "profit", "partnership",
        "contract", "approval", "launch", "innovation", "expansion",
        "better-than-expected", "guidance up", "synergy", "breakthrough",
        "이익", "성장", "호재", "상승", "긍정", "개선"
    ]
    # Warning keywords
    warning_kw = [
        "downgrade", "miss", "below expectation", "investigation", "lawsuit",
        "regulatory", "sell-off", "decline", "loss", "debt", "warning",
        "restructuring", "layoff", "delay", "probe", "fine", "penalty",
        "subpoena", "volatility", "bearish", "underperform", "reduce",
        "하락", "경고", "위험", "조사", "소송", "악재", "하향"
    ]
    # Critical keywords
    critical_kw = [
        "fraud", "bankruptcy", "delisting", "default", "going concern",
        "accounting error", "restatement", "insolvency", "liquidation",
        "class action", "criminal", "indictment", "cease", "suspend",
        "파산", "회계", "부정", "상장폐지", "디폴트", "파산 신청"
    ]

    pos_score = sum(1 for kw in positive_kw if kw in text)
    warn_score = sum(1 for kw in warning_kw if kw in text)
    crit_score = sum(1 for kw in critical_kw if kw in text)

    if crit_score > 0:
        return "critical"
    elif warn_score > pos_score:
        return "warning"
    elif pos_score > warn_score:
        return "positive"
    else:
        return "neutral"


def detect_signals(ticker_symbol: str) -> dict:
    """
    Detect daily signals for a given ticker.

    Args:
        ticker_symbol: Stock ticker symbol

    Returns:
        dict with keys:
          - ticker: str
          - overall_signal: str (Positive/Neutral/Warning/Critical)
          - price_info: dict with price changes
          - volume_info: dict with volume data
          - analyst_info: dict with analyst target info
          - news_items: list of recent news with sentiment
          - signals_found: list[str] of detected signals
          - warnings: list[str] of warning signals
          - timestamp: str
    """
    result = {
        "ticker": ticker_symbol,
        "overall_signal": "Neutral",
        "price_info": {},
        "volume_info": {},
        "analyst_info": {},
        "news_items": [],
        "signals_found": [],
        "warnings": [],
        "critical": [],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
    except Exception as e:
        logger.error(f"Failed to fetch ticker {ticker_symbol}: {e}")
        result["warnings"].append(f"데이터 조회 실패: {e}")
        result["overall_signal"] = "Warning"
        return result

    # 1. Price & volume signals
    pv_signals = _detect_price_signals(ticker)
    result["price_info"] = pv_signals.get("price", {})
    result["volume_info"] = pv_signals.get("volume", {})

    # Price drop + volume spike → warning
    d1 = pv_signals.get("price", {}).get("1d_change_pct", 0)
    vol_ratio = pv_signals.get("volume", {}).get("ratio", 1.0)
    vol_spike = pv_signals.get("volume", {}).get("spike", False)

    if d1 < -3 and vol_spike:
        result["warnings"].append("급락 + 거래량 급증 (패닉 셀링 의심)")
        result["overall_signal"] = "Warning"
    elif d1 < -5:
        result["warnings"].append(f"일일 {d1}% 하락")
        if result["overall_signal"] != "Critical":
            result["overall_signal"] = "Warning"
    elif d1 > 5:
        result["signals_found"].append(f"일일 {d1}% 상승 (강한 모멘텀)")

    # 5d / 1mo trends
    d5 = pv_signals.get("price", {}).get("5d_change_pct", 0)
    mo = pv_signals.get("price", {}).get("1mo_change_pct", 0)
    if d5 < -8:
        result["warnings"].append(f"5일 {d5}% 하락 (단기 약세)")
    if mo < -15:
        result["warnings"].append(f"1개월 {mo}% 하락 (중기 약세)")
    if mo > 20:
        result["signals_found"].append(f"1개월 {mo}% 상승 (강한 상승 추세)")

    # 2. Analyst signals
    analyst = _detect_analyst_signals(ticker, info)
    result["analyst_info"] = analyst.get("analyst", {})

    a_signal = analyst.get("analyst", {}).get("signal", "unknown")
    upside = analyst.get("analyst", {}).get("upside_pct", 0)
    if a_signal == "positive":
        result["signals_found"].append(f"애널리스트 목표가 상향 여력 {upside}%")
    elif a_signal == "negative":
        result["warnings"].append(f"애널리스트 목표가 하향 (여력 {upside}%)")

    # 3. News scanning
    try:
        news = ticker.news
        if news and len(news) > 0:
            for item in news[:10]:  # Top 10 news
                sentiment = _classify_news_item(item)
                news_entry = {
                    "title": item.get("title", "No title"),
                    "publisher": item.get("publisher", ""),
                    "link": item.get("link", ""),
                    "sentiment": sentiment,
                }
                # Convert timestamp if available
                ts = item.get("providerPublishTime")
                if ts:
                    try:
                        news_entry["time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        news_entry["time"] = str(ts)
                result["news_items"].append(news_entry)

                if sentiment == "critical":
                    result["critical"].append(f"뉴스(심각): {item.get('title', '')}")
                elif sentiment == "warning":
                    result["warnings"].append(f"뉴스(부정): {item.get('title', '')}")
                elif sentiment == "positive":
                    result["signals_found"].append(f"뉴스(긍정): {item.get('title', '')}")
    except Exception as e:
        logger.warning(f"News fetch failed for {ticker_symbol}: {e}")

    # 4. Check info for fundamental signals
    try:
        # Earnings beat / miss signal
        eps_trailing = _safe_float(info.get("trailingEps"))
        eps_forward = _safe_float(info.get("forwardEps"))
        if eps_trailing and eps_forward and eps_trailing > 0:
            eps_growth = (eps_forward - eps_trailing) / eps_trailing * 100
            if eps_growth > 10:
                result["signals_found"].append(f"EPS 성장 전망 {eps_growth:.1f}% (긍정)")
            elif eps_growth < -10:
                result["warnings"].append(f"EPS 감소 전망 {eps_growth:.1f}% (부정)")

        # Dividend signal
        div_rate = _safe_float(info.get("dividendRate"))
        div_yield = _safe_float(info.get("dividendYield"))
        if div_rate and div_yield:
            result["signals_found"].append(f"배당수익률 {div_yield:.2%}")

        # Debt signal
        debt = _safe_float(info.get("totalDebt"))
        equity = _safe_float(info.get("totalStockholderEquity"))
        if debt is not None and equity is not None and equity > 0:
            d_e = debt / equity
            if d_e > 2.0:
                result["warnings"].append(f"부채비율 {d_e:.2f} (과다 부채)")

        # Margin signal
        margins = _safe_float(info.get("operatingMargins"))
        gross_margins = _safe_float(info.get("grossMargins"))
        if margins is not None:
            if margins > 0.2:
                result["signals_found"].append(f"영업이익률 {margins:.1%} (양호)")

        # FCF signal
        fcf = _safe_float(info.get("freeCashFlow"))
        if fcf is not None and fcf < 0:
            result["warnings"].append("잉여현금흐름(FCF) 음수")
    except Exception as e:
        logger.warning(f"Info signal check error: {e}")

    # 5. Determine overall signal
    if len(result["critical"]) > 0:
        result["overall_signal"] = "Critical"
    elif len(result["warnings"]) >= 3:
        result["overall_signal"] = "Warning"
    elif len(result["warnings"]) >= 1 and len(result["signals_found"]) == 0:
        result["overall_signal"] = "Warning"
    elif len(result["signals_found"]) >= 2 and len(result["warnings"]) == 0:
        result["overall_signal"] = "Positive"
    elif len(result["signals_found"]) >= 3:
        result["overall_signal"] = "Positive"
    else:
        # Not enough strong signals either way
        pass  # stays Neutral

    return result


# ── standalone test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    result = detect_signals(sym)
    print(f"\n{'='*60}")
    print(f"  {result['ticker']} — 일일 신호: {result['overall_signal']}")
    print(f"{'='*60}")
    if result["price_info"]:
        p = result["price_info"]
        print(f"\n  가격: ${p.get('current_price', 'N/A'):.2f} | 1일: {p.get('1d_change_pct', 0):+.2f}% | 5일: {p.get('5d_change_pct', 0):+.2f}% | 1개월: {p.get('1mo_change_pct', 0):+.2f}%")
    if result["volume_info"]:
        v = result["volume_info"]
        ratio = v.get("ratio", 1)
        print(f"  거래량: 20일 평균 대비 {ratio:.2f}x{' (급증!)' if v.get('spike') else ''}")
    if result["analyst_info"]:
        a = result["analyst_info"]
        print(f"  애널리스트 목표가: ${a.get('target_price', 'N/A')} (상승여력: {a.get('upside_pct', 0):+.1f}%)")
    if result["signals_found"]:
        print(f"\n  ✅ 긍정 신호:")
        for s in result["signals_found"]:
            print(f"     - {s}")
    if result["warnings"]:
        print(f"\n  ⚠ 경고 신호:")
        for w in result["warnings"]:
            print(f"     - {w}")
    if result["critical"]:
        print(f"\n  🚨 심각 신호:")
        for c in result["critical"]:
            print(f"     - {c}")
    if result["news_items"]:
        print(f"\n  📰 최신 뉴스:")
        for item in result["news_items"][:5]:
            emoji = {"positive": "🟢", "warning": "🟡", "critical": "🔴", "neutral": "⚪"}
            print(f"     {emoji.get(item['sentiment'], '⚪')} [{item['sentiment']}] {item['title'][:80]}")
