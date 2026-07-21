#!/usr/bin/env python3
"""saveticker_report_archive.py — SaveTicker 데일리 리포트 PDF 아카이브 크론.

리포트 페이지에서 PDF 링크를 찾고, 최신 PDF를 원본으로 저장한 뒤
기존 PDF/이미지 OCR 헬퍼로 텍스트를 추출해 파생 텍스트를 보관한다.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from bot.attachment_parser import extract_text_from_pdf_or_ocr
from reports.raw_archive import cleanup_expired_raw_artifacts, save_extracted_text, save_raw_artifact

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
REPORT_PAGE_URL = os.getenv("SAVE_TICKER_REPORT_PAGE_URL", "https://saveticker.com/report")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}


def _discover_report_pdf_urls_from_html(html_text: str, base_url: str = REPORT_PAGE_URL) -> list[str]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href:
            continue
        if ".pdf" not in href.lower():
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        urls.append(full_url)
    if urls:
        return urls

    return urls


def discover_report_pdf_urls(report_page_url: str = REPORT_PAGE_URL) -> list[str]:
    resp = requests.get(report_page_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return _discover_report_pdf_urls_from_html(resp.text, base_url=report_page_url)


def download_report_pdf(url: str) -> Path:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    suffix = Path(urlparse(url).path).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(prefix="saveticker-report-", suffix=suffix, delete=False) as tf:
        tf.write(resp.content)
        return Path(tf.name)


def download_latest_saveticker_report(report_page_url: str = REPORT_PAGE_URL, ttl_days: int | None = None) -> dict | None:
    urls = discover_report_pdf_urls(report_page_url)
    if not urls:
        logger.info("SaveTicker 리포트 PDF 링크를 찾지 못함")
        return None

    report_url = urls[0]
    fetched_at = datetime.now(KST)
    pdf_path = download_report_pdf(report_url)
    try:
        raw_record = save_raw_artifact(
            source="saveticker_report_pdf",
            kind="pdf",
            fetched_at=fetched_at,
            title=Path(urlparse(report_url).path).stem or "saveticker report",
            url=report_url,
            payload=pdf_path.read_bytes(),
            suffix=Path(report_url).suffix or ".pdf",
        )
        text = extract_text_from_pdf_or_ocr(str(pdf_path)) or ""
        if text.strip():
            save_extracted_text(raw_record, text)
        cleanup_expired_raw_artifacts(now=fetched_at, ttl_days=ttl_days)
        return {
            **raw_record,
            "report_page_url": report_page_url,
            "downloaded_url": report_url,
            "pdf_local_path": str(pdf_path),
            "text_length": len(text.strip()),
        }
    finally:
        try:
            pdf_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("임시 PDF 삭제 실패: %s", exc)


def main() -> int:
    result = download_latest_saveticker_report()
    if result:
        logger.info("SaveTicker 리포트 아카이브 완료: %s", result.get("raw_path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
