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
import socket
import subprocess
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
# 뉴스 텔레그램 채널 — env 로 교체 가능(죽은 채널 무배포 교체): STOCK_COLLECTOR_TG_CHANNELS=a,b
TELEGRAM_NEWS_CHANNELS = [c.strip().lstrip("@") for c in os.getenv(
    "STOCK_COLLECTOR_TG_CHANNELS", "yuzukinaok1,insidertracking").split(",") if c.strip()]
NEWS_THEME_KEYWORDS = {
    "중동/전쟁": ("이스라엘", "이란", "가자", "하마스", "우크라이나", "러시아", "전쟁", "군", "미사일", "핵"),
    "금리/채권": ("금리", "국채", "채권", "연준", "fed", "treasury", "yield"),
    "유가/원자재": ("유가", "오일", "원유", "석유", "브렌트", "wti", "금 ", "gold"),
    "인플레/고용": ("cpi", "물가", "인플레", "고용", "실업", "임금"),
    "기술/AI": ("ai", "엔비디아", "반도체", "칩", "데이터센터"),
    "정책/재정": ("재무장관", "세금", "관세", "예산", "부채", "재정"),
}

SOURCE_CLASSIFICATION = {
    "saveticker": {"family": "news", "kind": "article", "trust": "B", "horizon": "1d"},
    "saveticker_report_pdf": {"family": "report", "kind": "report", "trust": "B", "horizon": "1w"},
    "arca": {"family": "community", "kind": "community_signal", "trust": "C", "horizon": "intraday"},
    "telegram": {"family": "community", "kind": "community_signal", "trust": "C", "horizon": "intraday"},
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


def _topic_from_text(text: str) -> str:
    lower = str(text or "").lower()
    for theme, words in NEWS_THEME_KEYWORDS.items():
        if any(word.lower() in lower for word in words):
            return theme
    return ""


def _classify_event(event: dict) -> dict:
    def _clip(value: object, limit: int = 240) -> str:
        text = str(value or "").replace("\x00", " ").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    row = dict(event or {})
    source = str(row.get("source") or "unknown").strip() or "unknown"
    root = _source_root(source)
    profile = SOURCE_CLASSIFICATION.get(root, {"family": "other", "kind": "event", "trust": "C", "horizon": "1d"})

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
    if root == "saveticker_report_pdf":
        kind = "report"
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
        if root.startswith("telegram"):
            topic = "텔레그램"
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
    elif root == "telegram":
        wiki_eligible = kind == "analysis" or text_len >= 900
    elif root == "polymarket":
        wiki_eligible = True

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
        "confidence": confidence,
        "labels": labels[:8],
    }
    row["classification"] = classification
    row["source_family"] = classification["source_family"]
    row["topic"] = classification["topic"]
    row["trust"] = classification["trust"]
    row["wiki_eligible"] = classification["wiki_eligible"]
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


def _proxy_from_env() -> str:
    return (os.getenv("STOCK_COLLECTOR_ARCA_PROXY")
            or os.getenv("ARCA_PROXY")
            or os.getenv("CRAWL_PROXY")
            or "").strip()


def _proxy_host_port(proxy: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(proxy)
    except Exception:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname, int(parsed.port or 1080)


def arca_proxy_status(proxy: str | None = None) -> dict:
    """Arca 전용 프록시 상태. 네트워크 요청 없이 로컬 SOCKS 포트 리슨 여부만 확인한다."""
    proxy = (proxy or _proxy_from_env() or "").strip()
    if not proxy:
        return {"enabled": False, "proxy": "", "reachable": False, "error": "proxy unset"}
    hp = _proxy_host_port(proxy)
    if not hp:
        return {"enabled": True, "proxy": proxy, "reachable": False, "error": "invalid proxy url"}
    host, port = hp
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return {"enabled": True, "proxy": proxy, "reachable": True, "host": host, "port": port}
    except Exception as exc:
        return {"enabled": True, "proxy": proxy, "reachable": False, "host": host, "port": port,
                "error": str(exc)[:160]}


def _curl_proxy_args(proxy: str) -> list[str]:
    parsed = urlparse(proxy)
    scheme = parsed.scheme.lower()
    host_port = f"{parsed.hostname}:{parsed.port or 1080}"
    if scheme in ("socks5", "socks5h", "socks"):
        return ["--socks5-hostname", host_port]
    if scheme == "socks4":
        return ["--socks4", host_port]
    if scheme in ("http", "https"):
        return ["--proxy", proxy]
    raise ValueError(f"지원하지 않는 프록시 프로토콜: {scheme or 'unknown'}")


def _bounded_get_via_proxy(url: str, proxy: str, *, timeout: int = 20,
                           max_bytes: int = 5_000_000, headers: dict | None = None):
    """curl 기반 프록시 fetch.

    requests는 PySocks가 없으면 SOCKS를 못 타므로, 서버에 이미 있는 curl을 사용한다.
    Cloudflare 우회를 자동화하지 않고 일반 GET만 수행한다.
    """
    status = arca_proxy_status(proxy)
    if not status.get("reachable"):
        raise RuntimeError(f"proxy unavailable: {status.get('error') or proxy}")
    cmd = [
        "curl", "-fsSL", "--compressed", "--max-time", str(int(timeout)),
        "--user-agent", (headers or HEADERS).get("User-Agent", HEADERS["User-Agent"]),
        *_curl_proxy_args(proxy),
    ]
    for key, value in (headers or {}).items():
        if key.lower() == "user-agent":
            continue
        cmd.extend(["-H", f"{key}: {value}"])
    cmd.append(url)
    proc = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout + 3)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip() or f"curl exit {proc.returncode}"
        raise RuntimeError(err[:240])
    if len(proc.stdout) > max_bytes:
        raise ValueError(f"응답 과대(>{max_bytes}B) — {url[:60]}")
    return _BoundedResponse(proc.stdout, "utf-8")


def _is_cloudflare_challenge(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "just a moment" in lower
        or "cf-challenge" in lower
        or "checking if the site connection is secure" in lower
        or "cloudflare challenge" in lower
    )


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


def _extract_news_tags(text: str) -> list[str]:
    lower = text.lower()
    return [theme for theme, words in NEWS_THEME_KEYWORDS.items() if any(word.lower() in lower for word in words)]


def _normalize_tickers(raw) -> list[str]:
    """티커 리스트를 문자열로 정규화.

    SaveTicker API 는 tickers 를 [{"id":.., "name":.., "symbol":"NVDA"}] 같은
    dict 리스트로 줄 때가 있다 → symbol 문자열만 추출한다. 다운스트림(build_digest
    등)은 list[str] 을 가정하므로 dict 가 새면 Counter·join 에서 크래시한다.
    """
    out: list[str] = []
    for t in raw or []:
        if isinstance(t, str):
            if t.strip():
                out.append(t.strip())
        elif isinstance(t, dict):
            sym = t.get("symbol") or t.get("ticker") or t.get("code")
            if sym:
                out.append(str(sym).strip())
    return out


def _combine_body_raw(*parts: object) -> str:
    body = "\n\n".join(
        str(part).replace("\x00", " ").strip()
        for part in parts
        if str(part or "").strip()
    ).strip()
    return body


def _saveticker_html_to_text(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text("\n", strip=True) if main else html_text
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned.strip()


def _saveticker_article_url(item: dict) -> str:
    """saveticker API 응답엔 url/link 필드가 없음(2026-07-25 실측 확인 — top-stories·news/list
    둘 다) — id 로 직접 구성. 실측: https://saveticker.com/news/{id} 가 실제 기사 페이지(200)."""
    url = str(item.get("url") or item.get("link") or "").strip()
    if url:
        return url
    aid = item.get("id")
    return f"https://saveticker.com/news/{aid}" if aid else ""


_SAVETICKER_DISCLAIMER = "본 콘텐츠는 투자 권유 목적이 아닌 정보 제공용입니다."


def _strip_saveticker_boilerplate(text: str, title: str = "") -> str:
    """추출 텍스트에서 상단 메타데이터 헤더(태그·티커·시각·출처+제목)와 하단 면책조항 제거.

    페이지 구조 실측(2026-07-25): <article> 첫 블록이 'SAVE PICK · 속보 · $TICKER · 시각 ·
    출처: X · {제목}' 형태로 제목까지 끝나 — 제목이 등장하는 줄까지를 헤더로 보고 그 다음
    줄부터를 본문으로 채택. 제목 매칭 실패 시(페이지 구조 변형 등) 원문 그대로 반환(안전).
    """
    lines = text.splitlines()
    title = (title or "").strip()
    if title:
        for i, line in enumerate(lines):
            if title in line:
                lines = lines[i + 1:]
                break
    cleaned = [ln for ln in lines if ln.strip() and ln.strip() != _SAVETICKER_DISCLAIMER]
    return "\n".join(cleaned).strip()


def _fetch_saveticker_article_body(url: str, title: str = "") -> str:
    if not url:
        return ""
    try:
        resp = _bounded_get(url, timeout=15)
        html_text = resp.text.strip()
        if not html_text or _is_cloudflare_challenge(html_text):
            return ""
        text = _saveticker_html_to_text(html_text)
        return _strip_saveticker_boilerplate(text, title)
    except Exception:
        return ""


def _saveticker_article_record(item: dict, base: str) -> dict:
    from reports.raw_archive import save_extracted_text, save_raw_artifact

    fetched_at = datetime.now(KST)
    title = str(item.get("title") or "").strip()
    url = _saveticker_article_url(item)
    content = str(item.get("content") or "").strip()
    summary = str(item.get("group_summary") or "").strip()
    body_raw = _combine_body_raw(content, summary)
    # saveticker 자체 미리보기가 80~90자 근처서 "..."로 잘려 오는 경우가 흔해 길이만으로는
    # 거의 안 걸림 — 말줄임표로 끝나면 길이 무관하게 전체 기사를 마저 가져온다(2026-07-25).
    looks_truncated = body_raw.endswith("...") or body_raw.endswith("…")
    if (len(body_raw) < 80 or looks_truncated) and url:
        fetched = _fetch_saveticker_article_body(url, title=title)
        if fetched and len(fetched) > len(body_raw):
            body_raw = fetched   # 전체 기사가 미리보기의 상위집합 — 미리보기 중복 없이 이걸로 대체
        elif fetched:
            body_raw = _combine_body_raw(content, summary, fetched)
    if not body_raw:
        body_raw = _combine_body_raw(title, content, summary) or title

    raw_payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
    raw_record = save_raw_artifact(
        source="saveticker_article",
        kind="json",
        fetched_at=fetched_at,
        title=title or url or "saveticker article",
        url=url or base,
        payload=raw_payload,
        suffix=".json",
    )
    save_extracted_text(raw_record, body_raw)
    return {
        "body_raw": body_raw,
        "body": body_raw,
        "body_excerpt": body_raw[:500],
        "raw_path": raw_record["raw_path"],
        "text_path": raw_record["text_path"],
        "manifest_path": raw_record["manifest_path"],
        "raw_sha256": raw_record["sha256"],
        "raw_source": raw_record["source"],
    }


def _fetch_arca_post_body(post_id: str, *, proxy: str | None = None) -> str:
    """Arca 게시글 본문을 가능한 한 원문에 가깝게 수집한다."""
    proxy = (proxy or _proxy_from_env() or "").strip()
    if proxy:
        try:
            resp = _bounded_get_via_proxy(
                f"https://arca.live/b/stock/{post_id}",
                proxy,
                timeout=18,
            )
            body = resp.text.strip()
            if body:
                return body
        except Exception:
            pass
    try:
        resp = _bounded_get(f"https://r.jina.ai/http://arca.live/b/stock/{post_id}", timeout=25)
        body = resp.text.strip()
        if body:
            return body
    except Exception:
        pass
    return ""


def _saveticker_max_pages() -> int:
    try:
        return max(1, min(20, int(os.getenv("STOCK_COLLECTOR_SAVETICKER_MAX_PAGES", "3"))))
    except (TypeError, ValueError):
        return 3


def fetch_saveticker_events() -> list[dict]:
    from reports.raw_archive import load_dedupe_index, save_dedupe_index

    base = os.getenv("SAVE_TICKER_API_BASE", "https://saveticker.com/api").rstrip("/")
    paths = [("news/top-stories", None)]
    paths.extend(
        ("news/list", {"page": page, "page_size": 30, "sort": "created_at_desc"})
        for page in range(1, _saveticker_max_pages() + 1)
    )
    events = []
    seen_keys: set[str] = set()
    dedupe_index = load_dedupe_index()
    dedupe_dirty = False
    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    for path, params in paths:
        try:
            resp = requests.get(f"{base}/{path}", headers=HEADERS, params=params, timeout=12)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        items = data.get("news_list") or data.get("data") or []
        if params and not items:
            break
        for item in items:
            title = item.get("title") or ""
            if not title:
                continue
            url = _saveticker_article_url(item)
            dedupe_key = str(url or title).strip().lower()
            if dedupe_key and dedupe_key in seen_keys:
                continue
            if dedupe_key:
                seen_keys.add(dedupe_key)
            text = " ".join(str(item.get(k) or "") for k in ("title", "content", "group_summary"))
            if dedupe_key and dedupe_key in dedupe_index:
                # 같은 기사가 API 응답에 계속 남아있는 동안 매 폴링(30분)마다 원본을 재저장하던
                # 버그 수정 — 최근에 이미 아카이브했으면 재수집/재저장 생략(디스크·네트워크 낭비 방지).
                body_raw = _combine_body_raw(item.get("content"), item.get("group_summary")) or text
                raw_path = text_path = manifest_path = raw_sha256 = raw_source = ""
            else:
                record = _saveticker_article_record(item, base)
                body_raw = record["body_raw"] or _combine_body_raw(item.get("content"), item.get("group_summary")) or text
                raw_path, text_path = record["raw_path"], record["text_path"]
                manifest_path, raw_sha256, raw_source = (
                    record["manifest_path"], record["raw_sha256"], record["raw_source"])
                if dedupe_key:
                    dedupe_index[dedupe_key] = now_iso
                    dedupe_dirty = True
            events.append({
                "source": "saveticker",
                "source_url": base,
                "title": title,
                "url": url,
                "published_at": item.get("created_at") or item.get("published_at") or "",
                "body_raw": body_raw,
                "body": body_raw,
                "body_excerpt": body_raw[:500],
                "tickers": _normalize_tickers(item.get("tickers")) or _extract_tickers(text),
                "tags": item.get("tag_names") or [],
                "raw_path": raw_path,
                "text_path": text_path,
                "manifest_path": manifest_path,
                "raw_sha256": raw_sha256,
                "raw_source": raw_source,
            })
    if dedupe_dirty:
        save_dedupe_index(dedupe_index, now=datetime.now(KST))
    return [_classify_event(event) for event in events]

def _parse_arca_html(html_text: str) -> list[tuple[str, str]]:
    """arca.live 게시판 HTML → [(post_id, 제목텍스트)] (순수 — jina 장애 시 직접 폴백용)."""
    out = []
    seen = set()
    for m in re.finditer(r'href="/b/stock/(\d+)[^"]*"[^>]*>(.*?)</a>', html_text, re.S):
        post_id = m.group(1)
        if post_id in seen:
            continue
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        text = " ".join(text.split())
        if not text:
            continue
        seen.add(post_id)
        out.append((post_id, text))
    return out


def fetch_arca_events(max_pages: int = 2, *, proxy: str | None = None,
                      prefer_proxy: bool = False) -> list[dict]:
    events = []
    # 스킴은 http/https 모두 허용 — jina 는 내부 URL 스킴을 href 에 그대로 반사(라이브 실증:
    # http 내부 요청이면 링크도 http://arca.live/... 라 https 고정 패턴은 0건). ?p= 꼬리도 선택.
    link_pat = re.compile(r"\[([^\]]+)\]\(https?://arca\.live/b/stock/(\d+)[^)]*\)")

    seen_posts: set[str] = set()

    def _add(post_id: str, text: str) -> None:
        if post_id in seen_posts:
            return                                    # 페이지 간 중복(핀 고정 글 등)
        label = next((lb for lb in ARCA_LABELS if lb in text), "")
        if not label:
            return
        seen_posts.add(post_id)
        title = text[text.index(label):]              # 게시글 번호 프리픽스 제거
        # 꼬리 메타([댓글수] 작성자 날짜 조회 추천) 제거 — 실패해도 원문 유지(graceful)
        title = re.sub(r"\s*(\[\d+\])?\s*\S{1,20}\s+\d{4}\.\d{2}\.\d{2}\s+\d+\s+\d+\s*$",
                       "", title) or title
        body_raw = _fetch_arca_post_body(post_id, proxy=proxy)
        body = body_raw or title
        events.append({
            "source": "arca",
            "title": title[:140],
            "url": f"https://arca.live/b/stock/{post_id}",
            "source_url": "https://arca.live/b/stock",
            "category": label,
            "body_raw": body_raw or body,
            "body": body,
            "body_excerpt": body[:500],
            "tickers": _extract_tickers(title),
        })

    proxy = (proxy or _proxy_from_env() or "").strip()
    if prefer_proxy and proxy:
        for page in range(1, max_pages + 1):
            try:
                resp = _bounded_get_via_proxy(f"https://arca.live/b/stock?p={page}", proxy, timeout=18)
                if _is_cloudflare_challenge(resp.text):
                    _note_error("arca", "proxy: Cloudflare challenge")
                    logger.warning("arca p%d proxy 응답이 Cloudflare challenge", page)
                    continue
                for post_id, text in _parse_arca_html(resp.text):
                    _add(post_id, text)
            except Exception as e:
                logger.warning("arca p%d proxy 실패: %s", page, e)
                _note_error("arca", f"proxy: {e}")
        if events:
            _LAST_ERRORS.pop("arca", None)
            return [_classify_event(event) for event in events]

    for page in range(1, max_pages + 1):
        try:
            # x-wait-for-selector: 게시글 행(.vrow) 렌더 완료까지 대기 — jina 부분 렌더
            # (공지만 있는 11KB 응답·간헐 0건의 유력 원인) 재발 방지
            resp = _bounded_get(f"https://r.jina.ai/http://arca.live/b/stock?p={page}",
                                timeout=25, headers={"x-wait-for-selector": ".vrow"})
            for match in link_pat.finditer(resp.text):
                _add(match.group(2), " ".join(match.group(1).split()).replace("**", "").strip())
        except Exception as e:
            logger.warning("arca p%d jina 실패: %s", page, e)
            _note_error("arca", f"jina: {e}")

    if not events:
        # 폴백: arca.live 직접 (jina 장애/레이트리밋 대응 — CF 차단이면 이것도 실패·헬스에 기록)
        for page in range(1, max_pages + 1):
            try:
                resp = _bounded_get(f"https://arca.live/b/stock?p={page}", timeout=15)
                for post_id, text in _parse_arca_html(resp.text):
                    _add(post_id, text)
            except Exception as e:
                logger.warning("arca p%d 직접 폴백도 실패: %s", page, e)
                _note_error("arca", f"직접: {e}")
    if events:
        _LAST_ERRORS.pop("arca", None)
    return [_classify_event(event) for event in events]


def fetch_arca_provider() -> list[dict]:
    """Importable Arca provider entrypoint for the isolated source worker."""
    try:
        pages = max(0, min(20, int(os.getenv("STOCK_COLLECTOR_ARCA_PAGES", "2"))))
    except (TypeError, ValueError):
        pages = 2
    return fetch_arca_events(max_pages=pages)


def _telegram_text_from_fragment(fragment: str) -> str:
    raw = re.sub(r"<br\s*/?>", " ", fragment or "", flags=re.I)
    txt = html_mod.unescape(re.sub(r"<[^>]+>", "", raw))
    return " ".join(txt.split()).strip()


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
            messages.append({
                "title": body_raw[:180],
                "url": _url_from(card),
                "body_raw": body_raw[:TELEGRAM_BODY_MAX],
                "raw_html": str(card),
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
        jina_messages: list[dict] = []
        try:
            resp = _bounded_get(f"https://r.jina.ai/http://t.me/s/{channel}", timeout=20)
            markdown = resp.text
            titles = [" ".join(m.group(1).split()) for m in re.finditer(r"\*\*([^*]+)\*\*", markdown)]
            urls = re.findall(rf"https://t\.me/{re.escape(channel)}/\d+", markdown)
            for idx, title in enumerate(titles):
                jina_messages.append({
                    "title": title[:180],
                    "url": urls[idx] if idx < len(urls) else "",
                    "body_raw": title[:TELEGRAM_BODY_MAX],
                    "raw_html": title,
                })
        except Exception as e:
            logger.warning("telegram:%s jina 수집 실패 — 직접 HTML 폴백 시도: %s", channel, e)

        # 직접 HTML 카드 파싱을 canonical 경로로 사용한다. 실패할 때만 jina 제목을 이벤트화한다.
        messages = _telegram_direct_messages(channel) or jina_messages

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
                "tickers": _extract_tickers(scan_text),
                "tags": tags,
                "raw_path": archive["raw_path"],
                "text_path": archive["text_path"],
                "manifest_path": archive["manifest_path"],
                "raw_sha256": archive["raw_sha256"],
                "raw_source": archive["raw_source"],
            })
    return [_classify_event(event) for event in events]

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


def fetch_economic_calendar_events() -> list[dict]:
    from reports.operational_events import fetch_economic_calendar_events as fetch

    return fetch(days=int(os.getenv("STOCK_COLLECTOR_CALENDAR_DAYS", "14")))


# ── 소스별 수집 헬스 (수집 공백 가시화 — 조용한 실패 차단) ────────────────────

HEALTH_FILE = "source_health.json"

# 소스별 "이만큼 수집 0이면 비정상" 임계(시간) — 크론 30분 주기 기준·주말 여유
SOURCE_STALE_HOURS = {
    "saveticker": 3,
    "arca": 24,
    "telegram:*": 12,
    "yahoo_finance": 24,
    "fred": 72,
    "worldgovernmentbonds": 72,
    "polymarket": 12,
    "kalshi": 12,
    "economic_calendar": 6,
}


def expected_sources() -> list[str]:
    """수집기가 시도해야 하는 소스 전체 (텔레그램은 채널별 분리 — 채널 단위 공백 감지)."""
    return (["saveticker", "arca"]
            + [f"telegram:{c}" for c in TELEGRAM_NEWS_CHANNELS]
            + ["yahoo_finance", "fred", "worldgovernmentbonds", "polymarket", "kalshi", "economic_calendar"])


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
    attempted = list(attempted_sources) if attempted_sources is not None else expected_sources()
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
            if not availability and stats_availability in {"blocked", "disabled"}:
                availability = {
                    "availability": stats_availability,
                    "availability_reason": str(stats.get("availability_reason") or stats.get("error") or ""),
                }
            availability_name = str((availability or {}).get("availability") or stats_availability or "")
            fetch_success_value = stats.get("fetch_success")
            if fetch_success_value is None:
                fetch_success = not bool(stats.get("error")) and availability_name not in {"error", "blocked", "disabled"}
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
    from reports.source_pipeline import ProviderSpec, run_providers

    registry = [
        ProviderSpec("saveticker", ("saveticker",), "news", fetch_saveticker_events, retries=1),
        ProviderSpec(
            "arca",
            ("arca",),
            "news",
            fetch_arca_provider,
            retries=1,
        ),
        ProviderSpec(
            "telegram",
            tuple(f"telegram:{channel}" for channel in TELEGRAM_NEWS_CHANNELS),
            "news",
            fetch_telegram_channel_events,
            retries=1,
        ),
        ProviderSpec("yahoo_finance", ("yahoo_finance",), "market", fetch_market_snapshot_events, retries=1, mutable=True),
        ProviderSpec("fred", ("fred",), "macro", fetch_fred_macro_events, retries=1, mutable=True),
        ProviderSpec("worldgovernmentbonds", ("worldgovernmentbonds",), "macro", fetch_world_gov_bond_events, retries=1, mutable=True),
        ProviderSpec("polymarket", ("polymarket",), "prediction", fetch_polymarket_events, mutable=True),
        ProviderSpec("kalshi", ("kalshi",), "prediction", fetch_kalshi_events, retries=1, mutable=True),
    ]
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
