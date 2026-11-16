#!/usr/bin/env python3
"""Collect stock-report source events into a daily JSONL cache."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_mod
import json
import logging
import os
import sys
import re
import time as time_mod
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/reports/source-cache"))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}
# 정직 UA — FRED fredgraph·r.jina.ai 는 브라우저 위장 UA 를 봇 판정(타르핏/403), 평범한 UA 는 통과
# (2026-07-07 라이브 실증: FRED 위장 UA=12s 타임아웃·정직 UA=0.3s 200, jina 위장 UA=403·정직 UA=200).
# 1차 경로가 살아나면 매 실행 12시리즈×2회×12s 타임아웃 낭비 없이 즉시 수집 — API 키 폴백은 유지.
PLAIN_HEADERS = {"User-Agent": "stock-report/1.0 (+yoobinkim2006@gmail.com)"}
# 보유 종목 — 단일 소스: portfolio_universe.py
_PROJECT_DIR = os.getenv("STOCK_REPORT_PROJECT_DIR",
                         os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from portfolio_universe import load_portfolio_tickers
from reports import source_publishers

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
# 뉴스 텔레그램 채널 — env 로 교체 가능(죽은 채널 무배포 교체): STOCK_COLLECTOR_TG_CHANNELS=a,b
# 기본 채널과 발행자·관점(증권사/커뮤니티)은 reports/source_publishers.py 한 곳에서 정한다.
_DEFAULT_TELEGRAM_CHANNELS = tuple(source_publishers.TELEGRAM_CHANNELS)
TELEGRAM_NEWS_CHANNELS = [c.strip().lstrip("@") for c in os.getenv(
    "STOCK_COLLECTOR_TG_CHANNELS", ",".join(_DEFAULT_TELEGRAM_CHANNELS)).split(",") if c.strip()]

# 공개 RSS만. 로그인·페이월 우회 없음. 피드 하나가 죽어도 나머지는 수집한다.
NEWS_RSS_FEEDS = (
    ("fed", "https://www.federalreserve.gov/feeds/press_all.xml", "연준"),
    ("cnbc", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "CNBC"),
    ("mk", "https://www.mk.co.kr/rss/30000001/", "매일경제"),
    ("hankyung", "https://www.hankyung.com/feed/finance", "한국경제"),
    ("yonhap", "https://www.yna.co.kr/rss/economy.xml", "연합뉴스"),
    ("sec", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=15&output=atom", "SEC 8-K"),
)
# Official directory: https://kr.investing.com/webmaster-tools/rss
# Separate provider budget: two additional feeds must not starve the existing RSS group.
INVESTING_RSS_FEEDS = (
    ("investing_kr_stock", "https://kr.investing.com/rss/news_25.rss", "Investing.com 주식"),
    ("investing_kr_economy", "https://kr.investing.com/rss/news_14.rss", "Investing.com 경제"),
)
# 주제 낱말. 영문·숫자 낱말은 낱말 경계에서만 맞춘다("ai" 가 "said" 에 걸리지 않게).
# "=" 로 시작하는 한글 낱말은 앞 글자가 한글이면 버린다("~이란" 조사, "개구리").
# "핵"·"군"·"가자"·"전쟁"·"칩"처럼 다른 말에 묻히는 낱말은 넣지 않는다.
NEWS_THEME_KEYWORDS = {
    "중동/전쟁": ("이스라엘", "=이란", "가자지구", "하마스", "헤즈볼라", "후티", "호르무즈", "우크라이나",
              "러시아", "휴전", "공습", "미사일", "핵무기", "핵협상", "핵시설", "지정학",
              "israel", "iran", "gaza", "ukraine", "russia", "ceasefire", "geopolitical"),
    "금리/채권": ("금리", "국채", "채권", "연준", "fomc", "fed", "treasury", "yield", "yields"),
    "유가/원자재": ("유가", "원유", "석유", "브렌트", "천연가스", "금값", "금 가격", "=구리", "원자재",
               "wti", "opec", "gold", "crude", "oil", "copper"),
    "인플레/고용": ("물가", "인플레", "고용", "실업", "임금", "cpi", "pce", "ppi", "inflation", "payrolls", "jobless"),
    "기술/AI": ("인공지능", "엔비디아", "반도체", "데이터센터", "ai", "gpu", "hbm", "nvidia", "semiconductor",
              "semiconductors"),
    "정책/재정": ("재무장관", "세금", "관세", "예산", "재정", "셧다운", "부채한도", "tariff", "tariffs", "shutdown"),
}


def _theme_pattern(word: str) -> re.Pattern:
    bound = word.startswith("=")
    word = word.lstrip("=").lower()
    if re.fullmatch(r"[a-z0-9 .&-]+", word):
        return re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")
    return re.compile((r"(?<![가-힣])" if bound else "") + re.escape(word))


_THEME_PATTERNS = {theme: tuple(_theme_pattern(word) for word in words)
                   for theme, words in NEWS_THEME_KEYWORDS.items()}


def _theme_hits(text: str) -> dict[str, int]:
    """주제별로 본문에 나온 서로 다른 낱말 수. 0 인 주제는 뺀다."""
    lower = str(text or "").lower()
    hits = {theme: sum(1 for pattern in patterns if pattern.search(lower))
            for theme, patterns in _THEME_PATTERNS.items()}
    return {theme: count for theme, count in hits.items() if count}

SOURCE_CLASSIFICATION = {
    "saveticker": {"family": "news", "kind": "article", "trust": "B", "horizon": "1d"},
    "yahoo_headline": {"family": "news", "kind": "headline", "trust": "B", "horizon": "1d"},
    "saveticker_report_pdf": {"family": "report", "kind": "report", "trust": "B", "horizon": "1w"},
    "arca": {"family": "community", "kind": "community_signal", "trust": "C", "horizon": "intraday"},
    "telegram": {"family": "community", "kind": "community_signal", "trust": "C", "horizon": "intraday"},
    # 증권사가 운영하는 채널(source_publishers 의 sell_side)의 글과, 그 글에 걸린 공개 리포트 PDF
    "telegram_sell_side": {"family": "research", "kind": "broker_comment", "trust": "B", "horizon": "1w"},
    "broker_report": {"family": "research", "kind": "report", "trust": "B", "horizon": "1w"},
    "rss": {"family": "news", "kind": "headline", "trust": "B", "horizon": "1d"},
    "polymarket": {"family": "prediction_market", "kind": "prediction_market", "trust": "B", "horizon": "intraday"},
    "kalshi": {"family": "prediction_market", "kind": "prediction_market", "trust": "B", "horizon": "intraday"},
    "economic_calendar": {"family": "macro_data", "kind": "economic_calendar", "trust": "B", "horizon": "1w"},
    "yahoo_finance": {"family": "market_data", "kind": "snapshot", "trust": "A", "horizon": "intraday"},
    "fred": {"family": "macro_data", "kind": "macro_snapshot", "trust": "A", "horizon": "1d"},
    "worldgovernmentbonds": {"family": "macro_data", "kind": "macro_snapshot", "trust": "A", "horizon": "1d"},
}
ARCA_KIND_MAP = {"🧠분석": "analysis", "📰뉴스": "news", "ℹ️정보": "info", "실적": "earnings"}
TELEGRAM_KIND_MAP = {"reddit_analysis": "analysis", "breaking": "breaking", "premarket": "premarket"}
POLYMARKET_API_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_DEFAULT_KEYWORDS = [
    "fed", "fomc", "rate", "rates", "cpi", "inflation", "recession", "tariff",
    "trump", "oil", "bitcoin", "btc", "ethereum", "nvidia", "ai", "semiconductor",
    "china", "taiwan", "russia", "ukraine", "israel", "iran",
]


class PolymarketUnavailable(RuntimeError):
    def __init__(self, reason: str, *, availability: str = "error", status_code: int | None = None):
        super().__init__(reason)
        self.availability = availability
        self.status_code = status_code


def _source_root(source: str) -> str:
    return str(source or "").strip().lower().split(":", 1)[0]


def _normalize_labels(values) -> list[str]:
    out: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            label = value
        elif isinstance(value, dict):
            label = value.get("name") or value.get("label") or value.get("symbol") or value.get("kind") or value.get("topic") or ""
        else:
            label = ""
        label = str(label).strip()
        if label:
            out.append(label)
    return out


TOPIC_ROUNDUP = "시황/종합"


def _topic_from_text(text: str) -> str:
    """가장 많은 낱말이 맞은 주제. 앞머리(제목)에 나온 낱말은 세 번으로 센다. 동률이면 표의 순서.

    네 주제 이상에 걸치고 1위가 2위의 두 배가 못 되는 글은 한 주제의 글이 아니라 시황 정리다.
    """
    hits = _theme_hits(text)
    if not hits:
        return ""
    lead = _theme_hits(str(text or "")[:200])
    score = {theme: count + 2 * lead.get(theme, 0) for theme, count in hits.items()}
    ranked = sorted(score, key=lambda theme: -score[theme])
    if len(ranked) >= 4 and score[ranked[0]] < 2 * score[ranked[1]]:
        return TOPIC_ROUNDUP
    return ranked[0]


def _classify_event(event: dict) -> dict:
    def _clip(value: object, limit: int = 240) -> str:
        text = str(value or "").replace("\x00", " ").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    row = dict(event or {})
    incoming_classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    source = str(row.get("source") or "unknown").strip() or "unknown"
    root = _source_root(source)
    publisher = source_publishers.publisher(source)
    sell_side = root == "telegram" and publisher["role"] == "sell_side"
    profile = SOURCE_CLASSIFICATION.get("telegram_sell_side" if sell_side else root,
                                        {"family": "other", "kind": "event", "trust": "C", "horizon": "1d"})

    title = _clip(row.get("title") or "", 260)
    body = _clip(row.get("body") or "", 4000)
    body_raw = _clip(row.get("body_raw") or "", 4000)
    excerpt = _clip(row.get("body_excerpt") or "", 500)
    category = _clip(row.get("category") or "", 60)
    scan_text = " ".join(part for part in (title, body_raw, body, excerpt, category) if part).strip()

    labels = _normalize_labels(row.get("tags"))
    if category:
        labels.append(category)

    kind = str(row.get("kind") or row.get("type") or profile["kind"] or "event").strip() or profile["kind"]
    if root in {"saveticker_report_pdf", "broker_report"}:
        kind = "report"
    elif sell_side:
        kind = "broker_comment"
    elif root == "arca":
        kind = ARCA_KIND_MAP.get(category, "community_signal")
    elif root == "telegram":
        try:
            from reports.social_sentiment import classify_post
            kind = TELEGRAM_KIND_MAP.get(classify_post(scan_text), "community_signal")
        except Exception:
            kind = "community_signal"
    elif root == "polymarket":
        kind = "prediction_market"
    elif root in {"yahoo_finance", "fred", "worldgovernmentbonds"}:
        kind = str(row.get("type") or kind or profile["kind"]).strip() or profile["kind"]
    elif root == "saveticker":
        if any(label.lower() in {"analysis", "report"} for label in labels):
            kind = "analysis"

    topic = _topic_from_text(" ".join([scan_text, " ".join(labels)]))
    if root == "polymarket" and labels:
        topic = labels[0]
    if not topic:
        if sell_side or root == "broker_report":
            topic = "증권사 리서치"
        elif root.startswith("telegram"):
            topic = "텔레그램"
        elif root == "rss":
            topic = labels[0] if labels else "뉴스"
        elif root == "arca":
            topic = category or "아카"
        elif root == "polymarket":
            topic = "예측시장"
        elif root.startswith("saveticker"):
            topic = "SaveTicker"
        elif root == "yahoo_finance":
            topic = "시장데이터"
        elif root in {"fred", "worldgovernmentbonds"}:
            topic = "금리/거시"
        else:
            topic = root

    text_len = len(scan_text)
    wiki_eligible = False
    if root == "saveticker_report_pdf":
        wiki_eligible = True
    elif root == "saveticker":
        wiki_eligible = kind in {"analysis", "report"} or text_len >= 700
    elif root == "arca":
        wiki_eligible = kind in {"analysis", "earnings"} or text_len >= 800
    elif sell_side:
        wiki_eligible = text_len >= 300
    elif root == "telegram":
        wiki_eligible = kind == "analysis" or text_len >= 900
    elif root == "polymarket":
        wiki_eligible = True

    article_discovery_eligible = profile["family"] == "news" and kind in {"article", "analysis"}
    if incoming_classification.get("article_discovery_eligible") is False:
        article_discovery_eligible = False

    trust = profile["trust"]
    confidence_map = {"A": 0.92, "B": 0.8, "C": 0.62, "D": 0.45}
    confidence = confidence_map.get(trust, 0.55)

    classification = {
        "source_family": profile["family"],
        "kind": kind,
        "topic": topic,
        "trust": trust,
        "horizon": profile["horizon"],
        "wiki_eligible": wiki_eligible,
        "article_discovery_eligible": article_discovery_eligible,
        "confidence": confidence,
        "labels": labels[:8],
        "publisher": publisher["name"],
        "perspective": publisher["role"],
    }
    row["classification"] = classification
    row["source_family"] = classification["source_family"]
    row["topic"] = classification["topic"]
    row["trust"] = classification["trust"]
    row["wiki_eligible"] = classification["wiki_eligible"]
    row["article_discovery_eligible"] = classification["article_discovery_eligible"]
    row["horizon"] = classification["horizon"]
    return row


class _BoundedResponse:
    """크기 제한 읽기를 마친 응답 래퍼 (.text/.json/.raise_for_status 호환)."""
    def __init__(self, content: bytes, encoding):
        self._content = content
        self._encoding = encoding or "utf-8"

    @property
    def text(self) -> str:
        return self._content.decode(self._encoding, errors="replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        return None


# 소스별 마지막 오류 (update_source_health 가 헬스 파일에 기록 → 경보에 원인 표시)
_LAST_ERRORS: dict[str, str] = {}
# append_events keeps the public integer return value for compatibility while
# exposing per-source write accounting to update_source_health in this process.
_LAST_APPEND_STATS: dict[str, dict] = {}
# 재시도로 해결되지 않는 운영 상태. 일반 수집 오류와 분리해 헬스 경보가
# 법적 지역 제한을 장애로 반복 보고하지 않게 한다.
_SOURCE_AVAILABILITY: dict[str, dict[str, str]] = {}


class SourceProviderUnavailable(RuntimeError):
    """이미 분류된 수집 중단. availability 는 헬스·워커 JSON 으로 전달된다."""

    def __init__(self, message: str, *, availability: str = "unavailable", status_code: int | None = None):
        super().__init__(message)
        self.availability = availability
        self.status_code = status_code


def _note_error(source: str, err) -> None:
    _LAST_ERRORS[source] = str(err)[:200]


def _bounded_get(url: str, *, timeout: int = 20, max_bytes: int = 5_000_000, **kwargs):
    """응답 크기 상한이 있는 requests.get — 외부 프록시(r.jina.ai 등)의 과대 응답으로 인한
    메모리 고갈(DoS)을 방어한다. 본문을 청크로 읽어 max_bytes 초과 시 즉시 중단."""
    kwargs.setdefault("headers", HEADERS)
    if url.startswith("https://r.jina.ai/"):
        # jina 는 브라우저 위장 UA 에 403 (라이브 실증) — 정직 UA 로 교체
        kwargs["headers"] = {**kwargs["headers"], **PLAIN_HEADERS}
        # 익명 레이트리밋이 빡빡 — JINA_API_KEY 있으면 인증(쿼터 상향·429 완화)
        if os.getenv("JINA_API_KEY"):
            kwargs["headers"]["Authorization"] = f"Bearer {os.getenv('JINA_API_KEY')}"
    with requests.get(url, timeout=timeout, stream=True, **kwargs) as r:
        r.raise_for_status()
        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            raise ValueError(f"응답 과대(Content-Length={cl} > {max_bytes})")
        total, chunks = 0, []
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"응답 과대(>{max_bytes}B) — {url[:60]}")
            chunks.append(chunk)
        return _BoundedResponse(b"".join(chunks), r.encoding)








def event_id(event: dict) -> str:
    from reports.source_identity import legacy_content_id

    return legacy_content_id(event)


def _event_file(cache_dir: Path, dt: datetime) -> Path:
    return cache_dir / f"events-{dt.astimezone(KST).strftime('%Y-%m-%d')}.jsonl"


def append_events(events: Iterable[dict], cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> int:
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _event_file(cache_dir, now)

    import safe_io
    from reports.source_identity import normalize_event_identity

    with safe_io.file_write_lock(str(path)):
        seen: dict[str, tuple[str, str, str, str]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    saved = json.loads(line)
                    saved_id = saved.get("id")
                    if saved_id:
                        seen[str(saved_id)] = (
                            str(saved.get("record_kind") or ""),
                            str(saved.get("entity_id") or ""),
                            str(saved.get("observation_bucket") or ""),
                            str(saved.get("content_id") or ""),
                        )
                except json.JSONDecodeError:
                    continue

        rows = []
        run_stats: dict[str, dict] = {}
        for event in events:
            row = dict(event)
            source = str(row.get("source") or "unknown")
            row.setdefault("source", source)
            row.setdefault("title", "")
            row["collected_at"] = now.astimezone(KST).isoformat(timespec="seconds")
            row = normalize_event_identity(row, now)
            stats = run_stats.setdefault(source, {
                "run_at": now.astimezone(KST).isoformat(timespec="seconds"),
                "fetched": 0,
                "persisted": 0,
                "deduped": 0,
                "collisions": 0,
                "persist_success": True,
            })
            stats["fetched"] += 1
            row_id = str(row["id"])
            identity = (
                str(row.get("record_kind") or ""),
                str(row.get("entity_id") or ""),
                str(row.get("observation_bucket") or ""),
                str(row.get("content_id") or ""),
            )
            previous_identity = seen.get(row_id)
            if previous_identity is not None:
                # Older cache rows may only have the id. Keep their historical
                # dedupe behavior; only two populated, different identities
                # are a collision.
                if previous_identity == identity or not any(previous_identity) or not any(identity):
                    stats["deduped"] += 1
                else:
                    stats["collisions"] += 1
                continue
            seen[row_id] = identity
            rows.append(row)
            stats["persisted"] += 1

        if rows:
            with path.open("a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        _LAST_APPEND_STATS.update(run_stats)
        return len(rows)


def _iter_recent_jsonl_rows(path: Path, cutoff: datetime, *, chunk_size: int = 1024 * 1024):
    """Yield recent JSONL rows from the file tail without loading the whole file.

    Event files are append-only and ``collected_at`` is assigned immediately before
    append. Walking backwards therefore lets recent, bounded callers stop after the
    requested number of rows instead of reading hundreds of megabytes of history.
    """
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        pending = b""
        while position > 0:
            start = max(0, position - chunk_size)
            stream.seek(start)
            pending = stream.read(position - start) + pending
            parts = pending.split(b"\n")
            if start:
                pending = parts[0]
                complete = parts[1:]
            else:
                pending = b""
                complete = parts

            for raw in reversed(complete):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                    ts = datetime.fromisoformat(row.get("collected_at", ""))
                except (AttributeError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if ts < cutoff:
                    return
                yield row
            position = start


def load_recent_events(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None,
                       hours: int = 24, limit: int | None = None) -> list[dict]:
    """Load recent events, optionally stopping after ``limit`` newest unique rows.

    A bounded lookup is served from the tail of each append-only daily file. The
    unbounded form keeps the historical behavior needed by report generation.
    """
    now = now or datetime.now(KST)
    cache_dir = Path(cache_dir)
    cutoff = now.astimezone(KST) - timedelta(hours=hours)
    events = []
    seen = set()
    max_rows = None if limit is None else max(0, int(limit))
    if max_rows == 0:
        return []

    for days_back in range((hours // 24) + 3):
        path = _event_file(cache_dir, now - timedelta(days=days_back))
        if not path.exists():
            continue
        rows = _iter_recent_jsonl_rows(path, cutoff)
        try:
            for row in rows:
                row_id = row.get("id") or event_id(row)
                if row_id in seen:
                    continue
                seen.add(row_id)
                events.append(row)
                if max_rows is not None and len(events) >= max_rows:
                    break
        finally:
            rows.close()
        if max_rows is not None and len(events) >= max_rows:
            break

    return sorted(events, key=lambda e: e.get("collected_at", ""))


def _normalize_symbols(values) -> list[str]:
    symbols = []
    for value in values or []:
        if isinstance(value, str):
            symbol = value
        elif isinstance(value, dict):
            symbol = value.get("symbol") or value.get("name") or ""
        else:
            symbol = ""
        symbol = str(symbol).strip()
        if symbol:
            symbols.append(symbol)
    return symbols


def build_digest(events: list[dict], limit: int = 12) -> str:
    if not events:
        return "## 누적 수집 자료\n\n- 최근 24시간 누적 캐시 없음\n"

    source_counts = Counter(e.get("source", "unknown") for e in events)
    ticker_counts = Counter(t for e in events for t in _normalize_symbols(e.get("tickers")))
    tag_counts = Counter(t for e in events for t in _normalize_symbols(e.get("tags")))
    kind_counts = Counter((e.get("classification") or {}).get("kind") for e in events if (e.get("classification") or {}).get("kind"))
    topic_counts = Counter((e.get("classification") or {}).get("topic") for e in events if (e.get("classification") or {}).get("topic"))
    trust_counts = Counter((e.get("classification") or {}).get("trust") for e in events if (e.get("classification") or {}).get("trust"))
    trusted_sources = sorted({url for e in events for url in [e.get("source_url")] if isinstance(url, str) and url})
    lines = ["## 누적 수집 자료", ""]
    lines.append("- " + ", ".join(f"{src} {cnt}건" for src, cnt in source_counts.most_common()))
    if ticker_counts:
        lines.append("- 반복 등장 종목: " + ", ".join(f"{t} {c}건" for t, c in ticker_counts.most_common(8)))
    if tag_counts:
        lines.append("- 반복 테마: " + ", ".join(f"{t} {c}건" for t, c in tag_counts.most_common(8)))
    if kind_counts:
        lines.append("- 반복 분류: " + ", ".join(f"{t} {c}건" for t, c in kind_counts.most_common(8)))
    if topic_counts:
        lines.append("- 반복 주제: " + ", ".join(f"{t} {c}건" for t, c in topic_counts.most_common(8)))
    if trust_counts:
        lines.append("- 신뢰 등급: " + ", ".join(f"{t} {c}건" for t, c in trust_counts.most_common(8)))
    if trusted_sources:
        lines.append("- 신뢰 소스: " + ", ".join(trusted_sources[:6]))
    # 레딧/WSB 심리 한 줄 (insidertracking 분석 포스트 구조화 — 있으면)
    try:
        from reports.social_sentiment import digest_line, sentiment_summary
        line = digest_line(sentiment_summary(events))
        if line:
            lines.append(f"- {line}")
    except Exception:
        pass
    lines.append("")

    for event in sorted(events, key=lambda e: e.get("collected_at", ""), reverse=True)[:limit]:
        title = event.get("title") or "[제목 없음]"
        source = event.get("source", "unknown")
        url = event.get("url") or event.get("source_url") or ""
        tickers = ", ".join(_normalize_symbols(event.get("tickers")))
        suffix = f" · {tickers}" if tickers else ""
        lines.append(f"- [{source}] {title}{suffix}" + (f" — {url}" if url else ""))
    return "\n".join(lines) + "\n"


def _extract_tickers(text: str, universe: Iterable[str] = PORTFOLIO_TICKERS) -> list[str]:
    upper = f" {text.upper()} "
    return [t for t in universe if f" {t.upper()} " in upper]


# 본문에서 종목보다 다른 뜻(업종·지수·일반 약어)으로 더 자주 쓰이는 대문자 약어.
# 회사 이름, `$IT`, `(IT US)` 표기로 나오면 그대로 잡힌다.
_PROSE_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z0-9$])(?:IT|SW|ESS|MSCI|IR|MTD|IP|KEY|GE|MS|ON|ALL|NOW|ES|PM|HD|ICE|FAST|LOW|WELL|APP|DD|CME)"
    r"(?![A-Za-z0-9])"
)
_INDEX_PROXIES = frozenset({"SPY", "QQQ", "IWM", "DIA"})
# 주소·채널 핸들과 금액 단위("$MLN")는 종목 표기가 아니다.
_NOT_MENTION_RE = re.compile(r"https?://\S+|@\w+|\$\s?(?:MLN|BLN|BN|TRN|MN|MM)(?![A-Za-z])")


def _mention_tickers(text: str, limit: int = 12) -> list[str]:
    """글이 언급한 종목. 보유 종목 티커가 먼저, 이어 종목 사전의 이름·티커·거래소 표기("네비우스(NBIS US)").

    지수·금리·환율·원자재 같은 시세 심볼과, 지수 이름이 ETF 로 바뀐 것("나스닥" → QQQ)은 종목 언급이
    아니므로 뺀다. 6자리 숫자는 사전에 있는 종목 코드이거나 `.KS`/`KS` 가 붙었을 때만 종목으로 본다.
    """
    import ticker_names

    found = _extract_tickers(text)
    clean = _PROSE_ACRONYM_RE.sub(" ", _NOT_MENTION_RE.sub(" ", str(text or "")))
    for ticker in ticker_names.mentions_in(clean, limit=limit * 3):
        if ticker in found or ticker_names.is_macro(ticker) or re.search(r"[=^]", ticker):
            continue
        if ticker in _INDEX_PROXIES and not re.search(rf"(?<![A-Za-z0-9]){ticker}(?![A-Za-z0-9])", clean):
            continue
        code = ticker.partition(".")[0]
        if (code.isdigit() and ticker_names.display_name(ticker, allow_net=False) is None
                and not re.search(rf"{code}[ .](?:KS|KQ)(?![A-Za-z])", clean)):
            continue
        found.append(ticker)
    return found[:limit]


def _extract_news_tags(text: str) -> list[str]:
    return list(_theme_hits(text))


YAHOO_HEADLINE_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"


def _yahoo_headline_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
        return ""
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    if suffix in {"KS", "KQ", "T", "HK", "L"}:
        return ""
    return symbol


def yahoo_headline_symbols(tickers=None) -> list[str]:
    """미국 상장 심볼만. 환경변수 목록이 있으면 보유 종목보다 우선한다."""
    raw = os.getenv("STOCK_COLLECTOR_YAHOO_HEADLINE_SYMBOLS", "").strip()
    values = [part.strip() for part in raw.split(",") if part.strip()] if raw else (
        list(PORTFOLIO_TICKERS) if tickers is None else list(tickers)
    )
    out: list[str] = []
    for value in values:
        symbol = _yahoo_headline_symbol(value)
        if symbol and symbol not in out:
            out.append(symbol)
    try:
        cap = int(os.getenv("STOCK_COLLECTOR_YAHOO_HEADLINE_MAX", "8"))
    except (TypeError, ValueError):
        cap = 8
    return out[: max(1, min(cap, 40))]


def _rss_published_at(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).isoformat(timespec="seconds")


def fetch_yahoo_headline_events(symbols=None, *, get=None) -> list[dict]:
    """SaveTicker 뉴스 API 대체. 공개 Yahoo 헤드라인 RSS의 제목·링크·요약만 저장한다."""
    _SOURCE_AVAILABILITY.pop("yahoo_headline", None)
    chosen = yahoo_headline_symbols(symbols)
    if not chosen:
        reason = "Yahoo headline symbols empty"
        _SOURCE_AVAILABILITY["yahoo_headline"] = {
            "availability": "disabled",
            "availability_reason": reason,
        }
        raise SourceProviderUnavailable(reason, availability="disabled")
    getter = get
    events = []
    seen: dict[str, dict] = {}
    errors: list[str] = []
    for symbol in chosen:
        url = f"{YAHOO_HEADLINE_RSS}?s={symbol}&region=US&lang=en-US"
        try:
            if getter is None:
                resp = _bounded_get(url, timeout=5, headers=PLAIN_HEADERS)
                payload = getattr(resp, "_content", None)
                xml_text = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, bytes) else resp.text
            else:
                xml_text = getter(url, symbol)
            items = rss_items_from_xml(xml_text, limit=6)
        except Exception as exc:
            errors.append(f"{symbol} {type(exc).__name__}")
            _note_error("yahoo_headline", exc)
            continue
        if not items:
            errors.append(f"{symbol} empty")
            continue
        for item in items:
            key = str(item.get("url") or item.get("title") or "").strip().lower()
            if not key:
                continue
            existing = seen.get(key)
            if existing is not None:
                if symbol not in existing["tickers"]:
                    existing["tickers"].append(symbol)
                continue
            body = item.get("body") or ""
            event = {
                "source": "yahoo_headline",
                "source_url": url,
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "published_at": _rss_published_at(item.get("published_at") or ""),
                "body_raw": body,
                "body": body,
                "body_excerpt": body[:500],
                "tickers": [symbol],
                "tags": ["Yahoo"],
                "fulltext_status": "feed_blurb",
                "body_raw_source": "rss_description",
                "body_coverage": "partial",
                "text_complete": False,
            }
            seen[key] = event
            events.append(event)
    if not events:
        reason = "Yahoo headline RSS failed: " + "; ".join(errors[:3])
        _SOURCE_AVAILABILITY["yahoo_headline"] = {
            "availability": "error",
            "availability_reason": reason,
        }
        raise SourceProviderUnavailable(reason, availability="error")
    _LAST_ERRORS.pop("yahoo_headline", None)
    return [_classify_event(event) for event in events]




def _telegram_text_from_fragment(fragment: str) -> str:
    raw = re.sub(r"<br\s*/?>", " ", fragment or "", flags=re.I)
    txt = html_mod.unescape(re.sub(r"<[^>]+>", "", raw))
    return " ".join(txt.split()).strip()


def _telegram_card_extras(card, text_node) -> dict:
    """카드에 걸린 외부 링크와 첨부 요약.

    텔레그램에 직접 올라간 파일과 사진은 로그인 없이 받을 공개 주소가 없어 이름과 장수만 남긴다.
    """
    links: list[str] = []
    for anchor in text_node.find_all("a", href=True) if text_node is not None else []:
        href = str(anchor.get("href") or "").strip()
        host = (urlparse(href).hostname or "").lower()
        if href.startswith(("http://", "https://")) and host not in {"t.me", "telegram.me"} and href not in links:
            links.append(href)
    documents = [" ".join(node.get_text(" ").split())
                 for node in card.select(".tgme_widget_message_document_title")]
    extras: dict = {"links": links[:8]}
    attachments = {"documents": [name for name in documents if name][:5],
                   "photos": len(card.select(".tgme_widget_message_photo_wrap"))}
    if attachments["documents"] or attachments["photos"]:
        extras["attachments"] = attachments
    return extras


def _telegram_messages_from_html(html_text: str, channel: str) -> list[dict]:
    """t.me/s 공개 HTML을 메시지 카드 단위로 파싱한다.

    제목, 본문, URL이 같은 카드에서 나오도록 묶어 jina 제목 리스트와 직접 HTML
    본문 리스트가 인덱스로 어긋나는 문제를 막는다.
    """
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select(".tgme_widget_message")
    messages: list[dict] = []

    def _url_from(node) -> str:
        link = node.select_one("a.tgme_widget_message_date[href]") if hasattr(node, "select_one") else None
        if link and link.get("href"):
            return str(link.get("href"))
        for a in node.find_all("a", href=True) if hasattr(node, "find_all") else []:
            href = str(a.get("href") or "")
            if re.match(rf"https://t\.me/{re.escape(channel)}/\d+", href):
                return href
        return ""

    if cards:
        for card in cards:
            text_node = card.select_one(".tgme_widget_message_text")
            if not text_node:
                continue
            body_raw = _telegram_text_from_fragment(str(text_node))
            if not body_raw:
                continue
            stamp = card.select_one("a.tgme_widget_message_date time[datetime]")
            messages.append({
                "title": body_raw[:180],
                "url": _url_from(card),
                "published_at": str(stamp.get("datetime") or "") if stamp else "",
                "body_raw": body_raw[:TELEGRAM_BODY_MAX],
                "raw_html": str(card),
                **_telegram_card_extras(card, text_node),
            })
        return messages

    # 테스트/간소 HTML 폴백: text div와 뒤따르는 링크를 순서대로 매칭한다.
    titles = []
    raw_fragments = []
    for m in re.finditer(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', html_text, re.S):
        raw_fragments.append(m.group(0))
        txt = _telegram_text_from_fragment(m.group(1))
        if txt:
            titles.append(txt)
    urls = re.findall(rf'href="(https://t\.me/{re.escape(channel)}/\d+)"', html_text)
    out = []
    for idx, title in enumerate(titles):
        out.append({
            "title": title[:180],
            "url": urls[idx] if idx < len(urls) else "",
            "body_raw": title[:TELEGRAM_BODY_MAX],
            "raw_html": raw_fragments[idx] if idx < len(raw_fragments) else title,
        })
    return out


def _telegram_titles_from_html(html_text: str, channel: str) -> tuple[list[str], list[str]]:
    """t.me/s/<channel> 공개 HTML 에서 메시지 텍스트·링크 추출 (순수 — 테스트 가능)."""
    messages = _telegram_messages_from_html(html_text, channel)
    return [m.get("title", "") for m in messages], [m.get("url", "") for m in messages]


TELEGRAM_BODY_MAX = 3000    # 이벤트 body 상한 (레딧 분석 등 장문 구조화 파싱용)


def _telegram_direct_messages(channel: str) -> list[dict]:
    """t.me/s 직접 HTML — 메시지 카드 목록. 실패 시 []."""
    try:
        resp = _bounded_get(f"https://t.me/s/{channel}", timeout=15)
        return _telegram_messages_from_html(resp.text, channel)
    except Exception as e:
        logger.info("telegram:%s 직접 HTML 실패: %s", channel, e)
        return []


def _telegram_message_texts(channel: str) -> tuple[list[str], list[str]]:
    """t.me/s 직접 HTML — (전체 메시지 텍스트 목록, 링크 목록). 실패 시 ([], [])."""
    messages = _telegram_direct_messages(channel)
    return [m.get("body_raw", "") for m in messages], [m.get("url", "") for m in messages]


def _archive_telegram_message(channel: str, message: dict) -> dict:
    from reports.raw_archive import save_extracted_text, save_raw_artifact

    title = str(message.get("title") or message.get("body_raw") or f"telegram {channel}").strip()
    body_raw = str(message.get("body_raw") or title).strip()
    url = str(message.get("url") or f"https://t.me/s/{channel}").strip()
    raw_payload = str(message.get("raw_html") or body_raw)
    raw_record = save_raw_artifact(
        source=f"telegram:{channel}",
        kind="html",
        fetched_at=datetime.now(KST),
        title=title or f"telegram {channel}",
        url=url,
        payload=raw_payload,
        suffix=".html",
    )
    save_extracted_text(raw_record, body_raw)
    return {
        "raw_path": raw_record["raw_path"],
        "text_path": raw_record["text_path"],
        "manifest_path": raw_record["manifest_path"],
        "raw_sha256": raw_record["sha256"],
        "raw_source": raw_record["source"],
    }


def fetch_telegram_channel_events(channels: list[str] = TELEGRAM_NEWS_CHANNELS) -> list[dict]:
    events = []
    for channel in channels:
        channel = channel.strip().lstrip("@")
        if not channel:
            continue
        # 직접 HTML 카드가 canonical 경로다. 비었을 때만 jina 리더로 제목을 받아 이벤트화한다.
        messages = _telegram_direct_messages(channel)
        if not messages:
            try:
                resp = _bounded_get(f"https://r.jina.ai/http://t.me/s/{channel}", timeout=20)
                markdown = resp.text
                titles = [" ".join(m.group(1).split()) for m in re.finditer(r"\*\*([^*]+)\*\*", markdown)]
                urls = re.findall(rf"https://t\.me/{re.escape(channel)}/\d+", markdown)
                for idx, title in enumerate(titles):
                    messages.append({
                        "title": title[:180],
                        "url": urls[idx] if idx < len(urls) else "",
                        "body_raw": title[:TELEGRAM_BODY_MAX],
                        "raw_html": title,
                    })
            except Exception as e:
                logger.warning("telegram:%s jina 수집 실패: %s", channel, e)

        if not messages:
            logger.warning("telegram:%s 수집 0건 (jina·직접 모두) — 채널명/차단 확인 필요", channel)
            _note_error(f"telegram:{channel}", "jina·직접 HTML 모두 0건 — 채널명/차단 확인")
        else:
            _LAST_ERRORS.pop(f"telegram:{channel}", None)
        for message in messages:
            title = str(message.get("title") or "").strip()
            if not title:
                continue
            if len(re.sub(r"[^\w가-힣]", "", title)) < 4:
                continue
            body = str(message.get("body_raw") or title).strip()[:TELEGRAM_BODY_MAX]
            url = str(message.get("url") or "").strip()
            scan_text = body or title
            tags = _extract_news_tags(scan_text)
            try:
                from reports.social_sentiment import classify_post
                kind = {"reddit_analysis": "레딧분석", "breaking": "속보",
                        "premarket": "프리마켓"}.get(classify_post(scan_text))
                if kind and kind not in tags:
                    tags = tags + [kind]
            except Exception:
                pass
            archive = _archive_telegram_message(channel, {**message, "title": title, "body_raw": body, "url": url})
            events.append({
                "source": f"telegram:{channel}",
                "source_url": f"https://t.me/s/{channel}",
                "title": title[:180],
                "url": url,
                "body_raw": body,
                "body": body,
                "body_excerpt": body[:500],
                "tickers": _mention_tickers(scan_text),
                "tags": tags,
                **{key: message[key] for key in ("published_at", "links", "attachments") if message.get(key)},
                "raw_path": archive["raw_path"],
                "text_path": archive["text_path"],
                "manifest_path": archive["manifest_path"],
                "raw_sha256": archive["raw_sha256"],
                "raw_source": archive["raw_source"],
            })
    return [_classify_event(event) for event in events]


def _xml_local(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _xml_child_text(node, names: set[str]) -> str:
    for child in list(node):
        if _xml_local(child.tag) not in names:
            continue
        href = child.get("href") if hasattr(child, "get") else None
        if href:
            return str(href).strip()
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def rss_items_from_xml(xml_text: str, *, limit: int = 12) -> list[dict]:
    """RSS 2.0 item 과 Atom entry 를 {title, url, body, published_at} 로 푼다."""
    import xml.etree.ElementTree as ET

    text = (xml_text or "").lstrip("\ufeff")
    if text.startswith("ï»¿"):
        text = text[3:]
    text = text.strip()
    start = min((i for i in (text.find("<?xml"), text.find("<rss"), text.find("<feed")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    if not text:
        return []
    root = ET.fromstring(text)
    items = []
    for node in root.iter():
        kind = _xml_local(node.tag)
        if kind not in {"item", "entry"}:
            continue
        title = html_mod.unescape(re.sub(r"<[^>]+>", " ", _xml_child_text(node, {"title"})))
        title = " ".join(title.split())
        if not title:
            continue
        url = _xml_child_text(node, {"link"})
        body = html_mod.unescape(re.sub(
            r"<[^>]+>", " ", _xml_child_text(node, {"description", "summary", "content"})))
        body = " ".join(body.split())
        published = _xml_child_text(node, {"pubDate", "published", "updated", "date"})
        items.append({
            "title": title[:180],
            "url": url,
            "body": (body or title)[:1500],
            "published_at": published,
        })
        if len(items) >= limit:
            break
    return items


def fetch_news_rss_events(feeds=NEWS_RSS_FEEDS) -> list[dict]:
    """공개 통신·공시 RSS. 피드 실패는 그 피드만 남기고 나머지는 반환한다."""
    events = []
    errors = []
    for key, url, label in feeds:
        source = f"rss:{key}"
        try:
            resp = _bounded_get(url, timeout=8, headers=PLAIN_HEADERS)
            payload = getattr(resp, "_content", None)
            xml_text = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, bytes) else resp.text
            items = rss_items_from_xml(xml_text)
        except Exception as exc:
            errors.append(f"{source} {type(exc).__name__}")
            _note_error(source, exc)
            logger.warning("rss:%s 수집 실패: %s", key, exc)
            continue
        if not items:
            errors.append(f"{source} empty")
            _note_error(source, "RSS 항목 0건")
            continue
        _LAST_ERRORS.pop(source, None)
        for item in items:
            events.append({
                "source": source,
                "source_url": url,
                "title": item["title"],
                "url": item["url"],
                "published_at": _rss_published_at(item["published_at"]),
                "body_raw": item["body"],
                "body": item["body"],
                "body_excerpt": item["body"][:500],
                "tags": [label],
                "tickers": _mention_tickers(f"{item['title']} {item['body']}"),
                "fulltext_status": "feed_blurb",
                "body_raw_source": "rss_description",
                "body_coverage": "partial",
                "text_complete": False,
            })
    if errors and not events:
        raise RuntimeError("RSS fetch failed: " + "; ".join(errors[:4]))
    return [_classify_event(event) for event in events]


def fetch_investing_news_events() -> list[dict]:
    """Collect only the organizer-published Korean stock and economy RSS feeds."""
    return fetch_news_rss_events(INVESTING_RSS_FEEDS)


def _pct(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return round((current - base) / base * 100, 2)


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _batch_close_series(frame, ticker: str, ticker_count: int):
    if frame is None or getattr(frame, "empty", True):
        return None
    columns = getattr(frame, "columns", None)
    nlevels = int(getattr(columns, "nlevels", 1) or 1)
    try:
        if nlevels > 1:
            for key in (("Close", ticker), (ticker, "Close"), ("Adj Close", ticker), (ticker, "Adj Close")):
                if key in columns:
                    return frame[key].dropna()
            return None
        if ticker_count == 1:
            for key in ("Close", "Adj Close"):
                if key in columns:
                    return frame[key].dropna()
    except Exception:
        return None
    return None


def fetch_market_snapshot_events(yf_module=None) -> list[dict]:
    """Collect compact Yahoo Finance market snapshots for low-token advisor grounding."""
    if yf_module is None:
        try:
            import yfinance as yf_module
        except Exception:
            return []

    tickers = list(MARKET_TICKERS)
    batch = None
    if hasattr(yf_module, "download"):
        try:
            batch = yf_module.download(
                tickers,
                period="1y",
                auto_adjust=True,
                progress=False,
                group_by="column",
                threads=True,
            )
        except Exception as exc:
            logger.warning("Yahoo market snapshot batch failed; using symbol fallback: %s", exc)

    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    events = []
    for ticker, label in MARKET_TICKERS.items():
        close = _batch_close_series(batch, ticker, len(tickers))
        try:
            if close is None or close.empty:
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
            "record_kind": "observation",
            "entity_id": f"yahoo_finance:{ticker}",
            "observed_at": observed_at,
            "transport": "batch" if _batch_close_series(batch, ticker, len(tickers)) is not None else "fallback",
            "title": title,
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "tickers": [ticker] if ticker.isalpha() else [],
            "metrics": {
                "ticker": ticker,
                "current": round(current, 4),
                "return_1d_pct": _pct(current, day_base),
                "return_5d_pct": _pct(current, week_base),
                "return_1m_pct": _pct(current, month_base),
                "return_1y_pct": _pct(current, year_base),
            },
        })
    return [_classify_event(event) for event in events]


def _fred_api_latest(series_id: str):
    """FRED 공식 API 최근 2관측 — (latest, previous) 각 (date, value)|None. 키 없으면 (None, None).

    fredgraph.csv 가 클라우드 IP/봇 UA 를 차단할 때의 폴백. 키는 무료:
    https://fred.stlouisfed.org/docs/api/api_key.html → .env FRED_API_KEY
    """
    key = os.getenv("FRED_API_KEY")
    if not key:
        return None, None
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": key, "file_type": "json",
                    "sort_order": "desc", "limit": 5},
            headers=HEADERS, timeout=12)
        resp.raise_for_status()
        obs = [(o.get("date", ""), o.get("value"))
               for o in resp.json().get("observations", [])
               if o.get("value") not in (None, ".", "")]
        if not obs:
            return None, None
        latest = obs[0]
        previous = obs[1] if len(obs) > 1 else None
        return latest, previous
    except Exception as e:
        logger.warning("FRED API 폴백 실패 %s: %s", series_id, e)
        _note_error("fred", f"api: {e}")
        return None, None


def fetch_fred_macro_events(series: dict[str, str] = FRED_SERIES) -> list[dict]:
    """Collect widely used US macro series from FRED public CSV endpoints."""
    events = []
    fail = 0
    for series_id, label in series.items():
        rows = None
        for attempt in (1, 2):                     # 일시 장애 1회 재시도 (백오프 2s)
            try:
                resp = requests.get(
                    "https://fred.stlouisfed.org/graph/fredgraph.csv",
                    headers=PLAIN_HEADERS,   # 위장 UA 는 봇감지 타르핏 — 정직 UA 만 통과(실증)
                    params={"id": series_id},
                    timeout=12,
                )
                resp.raise_for_status()
                rows = list(csv.DictReader(resp.text.splitlines()))
                break
            except Exception as e:
                if attempt == 2:
                    fail += 1
                    logger.warning("FRED %s csv 실패(재시도 포함): %s", series_id, e)
                    _note_error("fred", f"fredgraph.csv: {e}")
                else:
                    time_mod.sleep(2)

        latest = None
        previous = None
        if rows is not None:
            for row in rows:
                value = row.get(series_id)
                if not value or value == ".":
                    continue
                previous = latest
                latest = (row.get("observation_date", ""), value)

        if latest is None:
            # 폴백: FRED 공식 API (무료 키 — .env FRED_API_KEY. csv 가 봇/클라우드 IP 차단 시 경로)
            latest, previous = _fred_api_latest(series_id)
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
    if events:
        _LAST_ERRORS.pop("fred", None)
    return [_classify_event(event) for event in events]


def _parse_yields_from_world_gov_bonds(markdown: str, maturities: tuple[int, ...] = (5, 10, 20, 30)) -> dict[str, float]:
    yields = {}
    for maturity in maturities:
        match = re.search(rf"\|\s*\[({maturity}) years\]\([^)]*\)\s*\|\s*([0-9.]+)%", markdown)
        if match:
            yields[f"{maturity}Y"] = float(match.group(2))
    return yields


def _parse_yields_from_wgb_html(html_text: str, maturities: tuple[int, ...] = (5, 10, 20, 30)) -> dict[str, float]:
    """worldgovernmentbonds.com 직접 HTML → {'10Y': 4.395, ...} (순수 — jina 폴백용).

    행 단위로 'N years' 링크 근처(≤300자)의 첫 백분율만 취해 오매칭을 줄인다.
    """
    yields = {}
    for maturity in maturities:
        m = re.search(rf">\s*{maturity}\s*years?\s*<.{{0,300}}?([0-9]+\.[0-9]+)\s*%",
                      html_text, re.S | re.I)
        if m:
            yields[f"{maturity}Y"] = float(m.group(1))
    return yields


def fetch_world_gov_bond_events(countries: dict[str, str] = WORLD_GOV_BOND_COUNTRIES) -> list[dict]:
    events = []
    for country, label in countries.items():
        yields = {}
        try:
            resp = _bounded_get(f"https://r.jina.ai/http://www.worldgovernmentbonds.com/country/{country}/", timeout=20)
            yields = _parse_yields_from_world_gov_bonds(resp.text)
        except Exception as e:
            logger.warning("WGB %s jina 실패: %s", country, e)
            _note_error("worldgovernmentbonds", f"jina: {e}")
        if not yields:
            # 폴백: 직접 HTML (jina 장애/레이트리밋 대응)
            try:
                resp = _bounded_get(f"https://www.worldgovernmentbonds.com/country/{country}/", timeout=15)
                yields = _parse_yields_from_wgb_html(resp.text)
            except Exception as e:
                logger.warning("WGB %s 직접 폴백도 실패: %s", country, e)
                _note_error("worldgovernmentbonds", f"직접: {e}")
        for maturity, value in yields.items():
            events.append({
                "source": "worldgovernmentbonds",
                "source_url": "https://www.worldgovernmentbonds.com",
                "type": "macro_snapshot",
                "record_kind": "observation",
                "entity_id": f"worldgovernmentbonds:{country}:{maturity.lower()}",
                "title": f"{label} {maturity}: {value:.3f}%",
                "url": f"https://www.worldgovernmentbonds.com/country/{country}/#{maturity}",
                "tickers": [],
                "tags": ["금리/채권"],
                "metrics": {"country": country, "maturity": maturity, "yield_pct": value},
            })
    if events:
        _LAST_ERRORS.pop("worldgovernmentbonds", None)
    return [_classify_event(event) for event in events]


def _as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _polymarket_labels(event: dict) -> list[str]:
    labels = []
    for item in event.get("tags") or []:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("label") or item.get("name") or item.get("slug") or ""
        else:
            text = ""
        text = str(text).strip()
        if text:
            labels.append(text)
    category = str(event.get("category") or "").strip()
    if category:
        labels.append(category)
    return _normalize_symbols(labels)


def _polymarket_yes_probability(market: dict) -> float | None:
    outcomes = [str(item).strip().lower() for item in _json_list(market.get("outcomes"))]
    prices = [_as_float(item) for item in _json_list(market.get("outcomePrices"))]
    if not prices:
        return None
    try:
        idx = outcomes.index("yes")
    except ValueError:
        idx = 0
    if idx >= len(prices):
        return None
    price = prices[idx]
    if price is None:
        return None
    return round(price, 4)


def _polymarket_matches_keywords(event: dict, market: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(
        str(part or "")
        for part in [
            event.get("title"),
            event.get("slug"),
            event.get("category"),
            market.get("question"),
            market.get("slug"),
            " ".join(_polymarket_labels(event)),
        ]
    ).lower()
    for raw in keywords:
        word = str(raw).lower().strip()
        if not word:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", haystack):
            return True
    return False


def _polymarket_default_keywords() -> list[str]:
    raw = os.getenv("STOCK_COLLECTOR_POLYMARKET_KEYWORDS", "").strip()
    if not raw:
        return POLYMARKET_DEFAULT_KEYWORDS
    return [item.strip() for item in raw.split(",") if item.strip()]


def _polymarket_request_params(request_limit: int) -> dict:
    return {
        "active": True,
        "closed": False,
        "archived": False,
        "limit": min(200, max(1, int(request_limit))),
        "order": "volume",
        "ascending": False,
    }


def fetch_polymarket_payload(*, request_limit: int, get=None) -> tuple[list[dict], dict]:
    """Fetch Gamma directly, using the fixed-upstream relay only after HTTP 451."""
    get = get or requests.get
    direct_url = f"{POLYMARKET_API_BASE}/events"
    params = _polymarket_request_params(request_limit)
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        response = get(
            direct_url,
            headers=PLAIN_HEADERS,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise PolymarketUnavailable("invalid direct Polymarket payload")
        return payload, {
            "transport": "direct",
            "source_url": POLYMARKET_API_BASE,
            "retrieved_at": retrieved_at,
        }
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code != 451:
            raise PolymarketUnavailable(
                f"HTTP {status_code or 'error'} from Polymarket",
                status_code=status_code,
            ) from exc
    except PolymarketUnavailable:
        raise
    except requests.RequestException as exc:
        raise PolymarketUnavailable(str(exc)[:240]) from exc

    relay_url = (os.getenv("POLYMARKET_RELAY_URL") or "").strip()
    relay_token = (os.getenv("POLYMARKET_RELAY_TOKEN") or "").strip()
    parsed_relay = urlparse(relay_url) if relay_url else None
    if not relay_url or not relay_token or parsed_relay.scheme != "https" or not parsed_relay.netloc:
        raise PolymarketUnavailable(
            "HTTP 451: Polymarket unavailable from this server region; relay is not configured",
            availability="blocked",
            status_code=451,
        )

    try:
        response = get(
            relay_url,
            headers={"Authorization": f"Bearer {relay_token}", **PLAIN_HEADERS},
            params={"limit": params["limit"]},
            timeout=20,
        )
        response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            availability = str((envelope or {}).get("availability") or "error") if isinstance(envelope, dict) else "error"
            raise PolymarketUnavailable(
                "invalid or unsuccessful Polymarket relay payload",
                availability=availability if availability in {"blocked", "error"} else "error",
                status_code=(envelope or {}).get("upstream_status") if isinstance(envelope, dict) else None,
            )
        payload = envelope.get("events")
        source_url = str(envelope.get("source_url") or "")
        if not isinstance(payload, list) or not source_url.startswith(f"{POLYMARKET_API_BASE}/"):
            raise PolymarketUnavailable("Polymarket relay returned an invalid source envelope")
        return payload, {
            "transport": "relay",
            "source_url": POLYMARKET_API_BASE,
            "relay_url": relay_url,
            "retrieved_at": str(envelope.get("retrieved_at") or retrieved_at),
        }
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        blocked = status_code == 451
        raise PolymarketUnavailable(
            "HTTP 451: Polymarket relay region is also blocked" if blocked else f"Polymarket relay HTTP {status_code or 'error'}",
            availability="blocked" if blocked else "error",
            status_code=status_code,
        ) from exc
    except PolymarketUnavailable:
        raise
    except requests.RequestException as exc:
        raise PolymarketUnavailable(f"Polymarket relay failed: {str(exc)[:200]}") from exc


def fetch_polymarket_events(limit: int | None = None, *, min_volume: float | None = None,
                            keywords: list[str] | None = None) -> list[dict]:
    """Collect public Polymarket probabilities as auxiliary market-risk signals."""
    _SOURCE_AVAILABILITY.pop("polymarket", None)
    limit = limit if limit is not None else int(os.getenv("STOCK_COLLECTOR_POLYMARKET_LIMIT", "80"))
    min_volume = min_volume if min_volume is not None else float(os.getenv("STOCK_COLLECTOR_POLYMARKET_MIN_VOLUME", "10000"))
    keywords = _polymarket_default_keywords() if keywords is None else keywords
    request_limit = min(200, max(50, int(limit) * 4))
    try:
        payload, transport_meta = fetch_polymarket_payload(request_limit=request_limit)
        _LAST_ERRORS.pop("polymarket", None)
    except PolymarketUnavailable as exc:
        logger.warning("polymarket 수집 실패: %s", exc)
        if exc.availability == "blocked":
            reason = str(exc)
            _SOURCE_AVAILABILITY["polymarket"] = {
                "availability": "blocked",
                "availability_reason": reason,
                "status_code": exc.status_code,
            }
            _note_error("polymarket", reason)
        else:
            _note_error("polymarket", exc)
        return []

    events: list[dict] = []
    for event in payload if isinstance(payload, list) else []:
        if event.get("closed") is True or event.get("active") is False:
            continue
        labels = _polymarket_labels(event)
        event_slug = str(event.get("slug") or event.get("ticker") or event.get("id") or "").strip()
        event_url = f"https://polymarket.com/event/{event_slug}" if event_slug else "https://polymarket.com"
        markets = event.get("markets") if isinstance(event.get("markets"), list) else [event]
        for market in markets:
            if market.get("closed") is True or market.get("active") is False:
                continue
            probability = _polymarket_yes_probability(market)
            if probability is None:
                continue
            volume = _as_float(market.get("volume")) or _as_float(event.get("volume")) or 0.0
            if min_volume is not None and volume < float(min_volume):
                continue
            if not _polymarket_matches_keywords(event, market, keywords):
                continue
            liquidity = _as_float(market.get("liquidity")) or _as_float(event.get("liquidity"))
            open_interest = _as_float(market.get("openInterest")) or _as_float(event.get("openInterest"))
            end_date = str(market.get("endDate") or event.get("endDate") or "").strip()
            question = str(market.get("question") or event.get("title") or "").strip()
            event_title = str(event.get("title") or question or event_slug).strip()
            market_id = str(market.get("id") or market.get("conditionId") or "").strip()
            market_slug = str(market.get("slug") or market_id).strip()
            url = event_url
            if len(markets) > 1 and market_slug:
                url = f"{event_url}#market-{market_slug}"
            title = f"{question or event_title}: Yes {probability * 100:.1f}%"
            body = (
                f"Polymarket implied probability for '{question or event_title}' is "
                f"{probability * 100:.1f}% Yes. Volume {volume:.0f}; "
                f"liquidity {liquidity or 0:.0f}; open interest {open_interest or 0:.0f}. "
                "Prediction-market prices are crowd-implied probabilities, not verified facts."
            )
            events.append({
                "source": "polymarket",
                "source_url": POLYMARKET_API_BASE,
                "type": "prediction_market",
                "record_kind": "observation",
                "observed_at": transport_meta.get("retrieved_at"),
                "transport": transport_meta.get("transport"),
                "title": title,
                "url": url,
                "published_at": event.get("published_at") or event.get("publishedAt") or event.get("startDate") or "",
                "body_raw": body,
                "body": body,
                "body_excerpt": body[:500],
                "tickers": _extract_tickers(" ".join([question, event_title])),
                "tags": labels,
                "markets": ["prediction_market"],
                "metrics": {
                    "event_id": str(event.get("id") or ""),
                    "market_id": market_id,
                    "yes_probability": probability,
                    "volume": float(volume),
                    "liquidity": None if liquidity is None else float(liquidity),
                    "open_interest": None if open_interest is None else float(open_interest),
                    "end_date": end_date,
                    "transport": transport_meta.get("transport"),
                },
                "raw_payload": {
                    "event_id": str(event.get("id") or ""),
                    "market_id": market_id,
                    "event": event,
                    "market": market,
                },
            })
    if events:
        _LAST_ERRORS.pop("polymarket", None)
    events.sort(key=lambda row: ((row.get("metrics") or {}).get("volume") or 0.0), reverse=True)
    return [_classify_event(event) for event in events[: max(1, int(limit))]]


def fetch_kalshi_events() -> list[dict]:
    from reports.prediction_markets import fetch_kalshi_events as fetch

    try:
        events = fetch()
        _LAST_ERRORS.pop("kalshi", None)
        return events
    except Exception as exc:
        logger.warning("kalshi 수집 실패: %s", exc)
        _note_error("kalshi", exc)
        return []


def fetch_broker_report_events() -> list[dict]:
    from reports.broker_reports import fetch_broker_report_events as fetch

    return fetch()


def fetch_economic_calendar_events() -> list[dict]:
    from reports.operational_events import fetch_economic_calendar_events as fetch

    return fetch(days=int(os.getenv("STOCK_COLLECTOR_CALENDAR_DAYS", "14")))


# ── 소스별 수집 헬스 (수집 공백 가시화 — 조용한 실패 차단) ────────────────────

HEALTH_FILE = "source_health.json"

# 소스별 "이만큼 수집 0이면 비정상" 임계(시간) — 크론 30분 주기 기준·주말 여유
SOURCE_STALE_HOURS = {
    "yahoo_headline": 6,
    "telegram:*": 12,
    "rss:fed": 96,
    "rss:sec": 72,
    "rss:investing_kr_stock": 6,
    "rss:investing_kr_economy": 6,
    "rss:*": 36,
    "yahoo_finance": 24,
    "fred": 72,
    "worldgovernmentbonds": 72,
    "polymarket": 12,
    "kalshi": 12,
    "economic_calendar": 6,
}


def expected_sources() -> list[str]:
    """수집기가 시도해야 하는 소스 전체 (텔레그램은 채널별 분리 — 채널 단위 공백 감지)."""
    return (["yahoo_headline"]
            + [f"telegram:{c}" for c in TELEGRAM_NEWS_CHANNELS]
            + [f"rss:{key}" for key, _url, _label in (*NEWS_RSS_FEEDS, *INVESTING_RSS_FEEDS)]
            + ["yahoo_finance", "fred", "worldgovernmentbonds", "polymarket", "kalshi", "economic_calendar"])


def source_collection_mode(source: str, record: dict | None = None) -> str:
    """Separate passive ingress from a sender with an expected delivery cadence."""
    if str(source).startswith("broker_report:"):
        return "on_demand"      # 리포트는 채널 글에 링크가 걸릴 때만 생긴다. 공백이 장애가 아니다.
    if source != "hermes_webhook":
        return "scheduled"
    mode = os.getenv("HERMES_SOURCE_COLLECTION_MODE")
    if mode is None:
        mode = (record or {}).get("collection_mode", "on_demand")
    # Unknown configuration must not disable missing-delivery monitoring.
    return "on_demand" if mode == "on_demand" else "scheduled"


def update_source_health(events: list[dict], cache_dir: Path | str = DEFAULT_CACHE_DIR,
                         now: datetime | None = None,
                         attempted_sources: Iterable[str] | None = None,
                         run_stats: dict[str, dict] | None = None) -> dict:
    """Persist fetch/write health while keeping the legacy success fields.

    ``last_success`` remains the legacy "received event" timestamp.  The
    explicit fetch/persist timestamps and accounting fields below prevent a
    successful fetch with no new rows from looking like a write failure.
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / HEALTH_FILE
    import safe_io

    counts = Counter(str(e.get("source") or "") for e in events)
    attempted = (list(attempted_sources) if attempted_sources is not None
                 else list(dict.fromkeys([*expected_sources(), *counts])))
    stats_by_source = run_stats or {}
    with safe_io.file_write_lock(str(path)):
        health: dict = {}
        if path.exists():
            try:
                health = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                health = {}
        for src in attempted:
            rec = health.get(src) or {}
            n = int(counts.get(src, 0))
            stats = dict(stats_by_source.get(src) or stats_by_source.get(src.split(":", 1)[0]) or {})
            run_at = now.isoformat(timespec="seconds")
            append_stats = _LAST_APPEND_STATS.get(src)
            if append_stats and append_stats.get("run_at") == run_at:
                for key in ("deduped", "collisions", "persist_success"):
                    if key not in stats and key in append_stats:
                        stats[key] = append_stats[key]
            fetched = int(stats.get("fetched", n) or 0)
            persisted = int(stats.get("persisted", n) or 0)
            deduped = int(stats.get("deduped_count", stats.get("deduped", 0)) or 0)
            collisions = int(stats.get(
                "collision_count", stats.get("collisions", stats.get("collision", 0))
            ) or 0)
            availability = (_SOURCE_AVAILABILITY.get(src)
                            or _SOURCE_AVAILABILITY.get(src.split(":", 1)[0]))
            stats_availability = str(stats.get("availability") or "")
            if not availability and stats_availability in {"blocked", "disabled", "unavailable"}:
                availability = {
                    "availability": stats_availability,
                    "availability_reason": str(stats.get("availability_reason") or stats.get("error") or ""),
                }
            availability_name = str((availability or {}).get("availability") or stats_availability or "")
            fetch_success_value = stats.get("fetch_success")
            if fetch_success_value is None:
                fetch_success = not bool(stats.get("error")) and availability_name not in {"error", "blocked", "disabled", "unavailable"}
            else:
                fetch_success = bool(fetch_success_value)
            persist_success_value = stats.get("persist_success")
            if persist_success_value is None:
                persist_success = bool(
                    persisted > 0
                    or (fetched > 0 and deduped >= fetched and collisions == 0 and not stats.get("error"))
                )
            else:
                persist_success = bool(persist_success_value)
            previous_fetched = int(rec.get("last_fetched_count") or 0)
            cardinality_ratio = round(fetched / previous_fetched, 3) if previous_fetched > 0 else None
            cardinality_drop = previous_fetched > 0 and fetched < (previous_fetched * 0.5)
            rec.setdefault("first_run", now.isoformat())
            rec["last_run"] = now.isoformat()
            rec["last_count"] = n
            rec["last_fetched_count"] = fetched
            rec["last_persisted_count"] = persisted
            rec["last_deduped_count"] = max(0, deduped)
            rec["last_collision_count"] = max(0, collisions)
            rec["deduped_count"] = max(0, deduped)
            rec["collision_count"] = max(0, collisions)
            rec["fetch_success"] = fetch_success
            rec["persist_success"] = persist_success
            rec["last_duration_ms"] = max(0, int(stats.get("duration_ms") or 0))
            queue_stats = stats.get("article_queue")
            if isinstance(queue_stats, dict):
                errors = [str(value)[:500] for value in (queue_stats.get("errors") or [])[:1]]
                rec["article_queue"] = {
                    "enqueued": max(0, int(queue_stats.get("enqueued") or 0)),
                    "updated": max(0, int(queue_stats.get("updated") or 0)),
                    "invalid": max(0, int(queue_stats.get("invalid") or 0)),
                    "capacity_rejected": max(0, int(queue_stats.get("capacity_rejected") or 0)),
                    "error": errors[0] if errors else "",
                }
            if stats.get("transport"):
                rec["last_transport"] = str(stats["transport"])
            duplicate_only = (
                fetched > 0 and persisted == 0 and deduped >= fetched
                and collisions == 0 and persist_success
            )
            zero_persist_issue = fetched > 0 and persisted == 0 and not duplicate_only
            if zero_persist_issue:
                rec["zero_persist_streak"] = int(rec.get("zero_persist_streak") or 0) + 1
            elif persisted > 0 or duplicate_only:
                rec["zero_persist_streak"] = 0
            else:
                rec.setdefault("zero_persist_streak", 0)
            if cardinality_drop:
                rec["cardinality_drop_streak"] = int(rec.get("cardinality_drop_streak") or 0) + 1
            else:
                rec["cardinality_drop_streak"] = 0
            rec["last_cardinality_ratio"] = cardinality_ratio
            rec["cardinality_drop_detected"] = cardinality_drop
            if fetch_success:
                rec["last_fetch_success"] = now.isoformat()
                rec["last_fetch_success_count"] = fetched
            if persist_success:
                rec["last_persist_success"] = now.isoformat()
                rec["last_persist_success_count"] = persisted
            if availability:
                rec.update(availability)
            else:
                rec.pop("availability", None)
                rec.pop("availability_reason", None)
            if n > 0:
                rec["last_success"] = now.isoformat()
                rec["last_success_count"] = n
                rec.pop("last_error", None)
            else:
                err = stats.get("error") or _LAST_ERRORS.get(src) or _LAST_ERRORS.get(src.split(":")[0])
                if err:
                    rec["last_error"] = str(err)[:500]
                else:
                    rec.pop("last_error", None)
            health[src] = rec
        safe_io.atomic_write_json(str(path), health)
        return health


def load_source_health(cache_dir: Path | str = DEFAULT_CACHE_DIR) -> dict:
    path = Path(cache_dir) / HEALTH_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def stale_sources(health: dict | None = None, now: datetime | None = None,
                  thresholds: dict | None = None,
                  cache_dir: Path | str = DEFAULT_CACHE_DIR) -> list[dict]:
    """수집 공백 소스 목록 (순수 — health dict 주입 시 무 I/O·테스트 가능).

    반환: [{source, hours(공백 시간·성공 이력 없으면 None), threshold}] — 임계 초과만.
    """
    health = load_source_health(cache_dir) if health is None else health
    if not health:
        return []
    now = (now or datetime.now(KST)).astimezone(KST)
    th = thresholds or SOURCE_STALE_HOURS

    def _hours_since(iso: str):
        try:
            ts = datetime.fromisoformat(iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=KST)
            return (now - ts).total_seconds() / 3600
        except Exception:
            return None

    out = []
    for src, rec in sorted(health.items()):
        if source_collection_mode(src, rec) == "on_demand":
            continue
        if rec.get("availability") in {"blocked", "disabled"}:
            continue
        limit = th.get(src) or th.get(f"{src.split(':')[0]}:*") or th.get(src.split(":")[0]) or 24
        last_ok = rec.get("last_success")
        hours = _hours_since(last_ok) if last_ok else None
        if hours is None:
            # 성공 이력 없음 — 단, 관측 시작 직후(배포/신규 소스)엔 grace (오탐 방지):
            # 첫 기록 후 min(임계, 6h) 는 조용히 관찰, 그 뒤에도 무성공이면 경보.
            grace = min(limit, 6)
            since_first = _hours_since(rec.get("first_run") or "") if rec.get("first_run") else None
            if since_first is not None and since_first <= grace:
                continue
        if hours is None or hours > limit:
            out.append({"source": src, "hours": None if hours is None else round(hours, 1),
                        "threshold": limit, "error": rec.get("last_error")})
    return out


def collect_once(cache_dir: Path | str = DEFAULT_CACHE_DIR, now: datetime | None = None) -> tuple[int, int]:
    from reports.source_pipeline import default_registry, run_providers

    registry = [spec for spec in default_registry() if spec.group != "calendar"]
    result = run_providers(registry=registry, cache_dir=cache_dir, now=now)
    for name, stats in result["providers"].items():
        logger.info(
            "수집 %s: fetched=%d persisted=%d duration=%dms availability=%s",
            name,
            stats["fetched"],
            stats["persisted"],
            stats["duration_ms"],
            stats["availability"],
        )
    return int(result["fetched"]), int(result["persisted"])


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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetched, written = collect_once(args.cache_dir)
    removed = prune_old(args.cache_dir)
    print(f"stock source collector: fetched={fetched} new={written} pruned={removed} cache={args.cache_dir}")
    # 소스별 공백 요약 — 크론 로그에서 "어느 출처가 죽었는지" 즉시 확인
    for s in stale_sources(cache_dir=args.cache_dir):
        gap = "성공 이력 없음" if s["hours"] is None else f"{s['hours']:.0f}시간 공백"
        print(f"⚠️ 수집 공백: {s['source']} — {gap} (임계 {s['threshold']}h)")
    return 0


if __name__ == "__main__":
    # ⚠️ 같은 파일이 스크립트(`__main__`)와 패키지 모듈(`reports.source_collector`)로
    # **이중 로드**되면 두 사본의 전역이 갈린다. 그러면 이 사본의 fetch 가 채운
    # `_SOURCE_AVAILABILITY`(예: polymarket HTTP 451 → blocked)를 집계 코드
    # (`reports/source_pipeline.py` 가 패키지 사본을 읽음)가 보지 못해
    # availability=available 로 기록되고, stale_sources() 의 blocked 스킵이 무력화돼
    # "polymarket 61h 공백" 오경보가 영구히 반복된다(감사 2026-08-22 실측).
    # → 패키지 사본으로 위임해 전역을 하나로 유지한다.
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from reports.source_collector import main as _pkg_main

    raise SystemExit(_pkg_main())
