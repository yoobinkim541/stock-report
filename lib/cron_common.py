"""lib/cron_common.py — 크론 공통 텔레그램 발송 (crons 중복 제거, 행위 보존).

크론 11개가 반복하던 `_send`(5변종, 일부 불일치)를 단일 안전 발송기로 통합.
주의: import 하려면 호출 크론이 먼저 `sys.path.insert(repo_root)` 로 부트스트랩돼 있어야 함.

(후속: load_dotenv·logging.basicConfig·KST 부트스트랩 dedup(init_cron)은 별도 — sys.path 선행 제약상
 가치 작고 부트스트랩 블록이 크론별로 엉켜 있어 점진 이전.)
"""
from __future__ import annotations

import os


def send_cron_telegram(text: str, *, timeout: int = 15) -> bool:
    """env(STOCK_BOT_TOKEN/CHAT_ID) 텔레그램 발송 — 통일 안전 발송기.

    키 없으면 False, 실패해도 조용히 False(크론 중단 방지), 성공 True. (기존 11 _send 변종 통합.)
    """
    token, chat = os.getenv("STOCK_BOT_TOKEN"), os.getenv("STOCK_BOT_CHAT_ID")
    if not token or not chat:
        return False
    try:
        import notify
        notify.send_telegram(text, token=token, chat_id=chat, timeout=timeout)
        return True
    except Exception:
        return False
