"""증권사 채널 글에 걸린 공개 리포트 PDF 의 본문을 이벤트로 만든다.

받는 조건은 셋이다. 증권사(sell_side) 채널의 글에 걸린 링크이고, 주소가 `source_publishers` 의
리포트 호스트(또는 그곳으로 넘겨 주는 단축 주소)이며, 응답이 실제 PDF 일 때. 그 밖의 호스트에는
요청을 보내지 않는다. 로그인·우회는 없다. 텔레그램에 직접 올라간 파일과 사진은 공개 주소가 없어
여기서 다루지 않고, 글 이벤트의 `attachments` 에 이름과 장수만 남는다.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

import safe_io
from reports import source_collector, source_publishers

logger = logging.getLogger(__name__)

SEEN_FILE = "broker_report_seen.json"
SEEN_DAYS = 21
MAX_BYTES = 15_000_000
MAX_PAGES = 40
MAX_REDIRECTS = 5
BODY_MAX = 6000         # 이벤트에 싣는 본문. 전체 본문은 보관 파일(text_path)에 있다.
MIN_TEXT = 400          # 이보다 짧으면 스캔 이미지 PDF 로 보고 본문 없음으로 기록한다.
RAW_TTL_DAYS = 30


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_candidate(url: str) -> bool:
    return (urlparse(url).scheme in {"http", "https"}
            and _host(url) in source_publishers.REPORT_HOSTS | source_publishers.LINK_SHORTENERS)


def fetch_pdf(url: str, *, get=requests.get) -> tuple[bytes | None, str, str]:
    """(PDF 바이트, 최종 주소, 사유). 리다이렉트는 한 단계씩 보고, 허용 목록 밖으로 나가면 멈춘다."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if not is_candidate(current):
            return None, current, "host_not_allowed"
        with get(current, headers=source_collector.HEADERS, timeout=20, stream=True,
                 allow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                current = urljoin(current, response.headers.get("Location") or "")
                continue
            if response.status_code != 200:
                return None, current, f"http_{response.status_code}"
            if _host(current) not in source_publishers.REPORT_HOSTS:
                return None, current, "not_a_report_host"
            declared = response.headers.get("Content-Length") or ""
            if declared.isdigit() and int(declared) > MAX_BYTES:
                return None, current, "too_large"
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    return None, current, "too_large"
            if not bytes(data[:1024]).lstrip().startswith(b"%PDF-"):
                return None, current, "not_pdf"
            return bytes(data), current, "ok"
    return None, current, "too_many_redirects"

_PRIVATE_USE = re.compile("[\ue000-\uf8ff]")


def pdf_text(data: bytes, *, max_pages: int = MAX_PAGES) -> tuple[str, int]:
    """(본문, 전체 쪽수). 앞에서부터 max_pages 쪽까지만 읽는다."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    parts = []
    for index in range(min(pages, max_pages)):
        # PDF 글머리표는 글꼴 전용 영역(U+E000~F8FF) 문자로 나와 읽을 수 없으므로 뺀다.
        lines = [" ".join(_PRIVATE_USE.sub(" ", line).split())
                 for line in (reader.pages[index].extract_text() or "").splitlines()]
        parts.append("\n".join(line for line in lines if line))
    return "\n\n".join(part for part in parts if part).strip(), pages


def _load_seen(cache_dir: Path, now: datetime) -> dict[str, dict]:
    try:
        seen = json.loads((cache_dir / SEEN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cutoff = (now - timedelta(days=SEEN_DAYS)).isoformat()
    return {link: row for link, row in seen.items()
            if isinstance(row, dict) and str(row.get("at") or "") >= cutoff}


def _report_event(channel: str, message: dict, link: str, final_url: str, data: bytes,
                  text: str, pages: int, now: datetime) -> dict:
    from reports.raw_archive import save_extracted_text, save_raw_artifact

    post_title = str(message.get("title") or "").strip()
    title = f"[리포트] {post_title[:150]}" if post_title else f"[리포트] {channel}"
    record = save_raw_artifact(source=f"broker_report:{channel}", kind="pdf", fetched_at=now, title=title,
                               url=final_url, payload=data, suffix=".pdf", ttl_days=RAW_TTL_DAYS)
    save_extracted_text(record, text)
    complete = pages <= MAX_PAGES
    return {
        "source": f"broker_report:{channel}",
        "source_url": str(message.get("url") or f"https://t.me/s/{channel}"),
        "title": title,
        "url": final_url,
        "report_link": link,
        "body_raw": text[:BODY_MAX],
        "body": text[:source_collector.TELEGRAM_BODY_MAX],
        "body_excerpt": text[:500],
        "tickers": source_collector._mention_tickers(f"{message.get('body_raw') or ''}\n{text[:BODY_MAX]}"),
        "tags": source_collector._extract_news_tags(text[:BODY_MAX]),
        "pages": pages,
        "text_chars": len(text),
        "raw_path": record["raw_path"],
        "text_path": record["text_path"],
        "manifest_path": record["manifest_path"],
        "raw_sha256": record["sha256"],
        "raw_source": record["source"],
        "text_complete": complete,
        "fulltext_status": "full" if complete else "partial",
        "body_coverage": "full" if complete else "partial",
        "extraction_scope": "extracted_text",
        "source_text_scope": "extracted_text",
        "transport": "public_pdf",
    }


def fetch_broker_report_events(channels: list[str] | None = None, *, cache_dir: Path | str | None = None,
                               limit: int | None = None, now: datetime | None = None,
                               get=requests.get) -> list[dict]:
    """증권사 채널의 최근 글에서 아직 받지 않은 리포트 링크를 limit 개까지 받아 이벤트로 돌려준다."""
    now = now or datetime.now(source_collector.KST)
    cache_dir = Path(cache_dir or source_collector.DEFAULT_CACHE_DIR)
    limit = int(os.getenv("STOCK_COLLECTOR_REPORTS_PER_RUN", "4")) if limit is None else limit
    selected = [channel for channel in (channels or source_collector.TELEGRAM_NEWS_CHANNELS)
                if source_publishers.publisher(f"telegram:{channel}")["role"] == "sell_side"]
    seen = _load_seen(cache_dir, now)
    events: list[dict] = []
    attempts = 0
    for channel in selected:
        source = f"broker_report:{channel}"
        source_collector._LAST_ERRORS.pop(source, None)
        # 최신 글부터: 미리보기 화면은 오래된 글이 위에 있다.
        for message in reversed(source_collector._telegram_direct_messages(channel)):
            for link in message.get("links") or []:
                if link in seen or not is_candidate(link):
                    continue
                if len(events) >= limit or attempts >= limit * 3:
                    break
                attempts += 1
                status = "error"
                try:
                    data, final_url, status = fetch_pdf(link, get=get)
                    if data is not None:
                        text, pages = pdf_text(data)
                        if len(text) < MIN_TEXT:
                            status = "no_text"
                        else:
                            events.append(_report_event(channel, message, link, final_url, data, text, pages, now))
                except Exception as exc:  # 한 링크의 실패가 나머지 수집을 막지 않는다
                    logger.warning("%s 리포트 수집 실패 %s: %s", source, link, exc)
                    source_collector._note_error(source, exc)
                    status = f"error:{type(exc).__name__}"
                seen[link] = {"at": now.isoformat(), "channel": channel, "status": status}
    if attempts:
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(str(cache_dir / SEEN_FILE), seen)
    return [source_collector._classify_event(event) for event in events]
