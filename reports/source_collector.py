#!/usr/bin/env python3
"""Collect stock-report source events into a daily JSONL cache."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

KST = timezone(timedelta(hours=9))
DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/reports/source-cache"))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}
ARCA_LABELS = ("🧠분석", "📰뉴스", "ℹ️정보", "실적")
# 보유 종목 — 단일 소스: portfolio_universe.py
_PROJECT_DIR = os.getenv("STOCK_REPORT_PROJECT_DIR",
                         os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from portfolio_universe import load_portfolio_tickers

PORTFOLIO_TICKERS = load_portfolio_tickers()
MARKET_TICKERS = {
    "QQQ": "Nasdaq 100 ETF",
    "SPY": "S&P 500 ETF",
    "DIA": "Dow Jones ETF",
    "VTI": "US total market ETF",
    "RSP": "S&P 500 equal-weight ETF",
    "IWM": "Russell 2000 ETF",
    "SMH": "Semiconductor ETF",
    "SOXX": "Semiconductor ETF",
    "IGV": "Software ETF",
    "XLK": "Technology ETF",
    "XLC": "Communication services ETF",
    "XLY": "Consumer discretionary ETF",
    "XLP": "Consumer staples ETF",
    "XLF": "Financials ETF",
    "XLV": "Health care ETF",
    "XLI": "Industrials ETF",
    "XLE": "Energy ETF",
    "XLU": "Utilities ETF",
    "XLB": "Materials ETF",
    "XLRE": "Real estate ETF",
    "EFA": "Developed ex-US ETF",
    "EEM": "Emerging markets ETF",
    "HYG": "High-yield bond ETF",
    "LQD": "Investment-grade bond ETF",
    "IEF": "7-10Y Treasury ETF",
    "TLT": "20Y Treasury ETF",
    "SHY": "1-3Y Treasury ETF",
    "GLD": "Gold ETF",
    "USO": "Oil ETF",
    "CL=F": "WTI crude oil futures",
    "BZ=F": "Brent crude oil futures",
    "UUP": "US Dollar ETF",
    "GC=F": "Gold futures",
    "SI=F": "Silver futures",
    "^VIX": "VIX volatility index",
    "^TNX": "10Y Treasury yield index",
    "^TYX": "30Y Treasury yield index",
    "KRW=X": "USD/KRW FX",
    **{ticker: f"Portfolio holding {ticker}" for ticker in PORTFOLIO_TICKERS},
}
FRED_SERIES = {
    "DGS5": "미국 5년 국채금리",
    "DGS10": "미국 10년 국채금리",
    "DGS20": "미국 20년 국채금리",
    "DGS30": "미국 30년 국채금리",
    "DGS2": "미국 2년 국채금리",
    "T10Y2Y": "미국 10Y-2Y 장단기 금리차",
    "SOFR": "SOFR 단기금리",
    "DFF": "Fed Funds 실효금리",
    "BAMLH0A0HYM2": "미국 하이일드 옵션조정 스프레드",
    "UNRATE": "미국 실업률",
    "CPIAUCSL": "미국 CPI 지수",
    "M2SL": "미국 M2 통화량",
}
WORLD_GOV_BOND_COUNTRIES = {
    "united-states": "미국 국채금리",
    "japan": "일본 국채금리",
    "south-korea": "한국 국채금리",
}
TELEGRAM_NEWS_CHANNELS = ["yuzukinaok1", "insidertracking"]
NEWS_THEME_KEYWORDS = {
    "중동/전쟁": ("이스라엘", "이란", "가자", "하마스", "우크라이나", "러시아", "전쟁", "군", "미사일", "핵"),
    "금리/채권": ("금리", "국채", "채권", "연준", "fed", "treasury", "yield"),
    "유가/원자재": ("유가", "오일", "원유", "석유", "브렌트", "wti", "금 ", "gold"),
    "인플레/고용": ("cpi", "물가", "인플레", "고용", "실업", "임금"),
    "기술/AI": ("ai", "엔비디아", "반도체", "칩", "데이터센터"),
    "정책/재정": ("재무장관", "세금", "관세", "예산", "부채", "재정"),
}


def event_id(event: dict) -> str:
    key = event.get("url") or f"{event.get('source', '')}:{event.get('title', '')}"
    return hashlib.sha256(str(key).strip().lower().encode("utf-8")).hexdigest()[:16]


def _event_file(cache_dir: Path, dt: datetime) -> Path:
    return cache_dir / f"events-{dt.astimezone(KST).strftime('%Y-%m-%d')}.jsonl"


def append_events(events: Iterable[dict], cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> int:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _event_file(cache_dir, now)

    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("id"))
            except json.JSONDecodeError:
                continue

    rows = []
    for event in events:
        row = dict(event)
        row.setdefault("source", "unknown")
        row.setdefault("title", "")
        row["id"] = event_id(row)
        if row["id"] in seen:
            continue
        row["collected_at"] = now.astimezone(KST).isoformat(timespec="seconds")
        seen.add(row["id"])
        rows.append(row)

    if rows:
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def load_recent_events(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None, hours: int = 24) -> list[dict]:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cutoff = now.astimezone(KST) - timedelta(hours=hours)
    events = []
    seen = set()

    for days_back in range((hours // 24) + 3):
        path = _event_file(cache_dir, now - timedelta(days=days_back))
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row.get("collected_at", ""))
            except Exception:
                continue
            row_id = row.get("id") or event_id(row)
            if ts < cutoff or row_id in seen:
                continue
            seen.add(row_id)
            events.append(row)

    return sorted(events, key=lambda e: e.get("collected_at", ""))


def build_digest(events: list[dict], limit: int = 12) -> str:
    if not events:
        return "## 누적 수집 자료\n\n- 최근 24시간 누적 캐시 없음\n"

    source_counts = Counter(e.get("source", "unknown") for e in events)
    ticker_counts = Counter(t for e in events for t in (e.get("tickers") or []))
    tag_counts = Counter(t for e in events for t in (e.get("tags") or []))
    trusted_sources = sorted({url for e in events for url in [e.get("source_url")] if isinstance(url, str) and url})
    lines = ["## 누적 수집 자료", ""]
    lines.append("- " + ", ".join(f"{src} {cnt}건" for src, cnt in source_counts.most_common()))
    if ticker_counts:
        lines.append("- 반복 등장 종목: " + ", ".join(f"{t} {c}건" for t, c in ticker_counts.most_common(8)))
    if tag_counts:
        lines.append("- 반복 테마: " + ", ".join(f"{t} {c}건" for t, c in tag_counts.most_common(8)))
    if trusted_sources:
        lines.append("- 신뢰 소스: " + ", ".join(trusted_sources[:6]))
    lines.append("")

    for event in sorted(events, key=lambda e: e.get("collected_at", ""), reverse=True)[:limit]:
        title = event.get("title") or "[제목 없음]"
        source = event.get("source", "unknown")
        url = event.get("url") or event.get("source_url") or ""
        tickers = ", ".join(event.get("tickers") or [])
        suffix = f" · {tickers}" if tickers else ""
        lines.append(f"- [{source}] {title}{suffix}" + (f" — {url}" if url else ""))
    return "\n".join(lines) + "\n"


def _extract_tickers(text: str, universe: Iterable[str] = PORTFOLIO_TICKERS) -> list[str]:
    upper = f" {text.upper()} "
    return [t for t in universe if f" {t.upper()} " in upper]


def _extract_news_tags(text: str) -> list[str]:
    lower = text.lower()
    return [theme for theme, words in NEWS_THEME_KEYWORDS.items() if any(word.lower() in lower for word in words)]


def fetch_saveticker_events() -> list[dict]:
    base = os.getenv("SAVE_TICKER_API_BASE", "https://saveticker.com/api").rstrip("/")
    paths = [
        ("news/top-stories", None),
        ("news/list", {"page": 1, "page_size": 30, "sort": "created_at_desc"}),
    ]
    events = []
    for path, params in paths:
        try:
            resp = requests.get(f"{base}/{path}", headers=HEADERS, params=params, timeout=12)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        for item in data.get("news_list") or data.get("data") or []:
            title = item.get("title") or ""
            if not title:
                continue
            text = " ".join(str(item.get(k) or "") for k in ("title", "content", "group_summary"))
            events.append({
                "source": "saveticker",
                "source_url": base,
                "title": title,
                "url": item.get("url") or item.get("link") or "",
                "published_at": item.get("created_at") or item.get("published_at") or "",
                "tickers": item.get("tickers") or _extract_tickers(text),
                "tags": item.get("tag_names") or [],
            })
    return events


def fetch_arca_events(max_pages: int = 2) -> list[dict]:
    events = []
    link_pat = re.compile(r"\[([^\]]+)\]\(https://arca\.live/b/stock/(\d+)\?p=(\d+)\)")
    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(f"https://r.jina.ai/http://arca.live/b/stock?p={page}", headers=HEADERS, timeout=20)
            resp.raise_for_status()
            markdown = resp.text
        except Exception:
            continue
        for match in link_pat.finditer(markdown):
            text = " ".join(match.group(1).split()).replace("**", "").strip()
            if not any(label in text for label in ARCA_LABELS):
                continue
            post_id = match.group(2)
            events.append({
                "source": "arca",
                "title": text[:140],
                "url": f"https://arca.live/b/stock/{post_id}",
                "source_url": "https://arca.live/b/stock",
                "category": next((label for label in ARCA_LABELS if label in text), ""),
                "tickers": _extract_tickers(text),
            })
    return events


def fetch_telegram_channel_events(channels: list[str] = TELEGRAM_NEWS_CHANNELS) -> list[dict]:
    events = []
    for channel in channels:
        channel = channel.strip().lstrip("@")
        if not channel:
            continue
        try:
            resp = requests.get(f"https://r.jina.ai/http://t.me/s/{channel}", headers=HEADERS, timeout=20)
            resp.raise_for_status()
            markdown = resp.text
        except Exception:
            continue

        titles = [" ".join(m.group(1).split()) for m in re.finditer(r"\*\*([^*]+)\*\*", markdown)]
        urls = re.findall(rf"https://t\.me/{re.escape(channel)}/\d+", markdown)
        for idx, title in enumerate(titles):
            if not title:
                continue
            # 이모지·기호 단독 항목 제외 (실제 글자 4자 미만)
            if len(re.sub(r"[^\w가-힣]", "", title)) < 4:
                continue
            url = urls[idx] if idx < len(urls) else ""
            events.append({
                "source": f"telegram:{channel}",
                "source_url": f"https://t.me/s/{channel}",
                "title": title[:180],
                "url": url,
                "tickers": _extract_tickers(title),
                "tags": _extract_news_tags(title),
            })
    return events


def _pct(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return round((current - base) / base * 100, 2)


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def fetch_market_snapshot_events(yf_module=None) -> list[dict]:
    """Collect compact Yahoo Finance market snapshots for low-token advisor grounding."""
    if yf_module is None:
        try:
            import yfinance as yf_module
        except Exception:
            return []

    events = []
    for ticker, label in MARKET_TICKERS.items():
        try:
            hist = yf_module.Ticker(ticker).history(period="1y", auto_adjust=True)
            if hist.empty:
                continue
            close = hist["Close"].dropna()
            if close.empty:
                continue
            current = float(close.iloc[-1])
            day_base = float(close.iloc[-2]) if len(close) >= 2 else None
            week_base = float(close.iloc[-6]) if len(close) >= 6 else None
            month_base = float(close.iloc[-22]) if len(close) >= 22 else None
            year_base = float(close.iloc[0]) if len(close) >= 2 else None
        except Exception:
            continue

        title = (
            f"{ticker} {label}: 현재 {current:.2f}, "
            f"1D {_fmt_pct(_pct(current, day_base))}, "
            f"5D {_fmt_pct(_pct(current, week_base))}, "
            f"1M {_fmt_pct(_pct(current, month_base))}, "
            f"1Y {_fmt_pct(_pct(current, year_base))}"
        )
        events.append({
            "source": "yahoo_finance",
            "source_url": "https://finance.yahoo.com",
            "type": "market_snapshot",
            "title": title,
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "tickers": [ticker] if ticker.isalpha() else [],
            "metrics": {
                "current": round(current, 4),
                "return_1d_pct": _pct(current, day_base),
                "return_5d_pct": _pct(current, week_base),
                "return_1m_pct": _pct(current, month_base),
                "return_1y_pct": _pct(current, year_base),
            },
        })
    return events


def fetch_fred_macro_events(series: dict[str, str] = FRED_SERIES) -> list[dict]:
    """Collect widely used US macro series from FRED public CSV endpoints."""
    events = []
    for series_id, label in series.items():
        try:
            resp = requests.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                headers=HEADERS,
                params={"id": series_id},
                timeout=12,
            )
            resp.raise_for_status()
            rows = list(csv.DictReader(resp.text.splitlines()))
        except Exception:
            continue

        latest = None
        previous = None
        for row in rows:
            value = row.get(series_id)
            if not value or value == ".":
                continue
            previous = latest
            latest = (row.get("observation_date", ""), value)
        if not latest:
            continue

        try:
            current = float(latest[1])
            prior = float(previous[1]) if previous else None
        except (TypeError, ValueError):
            continue

        delta = None if prior is None else round(current - prior, 4)
        delta_text = "N/A" if delta is None else f"{delta:+.2f}p"
        title = f"{series_id} {label}: {latest[0]} {current:.2f}, 직전 대비 {delta_text}"
        events.append({
            "source": "fred",
            "source_url": "https://fred.stlouisfed.org",
            "type": "macro_snapshot",
            "title": title,
            "url": f"https://fred.stlouisfed.org/series/{series_id}",
            "tickers": [],
            "metrics": {"series_id": series_id, "current": current, "delta": delta},
        })
    return events


def _parse_yields_from_world_gov_bonds(markdown: str, maturities: tuple[int, ...] = (5, 10, 20, 30)) -> dict[str, float]:
    yields = {}
    for maturity in maturities:
        match = re.search(rf"\|\s*\[({maturity}) years\]\([^)]*\)\s*\|\s*([0-9.]+)%", markdown)
        if match:
            yields[f"{maturity}Y"] = float(match.group(2))
    return yields


def fetch_world_gov_bond_events(countries: dict[str, str] = WORLD_GOV_BOND_COUNTRIES) -> list[dict]:
    events = []
    for country, label in countries.items():
        try:
            resp = requests.get(f"https://r.jina.ai/http://www.worldgovernmentbonds.com/country/{country}/", headers=HEADERS, timeout=20)
            resp.raise_for_status()
            yields = _parse_yields_from_world_gov_bonds(resp.text)
        except Exception:
            continue
        for maturity, value in yields.items():
            events.append({
                "source": "worldgovernmentbonds",
                "source_url": "https://www.worldgovernmentbonds.com",
                "type": "macro_snapshot",
                "title": f"{label} {maturity}: {value:.3f}%",
                "url": f"https://www.worldgovernmentbonds.com/country/{country}/#{maturity}",
                "tickers": [],
                "tags": ["금리/채권"],
                "metrics": {"country": country, "maturity": maturity, "yield_pct": value},
            })
    return events


def collect_once(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> tuple[int, int]:
    events = (
        fetch_saveticker_events()
        + fetch_arca_events(max_pages=int(os.getenv("STOCK_COLLECTOR_ARCA_PAGES", "2")))
        + fetch_telegram_channel_events()
        + fetch_market_snapshot_events()
        + fetch_fred_macro_events()
        + fetch_world_gov_bond_events()
    )
    return len(events), append_events(events, cache_dir=cache_dir, now=now)


def prune_old(cache_dir: Path | str = DEFAULT_CACHE_DIR, days: int = 14, now: datetime | None = None) -> int:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    cutoff = now.astimezone(KST).date() - timedelta(days=days)
    removed = 0
    for path in cache_dir.glob("events-*.jsonl"):
        try:
            day = datetime.strptime(path.stem.replace("events-", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--digest", action="store_true")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args()

    if args.digest:
        print(build_digest(load_recent_events(args.cache_dir, hours=args.hours)))
        return 0

    fetched, written = collect_once(args.cache_dir)
    removed = prune_old(args.cache_dir)
    print(f"stock source collector: fetched={fetched} new={written} pruned={removed} cache={args.cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
