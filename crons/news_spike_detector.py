#!/usr/bin/env python3
"""
news_spike_detector.py — saveticker 속보 알림

동작 흐름:
  1. saveticker 수집 → JSONL 캐시 저장
  2. '속보' 태그 이벤트 중 미발송 건 필터
  3. 규칙 기반 중요도 판단 (포트폴리오 종목/핵심 키워드, 7+ 알림)
  4. 텔레그램 발송 + 발송 완료 ID 기록 (재발송 방지)

크론 (매 1분):
    * * * * * cd /home/ubuntu/projects/stock-report && uv run python news_spike_detector.py >> /tmp/news_spike.log 2>&1

환경변수:
    STOCK_BOT_TOKEN      — 텔레그램 봇 토큰 (필수)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID (필수)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

import notify

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST        = timezone(timedelta(hours=9))
CACHE_DIR  = Path(os.path.expanduser("~/reports/source-cache"))
STATE_FILE = Path(os.path.expanduser("~/.cache/news_spike_state.json"))

BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID")

# 발송 완료 ID를 이 시간 이상 지난 건 state에서 제거 (파일 비대화 방지)
STATE_TTL_HOURS      = 48
# 이 시간보다 오래된 속보는 처리 안 함 (재시작 후 과거 속보 폭탄 방지)
MAX_AGE_HOURS        = 2
# 중요도 임계값 (hermes 실패 시 rule-based fallback도 동일 기준)
IMPORTANCE_THRESHOLD = 7
MAX_SEND_PER_RUN     = 5



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
    stale = []
    for k, v in sent.items():
        try:
            ts = datetime.fromisoformat(v)
            if ts.tzinfo is None:          # naive 저장값 → KST 가정 (cutoff 는 tz-aware)
                ts = ts.replace(tzinfo=KST)
        except Exception:
            stale.append(k)                # 파싱 불가 → 정리 대상
            continue
        if ts < cutoff:
            stale.append(k)
    for k in stale:
        del sent[k]


# ── Collection ────────────────────────────────────────────────────────────────

def fetch_breaking_news(now: datetime) -> list[dict]:
    """saveticker에서 속보 태그 이벤트만 반환 (최근 MAX_AGE_HOURS 이내)."""
    from reports.source_collector import fetch_saveticker_events, append_events, event_id

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


# ── Importance judgment ───────────────────────────────────────────────────────

# 포트폴리오 직접 보유 종목 ($ 없이)
_PORTFOLIO = {"MSFT", "QQQI", "ORCL", "NVDA", "GOOGL", "SAP", "UNH", "SGOV", "SPMO"}

# 제목에 포함 시 높은 관련성 키워드
_HIGH_SIGNAL = (
    "연준", "fomc", "기준금리", "금리 인상", "금리 인하", "금리인상", "금리인하",
    "관세", "수출 금지", "수출금지", "제재", "반독점",
    "실적", "어닝", "earnings", "eps", "매출 전망", "매출전망", "가이던스",
    "파산", "파산보호", "상장폐지", "합병", "인수",
    "반도체", "ai칩", "hbm", "엔비디아", "nvidia",
    "전쟁 확전", "핵", "공습", "침공",
    "s&p500 편입", "나스닥 편입", "편출",
)

# 제목에 포함 시 낮은 관련성 (노이즈) — 점수 낮춤
_LOW_SIGNAL = (
    "교황", "스포츠", "올림픽", "월드컵", "연예", "드라마", "영화",
    "날씨", "기상", "항공편", "여행",
)


def _rule_score(event: dict) -> tuple[int, str]:
    """hermes 없을 때 규칙 기반 중요도 점수."""
    title   = (event.get("title") or "").lower()
    tickers = {t.lstrip("$").upper() for t in (event.get("tags") or []) if t.startswith("$")}

    # 포트폴리오 종목 직접 언급
    if tickers & _PORTFOLIO:
        return 8, f"포트폴리오 종목 직접 관련 ({', '.join(tickers & _PORTFOLIO)})"

    # 고신호 키워드
    for kw in _HIGH_SIGNAL:
        if kw in title:
            return 7, f"핵심 시장 키워드 포함 ({kw})"

    # 저신호 키워드
    for kw in _LOW_SIGNAL:
        if kw in title:
            return 3, f"투자 관련성 낮음 ({kw})"

    # 기타 속보 — 기본 5점 (threshold 미달로 알림 안 보냄)
    return 5, "일반 속보"


def judge_importance(event: dict) -> tuple[int, str]:
    return _rule_score(event)


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 미설정")
        return False
    return notify.send_telegram(text, token=BOT_TOKEN, chat_id=CHAT_ID)


def _realtime_tag(tickers: list) -> str:
    """태그된 종목의 실시간 시세 동반표시(REALTIME_ENABLED·신선시). 없으면 빈 문자열(부가·차단無)."""
    try:
        from providers import realtime_quotes
        if not realtime_quotes.enabled() or not tickers:
            return ""
        bits = []
        for t in tickers[:5]:
            p = realtime_quotes.get_price(str(t).split(".")[0])
            if p:
                bits.append(f"{t} ${p:,.2f}")
        return ("  📈 실시간 " + " · ".join(bits)) if bits else ""
    except Exception:
        return ""


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
        rt = _realtime_tag(tickers)
        if rt:
            lines.append(rt)
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
