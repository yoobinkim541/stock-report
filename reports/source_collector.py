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
    trusted_sources = sorted({url for e in events for url in [e.get("source_url")] if isinstance(url, str) and url})
    lines = ["## 누적 수집 자료", ""]
    lines.append("- " + ", ".join(f"{src} {cnt}건" for src, cnt in source_counts.most_common()))
    if ticker_counts:
        lines.append("- 반복 등장 종목: " + ", ".join(f"{t} {c}건" for t, c in ticker_counts.most_common(8)))
    if tag_counts:
        lines.append("- 반복 테마: " + ", ".join(f"{t} {c}건" for t, c in tag_counts.most_common(8)))
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


def _fetch_saveticker_article_body(url: str) -> str:
    if not url:
        return ""
    try:
        resp = _bounded_get(url, timeout=15)
        html_text = resp.text.strip()
        if not html_text or _is_cloudflare_challenge(html_text):
            return ""
        text = _saveticker_html_to_text(html_text)
        return text
    except Exception:
        return ""


def _saveticker_article_record(item: dict, base: str) -> dict:
    from reports.raw_archive import save_extracted_text, save_raw_artifact

    fetched_at = datetime.now(KST)
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or item.get("link") or "").strip()
    content = str(item.get("content") or "").strip()
    summary = str(item.get("group_summary") or "").strip()
    body_raw = _combine_body_raw(content, summary)
    if len(body_raw) < 80 and url:
        body_raw = _combine_body_raw(content, summary, _fetch_saveticker_article_body(url))
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
        ttl_days=30,
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
            record = _saveticker_article_record(item, base)
            body_raw = record["body_raw"] or _combine_body_raw(item.get("content"), item.get("group_summary")) or text
            events.append({
                "source": "saveticker",
                "source_url": base,
                "title": title,
                "url": item.get("url") or item.get("link") or "",
                "published_at": item.get("created_at") or item.get("published_at") or "",
                "body_raw": body_raw,
                "body": body_raw,
                "body_excerpt": body_raw[:500],
                "tickers": _normalize_tickers(item.get("tickers")) or _extract_tickers(text),
                "tags": item.get("tag_names") or [],
                "raw_path": record["raw_path"],
                "text_path": record["text_path"],
                "manifest_path": record["manifest_path"],
                "raw_sha256": record["raw_sha256"],
                "raw_source": record["raw_source"],
            })
    return events


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
            return events

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
    return events


def _telegram_titles_from_html(html_text: str, channel: str) -> tuple[list[str], list[str]]:
    """t.me/s/<channel> 공개 HTML 에서 메시지 텍스트·링크 추출 (순수 — 테스트 가능).

    jina 마크다운의 **bold** 파싱은 굵은 제목이 없는 채널에서 0건이 되는 함정
    (insidertracking 수집 공백의 유력 원인) → 위젯 메시지 div 직접 파싱 폴백.
    """
    titles = []
    for m in re.finditer(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                         html_text, re.S):
        raw = re.sub(r"<br\s*/?>", " ", m.group(1))
        txt = html_mod.unescape(re.sub(r"<[^>]+>", "", raw))
        txt = " ".join(txt.split())
        if txt:
            titles.append(txt)
    urls = re.findall(rf'href="(https://t\.me/{re.escape(channel)}/\d+)"', html_text)
    return titles, urls


TELEGRAM_BODY_MAX = 3000    # 이벤트 body 상한 (레딧 분석 등 장문 구조화 파싱용)


def _telegram_message_texts(channel: str) -> tuple[list[str], list[str]]:
    """t.me/s 직접 HTML — (전체 메시지 텍스트 목록, 링크 목록). 실패 시 ([], [])."""
    try:
        resp = _bounded_get(f"https://t.me/s/{channel}", timeout=15)
        return _telegram_titles_from_html(resp.text, channel)
    except Exception as e:
        logger.info("telegram:%s 직접 HTML 실패: %s", channel, e)
        return [], []


def fetch_telegram_channel_events(channels: list[str] = TELEGRAM_NEWS_CHANNELS) -> list[dict]:
    events = []
    for channel in channels:
        channel = channel.strip().lstrip("@")
        if not channel:
            continue
        titles: list[str] = []
        urls: list[str] = []
        bodies_by_url: dict[str, str] = {}
        try:
            resp = _bounded_get(f"https://r.jina.ai/http://t.me/s/{channel}", timeout=20)
            markdown = resp.text
            titles = [" ".join(m.group(1).split()) for m in re.finditer(r"\*\*([^*]+)\*\*", markdown)]
            urls = re.findall(rf"https://t\.me/{re.escape(channel)}/\d+", markdown)
        except Exception as e:
            logger.warning("telegram:%s jina 수집 실패 — 직접 HTML 폴백 시도: %s", channel, e)

        if titles:
            # jina 성공 = 제목만 확보 → 장문 본문(레딧 분석·프리마켓 등)은 직접 HTML 로 보강
            texts, t_urls = _telegram_message_texts(channel)
            bodies_by_url = {u: t[:TELEGRAM_BODY_MAX] for u, t in zip(t_urls, texts)}
        else:
            # 폴백: t.me/s 공개 프리뷰 직접 파싱 (jina 장애/레이트리밋·bold 없는 채널 대응)
            texts, urls = _telegram_message_texts(channel)
            titles = texts
            bodies_by_url = {u: t[:TELEGRAM_BODY_MAX] for u, t in zip(urls, texts)}

        if not titles:
            logger.warning("telegram:%s 수집 0건 (jina·직접 모두) — 채널명/차단 확인 필요", channel)
            _note_error(f"telegram:{channel}", "jina·직접 HTML 모두 0건 — 채널명/차단 확인")
        else:
            _LAST_ERRORS.pop(f"telegram:{channel}", None)
        for idx, title in enumerate(titles):
            if not title:
                continue
            # 이모지·기호 단독 항목 제외 (실제 글자 4자 미만)
            if len(re.sub(r"[^\w가-힣]", "", title)) < 4:
                continue
            url = urls[idx] if idx < len(urls) else ""
            body = bodies_by_url.get(url, "")
            scan_text = body or title           # 티커/테마 추출은 본문 우선(장문 포스트 대응)
            tags = _extract_news_tags(scan_text)
            try:                                # 포스트 유형 태그 (레딧분석·속보·프리마켓 — 표시/필터용)
                from reports.social_sentiment import classify_post
                kind = {"reddit_analysis": "레딧분석", "breaking": "속보",
                        "premarket": "프리마켓"}.get(classify_post(scan_text))
                if kind and kind not in tags:
                    tags = tags + [kind]
            except Exception:
                pass
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
    return events


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
                "title": f"{label} {maturity}: {value:.3f}%",
                "url": f"https://www.worldgovernmentbonds.com/country/{country}/#{maturity}",
                "tickers": [],
                "tags": ["금리/채권"],
                "metrics": {"country": country, "maturity": maturity, "yield_pct": value},
            })
    if events:
        _LAST_ERRORS.pop("worldgovernmentbonds", None)
    return events


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
}


def expected_sources() -> list[str]:
    """수집기가 시도해야 하는 소스 전체 (텔레그램은 채널별 분리 — 채널 단위 공백 감지)."""
    return (["saveticker", "arca"]
            + [f"telegram:{c}" for c in TELEGRAM_NEWS_CHANNELS]
            + ["yahoo_finance", "fred", "worldgovernmentbonds"])


def update_source_health(events: list[dict], cache_dir: Path | str = DEFAULT_CACHE_DIR,
                         now: datetime | None = None) -> dict:
    """이번 수집 결과를 소스별 헬스 파일에 반영 — {source: {last_run, last_count, last_success, ...}}.

    count>0 이면 last_success 갱신. 0 이면 last_success 는 보존(공백 기간 측정의 기준점).
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / HEALTH_FILE
    health: dict = {}
    if path.exists():
        try:
            health = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            health = {}
    counts = Counter(str(e.get("source") or "") for e in events)
    for src in expected_sources():
        rec = health.get(src) or {}
        n = int(counts.get(src, 0))
        rec.setdefault("first_run", now.isoformat())   # 무성공 grace 기준점
        rec["last_run"] = now.isoformat()
        rec["last_count"] = n
        if n > 0:
            rec["last_success"] = now.isoformat()
            rec["last_success_count"] = n
            rec.pop("last_error", None)
        else:
            err = _LAST_ERRORS.get(src) or _LAST_ERRORS.get(src.split(":")[0])
            if err:
                rec["last_error"] = err
        health[src] = rec
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
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
    fetchers = [
        ("saveticker", fetch_saveticker_events),
        ("arca", lambda: fetch_arca_events(max_pages=int(os.getenv("STOCK_COLLECTOR_ARCA_PAGES", "2")))),
        ("telegram", fetch_telegram_channel_events),
        ("yahoo_finance", fetch_market_snapshot_events),
        ("fred", fetch_fred_macro_events),
        ("worldgovernmentbonds", fetch_world_gov_bond_events),
    ]
    events: list[dict] = []
    for name, fn in fetchers:
        try:
            got = fn()
            events.extend(got)
            logger.info("수집 %s: %d건", name, len(got))
        except Exception as e:                      # 한 소스 크래시가 전체 수집을 죽이지 않게
            logger.warning("수집 %s 실패(격리): %s", name, e)
    try:
        update_source_health(events, cache_dir=cache_dir, now=now)
    except Exception as e:
        logger.warning("소스 헬스 기록 실패(무시): %s", e)
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
    raise SystemExit(main())
