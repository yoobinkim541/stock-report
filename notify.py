"""notify.py — 텔레그램 발송 단일 진실원.

봇·전략·크론 14곳이 제각각 구현하던 sendMessage(토큰·chat_id·4096자 분할·에러 로깅·토큰
마스킹)를 하나로 통합한다. 환경변수 STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 를 기본으로 쓰되
인자로 override 가능. 동작은 기존 구현들의 합집합(줄바꿈 경계 분할 + 로그 토큰 마스킹).

공개 API:
    send_telegram(text, *, token=None, chat_id=None, timeout=10, split=True, parse_mode=None) -> bool
    send_photo(path, *, caption=None, ...) -> bool
    split_message(text, limit=4000) -> list[str]
"""
from __future__ import annotations

import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

TG_MAX_CHARS = 4000          # Telegram 4096자 제한 — 여유 96자
_DEFAULT_CHAT_ID = "5771238245"
_TOKEN_RE = re.compile(r"/bot[0-9]+:[A-Za-z0-9_-]+")


def _env_token() -> str | None:
    return os.getenv("STOCK_BOT_TOKEN")


def _env_chat_id() -> str:
    return os.getenv("STOCK_BOT_CHAT_ID", _DEFAULT_CHAT_ID)


def _mask(text: object, token: str | None) -> str:
    """로그에서 봇 토큰 마스킹 (URL 에 박힌 토큰까지)."""
    s = str(text)
    if token:
        s = s.replace(token, "***")
    return _TOKEN_RE.sub("/bot***", s)


def split_message(message: str, limit: int = TG_MAX_CHARS) -> list[str]:
    """4096자 제한 대응 — 줄바꿈 경계에서 분할(절단 금지)."""
    parts: list[str] = []
    message = str(message)
    while len(message) > limit:
        cut = message.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(message[:cut])
        message = message[cut:].lstrip("\n")
    if message:
        parts.append(message)
    return parts or [""]


def _post_with_retry(url: str, payload: dict, *, timeout: int, token: str | None,
                     attempts: int = 3) -> bool:
    """429(retry_after 존중)·5xx·네트워크 예외만 재시도. 4xx 는 즉시 실패(재시도 무의미)."""
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            if resp.ok:
                return True
            if resp.status_code == 429:
                try:
                    delay = float((resp.json().get("parameters") or {}).get("retry_after") or delay)
                except Exception:
                    pass
                logger.warning("텔레그램 429 — %.0fs 후 재시도 (%d/%d)", delay, attempt, attempts)
            elif resp.status_code >= 500:
                logger.warning("텔레그램 HTTP %s (%d/%d)", resp.status_code, attempt, attempts)
            else:
                logger.error("텔레그램 전송 실패 HTTP %s: %s", resp.status_code, _mask(resp.text[:200], token))
                return False
        except Exception as e:
            logger.error("텔레그램 전송 실패 (%d/%d): %s", attempt, attempts, _mask(e, token))
        if attempt < attempts:
            time.sleep(min(delay, 30.0))
            delay = min(delay * 2, 30.0)
    return False


def send_telegram(text, *, token: str | None = None, chat_id=None,
                  timeout: int = 10, split: bool = True, parse_mode: str | None = None) -> bool:
    """텔레그램 sendMessage. 성공 True. 토큰 없으면 skip(False). 429/5xx/네트워크 오류는 최대 3회 재시도."""
    token = token or _env_token()
    if not token:
        logger.warning("STOCK_BOT_TOKEN 없음 — 텔레그램 발송 skip")
        return False
    chat_id = chat_id if chat_id is not None else _env_chat_id()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = split_message(text) if split else [str(text)]
    ok = True
    for part in parts:
        payload = {"chat_id": chat_id, "text": part}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if not _post_with_retry(url, payload, timeout=timeout, token=token):
            ok = False
    return ok


def send_photo(photo_path, *, caption: str | None = None, token: str | None = None,
               chat_id=None, timeout: int = 30) -> bool:
    """텔레그램 sendPhoto. 성공 True."""
    token = token or _env_token()
    if not token:
        logger.warning("STOCK_BOT_TOKEN 없음 — 사진 발송 skip")
        return False
    chat_id = chat_id if chat_id is not None else _env_chat_id()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            resp = requests.post(url, data=data, files={"photo": f}, timeout=timeout)
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("텔레그램 사진 전송 실패: %s", _mask(e, token))
        return False
