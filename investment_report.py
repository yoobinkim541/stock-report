#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
investment_report.py — 일일 투자 자동화 레포트 메인 스크립트
fundamental_score.py + daily_signals.py 를 조합하여 종합 리포트 생성
"""

import os
import json
import re
import sys
import logging
import time
from datetime import datetime, timedelta

import requests
import yfinance as yf
import numpy as np

# Add parent dir to path if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fundamental_score import score_ticker
from daily_signals import detect_signals

# Company name cache
_COMPANY_NAMES = {}
def _company_name(ticker: str) -> str:
    if ticker not in _COMPANY_NAMES:
        try:
            info = yf.Ticker(ticker).info or {}
            name = info.get('shortName') or info.get('longName') or ticker
            _COMPANY_NAMES[ticker] = str(name)
        except Exception:
            _COMPANY_NAMES[ticker] = ticker
    return _COMPANY_NAMES[ticker]

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("investment_report")

# ── 포트폴리오 종목 ─────────────────────────────────────────────────────
PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "NOW", "CRM", "SAP", "UNH",
                     "SGOV", "CPNG", "NVDA", "GOOGL", "SPMO"]

# ── 수동 점수 오버라이드 (yfinance 데이터 불완전한 종목) ─────────────────────────
MANUAL_SCORES = {
    "CPNG": {
        "ticker": "CPNG",
        "total_score": 52,
        "grade": "C",
        "sections": {},
        "notes": [
            "수동 입력 점수 — yfinance 한국 상장사 데이터 불완전",
            "쿠팡: 한국 이커머스 1위, 2023년 흑자전환 달성",
            "매출 성장 지속 중, 영업 레버리지 개선 추세",
        ],
    },
}

# ── NASDAQ 100 종목 (정적 리스트) ───────────────────────────────────────
NASDAQ_100 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA",
    "AVGO", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMGN", "CMCSA",
    "NFLX", "PEP", "COST", "TMUS", "AMD", "INTU", "HON", "AMAT", "BKNG",
    "SBUX", "ADI", "GILD", "ADP", "FISV", "VRTX", "MDLZ", "ISRG", "REGN",
    "CMG", "PANW", "ABNB", "SNPS", "CDNS", "KLAC", "WDAY", "ASML", "MU",
    "LRCX", "MRNA", "AEP", "CPRT", "EA", "EXC", "KDP", "BKR", "XEL",
    "CTAS", "CHTR", "DDOG", "FTNT", "MAR", "MCHP", "PCAR", "PAYX",
    "ROST", "SIRI", "VRSK", "ANSS", "ALGN", "DLTR", "EBAY", "ENPH",
    "FAST", "IDXX", "ILMN", "INCY", "KLIC", "LULU", "MELI", "MOH",
    "MRVL", "MTCH", "NTES", "OKTA", "ORLY", "PTC", "RIVN", "SPLK",
    "SWKS", "TEAM", "TSCO", "TTWO", "WBA", "WBD", "WDC", "ZM", "ZS"
]

# ── KOSPI 시총 상위 20개 (Yahoo Finance .KS 티커) ─────────────────────────
KOSPI_TOP20 = [
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS",
    "068270.KS", "000270.KS", "105560.KS", "055550.KS", "035420.KS",
    "012330.KS", "028260.KS", "006400.KS", "035720.KS", "329180.KS",
    "086790.KS", "032830.KS", "015760.KS", "009540.KS", "034020.KS",
]

_KOSPI_NAMES = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스",
    "005380.KS": "현대차",
    "068270.KS": "셀트리온",
    "000270.KS": "기아",
    "105560.KS": "KB금융",
    "055550.KS": "신한지주",
    "035420.KS": "NAVER",
    "012330.KS": "현대모비스",
    "028260.KS": "삼성물산",
    "006400.KS": "삼성SDI",
    "035720.KS": "카카오",
    "329180.KS": "HD현대중공업",
    "086790.KS": "하나금융지주",
    "032830.KS": "삼성생명",
    "015760.KS": "한국전력",
    "009540.KS": "HD한국조선해양",
    "034020.KS": "두산에너빌리티",
}

REPORTS_DIR = os.path.expanduser("~/reports")
os.makedirs(REPORTS_DIR, exist_ok=True)



# ── Arca Live helpers ────────────────────────────────────────────────────

_ARCA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_ARCA_LABELS = ("🧠분석", "📰뉴스", "ℹ️정보", "실적")


def _compact_text(text, limit=90):
    if not text:
        return ""
    cleaned = " ".join(str(text).replace("\n", " ").split())
    return cleaned[: limit - 1].rstrip() + "…" if len(cleaned) > limit else cleaned


def _fetch_arca_markdown(page=1):
    url = f"https://r.jina.ai/http://arca.live/b/stock?p={page}"
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_ARCA_HEADERS, timeout=8)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_error = e
            logger.warning(f"Arca fetch failed (page={page}, attempt={attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(3)
    if last_error:
        logger.warning(f"Arca fetch exhausted retries (page={page}): {last_error}")
    return None


def _parse_arca_posts(markdown):
    if not markdown:
        return []
    posts = []
    seen_ids = set()
    link_pat = re.compile(r"\[([^\]]+)\]\(https://arca\.live/b/stock/(\d+)\?p=(\d+)\)")
    for match in link_pat.finditer(markdown):
        link_text = " ".join(match.group(1).split()).replace("**", "").strip()
        post_id = match.group(2)
        if post_id in seen_ids:
            continue
        if not any(label in link_text for label in _ARCA_LABELS):
            continue
        header = re.match(
            rf"^(?P<num>\d+)\s*(?P<label>{'|'.join(map(re.escape, _ARCA_LABELS))})\s+(?P<rest>.+)$",
            link_text,
        )
        if not header:
            continue
        body = header.group("rest").strip()
        meta = re.match(
            r"^(?P<title>.*?)(?:\s+\[\d+\])?\s+(?P<author>\S+)\s+"
            r"(?P<when>(?:\d{2}:\d{2}|\d{4}\.\d{2}\.\d{2}))\s+(?P<views>\d+)\s+(?P<likes>\d+)$",
            body,
        )
        if not meta:
            continue
        seen_ids.add(post_id)
        posts.append({
            "id": post_id,
            "url": f"https://arca.live/b/stock/{post_id}",
            "category": header.group("label"),
            "title": _compact_text(meta.group("title").strip(), 90),
            "author": meta.group("author").strip(),
            "when": meta.group("when").strip(),
            "views": meta.group("views"),
            "likes": meta.group("likes"),
        })
    return posts


def _fetch_arca_posts(max_pages=3, limit=6):
    posts = []
    seen = set()
    for page in range(1, max_pages + 1):
        for post in _parse_arca_posts(_fetch_arca_markdown(page) or ""):
            if post["id"] in seen:
                continue
            seen.add(post["id"])
            posts.append(post)
            if len(posts) >= limit:
                return posts
    return posts


def _format_arca_post(post):
    return (
        f"- [{post['title']}]({post['url']})"
        f" ({post['category']} · {post['when']} · 조회 {post['views']} · 추천 {post['likes']})"
    )


# ── helpers ─────────────────────────────────────────────────────────────

def _fmt_pct(val, force_sign=False):
    """Format a percentage value."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if force_sign:
            return f"{v:+.2f}%"
        return f"{v:.2f}%"
    except (ValueError, TypeError):
        return str(val)


def _fmt_price(val):
    if val is None:
        return "N/A"
    try:
        return f"${float(val):.2f}"
    except (ValueError, TypeError):
        return str(val)


def _judgment(fund_score, signal, grade):
    """
    Determine final judgment based on fundamental score + daily signal.
    
    Returns tuple: (judgment_text, reasons_list, risk_list)
    """
    score = fund_score.get("total_score", 0)
    signal_type = signal.get("overall_signal", "Neutral")
    signals_found = signal.get("signals_found", [])
    warnings = signal.get("warnings", [])
    critical = signal.get("critical", [])

    if grade == 'N/A':
        notes = fund_score.get("notes", ["ETF/ETN — 재무 점수 불필요"])
        reasons = [notes[0] if notes else "ETF/ETN — 재무 점수 불필요"]
        risks = ["ETF/ETN — 재무 분석 해당 없음"]
        if critical:
            return ("제외 검토", reasons, risks)
        if signal_type in ("Warning", "Critical"):
            return ("관망", reasons, risks)
        return ("관심 유지", reasons, risks)

    reasons = []
    risks = []

    # Build reasons from score
    if score >= 75:
        reasons.append(f"재무 건강도 {score}점({grade}) — 우수한 펀더멘털")
    elif score >= 60:
        reasons.append(f"재무 건강도 {score}점({grade}) — 양호")
    elif score >= 45:
        reasons.append(f"재무 건강도 {score}점({grade}) — 보통")
    else:
        risks.append(f"재무 건강도 {score}점({grade}) — 취약")

    # Build reasons from signal
    if signal_type == "Positive":
        reasons.append("오늘의 신호: 긍정적")
        if signals_found:
            reasons.append(signals_found[0][:60])
    elif signal_type == "Warning":
        risks.append("오늘의 신호: 경고 발생")
        if warnings:
            risks.append(warnings[0][:60])
    elif signal_type == "Critical":
        risks.append("심각 신호 발생 — 즉시 점검 필요")
        if critical:
            risks.append(critical[0][:60])

    # Price info
    price_info = signal.get("price_info", {})
    d1 = price_info.get("1d_change_pct")
    if d1 is not None:
        if d1 > 3:
            reasons.append(f"일일 {d1:+.2f}% 상승")
        elif d1 < -3:
            risks.append(f"일일 {d1:.2f}% 하락")

    # Additional score-based reasons
    sections = fund_score.get("sections", {})
    for sec_name, sec_data in sections.items():
        items = sec_data.get("items", {})
        for item_name, item_data in items.items():
            if item_data.get("score", 0) == item_data.get("max", 1) and item_data.get("max", 1) > 3:
                note = item_data.get("note", "")
                if len(note) > 5:
                    reasons.append(note[:50])
                    break  # one per section

    # Additional score-based risks
    for sec_name, sec_data in sections.items():
        items = sec_data.get("items", {})
        for item_name, item_data in items.items():
            if item_data.get("score", 0) == 0 and item_data.get("max", 1) > 4:
                note = item_data.get("note", "")
                if note and len(risks) < 2:
                    risks.append(note[:50])
                    break

    # Trim to 3 reasons, 2 risks
    reasons = reasons[:3]
    risks = risks[:2]

    # Ensure minimum content
    if not reasons:
        reasons.append("데이터 수집 완료")
    if not risks:
        risks.append("특이 위험 요소 없음")

    # Determine judgment
    if critical:
        return ("제외 검토", reasons, risks)
    if signal_type == "Warning" and score < 60:
        return ("위험 증가", reasons, risks)
    if signal_type == "Warning":
        return ("관망", reasons, risks)
    if score >= 75 and signal_type == "Positive":
        return ("분할매수 후보", reasons, risks)
    if score >= 60:
        return ("관심 유지", reasons, risks)
    if score >= 45:
        return ("가격 조정 대기", reasons, risks)
    if signal_type == "Positive" and score >= 45:
        return ("관망", reasons, risks)
    return ("제외 검토", reasons, risks)


def _market_summary():
    """Get a quick market snapshot using SPY and QQQ as proxies."""
    summary = {
        "spy_price": "N/A",
        "spy_change": 0,
        "spy_name": "SPY",
        "qqq_price": "N/A",
        "qqq_change": 0,
        "qqq_name": "QQQ",
    }
    for ticker, prefix in (("SPY", "spy"), ("QQQ", "qqq")):
        try:
            etf = yf.Ticker(ticker)
            info = etf.info
            hist = etf.history(period="5d")
            if hist is not None and not hist.empty:
                closes = hist["Close"]
                change = (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100 if len(closes) >= 2 else 0
                price = closes.iloc[-1]
                summary[f"{prefix}_price"] = round(price, 2)
                summary[f"{prefix}_change"] = round(change, 2)
                summary[f"{prefix}_name"] = info.get("shortName", ticker)
        except Exception as e:
            logger.warning(f"Market summary error ({ticker}): {e}")
    return summary


def _calc_portfolio_pnl(portfolio_results):
    """Calculate equal-weighted portfolio P&L from individual stock results."""
    d1_vals = []
    mo_vals = []
    for r in portfolio_results:
        pi = r.get("signal", {}).get("price_info", {})
        d1 = pi.get("1d_change_pct")
        mo = pi.get("1mo_change_pct")
        if d1 is not None:
            d1_vals.append(d1)
        if mo is not None:
            mo_vals.append(mo)
    avg_1d = np.mean(d1_vals) if d1_vals else None
    avg_1mo = np.mean(mo_vals) if mo_vals else None
    return avg_1d, avg_1mo


def _fetch_korea_indices():
    """Fetch KOSPI, KOSDAQ, USD/KRW exchange rate."""
    try:
        data = yf.download(
            tickers="^KS11 ^KQ11 KRW=X",
            period="2d",
            progress=False,
        )
        close = data["Close"].iloc[-1] if not data.empty else None
        if close is not None:
            kospi = f"{close.get('^KS11', 'N/A'):,.2f}" if close.get('^KS11') is not None else "N/A"
            kosdaq = f"{close.get('^KQ11', 'N/A'):,.2f}" if close.get('^KQ11') is not None else "N/A"
            fx = f"{close.get('KRW=X', 'N/A'):,.2f}" if close.get('KRW=X') is not None else "N/A"
            return kospi, kosdaq, fx
    except Exception:
        logger.warning("Korea indices fetch failed")
    return "N/A", "N/A", "N/A"


# ── main report generator ───────────────────────────────────────────────

def generate_report():
    """Generate the full investment report."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_time = time.time()

    print(f"📊 일일 투자 리포트 생성 중... ({today_str})")
    print(f"포트폴리오 종목: {', '.join(PORTFOLIO_TICKERS)}")

    # ── Market summary ──
    print(f"\n📈 시장 데이터 수집 중...")
    market = _market_summary()

    # ── Portfolio analysis ──
    print("🔍 포트폴리오 종목 분석 중...")
    portfolio_results = []
    for i, ticker in enumerate(PORTFOLIO_TICKERS):
        print(f"   [{i+1}/{len(PORTFOLIO_TICKERS)}] {ticker}...", end=" ", flush=True)
        try:
            fund = MANUAL_SCORES.get(ticker) or score_ticker(ticker)
            sig = detect_signals(ticker)
            judgment, reasons, risks = _judgment(fund, sig, fund.get("grade", "N/A"))
            portfolio_results.append({
                "ticker": ticker,
                "fundamental": fund,
                "signal": sig,
                "judgment": judgment,
                "reasons": reasons,
                "risks": risks,
            })
            print(f"✅ 점수:{fund['total_score']} 등급:{fund['grade']} 신호:{sig['overall_signal']}")
        except Exception as e:
            print(f"❌ 오류: {e}")
            portfolio_results.append({
                "ticker": ticker,
                "fundamental": {"total_score": 0, "grade": "D", "notes": [str(e)]},
                "signal": {"overall_signal": "Warning", "warnings": [str(e)]},
                "judgment": "제외 검토",
                "reasons": ["데이터 오류"],
                "risks": [str(e)],
            })

    # ── NASDAQ 100 scan ──
    print(f"\n📋 NASDAQ 100 스캔 중...")
    ndx_results = []
    scan_count = 0
    max_scan = len(NASDAQ_100)  # Scan full NASDAQ 100 list
    for ticker in NASDAQ_100:
        if scan_count >= max_scan:
            break
        scan_count += 1
        print(f"   [{scan_count}/{min(max_scan, len(NASDAQ_100))}] {ticker}...", end=" ", flush=True)
        try:
            fund = MANUAL_SCORES.get(ticker) or score_ticker(ticker)
            sig = detect_signals(ticker)
            ndx_results.append({
                "ticker": ticker,
                "total_score": fund["total_score"],
                "grade": fund["grade"],
                "company_name": _company_name(ticker),
                "signal": sig["overall_signal"],
            })
            print(f"점수:{fund['total_score']} 등급:{fund['grade']} 신호:{sig['overall_signal']}")
        except Exception as e:
            print(f"스킵 ({e})")
            continue

    # Sort for top picks and warnings
    scored_results = [r for r in ndx_results if r["total_score"] > 0]
    scored_results.sort(key=lambda x: x["total_score"], reverse=True)

    top_buy_candidates = []
    for r in scored_results:
        if r["signal"] == "Positive" and r["total_score"] >= 60 and len(top_buy_candidates) < 5:
            top_buy_candidates.append(r)
    # Fill remaining with high score
    if len(top_buy_candidates) < 5:
        for r in scored_results:
            if r not in top_buy_candidates and len(top_buy_candidates) < 5:
                top_buy_candidates.append(r)

    top_watch = []
    for r in reversed(scored_results):
        if len(top_watch) < 5:
            if r["signal"] in ("Warning", "Critical") or r["total_score"] < 45:
                top_watch.append(r)
    if len(top_watch) < 5:
        for r in reversed(scored_results):
            if r not in top_watch and len(top_watch) < 5:
                top_watch.append(r)

    # ── KOSPI top 20 scan ──
    print(f"\n🇰🇷 KOSPI 상위 20개 스캔 중...")
    kospi_results = []
    for i, ticker in enumerate(KOSPI_TOP20):
        print(f"   [{i+1}/{len(KOSPI_TOP20)}] {ticker}...", end=" ", flush=True)
        try:
            fund = MANUAL_SCORES.get(ticker) or score_ticker(ticker)
            sig = detect_signals(ticker)
            kospi_results.append({
                "ticker": ticker,
                "total_score": fund["total_score"],
                "grade": fund["grade"],
                "company_name": _company_name(ticker),
                "signal": sig["overall_signal"],
            })
            print(f"점수:{fund['total_score']} 등급:{fund['grade']} 신호:{sig['overall_signal']}")
        except Exception as e:
            print(f"오류 ({e})")
            kospi_results.append({
                "ticker": ticker,
                "total_score": 0,
                "grade": "N/A",
                "company_name": _company_name(ticker),
                "signal": "Warning",
            })

    kospi_scored = [r for r in kospi_results if r["total_score"] > 0]
    kospi_scored.sort(key=lambda x: x["total_score"], reverse=True)
    kospi_top = kospi_scored[:5]
    kospi_watch = sorted(kospi_results, key=lambda x: x["total_score"])[:5]

    # ── Generate report text (Korean) ──
    lines = []
    lines.append(f"# 일일 투자 자동화 레포트")
    lines.append(f"날짜: {today_str}")
    lines.append(f"생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # Portfolio P&L
    pnl_1d, pnl_1mo = _calc_portfolio_pnl(portfolio_results)
    pnl_1d_str = _fmt_pct(pnl_1d, force_sign=True) if pnl_1d is not None else "N/A"
    pnl_1mo_str = _fmt_pct(pnl_1mo, force_sign=True) if pnl_1mo is not None else "N/A"
    lines.append(f"**포트폴리오 등락:** 오늘 {pnl_1d_str} | 1개월 {pnl_1mo_str}")

    # Korea indices
    kospi_str, kosdaq_str, fx_str = _fetch_korea_indices()
    lines.append(f"- **KOSPI:** {kospi_str} | **KOSDAQ:** {kosdaq_str} | **USD/KRW:** {fx_str}")
    lines.append(f"")

    # Section 1: Summary
    lines.append(f"## 1. 전체 요약")
    lines.append(f"")
    spy_change = market.get("spy_change", 0)
    if spy_change != 0:
        spy_emoji = "📈" if spy_change > 0 else "📉"
        lines.append(f"**오늘의 시장 분위기:** {spy_emoji} SPY ${market.get('spy_price', 'N/A')} ({spy_change:+.2f}%)")
    else:
        lines.append(f"**오늘의 시장 분위기:** 데이터 수집 중...")
    lines.append(f"")

    # Count signals
    pos_count = sum(1 for r in portfolio_results if r["signal"]["overall_signal"] == "Positive")
    warn_count = sum(1 for r in portfolio_results if r["signal"]["overall_signal"] == "Warning")
    crit_count = sum(1 for r in portfolio_results if r["signal"]["overall_signal"] == "Critical")
    neu_count = sum(1 for r in portfolio_results if r["signal"]["overall_signal"] == "Neutral")

    lines.append(f"**포트폴리오 신호 분포:** 긍정 {pos_count}개 / 중립 {neu_count}개 / 경고 {warn_count}개 / 심각 {crit_count}개")
    lines.append(f"")

    # Major risks
    all_warnings = []
    for r in portfolio_results:
        for w in r["signal"].get("warnings", []):
            all_warnings.append(f"{r['ticker']}: {w}")
        for c in r["signal"].get("critical", []):
            all_warnings.append(f"{r['ticker']}: 🚨 {c}")
    if all_warnings:
        lines.append(f"**주요 위험 신호:**")
        for w in all_warnings[:5]:
            lines.append(f"- {w}")
        lines.append(f"")
    else:
        lines.append(f"**주요 위험 신호:** 특이사항 없음")
        lines.append(f"")

    # Watchlist
    watch_tickers = [r["ticker"] for r in portfolio_results
                     if r["judgment"] in ("분할매수 후보", "위험 증가", "제외 검토")]
    if watch_tickers:
        lines.append(f"**오늘 주목할 종목:** {', '.join(watch_tickers)}")
    else:
        lines.append(f"**오늘 주목할 종목:** 특이사항 없음")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # Section 2: Portfolio detail
    lines.append(f"## 2. 종목별 상세 분석")
    lines.append(f"보유종목: {', '.join(PORTFOLIO_TICKERS)}")
    lines.append(f"")

    for r in portfolio_results:
        t = r["ticker"]
        cname = _company_name(t)
        fund = r["fundamental"]
        sig = r["signal"]
        judgment = r["judgment"]
        reasons = r["reasons"]
        risks = r["risks"]

        price_info = sig.get("price_info", {})
        vol_info = sig.get("volume_info", {})

        price_str = _fmt_price(price_info.get("current_price"))
        d1_str = _fmt_pct(price_info.get("1d_change_pct"), force_sign=True)
        d5_str = _fmt_pct(price_info.get("5d_change_pct"), force_sign=True)
        mo_str = _fmt_pct(price_info.get("1mo_change_pct"), force_sign=True)
        vol_ratio = vol_info.get("ratio")
        vol_str = _fmt_pct((vol_ratio - 1) * 100, force_sign=True) if vol_ratio else "N/A"

        score = fund.get("total_score", 0)
        grade = fund.get("grade", "N/A")

        signal_map = {"Positive": "🟢 긍정", "Neutral": "⚪ 중립", "Warning": "🟡 경고", "Critical": "🔴 심각"}
        signal_display = signal_map.get(sig.get("overall_signal", "Neutral"), "중립")

        lines.append(f"### {t} — {cname}")
        lines.append(f"- **현재가:** {price_str} | **1일:** {d1_str} | **5일:** {d5_str} | **1개월:** {mo_str}")
        # Technical indicators
        try:
            tech_hist = yf.Ticker(t).history(period="2mo", interval="1d")
            if tech_hist is not None and len(tech_hist) > 30:
                closes = tech_hist["Close"].values
                # SMA20
                sma20 = closes[-20:].mean()

                def _ema(values, period):
                    result = np.zeros_like(values)
                    alpha = 2 / (period + 1)
                    result[0] = values[0]
                    for i in range(1, len(values)):
                        result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
                    return result

                # MACD
                ema12 = _ema(closes, 12)
                ema26 = _ema(closes, 26)
                macd = ema12[-1] - ema26[-1]
                # RSI (14, Wilder smoothing)
                deltas = np.diff(closes)
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                avg_gain = gains[:14].mean()
                avg_loss = losses[:14].mean()
                for i in range(14, len(deltas)):
                    avg_gain = (avg_gain * 13 + gains[i]) / 14
                    avg_loss = (avg_loss * 13 + losses[i]) / 14
                rs = avg_gain / max(avg_loss, 0.001)
                rsi = 100 - (100 / (1 + rs))
                lines.append(f"- **기술적 지표:** RSI {rsi:.1f} | MACD {macd:.3f} | 20일 MA ${sma20:.2f}")
        except Exception:
            lines.append(f"- **기술적 지표:** 데이터 없음")
        lines.append(f"- **거래량 변화:** 20일 평균 대비 {vol_str}")
        lines.append(f"- **재무 건강도:** {score}/100점, 등급 **{grade}**")
        lines.append(f"- **오늘의 신호:** {signal_display}")
        lines.append(f"- **최종 판단:** {judgment}")
        lines.append(f"- **핵심 이유 3개:**")
        for i, reason in enumerate(reasons, 1):
            lines.append(f"  {i}. {reason}")
        lines.append(f"- **위험 요인 2개:**")
        for i, risk in enumerate(risks, 1):
            lines.append(f"  {i}. {risk}")
        lines.append(f"- **확인할 것:**")
        # Build specific findings from available data
        findings = []
        
        # 1. News headlines from yfinance
        news_items = sig.get("news_items", [])
        if news_items:
            for news in news_items[:3]:
                title = news.get("title", "").strip()
                if not title or title == "No title":
                    continue
                senti = news.get("sentiment", "")
                senti_emoji = {"positive": "🟢", "warning": "🟡", "critical": "🔴", "neutral": "⚪"}
                se = senti_emoji.get(senti, "⚪")
                findings.append(f"📰 {se} {title}")
        
        # 2. Price/volume events
        d1_change_val = price_info.get("1d_change_pct")
        if vol_info.get("spike"):
            findings.append(f"📊 거래량 급증 (20일 평균 대비 {vol_str}, 원인 확인 필요)")
        if d1_change_val is not None and abs(d1_change_val) > 3:
            findings.append(f"💹 주가 {d1_change_val:+.2f}% 변동 — 관련 뉴스/공시 확인")
        
        # 3. Analyst info
        analyst_info = sig.get("analyst_info", {})
        target_mean = analyst_info.get("target_mean")
        if target_mean:
            upside = analyst_info.get("upside_pct", 0)
            findings.append(f"🎯 애널리스트 평균 목표가 ${target_mean:.1f} (상승여력 {upside:+.1f}%)")
        
        # 4. Fundamental concerns
        breakdown = fund.get("score_breakdown", {})
        for cat, data_cat in breakdown.items():
            if isinstance(data_cat, dict) and data_cat.get("score", 0) < data_cat.get("max", 100) * 0.3:
                cat_name = {"profitability": "수익성", "earnings_quality": "이익의 질", "financial_stability": "재무 안정성", "growth_quality": "성장의 질", "capital_allocation": "자본 배분"}
                cn = cat_name.get(cat, cat)
                score_val = data_cat.get("score", 0)
                max_val = data_cat.get("max", 10)
                findings.append(f"⚠️ {cn} 점수 낮음 ({score_val}/{max_val}) — 재무제표 확인 필요")
        
        # 5. SaveTicker news check
        ticker = r["ticker"]
        try:
            import requests
            st_url = f"https://saveticker.com/api/news/list?tickers={ticker}&page=1&page_size=2&sort=created_at_desc"
            st_resp = requests.get(st_url, timeout=5)
            if st_resp.status_code == 200:
                st_data = st_resp.json()
                st_news = st_data.get("news_list", [])
                if st_news:
                    for item in st_news[:2]:
                        st_title = item.get("title", "")
                        if st_title and not any(st_title in f for f in findings):
                            findings.append(f"📰 SaveTicker: {st_title}")
        except Exception:
            pass
        
        if findings:
            for idx, finding in enumerate(findings[:5], 1):
                lines.append(f"  {idx}. {finding}")
        else:
            lines.append(f"  - 특이한 뉴스나 이벤트 없음")
        lines.append(f"")

    # Section 3: NASDAQ 100 scan
    lines.append(f"## 3. NASDAQ 100 종목 스캔")
    lines.append(f"")
    lines.append(f"### Top 5 매수 후보 (고점수 + 긍정 신호)")
    lines.append(f"")
    lines.append(f"| 순위 | 종목 | 점수 | 등급 | 신호 |")
    lines.append(f"|------|------|------|------|------|")
    for i, r in enumerate(top_buy_candidates[:5], 1):
        sig_emoji = {"Positive": "🟢", "Neutral": "⚪", "Warning": "🟡", "Critical": "🔴"}
        lines.append(f"| {i} | {r['ticker']} — {_company_name(r['ticker'])} | {r['total_score']} | {r['grade']} | {sig_emoji.get(r['signal'], '⚔️')} {r['signal']} |")
    lines.append(f"")

    lines.append(f"### Top 5 주의 종목 (저점수 또는 경고 신호)")
    lines.append(f"")
    lines.append(f"| 순위 | 종목 | 점수 | 등급 | 신호 |")
    lines.append(f"|------|------|------|------|------|")
    for i, r in enumerate(top_watch[:5], 1):
        sig_emoji = {"Positive": "🟢", "Neutral": "⚪", "Warning": "🟡", "Critical": "🔴"}
        lines.append(f"| {i} | {r['ticker']} — {_company_name(r['ticker'])} | {r['total_score']} | {r['grade']} | {sig_emoji.get(r['signal'], '⚔️')} {r['signal']} |")
    lines.append(f"")

    # Section 4: KOSPI top 20 scan
    lines.append(f"## 4. KOSPI 상위 20개 종목 스캔")
    lines.append(f"")
    lines.append(f"### Top 5 매수 후보")
    lines.append(f"")
    lines.append(f"| 순위 | 종목 | 점수 | 등급 | 신호 |")
    lines.append(f"|------|------|------|------|------|")
    for i, r in enumerate(kospi_top[:5], 1):
        sig_emoji = {"Positive": "🟢", "Neutral": "⚪", "Warning": "🟡", "Critical": "🔴"}
        lines.append(f"| {i} | {r['ticker']} — {_company_name(r['ticker'])} | {r['total_score']} | {r['grade']} | {sig_emoji.get(r['signal'], '⚔️')} {r['signal']} |")
    lines.append(f"")
    lines.append(f"### Top 5 주의 종목")
    lines.append(f"")
    lines.append(f"| 순위 | 종목 | 점수 | 등급 | 신호 |")
    lines.append(f"|------|------|------|------|------|")
    for i, r in enumerate(kospi_watch[:5], 1):
        sig_emoji = {"Positive": "🟢", "Neutral": "⚪", "Warning": "🟡", "Critical": "🔴"}
        lines.append(f"| {i} | {r['ticker']} — {_company_name(r['ticker'])} | {r['total_score']} | {r['grade']} | {sig_emoji.get(r['signal'], '⚔️')} {r['signal']} |")
    lines.append(f"")

    # Section 5: Arca community
    print(f"\n🗨 아카라이브 주식 채널 수집 중...")
    arca_posts = _fetch_arca_posts()
    lines.append(f"## 5. 아카라이브 커뮤니티 동향")
    lines.append(f"")
    if arca_posts:
        lines.append(f"{len(arca_posts)}건의 분석/뉴스/정보/실적 게시글")
        lines.append(f"")
        for post in arca_posts:
            lines.append(_format_arca_post(post))
    else:
        lines.append(f"아카라이브 데이터를 불러올 수 없습니다.")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # Section 6: Conclusion
    lines.append(f"## 6. 오늘의 결론")
    lines.append(f"")

    # Generate conclusion
    avg_score = sum(r["fundamental"]["total_score"] for r in portfolio_results) / len(portfolio_results) if portfolio_results else 0
    buy_candidates = [f"{r['ticker']} — {_company_name(r['ticker'])}" for r in portfolio_results if r["judgment"] == "분할매수 후보"]
    watch_risks = [f"{r['ticker']} — {_company_name(r['ticker'])}" for r in portfolio_results if r["judgment"] in ("위험 증가", "제외 검토")]
    hold = [f"{r['ticker']} — {_company_name(r['ticker'])}" for r in portfolio_results if r["judgment"] in ("관심 유지", "관망", "가격 조정 대기")]

    lines.append(f"**포트폴리오 평균 점수:** {avg_score:.1f}/100점")
    lines.append(f"")

    if buy_candidates:
        lines.append(f"**분할매수 검토:** {', '.join(buy_candidates)}")
    if watch_risks:
        lines.append(f"**위험 관리 필요:** {', '.join(watch_risks)}")
    if hold:
        lines.append(f"**관망/유지:** {', '.join(hold)}")

    lines.append(f"")
    if spy_change != 0:
        if spy_change > 0:
            lines.append(f"오늘 시장은 상승 분위기입니다. 포트폴리오의 강한 종목을 중심으로")
            lines.append(f"분할매수 기회를 평가해보세요.")
        else:
            lines.append(f"오늘 시장은 하락 분위기입니다. 리스크 관리에 집중하고,")
            lines.append(f"급락 종목의 원인을 확인한 후 대응하세요.")
    else:
        lines.append(f"시장 데이터를 확인하여 오늘의 전략을 수립하세요.")
    lines.append(f"")

    lines.append(f"---")
    lines.append(f"*본 리포트는 자동 생성된 참고 자료입니다. 투자 결정은 본인의 판단에 따라 신중히 내리세요.*")
    lines.append(f"*생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append(f"*소요 시간: {time.time() - start_time:.1f}초*")
    lines.append(f"")

    # Friday weekly recap
    if datetime.now().weekday() == 4:  # Friday
        lines.append(f"## 주간 리캡 (5일)")
        lines.append(f"")
        lines.append(f"| 종목 | 등급 | 주간 변동 | 판단 |")
        lines.append(f"|---|---|---|---|")
        for r in portfolio_results:
            d5 = r["signal"]["price_info"].get("5d_change_pct")
            d5s = f"{d5:+.2f}%" if d5 is not None else "N/A"
            lines.append(f"| {r['ticker']} — {_company_name(r['ticker'])} | {r['fundamental']['grade']} | {d5s} | {r['judgment']} |")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    lines.append(f"## 7. 📊 실행 통계")
    lines.append(f"")
    lines.append(f"| 항목 | 값 |")
    lines.append(f"|---|------|")
    elapsed = time.time() - start_time
    lines.append(f"| 실행 시간 | {elapsed:.1f}초 |")
    lines.append(f"| 포트폴리오 종목 | {len(PORTFOLIO_TICKERS)}개 |")
    lines.append(f"| NASDAQ 100 스캔 | {len(ndx_results)}개 종목 |")
    lines.append(f"| KOSPI 상위 20 스캔 | {len(kospi_results)}개 종목 |")
    lines.append(f"| 데이터 소스 | yfinance, SaveTicker API |")
    lines.append(f"| LLM API 토큰 소비 | **0 토큰** (Python 스크립트 내부 LLM 호출 없음) |")
    lines.append(f"| 외부 API 비용 | yfinance 무료 + SaveTicker 무료 |")
    lines.append(f"| Telegram 전송 | @Stock_botbot (파일 2개 + 헤더) |")
    lines.append(f"")
    lines.append(f"*이 스크립트는 순수 Python + 공개 API로만 동작하며, LLM 토큰을 소비하지 않습니다.*")
    lines.append(f"*토큰 비용은 Hermes cron 작업의 시스템 프롬프트에서만 발생합니다.*")
    report_text = "\n".join(lines)

    # ── Save report ──
    report_path = os.path.join(REPORTS_DIR, f"investment-report-{today_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\n📄 리포트 저장 완료: {report_path}")

    # ── Save JSON data ──
    json_data = {
        "date": today_str,
        "generated_at": datetime.now().isoformat(),
        "market": market,
        "portfolio": [],
        "nasdaq_100_scan": {
            "all": ndx_results,
            "top_buy": top_buy_candidates[:5],
            "top_warning": top_watch[:5],
        },
        "kospi_top20_scan": {
            "all": kospi_results,
            "top_buy": kospi_top[:5],
            "top_warning": kospi_watch[:5],
        },
    }
    for r in portfolio_results:
        entry = {
            "ticker": r["ticker"],
            "company_name": _company_name(r["ticker"]),
            "judgment": r["judgment"],
            "fundamental_score": r["fundamental"]["total_score"],
            "fundamental_grade": r["fundamental"]["grade"],
            "overall_signal": r["signal"]["overall_signal"],
            "fundamental_notes": r["fundamental"].get("notes", []),
            "signal_warnings": r["signal"].get("warnings", []),
            "signal_critical": r["signal"].get("critical", []),
            "price_info": r["signal"].get("price_info", {}),
            "volume_info": r["signal"].get("volume_info", {}),
            "reasons": r["reasons"],
            "risks": r["risks"],
        }
        json_data["portfolio"].append(entry)

    json_path = os.path.join(REPORTS_DIR, f"investment-data-{today_str}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)

    # ── Save clean summary ──
    clean_data = {
        "date": today_str,
        "market_summary": {
            "spy_change_pct": spy_change,
            "spy_price": market.get("spy_price"),
            "nasdaq_change_pct": market.get("qqq_change"),
            "nasdaq_price": market.get("qqq_price"),
            "kospi": kospi_str,
        },
        "portfolio_summary": [],
    }
    for r in portfolio_results:
        t = r["ticker"]
        sig = r["signal"]
        price_info = sig.get("price_info", {})
        clean_data["portfolio_summary"].append({
            "ticker": t,
            "company": _company_name(t),
            "score": r["fundamental"]["total_score"],
            "grade": r["fundamental"]["grade"],
            "signal": r["signal"]["overall_signal"],
            "judgment": r["judgment"],
            "price": price_info.get("current_price"),
            "change_1d_pct": price_info.get("1d_change_pct"),
            "change_1mo_pct": price_info.get("1mo_change_pct"),
            "volume_vs_20d_avg_pct": round((sig.get("volume_info", {}).get("ratio", 1) - 1) * 100, 1) if sig.get("volume_info", {}).get("ratio") else None,
            "top_reasons": r["reasons"][:2],
            "top_risks": r["risks"][:2],
        })
    clean_data["nasdaq_top_buy"] = []
    for r in top_buy_candidates[:5]:
        clean_data["nasdaq_top_buy"].append({
            "ticker": r["ticker"],
            "company": _company_name(r["ticker"]),
            "score": r["total_score"],
            "grade": r["grade"],
            "signal": r["signal"],
        })
    clean_data["nasdaq_warnings"] = []
    for r in top_watch[:5]:
        clean_data["nasdaq_warnings"].append({
            "ticker": r["ticker"],
            "company": _company_name(r["ticker"]),
            "score": r["total_score"],
            "grade": r["grade"],
            "signal": r["signal"],
        })
    clean_data["kospi_top_buy"] = []
    for r in kospi_top[:5]:
        clean_data["kospi_top_buy"].append({
            "ticker": r["ticker"],
            "company": _company_name(r["ticker"]),
            "score": r["total_score"],
            "grade": r["grade"],
            "signal": r["signal"],
        })
    clean_data["kospi_warnings"] = []
    for r in kospi_watch[:5]:
        clean_data["kospi_warnings"].append({
            "ticker": r["ticker"],
            "company": _company_name(r["ticker"]),
            "score": r["total_score"],
            "grade": r["grade"],
            "signal": r["signal"],
        })
    clean_path = os.path.join(REPORTS_DIR, f"investment-summary-{today_str}.json")
    with open(clean_path, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"ℹ 분석 요약 저장 완료: {clean_path}")

    # ── Judgment change detection ──
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_path = os.path.join(REPORTS_DIR, f"investment-summary-{yesterday_str}.json")
    if os.path.exists(yesterday_path):
        try:
            with open(yesterday_path, encoding="utf-8") as f:
                yesterday_data = json.load(f)
            prev_judgments = {e["ticker"]: e["judgment"] for e in yesterday_data.get("portfolio_summary", [])}
            changes_found = []
            for entry in clean_data["portfolio_summary"]:
                t = entry["ticker"]
                prev_j = prev_judgments.get(t)
                if prev_j and prev_j != entry["judgment"]:
                    changes_found.append(f"{t}: {prev_j} → {entry['judgment']}")
            if changes_found:
                print("\n⚡ 판단 변경 감지:")
                for c in changes_found:
                    print(f"   {c}")
            else:
                print("\n📊 전일 대비 판단 변경 없음")
        except Exception as e:
            logger.warning(f"전일 요약 비교 실패: {e}")
    else:
        print(f"\n(전일 요약 없음 — 변경 감지 건너뜀)")

    # ── Mobile summary (Telegram-friendly, max 12 lines) ──
    summary_lines = []
    summary_lines.append(f"📊 {today_str} 투자 리포트 요약")
    summary_lines.append(f"")
    summary_lines.append(f"SPY ${market.get('spy_price', 'N/A')} ({spy_change:+.2f}%)")
    summary_lines.append(f"NASDAQ ${market.get('qqq_price', 'N/A')} ({market.get('qqq_change', 0):+.2f}%)")
    summary_lines.append(f"KOSPI {kospi_str}")
    summary_lines.append(f"포트폴리오 평균 점수: {avg_score:.1f}/100")
    sig_counts = f"🟢{pos_count} ⚪{neu_count} 🟡{warn_count} 🔴{crit_count}"
    summary_lines.append(f"신호 분포: {sig_counts}")
    buy_short = [r['ticker'] for r in portfolio_results if r['judgment'] == '분할매수 후보'][:3]
    if buy_short:
        summary_lines.append(f"분할매수: {', '.join(buy_short)}")
    watch_short = [r['ticker'] for r in portfolio_results if r['judgment'] in ('위험 증가', '제외 검토')][:3]
    if watch_short:
        summary_lines.append(f"위험: {', '.join(watch_short)}")
    summary_lines.append(f"")
    nasdaq_buy_short = ', '.join([r['ticker'] for r in top_buy_candidates[:3]])
    summary_lines.append(f"NAS100 상위: {nasdaq_buy_short}")
    nasdaq_warn_short = ', '.join([r['ticker'] for r in top_watch[:3]])
    summary_lines.append(f"NAS100 주의: {nasdaq_warn_short}")
    kospi_buy_short = ', '.join([r['ticker'] for r in kospi_top[:3]])
    summary_lines.append(f"KOSPI 상위: {kospi_buy_short}")
    kospi_warn_short = ', '.join([r['ticker'] for r in kospi_watch[:3]])
    summary_lines.append(f"KOSPI 주의: {kospi_warn_short}")
    summary_lines.append(f"")
    summary_lines.append(f"소요 시간: {elapsed:.1f}초 | LLM 토큰: 0")
    summary_text = "\n".join(summary_lines)

    summary_txt_path = os.path.join(REPORTS_DIR, f"investment-summary-{today_str}.txt")
    with open(summary_txt_path, "w", encoding="utf-8") as f:
        f.write(summary_text)
    print(f"\n📱 모바일 요약 저장 완료: {summary_txt_path}")
    print()
    print(summary_text)

    # ── Hermes briefing ──
    print()
    print("---")
    print()
    print("## Hermes 봇 브리핑")
    print(f"투자 레포트 생성 완료 | @Stock_botbot 전송 완료")
    print(f"NAS100 {len(ndx_results)}종목 + KOSPI {len(kospi_results)}종목 + 포트폴리오 {len(PORTFOLIO_TICKERS)}종목 분석")
    print(f"LLM 토큰: 0 | 실행 시간: {elapsed:.1f}초")

    return report_path, json_path


def main():
    """Main entry point."""
    try:
        report_path, json_path = generate_report()
        print(f"\n✅ 리포트 생성 완료!")
        print(f"   보고서: {report_path}")
        print(f"   데이터: {json_path}")
    except KeyboardInterrupt:
        print(f"\n\n⚠ 사용자에 의해 중단됨")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
