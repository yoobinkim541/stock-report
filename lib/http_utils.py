"""lib/http_utils.py — 공유 HTTP GET + User-Agent (providers 중복 제거, 행위 보존).

naver_kr·index_membership·edgar 가 각자 urllib + UA 를 반복하던 것을 통합. 각 호출처는 자신의
UA/timeout 으로 위임만 한다(동작 동일). edgar 는 SEC 준수용 별도 UA 유지.
"""
from __future__ import annotations

import urllib.request

# 브라우저 위장 UA(Naver·위키 등 공개 페이지) / SEC EDGAR 준수 UA(연락처 포함 필수)
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
EDGAR_UA = "stock-report research (yoobinkim2006@gmail.com)"


def http_get(url: str, *, timeout: int = 30, ua: str = DEFAULT_UA) -> bytes:
    """User-Agent 헤더로 GET → bytes. (예외는 호출처가 처리 — 기존 동작과 동일)."""
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    return urllib.request.urlopen(req, timeout=timeout).read()
