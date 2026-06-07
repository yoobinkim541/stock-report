#!/usr/bin/env python3
"""
Daily Stock Market Report (Korean Language)
Generates a comprehensive daily report covering US and Korean markets.
"""

import os
import sys
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

import yfinance as yf
import requests
from bs4 import BeautifulSoup

try:
    from source_collector import build_digest, load_recent_events
except Exception:
    build_digest = None
    load_recent_events = None

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY_STR = NOW.strftime("%Y-%m-%d")
TODAY_STR_SHORT = NOW.strftime("%Y-%m-%d")
WEEKDAY = NOW.weekday()  # 0=Monday .. 6=Sunday

REPORT_FILE = os.path.expanduser(f"~/reports/daily-report-{TODAY_STR}.md")
SUMMARY_FILE = os.path.expanduser(f"~/reports/daily-summary-{TODAY_STR}.txt")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

# Sector ETFs
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLV": "Healthcare",
    "XLF": "Financial",
    "XLE": "Energy",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrial",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

# Portfolio tickers
DEFAULT_PORTFOLIO_TICKERS = [
    "MSFT", "QQQI", "ORCL", "NOW", "CRM",
    "SAP", "UNH", "SGOV", "CPNG", "NVDA",
    "GOOGL", "SPMO",
]
PORTFOLIO_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_snapshot.json")


def load_portfolio_tickers(path=PORTFOLIO_SNAPSHOT_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception:
        return list(DEFAULT_PORTFOLIO_TICKERS)

    tickers = []
    for section in ("overseas_general", "overseas_fractional"):
        for h in snap.get(section, {}).get("holdings_usd", []):
            ticker = h.get("ticker")
            shares = float(h.get("shares") or 0)
            value = float(h.get("value_usd") or 0)
            if ticker and (shares > 0 or value > 0) and ticker not in tickers:
                tickers.append(ticker)
    return tickers or list(DEFAULT_PORTFOLIO_TICKERS)


PORTFOLIO_TICKERS = load_portfolio_tickers()

# Major indices
MAJOR_INDICES = {
    "SPY": "S&P 500",
    "QQQ": "NASDAQ",
    "DIA": "Dow Jones",
    "^KS11": "KOSPI",
}

# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────

def safe_get(data: dict, key: str, default: Any = None) -> Any:
    """Safely get a value from a dict."""
    if data is None:
        return default
    return data.get(key, default)


def pct_str(value: Optional[float], decimals: int = 2) -> str:
    """Format a percentage value, handling None."""
    if value is None:
        return "[데이터 없음]"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def price_str(value: Optional[float]) -> str:
    """Format a price value."""
    if value is None:
        return "[데이터 없음]"
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def rsi_from_prices(prices):
    """Calculate RSI(14) from a list of close prices."""
    if prices is None or len(prices) < 15:
        return None
    closes = prices[-15:].values if hasattr(prices, 'values') else prices[-15:]
    if len(closes) < 15:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def macd_from_prices(prices):
    """Calculate MACD line and signal line (12, 26, 9)."""
    if prices is None or len(prices) < 35:
        return None, None
    closes = prices.values if hasattr(prices, 'values') else prices
    closes = closes[-35:]
    if len(closes) < 35:
        return None, None

    def ema(data, period):
        multiplier = 2 / (period + 1)
        result = [data[0]]
        for val in data[1:]:
            result.append((val - result[-1]) * multiplier + result[-1])
        return result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12[-1] - ema26[-1]

    # Signal line: 9-period EMA of MACD line
    macd_values = [ema12[i] - ema26[i] for i in range(len(ema26))]
    signal_vals = ema(macd_values, 9)
    signal_line = signal_vals[-1]

    return round(macd_line, 2), round(signal_line, 2)


def sma(prices, period):
    """Simple moving average."""
    if prices is None or len(prices) < period:
        return None
    closes = prices.values if hasattr(prices, 'values') else prices
    return round(sum(closes[-period:]) / period, 2)


def arrow_for_change(change: Optional[float]) -> str:
    """Return an arrow emoji based on change."""
    if change is None:
        return "➡️"
    if change > 0:
        return "▲"
    if change < 0:
        return "▼"
    return "➡️"


def compact_text(text: Optional[str], limit: int = 110) -> str:
    """Compress a long text into a single readable snippet."""
    if not text:
        return ""
    cleaned = " ".join(str(text).replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def format_kst_time(iso_text: Optional[str]) -> str:
    """Format an ISO timestamp into KST month-day hour:minute."""
    if not iso_text:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        return dt.astimezone(KST).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def fetch_saveticker_json(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """Fetch JSON from SaveTicker's public API."""
    base = os.getenv("SAVE_TICKER_API_BASE", "https://saveticker.com/api")
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def load_cached_source_digest() -> str:
    """Return a recent source-cache digest if the collector cache is available."""
    if not build_digest or not load_recent_events:
        return ""
    try:
        events = load_recent_events(hours=24)
    except Exception:
        return ""
    if not events:
        return ""
    return build_digest(events)


def format_news_item(item: dict, include_snippet: bool = True) -> str:
    """Format a SaveTicker news item into a markdown bullet."""
    title = item.get("title") or "[제목 없음]"
    source = item.get("source") or ""
    created_at = format_kst_time(item.get("created_at"))
    tags = [t for t in (item.get("tag_names") or []) if t]
    parts = []
    if source:
        parts.append(source)
    if created_at:
        parts.append(created_at)
    if tags:
        parts.append(", ".join(tags[:3]))
    meta = f" ({' · '.join(parts)})" if parts else ""
    line = f"- **{title}**{meta}"
    if include_snippet:
        snippet = compact_text(item.get("content") or item.get("group_summary") or "", 120)
        if snippet:
            line += f"\n  - {snippet}"
    return line


ARCA_STOCK_LABELS = ("🧠분석", "📰뉴스", "ℹ️정보", "실적")
ARCA_STOCK_MAX_PAGES = 5
ARCA_STOCK_MAX_POSTS = 8


def fetch_arca_stock_markdown(page: int = 1) -> Optional[str]:
    """Fetch Arca stock channel listing from the r.jina.ai text mirror."""
    url = f"https://r.jina.ai/http://arca.live/b/stock?p={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def parse_arca_stock_posts(markdown: str) -> list[dict]:
    """Parse Arca stock listing markdown into structured relevant posts."""
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
        if not any(label in link_text for label in ARCA_STOCK_LABELS):
            continue

        header = re.match(rf"^(?P<num>\d+)\s*(?P<label>{'|'.join(map(re.escape, ARCA_STOCK_LABELS))})\s+(?P<rest>.+)$", link_text)
        if not header:
            continue

        body = header.group("rest").strip()
        meta = re.match(
            r"^(?P<title>.*?)(?:\s+\[\d+\])?\s+(?P<author>\S+)\s+(?P<when>(?:\d{2}:\d{2}|\d{4}\.\d{2}\.\d{2}))\s+(?P<views>\d+)\s+(?P<likes>\d+)$",
            body,
        )
        if not meta:
            continue

        seen_ids.add(post_id)
        title = compact_text(meta.group("title").strip(), 90)
        posts.append(
            {
                "id": post_id,
                "url": f"https://arca.live/b/stock/{post_id}",
                "category": header.group("label"),
                "title": title,
                "author": meta.group("author").strip(),
                "when": meta.group("when").strip(),
                "views": meta.group("views"),
                "likes": meta.group("likes"),
            }
        )

    return posts


def fetch_arca_stock_posts(max_pages: int = ARCA_STOCK_MAX_PAGES, limit: int = ARCA_STOCK_MAX_POSTS) -> list[dict]:
    """Fetch recent relevant Arca stock posts across a few pages."""
    posts = []
    seen = set()
    for page in range(1, max_pages + 1):
        markdown = fetch_arca_stock_markdown(page)
        for post in parse_arca_stock_posts(markdown or ""):
            if post["id"] in seen:
                continue
            seen.add(post["id"])
            posts.append(post)
            if len(posts) >= limit:
                return posts
    return posts


def format_arca_post(post: dict) -> str:
    """Format a parsed Arca post for the report."""
    return (
        f"- [{post['title']}]({post['url']})"
        f" ({post['category']} · {post['author']} · {post['when']} · 조회 {post['views']} · 추천 {post['likes']})"
    )


# ─────────────────────────────────────────────
# Section Builders
# ─────────────────────────────────────────────

def section_1_market_overview() -> str:
    """📈 시장 개요"""
    lines = []
    lines.append("## 1. 📈 시장 개요 (Market Overview)")
    lines.append("")

    details = {}
    try:
        for symbol, name in MAJOR_INDICES.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if hist.empty or len(hist) < 2:
                    lines.append(f"- **{name} ({symbol})**: [데이터 없음] (사유: 히스토리 데이터 없음)")
                    continue
                prev_close = hist["Close"].iloc[-2]
                curr_close = hist["Close"].iloc[-1]
                change = ((curr_close - prev_close) / prev_close) * 100
                arrow = arrow_for_change(change)
                lines.append(
                    f"- **{name} ({symbol})**: {price_str(curr_close)} "
                    f"{arrow} {pct_str(change)}"
                )
                details[symbol] = {"change": change, "close": curr_close}
            except Exception as e:
                lines.append(f"- **{name} ({symbol})**: [데이터 없음] (사유: {e})")
    except Exception as e:
        lines.append(f"- 전체 시장 데이터 로드 실패: {e}")

    lines.append("")
    # Summary
    try:
        all_changes = [d["change"] for d in details.values() if d.get("change") is not None]
        if all_changes:
            avg = sum(all_changes) / len(all_changes)
            if avg > 0.5:
                sentiment = "미국 증시는 전반적으로 상승 마감했습니다."
            elif avg < -0.5:
                sentiment = "미국 증시는 전반적으로 하락 마감했습니다."
            else:
                sentiment = "미국 증시는 혼조세를 보였습니다."
            lines.append(f"{sentiment} S&P 500은 {details.get('SPY', {}).get('change', 0):.2f}% 변동을 기록했습니다.")
        else:
            lines.append("시장 변동 데이터를 확인할 수 없습니다.")
    except Exception:
        lines.append("시장 요약을 생성할 수 없습니다.")

    lines.append("")
    return "\n".join(lines)


def section_2_top_news() -> str:
    """📰 주요 뉴스 & 이슈"""
    lines = []
    lines.append("## 2. 📰 주요 뉴스 & 이슈 (Top News)")
    lines.append("")

    # 1) SaveTicker top stories: market-wide headlines
    top_stories = []
    data = fetch_saveticker_json("news/top-stories")
    if data and data.get("news_list"):
        top_stories = data["news_list"][:5]

    # 2) SaveTicker portfolio-related news via ticker filter
    portfolio_news = []
    portfolio_filter = ",".join(PORTFOLIO_TICKERS)
    data = fetch_saveticker_json(
        "news/list",
        params={"page": 1, "page_size": 6, "sort": "created_at_desc", "tickers": portfolio_filter},
    )
    if data and data.get("news_list"):
        portfolio_news = data["news_list"][:6]

    # 3) Keep a small fallback path for resilience
    headlines = []
    seen_titles = set()

    def add_news_batch(batch, prefix):
        for item in batch:
            title = item.get("title") or ""
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            headlines.append((prefix, item))

    add_news_batch(top_stories, "시장")
    add_news_batch(portfolio_news, "보유종목")

    if len(headlines) < 6:
        try:
            # Fallback: Google News RSS (public and more stable than HTML scraping)
            url = "https://news.google.com/rss/search?q=stock+market+today+OR+fed+OR+earnings&hl=en-US&gl=US&ceid=US:en"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "xml")
                for item in soup.select("item")[:8]:
                    title = item.title.get_text(strip=True) if item.title else ""
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        headlines.append(("RSS", {
                            "title": title,
                            "source": item.source.get_text(strip=True) if item.source else "Google News",
                            "created_at": item.pubDate.get_text(strip=True) if item.pubDate else None,
                            "content": item.description.get_text(strip=True) if item.description else "",
                            "tag_names": [],
                        }))
        except Exception:
            pass

    if len(headlines) < 6:
        try:
            for sym in ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]:
                if len(headlines) >= 8:
                    break
                ticker = yf.Ticker(sym)
                news_items = ticker.news
                if news_items:
                    for item in news_items[:2]:
                        title = item.get("title", "")
                        if title and title not in seen_titles:
                            seen_titles.add(title)
                            headlines.append(("YF", item))
        except Exception:
            pass

    if headlines:
        lines.append("### 시장 헤드라인")
        lines.append("")
        for prefix, item in headlines[:5]:
            lines.append(format_news_item(item))
        lines.append("")

        if portfolio_news:
            lines.append("### 포트폴리오 관련 뉴스")
            lines.append("")
            for item in portfolio_news[:5]:
                lines.append(format_news_item(item))
            lines.append("")
    else:
        lines.append("[데이터 없음] (사유: 뉴스 데이터를 불러올 수 없음)")
        lines.append("")

    cached_digest = load_cached_source_digest()
    if cached_digest:
        lines.append(cached_digest.rstrip())
        lines.append("")

    arca_posts = fetch_arca_stock_posts()
    if arca_posts:
        lines.append("### 아카라이브 주식 채널 최신 글 (분석/뉴스/정보/실적)")
        lines.append("")
        lines.append(f"- 최근 {ARCA_STOCK_MAX_PAGES}페이지에서 {len(arca_posts)}건의 관련 글을 확인했습니다.")
        for post in arca_posts[:ARCA_STOCK_MAX_POSTS]:
            lines.append(format_arca_post(post))
        lines.append("")

    return "\n".join(lines)


def section_3_sector_performance() -> str:
    """🏭 섹터별 시황"""
    lines = []
    lines.append("## 3. 🏭 섹터별 시황 (Sector Performance)")
    lines.append("")

    for symbol, name in SECTOR_ETFS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty or len(hist) < 2:
                lines.append(f"- **{name} ({symbol})**: [데이터 없음]")
                continue
            prev = hist["Close"].iloc[-2]
            curr = hist["Close"].iloc[-1]
            change = ((curr - prev) / prev) * 100
            arrow = arrow_for_change(change)
            lines.append(f"- **{name} ({symbol})**: {price_str(curr)} {arrow} {pct_str(change)}")
        except Exception as e:
            lines.append(f"- **{name} ({symbol})**: [데이터 없음] (사유: {e})")

    lines.append("")
    return "\n".join(lines)


def section_4_technical_analysis() -> str:
    """📊 기술적 분석"""
    lines = []
    lines.append("## 4. 📊 기술적 분석 (Technical Analysis - SPY)")
    lines.append("")

    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="6mo")
        closes = hist["Close"] if not hist.empty else None

        if closes is None or len(closes) < 50:
            lines.append("[데이터 없음] (사유: 충분한 히스토리 데이터 없음)")
            lines.append("")
            return "\n".join(lines)

        # RSI(14)
        rsi_val = rsi_from_prices(closes)
        lines.append(f"- **RSI(14)**: {rsi_val if rsi_val is not None else '[데이터 없음]'}")
        if rsi_val is not None:
            if rsi_val >= 70:
                lines.append("  - ⚠️ 과매수 구간 (Overbought)")
            elif rsi_val <= 30:
                lines.append("  - 🔔 과매도 구간 (Oversold)")
            else:
                lines.append("  - ✅ 중립 구간 (Neutral)")

        # MACD
        macd_line, signal_line = macd_from_prices(closes)
        lines.append(f"- **MACD Line**: {macd_line if macd_line is not None else '[데이터 없음]'}")
        lines.append(f"- **Signal Line**: {signal_line if signal_line is not None else '[데이터 없음]'}")
        if macd_line is not None and signal_line is not None:
            if macd_line > signal_line:
                lines.append("  - 📈 MACD가 시그널선 위 → 상승 신호 (Bullish)")
            else:
                lines.append("  - 📉 MACD가 시그널선 아래 → 하락 신호 (Bearish)")

        # SMA
        sma50 = sma(closes, 50)
        sma200 = sma(closes, 200)
        current_price = closes.iloc[-1]
        lines.append(f"- **50일 SMA**: {price_str(sma50) if sma50 is not None else '[데이터 없음]'}")
        lines.append(f"- **200일 SMA**: {price_str(sma200) if sma200 is not None else '[데이터 없음]'}")
        if sma50 is not None:
            pct_above_50 = ((current_price - sma50) / sma50) * 100
            lines.append(f"  - 현재가 vs 50일선: {pct_str(pct_above_50)}")
        if sma200 is not None:
            pct_above_200 = ((current_price - sma200) / sma200) * 100
            lines.append(f"  - 현재가 vs 200일선: {pct_str(pct_above_200)}")

        # Support / Resistance
        recent_20 = closes[-20:]
        support = recent_20.min()
        resistance = recent_20.max()
        lines.append(f"- **20일 지지선(Support)**: {price_str(support)}")
        lines.append(f"- **20일 저항선(Resistance)**: {price_str(resistance)}")

        # Golden cross / Death cross check
        if sma50 is not None and sma200 is not None:
            # Check previous day's SMAs
            sma50_prev = sma(closes[:-1], 50) if len(closes) > 50 else None
            sma200_prev = sma(closes[:-1], 200) if len(closes) > 200 else None
            if sma50_prev is not None and sma200_prev is not None:
                if sma50_prev <= sma200_prev and sma50 > sma200:
                    lines.append("  - 🟢 **골든크로스 발생!** (50일선이 200일선 상회)")
                elif sma50_prev >= sma200_prev and sma50 < sma200:
                    lines.append("  - 🔴 **데스크로스 발생!** (50일선이 200일선 하회)")

    except Exception as e:
        lines.append(f"[데이터 없음] (사유: 기술적 분석 계산 오류 - {e})")

    lines.append("")
    return "\n".join(lines)


def section_5_fear_greed() -> str:
    """😱 공포·탐욕 지수"""
    lines = []
    lines.append("## 5. 😱 공포·탐욕 지수 (Fear & Greed Index)")
    lines.append("")

    value = None
    label = "N/A"

    # Try scraping CNN Fear & Greed
    try:
        url = "https://edition.cnn.com/markets/fear-and-greed"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Look for the numeric value in various selectors
            for selector in [
                "[class*='fear-greed'] span",
                "[class*='index-value']",
                "[class*='market-fng']",
                "[class*='value']",
            ]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(strip=True)
                    # Try to parse a number
                    try:
                        val = float(text.replace(",", "").replace("+", "").replace("%", "").strip())
                        if 0 <= val <= 100:
                            value = val
                            break
                    except ValueError:
                        continue
    except Exception:
        pass

    # Fallback: use VIX
    if value is None:
        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="5d")
            if not hist.empty:
                vix_close = hist["Close"].iloc[-1]
                if vix_close < 20:
                    value = 75  # Greed
                    label = "탐욕 (Greed)"
                elif vix_close <= 30:
                    value = 50  # Neutral
                    label = "중립 (Neutral)"
                else:
                    value = 25  # Fear
                    label = "공포 (Fear)"
                lines.append(f"- **VIX 지수**: {vix_close:.2f}")
                lines.append(f"- **추정 공포·탐욕 지수**: {value} - {label}")
            else:
                lines.append("[데이터 없음] (사유: VIX 데이터 없음)")
        except Exception as e:
            lines.append(f"[데이터 없음] (사유: {e})")
    else:
        if value >= 50:
            label = "탐욕 (Greed)"
        elif value >= 25:
            label = "중립 (Neutral)"
        else:
            label = "공포 (Fear)"
        lines.append(f"- **CNN 공포·탐욕 지수**: {value:.1f}")
        lines.append(f"- **상태**: {label}")
        if value >= 75:
            lines.append("  - ⚠️ 극단적 탐욕 - 조정 가능성")
        elif value <= 25:
            lines.append("  - 🔔 극단적 공포 - 반등 가능성")

    lines.append("")
    return "\n".join(lines)


def section_6_portfolio_analysis() -> str:
    """💼 보유종목 분석"""
    lines = []
    lines.append("## 6. 💼 보유종목 분석 (Portfolio Analysis)")
    lines.append("")

    for sym in PORTFOLIO_TICKERS:
        lines.append(f"### {sym}")
        try:
            ticker = yf.Ticker(sym)
            info = {}
            try:
                info = ticker.info or {}
            except Exception:
                info = {}

            hist = ticker.history(period="6mo")
            if hist.empty:
                lines.append("  [데이터 없음] (사유: 히스토리 데이터 없음)")
                lines.append("")
                continue

            closes = hist["Close"]
            curr_price = closes.iloc[-1]
            prev_price = closes.iloc[-2] if len(closes) >= 2 else None

            # Daily change
            daily_change = None
            if prev_price and prev_price > 0:
                daily_change = ((curr_price - prev_price) / prev_price) * 100

            arrow = arrow_for_change(daily_change)
            lines.append(f"  - **현재가**: {price_str(curr_price)} {arrow} {pct_str(daily_change)}")

            # RSI(14)
            rsi_val = rsi_from_prices(closes)
            lines.append(f"  - **RSI(14)**: {rsi_val if rsi_val is not None else '[데이터 없음]'}")

            # SMAs
            sma50_val = sma(closes, 50)
            sma200_val = sma(closes, 200)
            status_50 = ""
            status_200 = ""
            if sma50_val is not None:
                status_50 = "🟢 상회" if curr_price > sma50_val else "🔴 하회"
                lines.append(f"  - **50일 SMA** ({price_str(sma50_val)}): 현재가 {status_50}")
            if sma200_val is not None:
                status_200 = "🟢 상회" if curr_price > sma200_val else "🔴 하회"
                lines.append(f"  - **200일 SMA** ({price_str(sma200_val)}): 현재가 {status_200}")

            # Analyst targets
            target_mean = info.get("targetMeanPrice")
            target_high = info.get("targetHighPrice")
            target_low = info.get("targetLowPrice")
            if target_mean:
                upside = ((target_mean - curr_price) / curr_price) * 100
                lines.append(f"  - **애널리스트 목표가**: 평균 {price_str(target_mean)} "
                             f"(고가 {price_str(target_high) if target_high else 'N/A'} / "
                             f"저가 {price_str(target_low) if target_low else 'N/A'})")
                lines.append(f"    - 현재가 대비: {pct_str(upside)}")

            # Recent news
            try:
                news_items = ticker.news
                if news_items:
                    for item in news_items[:2]:
                        title = item.get("title", "")
                        if title:
                            lines.append(f"  - 📰 {title}")
            except Exception:
                pass

            # Verdict
            signals = []
            if rsi_val is not None and rsi_val > 70:
                signals.append("과매수")
            if rsi_val is not None and rsi_val < 30:
                signals.append("과매도")
            if sma50_val and sma200_val:
                if curr_price > sma50_val and curr_price > sma200_val:
                    signals.append("장기 상승세")
                elif curr_price < sma50_val and curr_price < sma200_val:
                    signals.append("장기 하락세")
                else:
                    signals.append("혼조")
            if signals:
                parts = []
                for s in signals:
                    if "과매수" in s:
                        parts.append(f"⚠️ {s}")
                    elif "과매도" in s:
                        parts.append(f"🟢 {s}")
                    elif "상승세" in s:
                        parts.append(f"🟢 {s}")
                    elif "하락세" in s:
                        parts.append(f"🔴 {s}")
                    else:
                        parts.append(f"➡️ {s}")
                lines.append(f"  - **판단**: {', '.join(parts)}")
            else:
                lines.append("  - **판단**: 데이터 부족")

        except Exception as e:
            lines.append(f"  [데이터 없음] (사유: {e})")

        lines.append("")

    return "\n".join(lines)


def section_7_buy_sell_signals() -> str:
    """🎯 매수/매도 타이밍"""
    lines = []
    lines.append("## 7. 🎯 매수/매도 타이밍 (Buy/Sell Signals)")
    lines.append("")

    signals_found = False
    all_results = {}

    for sym in PORTFOLIO_TICKERS:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="6mo")
            if hist.empty or len(hist) < 50:
                continue

            closes = hist["Close"]
            curr_price = closes.iloc[-1]
            rsi_val = rsi_from_prices(closes)
            sma50_val = sma(closes, 50)
            sma200_val = sma(closes, 200)

            sym_signals = {}

            # RSI signals
            if rsi_val is not None:
                if rsi_val > 70:
                    sym_signals["과매수 (Overbought)"] = f"RSI {rsi_val:.1f} > 70, 매도 신호 구간"
                elif rsi_val < 30:
                    sym_signals["과매도 (Oversold)"] = f"RSI {rsi_val:.1f} < 30, 매수 기회 구간"

            # SMA signals
            if sma50_val is not None:
                if curr_price > sma50_val:
                    sym_signals["50일선 상회"] = f"현재가 ${curr_price:.2f} > 50일선 ${sma50_val:.2f}"
                else:
                    sym_signals["50일선 하회"] = f"현재가 ${curr_price:.2f} < 50일선 ${sma50_val:.2f}"

            if sma200_val is not None:
                if curr_price > sma200_val:
                    sym_signals["장기 상승세 (200MA 상회)"] = f"200일선 ${sma200_val:.2f} 상회 중"
                else:
                    sym_signals["장기 하락세 (200MA 하회)"] = f"200일선 ${sma200_val:.2f} 하회 중"

            # Golden/death cross
            if sma50_val is not None and sma200_val is not None and len(closes) > 200:
                sma50_prev = sma(closes[:-1], 50)
                sma200_prev = sma(closes[:-1], 200)
                if sma50_prev is not None and sma200_prev is not None:
                    if sma50_prev <= sma200_prev and sma50_val > sma200_val:
                        sym_signals["🟢 골든크로스"] = "50일선이 200일선을 돌파!"
                    elif sma50_prev >= sma200_prev and sma50_val < sma200_val:
                        sym_signals["🔴 데스크로스"] = "50일선이 200일선을 하향 돌파!"

            if sym_signals:
                all_results[sym] = sym_signals

        except Exception:
            continue

    if all_results:
        for sym, sym_signals in all_results.items():
            lines.append(f"### {sym}")
            for signal_type, desc in sym_signals.items():
                lines.append(f"  - **{signal_type}**: {desc}")
            lines.append("")
            signals_found = True

    if not signals_found:
        lines.append("[데이터 없음] (사유: 신호를 계산할 충분한 데이터 없음)")
        lines.append("")

    return "\n".join(lines)


def section_8_major_investors() -> str:
    """👑 대형 투자자 동향"""
    lines = []
    lines.append("## 8. 👑 대형 투자자 동향 (Major Investors)")
    lines.append("")
    lines.append("*참고: 이 자료는 알려진 공개 포지션 기준이며, 실제 최신 13F 데이터는 SEC EDGAR에서 확인하세요.*")
    lines.append("")

    investors = {
        "워렌 버핏 (Berkshire Hathaway)": {
            "desc": "버크셔 해서웨이의 대표적인 장기 보유 종목",
            "holdings": {
                "AAPL": "약 3.9억주 (포트폴리오 40%+)",
                "BAC": "은행 부문 대표 투자",
                "AXP": "아메리칸 익스프레스 - 장기 보유",
                "KO": "코카콜라 - 배당주 장기 보유",
                "OXY": "옥시덴탈 - 최근 추가 매수",
            },
            "recent": "2024년 들어 AAPL 일부 축소, OXY 지분 확대, 현금성 자산 증가",
        },
        "빌 애크먼 (Pershing Square)": {
            "desc": "헤지펀드 퍼싱스퀘어의 집중 투자 포트폴리오",
            "holdings": {
                "CP": "캐나다 퍼시픽 캔자스시티 철도 - 최대 보유",
                "GOOGL": "알파벳 - 대형 기술주",
                "HLT": "힐튼 호텔 - 소비 관련",
            },
            "recent": "GOOGL 비중 확대 및 장기 투자 지속",
        },
        "스탠리 드러켄밀러 (Duquesne)": {
            "desc": "유명 헤지펀드 매니저, 기술주 중심",
            "holdings": {
                "NVDA": "AI 반도체 대표주 - 대량 매수",
                "COIN": "코인베이스 - 암호화폐 익스포저",
                "META": "메타플랫폼스 - AI 투자 수혜 기대",
            },
            "recent": "NVDA 및 AI 관련주에 적극적 투자, COIN 신규 매수",
        },
        "레이 달리오 (Bridgewater)": {
            "desc": "세계 최대 헤지펀드, 분산 투자 전략",
            "holdings": {
                "GOOGL": "알파벳 - 기술주 비중 유지",
                "NVDA": "AI/반도체 익스포저",
                "KO": "코카콜라 - 안전 자산",
            },
            "recent": "2024년 빅테크 비중 축소 및 방어주 비중 확대",
        },
        "국민연금 (NPS)": {
            "desc": "한국 국민연금공단, 글로벌 분산 투자",
            "holdings": {
                "AAPL": "애플 - 대형 기술주",
                "MSFT": "마이크로소프트 - AI 투자",
                "NVDA": "엔비디아 - 반도체",
                "AMZN": "아마존 - 전자상거래/클라우드",
            },
            "recent": "해외 주식 비중 확대 추세, AI 관련주 중심 리밸런싱",
        },
    }

    for name, data in investors.items():
        lines.append(f"### {name}")
        lines.append(f"- **설명**: {data['desc']}")
        lines.append("- **주요 보유 종목**:")
        for sym, desc in data["holdings"].items():
            lines.append(f"  - {sym}: {desc}")
        lines.append(f"- **최근 동향**: {data['recent']}")
        lines.append("")

    return "\n".join(lines)


def section_9_analyst_reports() -> str:
    """🏢 애널리스트 리포트"""
    lines = []
    lines.append("## 9. 🏢 애널리스트 리포트 (Analyst Reports)")
    lines.append("")

    for sym in PORTFOLIO_TICKERS:
        lines.append(f"### {sym}")
        try:
            ticker = yf.Ticker(sym)
            info = ticker.info or {}

            curr_price = None
            try:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    curr_price = hist["Close"].iloc[-1]
            except Exception:
                pass

            target_mean = info.get("targetMeanPrice")
            target_high = info.get("targetHighPrice")
            target_low = info.get("targetLowPrice")
            num_analysts = info.get("numberOfAnalystOpinions")
            rec_mean = info.get("recommendationMean")
            rec_key = info.get("recommendationKey")

            upside_str = ""
            if target_mean and curr_price and curr_price > 0:
                upside = ((target_mean - curr_price) / curr_price) * 100
                upside_str = f" (상승여력: {pct_str(upside)})"
            elif target_mean and curr_price:
                upside_str = ""

            lines.append(f"  - **목표가**: 평균 {price_str(target_mean)}{upside_str}")
            if target_high:
                lines.append(f"    - 고가 목표: {price_str(target_high)}")
            if target_low:
                lines.append(f"    - 저가 목표: {price_str(target_low)}")

            if num_analysts:
                lines.append(f"  - **애널리스트 수**: {int(num_analysts)}명")
            if rec_mean:
                lines.append(f"  - **추천 평균**: {rec_mean:.2f} (1=강력매수, 5=매도)")
            if rec_key:
                rec_labels = {
                    "buy": "매수",
                    "strong_buy": "강력매수",
                    "hold": "보유",
                    "neutral": "중립",
                    "sell": "매도",
                    "strong_sell": "강력매도",
                }
                label = rec_labels.get(rec_key.lower(), rec_key)
                lines.append(f"  - **추천 등급**: {label}")

        except Exception as e:
            lines.append(f"  [데이터 없음] (사유: {e})")

        lines.append("")

    return "\n".join(lines)


def section_10_economic_calendar() -> str:
    """📅 경제 일정"""
    lines = []
    lines.append("## 10. 📅 경제 일정 (Economic Calendar)")
    lines.append("")

    weekday_names = {
        0: "월요일",
        1: "화요일",
        2: "수요일",
        3: "목요일",
        4: "금요일",
        5: "토요일",
        6: "일요일",
    }

    today_name = weekday_names.get(WEEKDAY, "알 수 없음")
    lines.append(f"**오늘 ({TODAY_STR}, {today_name}) 주요 경제 일정:**")
    lines.append("")

    # Generic weekly schedule
    weekly_schedule = {
        0: ("월요일", [
            "📊 제조업 PMI (Markit/S&P Global)",
            "📊 서비스업 PMI (Markit/S&P Global)",
            "🏭 내구재 주문 (Durable Goods Orders)",
        ]),
        1: ("화요일", [
            "📈 소비자물가지수(CPI) - 매월 둘째주",
            "📈 생산자물가지수(PPI) - 매월 셋째주",
            "🏠 주택가격지수 (Case-Shiller)",
            "📊 컨퍼런스보드 소비자신뢰지수",
        ]),
        2: ("수요일", [
            "📊 ADP 고용보고서",
            "📈 GDP (분기별, 수정치)",
            "🏠 주택착공/건축허가",
            "📋 연준 FOMC 회의록 (8주간격)",
            "🛢️ EIA 원유재고",
        ]),
        3: ("목요일", [
            "📋 주간 실업수당 청구건수",
            "📊 무역수지",
            "🏠 기존주택매매",
            "📈 생산자물가지수(PPI) (월 셋째주)",
        ]),
        4: ("금요일", [
            "📊 고용보고서 (비농업고용지수, 실업률) - 매월 첫째주",
            "📈 미시건대 소비자심리지수",
            "🏠 신규주택매매",
        ]),
    }

    if WEEKDAY in weekly_schedule:
        day_name, events = weekly_schedule[WEEKDAY]
        for event in events:
            lines.append(f"  - {event}")
    else:
        lines.append("  - 오늘은 주요 경제 지표 발표가 없는 주말입니다.")

    lines.append("")
    lines.append("**이번 주 주요 일정:**")
    lines.append("")
    for day_num in range(5):
        if day_num in weekly_schedule:
            day_name, events = weekly_schedule[day_num]
            lines.append(f"  **{day_name}**")
            for event in events:
                lines.append(f"    - {event}")
            lines.append("")

    lines.append("*출처: 경제 캘린더는 일반적인 일정이며, 실제 발표 일정은 변경될 수 있습니다.*")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Main Report Assembler
# ─────────────────────────────────────────────

def clean_summary_line(line: str) -> str:
    """Convert markdown-heavy report lines into mobile-friendly text."""
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line.strip())
    line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
    return line


def collect_section_bullets(report: str, heading: str, limit: int) -> list[str]:
    """Collect top-level bullets from a report section."""
    lines = report.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("## ") and heading in line:
            start = idx + 1
            break
    if start is None:
        return []

    bullets = []
    for line in lines[start:]:
        if line.startswith("## ") or line == "---":
            break
        if line.startswith("- ") and not line.startswith("  - "):
            bullets.append(clean_summary_line(line))
            if len(bullets) >= limit:
                break
    return bullets


def build_mobile_summary(report: str) -> str:
    """Build a short Telegram-first market summary."""
    market_lines = collect_section_bullets(report, "시장 개요", 4)
    news_lines = collect_section_bullets(report, "주요 뉴스", 3)
    signal_lines = collect_section_bullets(report, "매수/매도 타이밍", 3)

    if "전반적으로 상승" in report:
        conclusion = "상승 우위. 신규 매수는 과열 여부 확인 후 분할 접근."
    elif "전반적으로 하락" in report:
        conclusion = "하락 우위. 현금 비중과 리스크 관리 우선."
    elif "혼조세" in report:
        conclusion = "혼조세. 보유 유지 + 강한 종목만 선별 관찰."
    else:
        conclusion = "핵심 지표 확인 후 보수적으로 대응."

    lines = [
        f"📈 {TODAY_STR} 시황 요약",
        "",
        f"결론: {conclusion}",
        "",
        "핵심 지수:",
    ]

    if market_lines:
        lines.extend(market_lines)
    else:
        lines.append("- 시장 지수 데이터 확인 불가")

    lines.append("")
    lines.append("핵심 뉴스:")
    if news_lines:
        lines.extend(news_lines)
    else:
        lines.append("- 주요 뉴스 데이터 확인 불가")

    lines.append("")
    lines.append("오늘 체크:")
    if signal_lines:
        lines.extend(signal_lines)
    else:
        lines.extend([
            "- 금리/달러 움직임",
            "- 반도체·빅테크 강도",
            "- 보유종목 개별 뉴스",
        ])

    lines.append("")
    lines.append("상세 리포트는 첨부 파일 참고")
    return "\n".join(lines)


def build_report() -> str:
    """Build the full report."""
    lines = []
    lines.append(f"# 📊 주식시장 일일 리포트")
    lines.append(f"**발행일**: {TODAY_STR} (KST 기준)")
    lines.append(f"**데이터 출처**: Yahoo Finance, SaveTicker API, Google News RSS, CNN Money, Arca Live (r.jina.ai mirror)")
    lines.append("---")
    lines.append("")

    sections = [
        ("시장 개요", section_1_market_overview),
        ("주요 뉴스 & 이슈", section_2_top_news),
        ("섹터별 시황", section_3_sector_performance),
        ("기술적 분석", section_4_technical_analysis),
        ("공포·탐욕 지수", section_5_fear_greed),
        ("보유종목 분석", section_6_portfolio_analysis),
        ("매수/매도 타이밍", section_7_buy_sell_signals),
        ("대형 투자자 동향", section_8_major_investors),
        ("애널리스트 리포트", section_9_analyst_reports),
        ("경제 일정", section_10_economic_calendar),
    ]

    for name, section_fn in sections:
        try:
            section_output = section_fn()
            lines.append(section_output)
            lines.append("---")
            lines.append("")
        except Exception as e:
            lines.append(f"## {name}")
            lines.append("")
            lines.append(f"[데이터 없음] (사유: 섹션 생성 중 오류 - {e})")
            lines.append("")
            lines.append("---")
            lines.append("")

    # Footer
    lines.append("*본 리포트는 자동 생성되었으며 투자 조언이 아닙니다. 투자는 본인의 판단으로 신중히 결정하세요.*")
    lines.append("")

    return "\n".join(lines)


def main():
    """Main entry point."""
    print(f"📊 주식시장 일일 리포트 생성 중... ({TODAY_STR})")
    print("=" * 60)

    report = build_report()

    # Print to stdout
    print(report)

    # Save to file
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    summary = build_mobile_summary(report)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(summary)

    print(f"\n✅ 리포트 저장 완료: {REPORT_FILE}")
    print(f"✅ 모바일 요약 저장 완료: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()