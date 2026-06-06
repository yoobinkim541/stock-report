#!/usr/bin/env python3
# /// script
# dependencies = ["anthropic>=0.100", "requests", "python-dotenv"]
# ///
"""
news_spike_detector.py — saveticker 속보 알림

동작 흐름:
  1. saveticker 수집 → JSONL 캐시 저장
  2. '속보' 태그 이벤트 중 미발송 건 필터
  3. Claude Haiku 중요도 판단 (1~10점, 7+ 알림)
  4. 텔레그램 발송 + 발송 완료 ID 기록 (재발송 방지)

크론 (매 1분):
    * * * * * cd /home/ubuntu/projects/stock-report && uv run python news_spike_detector.py >> /tmp/news_spike.log 2>&1

환경변수:
    STOCK_BOT_TOKEN      — 텔레그램 봇 토큰 (필수)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID (필수)
    ANTHROPIC_API_KEY    — Claude 중요도 판단용 (없으면 모든 속보 발송)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST        = timezone(timedelta(hours=9))
CACHE_DIR  = Path(os.path.expanduser("~/reports/source-cache"))
STATE_FILE = Path(os.path.expanduser("~/.cache/news_spike_state.json"))

BOT_TOKEN     = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID       = os.getenv("STOCK_BOT_CHAT_ID")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

# 발송 완료 ID를 이 시간 이상 지난 건 state에서 제거 (파일 비대화 방지)
STATE_TTL_HOURS    = 48
# 이 시간보다 오래된 속보는 처리 안 함 (재시작 후 과거 속보 폭탄 방지)
MAX_AGE_HOURS      = 2
# AI 중요도 임계값 (없으면 모든 속보 발송)
IMPORTANCE_THRESHOLD = 7
AI_MODEL           = "claude-haiku-4-5-20251001"
MAX_SEND_PER_RUN   = 5


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """{'sent_ids': {'abc123': '2026-06-06T10:00:00+09:00', ...}}"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sent_ids": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(STATE_FILE)


def _prune_state(state: dict, now: datetime) -> None:
    cutoff = now - timedelta(hours=STATE_TTL_HOURS)
    sent = state.setdefault("sent_ids", {})
    stale = [k for k, v in sent.items() if datetime.fromisoformat(v) < cutoff]
    for k in stale:
        del sent[k]


# ── Collection ────────────────────────────────────────────────────────────────

def fetch_breaking_news(now: datetime) -> list[dict]:
    """saveticker에서 속보 태그 이벤트만 반환 (최근 MAX_AGE_HOURS 이내)."""
    from source_collector import fetch_saveticker_events, append_events, event_id

    events = fetch_saveticker_events()
    append_events(events, cache_dir=CACHE_DIR)

    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    breaking = []
    for e in events:
        if "속보" not in (e.get("tags") or []):
            continue
        pub = e.get("published_at") or ""
        try:
            ts = datetime.fromisoformat(pub)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=KST)
            if ts < cutoff:
                continue
        except Exception:
            continue
        e["id"] = e.get("id") or event_id(e)
        breaking.append(e)

    return sorted(breaking, key=lambda e: e.get("published_at", ""))


# ── AI importance judgment ────────────────────────────────────────────────────

def judge_importance(event: dict) -> tuple[int, str]:
    """Claude Haiku로 단일 속보의 주식 시장 중요도 판단.

    Returns:
        (score, reason) — score 1~10
        ANTHROPIC_API_KEY 없으면 (10, "AI 판단 건너뜀") 반환
    """
    if not ANTHROPIC_KEY:
        return 10, "AI 판단 건너뜀"

    title   = event.get("title") or ""
    tags    = [t for t in (event.get("tags") or []) if not t.startswith("$") and t != "속보"]
    tickers = [t.lstrip("$") for t in (event.get("tags") or []) if t.startswith("$")]

    context = []
    if tickers:
        context.append(f"관련 종목: {', '.join(tickers)}")
    if tags:
        context.append(f"분류: {', '.join(tags)}")
    context_str = " | ".join(context) if context else "없음"

    prompt = f"""미국 기술주 중심 포트폴리오(MSFT·NVDA·GOOGL·ORCL·QQQI·SAP·UNH·SGOV·SPMO) 투자자 관점에서, 아래 속보가 즉각적인 대응이 필요한 중요한 이벤트인지 판단해줘.

속보: {title}
추가 정보: {context_str}

답변 형식 (반드시 이 형식만, 다른 말 없이):
점수: [1-10]
이유: [한 줄, 40자 이내]

점수 기준:
9~10: 시장 충격 (연준 긴급발표·전쟁 확전·반도체 수출금지 등)
7~8: 즉각 대응 필요 (보유 종목 실적 쇼크·급등락 원인·핵심 규제)
5~6: 모니터링 (간접 영향 업종)
1~4: 노이즈 (무관한 뉴스·단순 루머·중요도 낮은 일반 정보)"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        logger.debug("AI 원문: %s", raw)

        score, reason = 5, "파싱 실패"
        for line in raw.splitlines():
            if line.startswith("점수:"):
                try:
                    score = max(1, min(10, int(line.split(":")[1].strip().split()[0])))
                except Exception:
                    pass
            elif line.startswith("이유:"):
                reason = line.split(":", 1)[1].strip()
        return score, reason

    except Exception as e:
        logger.warning("AI 판단 실패: %s", e)
        return 10, f"AI 오류"


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 미설정")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("텔레그램 발송 실패: %s", e)
        return False


def _format_alert(event: dict, score: int, reason: str) -> str:
    title   = event.get("title") or "[제목 없음]"
    tickers = [t.lstrip("$") for t in (event.get("tags") or []) if t.startswith("$")]
    pub     = event.get("published_at", "")[:16].replace("T", " ")

    importance = "🔴" if score >= 9 else "🟠" if score >= 7 else "🟡"
    ticker_str = f"  종목: {' · '.join(tickers)}" if tickers else ""

    lines = [
        f"📡 속보",
        f"{title}",
        f"{importance} 중요도 {score}/10 — {reason}",
    ]
    if ticker_str:
        lines.append(ticker_str)
    lines.append(f"  {pub} KST · saveticker")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(KST)
    logger.info("=== news_spike_detector [%s] ===", now.strftime("%Y-%m-%d %H:%M"))

    # 속보 수집
    try:
        breaking = fetch_breaking_news(now)
    except Exception as e:
        logger.error("수집 실패: %s", e)
        return
    logger.info("최근 %dh 속보: %d건", MAX_AGE_HOURS, len(breaking))

    if not breaking:
        return

    # 미발송 필터
    state = _load_state()
    _prune_state(state, now)
    sent_ids = state.setdefault("sent_ids", {})

    new_items = [e for e in breaking if e["id"] not in sent_ids]
    logger.info("미발송 속보: %d건", len(new_items))

    if not new_items:
        return

    # 중요도 판단 + 발송
    sent_count  = 0
    state_dirty = False

    for event in new_items:
        if sent_count >= MAX_SEND_PER_RUN:
            break

        score, reason = judge_importance(event)
        logger.info("[%s] 중요도 %d — %s", event["id"][:8], score, reason)

        # 중요도 미달이어도 발송 안 한 ID는 기록해서 재처리 방지
        sent_ids[event["id"]] = now.isoformat()
        state_dirty = True

        if score < IMPORTANCE_THRESHOLD:
            logger.info("중요도 미달 (%d) — 알림 건너뜀", score)
            continue

        msg = _format_alert(event, score, reason)
        if _send_telegram(msg):
            sent_count += 1
            logger.info("발송 완료: %s", (event.get("title") or "")[:40])

    if state_dirty:
        _save_state(state)

    logger.info("발송 %d건 완료", sent_count)


if __name__ == "__main__":
    main()
