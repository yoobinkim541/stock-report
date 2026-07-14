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
KST_NOW = datetime.now(KST)
TODAY_STR = KST_NOW.strftime("%Y-%m-%d")
TODAY_STR_SHORT = KST_NOW.strftime("%Y-%m-%d")
WEEKDAY = KST_NOW.weekday()  # 0=Monday .. 6=Sunday

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

# ── 포트폴리오 종목 — 단일 소스: portfolio_universe.py ──────────────────
_PROJECT_DIR = os.getenv("STOCK_REPORT_PROJECT_DIR",
                         os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from portfolio_universe import (DEFAULT_PORTFOLIO_TICKERS, PORTFOLIO_SNAPSHOT_PATH,
                                load_portfolio_tickers)

PORTFOLIO_TICKERS = load_portfolio_tickers()

# Major indices
MAJOR_INDICES = {
    "SPY": "S&P 500",
    "QQQ": "NASDAQ",
    "DIA": "Dow Jones",
    "^KS11": "KOSPI",
}

NON_EQUITY_TICKERS = {"SGOV", "BIL", "SHV", "SHY", "QQQI", "SPMO", "QLD", "TQQQ", "UPRO"}
LOW_TRUST_MARKERS = ("카더라", "소식통", "rumor", "unconfirmed")
MARKET_NEWS_KEYWORDS = (
    "fed", "fomc", "cpi", "ppi", "gdp", "employment", "jobs", "oil", "iran",
    "tariff", "rate", "inflation", "연준", "금리", "고용", "물가", "유가", "이란",
    "호르무즈", "전쟁", "원유", "국채",
)

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


def price_str(value: Optional[float], symbol: str | None = None) -> str:
    """Format a price value."""
    if value is None:
        return "[데이터 없음]"
    if symbol and symbol.startswith("^KS"):
        return f"{value:,.2f}pt"
    if symbol == "KRW=X":
        return f"₩{value:,.2f}"
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def is_non_equity(sym: str) -> bool:
    return (sym or "").upper().split(".")[0] in NON_EQUITY_TICKERS


def _short_ticker_list(tickers: list[str], limit: int = 4) -> str:
    shown = [t for t in tickers if t][:limit]
    suffix = f" 외 {len(tickers) - limit}개" if len(tickers) > limit else ""
    return ", ".join(shown) + suffix if shown else "없음"


def _market_snapshot() -> dict:
    """섹션 0/요약용 빠른 시장 스냅샷. 실패해도 빈 dict."""
    out = {}
    for symbol, name in MAJOR_INDICES.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if hist is None or hist.empty or len(hist) < 2:
                continue
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            change = ((curr - prev) / prev) * 100 if prev > 0 else None
            out[symbol] = {"name": name, "close": curr, "change": change}
        except Exception:
            continue
    return out


def _sector_snapshot() -> list[dict]:
    rows = []
    for symbol, name in SECTOR_ETFS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if hist is None or hist.empty or len(hist) < 2:
                continue
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            change = ((curr - prev) / prev) * 100 if prev > 0 else None
            if change is not None:
                rows.append({"symbol": symbol, "name": name, "change": change, "close": curr})
        except Exception:
            continue
    rows.sort(key=lambda r: r["change"], reverse=True)
    return rows


def _news_title(item: dict) -> str:
    return str(item.get("title") or "")


def _news_has_portfolio_ticker(item: dict) -> bool:
    text = (_news_title(item) + " " + " ".join(item.get("tickers") or [])).upper()
    for ticker in PORTFOLIO_TICKERS:
        base = ticker.upper().split(".")[0]
        if base and (f"${base}" in text or re.search(rf"\b{re.escape(base)}\b", text)):
            return True
    return False


def _news_is_market_relevant(item: dict) -> bool:
    text = (_news_title(item) + " " + str(item.get("content") or item.get("group_summary") or "")).lower()
    return any(k.lower() in text for k in MARKET_NEWS_KEYWORDS)


def _news_is_low_trust(item: dict) -> bool:
    text = _news_title(item).lower()
    return any(marker.lower() in text for marker in LOW_TRUST_MARKERS)


def _news_trust_label(item: dict) -> str:
    return "신뢰도 낮음·확인 필요" if _news_is_low_trust(item) else ""


def _format_news_bullet(item: dict, include_snippet: bool = True) -> str:
    line = format_news_item(item, include_snippet=include_snippet)
    trust = _news_trust_label(item)
    if trust:
        line = line.replace("- **", f"- `{trust}` **", 1)
    return line


def _compact_cached_digest(digest: str, max_items: int = 6) -> str:
    """source cache digest를 본문용으로 축약. 포트/시장 관련 항목을 우선 보존."""
    if not digest:
        return ""
    lines = [line.rstrip() for line in digest.splitlines() if line.strip()]
    summary = [line for line in lines if line.startswith("- ") and " — " not in line][:4]
    item_lines = [line for line in lines if line.startswith("- [")]

    def relevant(line: str) -> bool:
        upper = line.upper()
        if any(t.upper().split(".")[0] in upper for t in PORTFOLIO_TICKERS):
            return True
        return any(k.upper() in upper for k in MARKET_NEWS_KEYWORDS)

    picked = [line for line in item_lines if relevant(line)][:max_items]
    if len(picked) < max_items:
        picked.extend([line for line in item_lines if line not in picked][: max_items - len(picked)])
    out = ["## 누적 수집 자료", ""]
    out.extend(summary[:4])
    if picked:
        out.append("")
        out.extend(picked[:max_items])
    return "\n".join(out)


def _portfolio_focus_line() -> str:
    non_equity = [t for t in PORTFOLIO_TICKERS if is_non_equity(t)]
    equity = [t for t in PORTFOLIO_TICKERS if not is_non_equity(t)]
    bits = []
    if equity:
        bits.append(f"개별주 뉴스/목표가 확인: {_short_ticker_list(equity)}")
    if non_equity:
        bits.append(f"역할형 ETF RSI 매매판정 제외: {_short_ticker_list(non_equity)}")
    return " · ".join(bits) if bits else "보유종목 데이터 확인 필요"


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


def escape_markdown(text: Optional[str]) -> str:
    """마크다운 특수문자 이스케이프 — 외부 뉴스 제목 등을 마크다운에 임베드할 때
    인젝션(굵게/링크/코드블록 깨짐) 방지. None/빈값은 빈 문자열로."""
    if not text:
        return ""
    return re.sub(r'([\*\[\]\(\)_`~])', r'\\\1', str(text))


def format_news_item(item: dict, include_snippet: bool = True) -> str:
    """Format a SaveTicker news item into a markdown bullet."""
    # 외부 입력(제목·출처)은 마크다운 임베드 전 이스케이프 — 인젝션 방어
    title = escape_markdown(item.get("title")) or "[제목 없음]"
    source = escape_markdown(item.get("source"))
    created_at = format_kst_time(item.get("created_at"))
    tags = [t for t in (item.get("tag_names") or []) if t]
    parts = []
    if source:
        parts.append(source)
    if created_at:
        parts.append(created_at)
    if tags:
        # 태그도 외부 입력 — 마크다운 이스케이프
        parts.append(", ".join(escape_markdown(t) for t in tags[:3]))
    meta = f" ({' · '.join(parts)})" if parts else ""
    line = f"- **{title}**{meta}"
    if include_snippet:
        # 스니펫(본문/요약)도 외부 입력 — 마크다운 이스케이프 후 임베드
        snippet = escape_markdown(compact_text(item.get("content") or item.get("group_summary") or "", 120))
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

def section_0_today_summary() -> str:
    """오늘 결론 — 시황 리포트의 읽기 방향을 먼저 제시."""
    lines = ["## 0. 오늘 요약", ""]
    market = _market_snapshot()
    sectors = _sector_snapshot()

    spy = market.get("SPY", {})
    qqq = market.get("QQQ", {})
    spy_chg = spy.get("change")
    qqq_chg = qqq.get("change")

    if qqq_chg is not None and qqq_chg <= -1:
        market_note = f"기술주 약세: NASDAQ {pct_str(qqq_chg)}"
    elif spy_chg is not None and spy_chg <= -0.5:
        market_note = f"시장 약세: S&P 500 {pct_str(spy_chg)}"
    elif spy_chg is not None and spy_chg >= 0.5:
        market_note = f"시장 강세: S&P 500 {pct_str(spy_chg)}"
    elif spy_chg is not None or qqq_chg is not None:
        market_note = "시장 혼조/보합권"
    else:
        market_note = "시장 지수 데이터 확인 필요"

    if sectors:
        leaders = ", ".join(f"{r['name']} {pct_str(r['change'], 1)}" for r in sectors[:2])
        laggards = ", ".join(f"{r['name']} {pct_str(r['change'], 1)}" for r in sectors[-2:])
        sector_note = f"섹터: 강세 {leaders} / 약세 {laggards}"
    else:
        sector_note = "섹터: 데이터 확인 필요"

    lines.extend([
        f"- **시장:** {market_note}",
        f"- **포트:** {_portfolio_focus_line()}",
        f"- **섹터:** {sector_note}",
        "- **주의:** 이 리포트는 시장·뉴스 브리핑입니다. 체결 판단은 투자 리포트/리밸런싱 리포트 기준으로 분리합니다.",
        "- **데이터 품질:** KOSPI는 지수 포인트로 표시하고, 현금성/역할형 ETF에는 RSI 매매판정을 적용하지 않습니다.",
        "",
    ])
    return "\n".join(lines)


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
                # 0/None 분모 방어 — prev_close 가 0이거나 결측이면 변화율 계산 불가
                if prev_close and prev_close > 0:
                    change = ((curr_close - prev_close) / prev_close) * 100
                else:
                    change = None
                arrow = arrow_for_change(change)
                lines.append(
                    f"- **{name} ({symbol})**: {price_str(curr_close, symbol)} "
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
        all_items = [item for _, item in headlines]
        direct = []
        market_related = []
        low_trust = []
        other = []
        for item in all_items:
            if _news_is_low_trust(item):
                low_trust.append(item)
            if _news_has_portfolio_ticker(item):
                direct.append(item)
            elif _news_is_market_relevant(item):
                market_related.append(item)
            else:
                other.append(item)

        if direct:
            lines.append("### 포트폴리오 직접 관련")
            lines.append("")
            for item in direct[:5]:
                lines.append(_format_news_bullet(item))
            lines.append("")

        if market_related:
            lines.append("### 시장 영향")
            lines.append("")
            for item in market_related[:5]:
                lines.append(_format_news_bullet(item))
            lines.append("")

        if low_trust:
            lines.append("### 저신뢰·확인 필요")
            lines.append("")
            for item in low_trust[:3]:
                lines.append(_format_news_bullet(item, include_snippet=False))
            lines.append("")

        if other and not direct and not market_related:
            lines.append("### 기타 헤드라인")
            lines.append("")
            for item in other[:3]:
                lines.append(_format_news_bullet(item, include_snippet=False))
            lines.append("")
    else:
        lines.append("[데이터 없음] (사유: 뉴스 데이터를 불러올 수 없음)")
        lines.append("")

    cached_digest = load_cached_source_digest()
    if cached_digest:
        lines.append(_compact_cached_digest(cached_digest).rstrip())
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

    rows = []
    for symbol, name in SECTOR_ETFS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty or len(hist) < 2:
                lines.append(f"- **{name} ({symbol})**: [데이터 없음]")
                continue
            prev = hist["Close"].iloc[-2]
            curr = hist["Close"].iloc[-1]
            # 0/None 분모 방어 — prev 가 0이거나 결측이면 변화율 계산 불가
            if prev and prev > 0:
                change = ((curr - prev) / prev) * 100
            else:
                change = None
            arrow = arrow_for_change(change)
            lines.append(f"- **{name} ({symbol})**: {price_str(curr)} {arrow} {pct_str(change)}")
            if change is not None:
                rows.append({"symbol": symbol, "name": name, "change": change})
        except Exception as e:
            lines.append(f"- **{name} ({symbol})**: [데이터 없음] (사유: {e})")

    if rows:
        rows.sort(key=lambda r: r["change"], reverse=True)
        leaders = ", ".join(f"{r['name']} {pct_str(r['change'], 1)}" for r in rows[:3])
        laggards = ", ".join(f"{r['name']} {pct_str(r['change'], 1)}" for r in rows[-3:])
        lines.extend([
            "",
            f"- **해석:** 상대강세 {leaders}",
            f"- **주의:** 상대약세 {laggards}",
        ])

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
                lines.append("  - 📈 MACD가 시그널선 위 → 단기 모멘텀 우위 참고")
            else:
                lines.append("  - 📉 MACD가 시그널선 아래 → 단기 모멘텀 약세 참고")

        # SMA
        sma50 = sma(closes, 50)
        sma200 = sma(closes, 200)
        current_price = closes.iloc[-1]
        lines.append(f"- **50일 SMA**: {price_str(sma50) if sma50 is not None else '[데이터 없음]'}")
        lines.append(f"- **200일 SMA**: {price_str(sma200) if sma200 is not None else '[데이터 없음]'}")
        if sma200 is None:
            lines.append("  - 장기 추세 판단은 200일선 결측으로 생략")
        # 0 분모 방어 — sma 값이 0이면 이격도(%) 계산 불가
        if sma50 is not None and sma50 > 0:
            pct_above_50 = ((current_price - sma50) / sma50) * 100
            lines.append(f"  - 현재가 vs 50일선: {pct_str(pct_above_50)}")
        if sma200 is not None and sma200 > 0:
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


def _portfolio_verdict(sym: str, rsi_val, curr_price, sma50_val, sma200_val) -> str:
    if is_non_equity(sym):
        return "역할형/현금성 ETF — RSI 매매판정 제외, 보유 목적 기준 확인"

    signals = []
    if rsi_val is not None and rsi_val > 70:
        signals.append("기술 과열 참고")
    if rsi_val is not None and rsi_val < 30:
        signals.append("기술 과매도 참고")
    if curr_price is not None and sma50_val is not None and sma200_val is not None:
        if curr_price > sma50_val and curr_price > sma200_val:
            signals.append("50/200일선 상회")
        elif curr_price < sma50_val and curr_price < sma200_val:
            signals.append("50/200일선 하회")
        else:
            signals.append("추세 혼조")
    elif curr_price is not None and sma50_val is not None:
        signals.append("50일선 상회" if curr_price > sma50_val else "50일선 하회")

    if signals:
        return " · ".join(signals) + " (자동 매매 신호 아님)"
    if rsi_val is not None or sma50_val is not None:
        return "기술 지표 중립권"
    return "데이터 부족"


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
                # 0/None 분모 방어 — curr_price 가 0/결측이면 상승여력 계산 생략
                upside = ((target_mean - curr_price) / curr_price) * 100 if (curr_price and curr_price > 0) else None
                lines.append(f"  - **애널리스트 목표가**: 평균 {price_str(target_mean)} "
                             f"(고가 {price_str(target_high) if target_high else 'N/A'} / "
                             f"저가 {price_str(target_low) if target_low else 'N/A'})")
                if upside is not None:
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

            lines.append(f"  - **상태 해석**: {_portfolio_verdict(sym, rsi_val, curr_price, sma50_val, sma200_val)}")

        except Exception as e:
            lines.append(f"  [데이터 없음] (사유: {e})")

        lines.append("")

    return "\n".join(lines)


def section_7_buy_sell_signals() -> str:
    """🎯 참고 기술 신호"""
    lines = []
    lines.append("## 7. 🎯 참고 기술 신호 (자동 매매 신호 아님)")
    lines.append("")

    signals_found = False
    all_results = {}
    skipped = []

    for sym in PORTFOLIO_TICKERS:
        try:
            if is_non_equity(sym):
                skipped.append(sym)
                continue
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
                    sym_signals["과열 참고"] = f"RSI {rsi_val:.1f} > 70, 추격 주의"
                elif rsi_val < 30:
                    sym_signals["과매도 참고"] = f"RSI {rsi_val:.1f} < 30, 반등 확인 필요"

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
                        sym_signals["🟢 골든크로스"] = "50일선이 200일선을 상향 돌파"
                    elif sma50_prev >= sma200_prev and sma50_val < sma200_val:
                        sym_signals["🔴 데스크로스"] = "50일선이 200일선을 하향 돌파"

            if sym_signals:
                all_results[sym] = sym_signals

        except Exception:
            continue

    if all_results:
        if skipped:
            lines.append(f"- 역할형/현금성 ETF는 RSI 매매판정 제외: {_short_ticker_list(skipped)}")
            lines.append("")
        for sym, sym_signals in all_results.items():
            lines.append(f"### {sym}")
            for signal_type, desc in sym_signals.items():
                lines.append(f"  - **{signal_type}**: {desc}")
            lines.append("")
            signals_found = True

    if not signals_found:
        if skipped:
            lines.append(f"- 역할형/현금성 ETF는 RSI 매매판정 제외: {_short_ticker_list(skipped)}")
        lines.append("[데이터 없음] (사유: 신호를 계산할 충분한 데이터 없음)")
        lines.append("")

    return "\n".join(lines)


def section_8_major_investors() -> str:
    """👑 대형 투자자 고정 참고자료"""
    lines = []
    lines.append("## 부록 A. 👑 대형 투자자 동향 (고정 참고자료)")
    lines.append("")
    lines.append("*당일 신호가 아니라 공개 13F/알려진 성향 기반의 고정 참고자료입니다. 최신성은 SEC EDGAR 등에서 별도 확인하세요.*")
    lines.append("")

    investors = [
        ("Berkshire Hathaway", "가치/현금 비중 참고", "최신 보유 변화는 13F 확인 필요"),
        ("Pershing Square", "집중 포트폴리오 참고", "보유 종목은 분기별 변동 가능"),
        ("Duquesne", "매크로/성장주 방향성 참고", "당일 매매 근거로 사용하지 않음"),
        ("Bridgewater", "분산·매크로 포지션 참고", "시장 레짐 참고자료"),
        ("NPS", "장기 글로벌 분산 투자 참고", "국내외 대형주 수급 힌트 수준"),
    ]
    for name, focus, caveat in investors:
        lines.append(f"- **{name}:** {focus} · {caveat}")

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
    lines.append("## 10. 📅 경제 일정 (실제 캘린더 우선)")
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

    events = []
    try:
        from providers.econ_calendar import upcoming_events
        events = upcoming_events(days=7, start=KST_NOW.date())
    except Exception:
        events = []

    today_events = [e for e in events if e.get("when") and e["when"].date().isoformat() == TODAY_STR]
    if today_events:
        for ev in today_events[:8]:
            lines.append(f"  - {ev.get('marker', '⚪')} {ev.get('date_str', '—')} · {ev.get('title')}")
    else:
        lines.append("  - 실제 캘린더 기준 확인된 오늘 일정 없음 또는 데이터 미확보")

    upcoming = [e for e in events if e not in today_events]
    lines.append("")
    lines.append("**향후 7일 확인 일정:**")
    lines.append("")
    if upcoming:
        for ev in upcoming[:12]:
            lines.append(f"  - {ev.get('marker', '⚪')} {ev.get('date_str', '—')} · {ev.get('title')}")
    else:
        lines.append("  - 실제 캘린더 데이터 미확보")

    lines.append("")
    lines.append("*출처: SaveTicker 경제 캘린더. API 실패 시 일반 반복 일정을 오늘 일정처럼 표시하지 않습니다.*")
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
    summary_lines = collect_section_bullets(report, "오늘 요약", 5)
    market_lines = collect_section_bullets(report, "시장 개요", 4)
    news_lines = collect_section_bullets(report, "주요 뉴스", 3)
    signal_lines = collect_section_bullets(report, "참고 기술 신호", 3)

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
        "오늘 요약:",
    ]

    if summary_lines:
        lines.extend(summary_lines)
    else:
        lines.append("- 시황 요약 데이터 확인 불가")

    lines.extend([
        "",
        "핵심 지수:",
    ])

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
        ("오늘 요약", section_0_today_summary),
        ("시장 개요", section_1_market_overview),
        ("주요 뉴스 & 이슈", section_2_top_news),
        ("섹터별 시황", section_3_sector_performance),
        ("기술적 분석", section_4_technical_analysis),
        ("공포·탐욕 지수", section_5_fear_greed),
        ("보유종목 분석", section_6_portfolio_analysis),
        ("참고 기술 신호", section_7_buy_sell_signals),
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
