#!/usr/bin/env python3
# /// script
# dependencies = ["anthropic>=0.100", "requests", "python-dotenv"]
# ///
"""
news_spike_detector.py — 1분마다 실행, 뉴스 급증 시 AI 중요도 판단 후 텔레그램 알림

동작 흐름:
  1. 뉴스 소스(saveticker, arca, telegram) 수집 → JSONL 캐시 저장
  2. 최근 RECENT_WINDOW_MINS vs 이전 BASELINE_WINDOW_MINS 테마/티커 빈도 비교
  3. 스파이크 감지 → Claude Haiku로 중요도 판단 (1~10점)
  4. 임계점 이상이면 텔레그램 알림 (AI 판단 이유 포함)
  5. 동일 키 COOLDOWN_HOURS 이내 재알림 방지

크론 (매 1분):
    * * * * * cd /home/ubuntu/projects/stock-report && uv run python news_spike_detector.py >> /tmp/news_spike.log 2>&1

환경변수:
    STOCK_BOT_TOKEN      — 텔레그램 봇 토큰 (필수)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID (필수)
    ANTHROPIC_API_KEY    — Claude 중요도 판단용 (없으면 AI 판단 건너뜀)
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
CACHE_DIR  = Path(os.path.expanduser("~/reports/source-cache"))
STATE_FILE = Path(os.path.expanduser("~/.cache/news_spike_state.json"))

BOT_TOKEN       = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID         = os.getenv("STOCK_BOT_CHAT_ID")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")

# ── Spike detection parameters ────────────────────────────────────────────────
RECENT_WINDOW_MINS   = 15    # 최근 창 (분) — 10→15 덜 민감하게
BASELINE_WINDOW_MINS = 120   # 기준 창 (분, recent 포함)
SPIKE_RATIO          = 5.0   # 최소 배율 — 3.0→5.0 덜 민감하게
MIN_RECENT_COUNT     = 5     # 최소 건수 — 3→5 노이즈 방지
MIN_BASELINE_EVENTS  = 10    # cold start 방지: 베이스라인 최소 이벤트 수
COOLDOWN_HOURS       = 2     # 동일 키 재알림 방지 — 1→2시간
MAX_ALERTS_PER_RUN   = 2     # 단일 실행당 최대 알림 수 — 3→2

# ── AI importance filter ──────────────────────────────────────────────────────
AI_IMPORTANCE_THRESHOLD = 7  # 7점 이상만 알림 (1~10)
AI_MODEL                = "claude-haiku-4-5-20251001"


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("STOCK_BOT_TOKEN / STOCK_BOT_CHAT_ID 미설정 — 알림 건너뜀")
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


# ── State (cooldown) ──────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(STATE_FILE)


def _is_cooled_down(state: dict, key: str, now: datetime) -> bool:
    ts_str = state.get(key)
    if not ts_str:
        return True
    try:
        last = datetime.fromisoformat(ts_str)
        return (now - last).total_seconds() >= COOLDOWN_HOURS * 3600
    except Exception:
        return True


# ── Collection ────────────────────────────────────────────────────────────────

def collect_news() -> int:
    """뉴스 소스만 수집 (market snapshot / FRED 제외). 새 이벤트 수 반환."""
    from source_collector import (
        fetch_saveticker_events,
        fetch_arca_events,
        fetch_telegram_channel_events,
        append_events,
    )
    events = (
        fetch_saveticker_events()
        + fetch_arca_events(max_pages=1)
        + fetch_telegram_channel_events()
    )
    return append_events(events, cache_dir=CACHE_DIR)


# ── Windowing ─────────────────────────────────────────────────────────────────

def _parse_ts(event: dict) -> datetime:
    ts = datetime.fromisoformat(event["collected_at"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)
    return ts


def _split_windows(now: datetime) -> tuple[list[dict], list[dict]]:
    """최근 RECENT_WINDOW_MINS 이벤트(recent)와 그 이전 이벤트(baseline) 분리."""
    from source_collector import load_recent_events

    all_events    = load_recent_events(CACHE_DIR, now=now, hours=BASELINE_WINDOW_MINS // 60)
    recent_cutoff = now - timedelta(minutes=RECENT_WINDOW_MINS)

    recent   = [e for e in all_events if _parse_ts(e) >= recent_cutoff]
    baseline = [e for e in all_events if _parse_ts(e) < recent_cutoff]
    return recent, baseline


# ── Spike detection ───────────────────────────────────────────────────────────

def _count_themes(events: list[dict]) -> Counter:
    return Counter(tag for e in events for tag in (e.get("tags") or []))


def _count_tickers(events: list[dict]) -> Counter:
    from source_collector import PORTFOLIO_TICKERS
    return Counter(
        t for e in events for t in (e.get("tickers") or [])
        if t in PORTFOLIO_TICKERS
    )


def detect_spikes(recent: list[dict], baseline: list[dict]) -> list[dict]:
    """스파이크 목록을 ratio 내림차순으로 반환.

    반환 항목:
      key           — 쿨다운 키 (예: "theme/기술/AI", "ticker/NVDA")
      label         — 표시 레이블
      recent_count  — 최근 창 건수
      baseline_avg  — 같은 창 크기 기준 베이스라인 평균 건수
      ratio         — recent_rate / baseline_rate (inf = 신규 등장)
      events        — 매칭 이벤트 목록
    """
    if len(baseline) < MIN_BASELINE_EVENTS:
        logger.info("베이스라인 부족 (%d건) — cold start, 스파이크 감지 건너뜀", len(baseline))
        return []

    baseline_mins = BASELINE_WINDOW_MINS - RECENT_WINDOW_MINS

    def _spike_entry(key: str, label: str, r_count: int, b_count: int, matched: list[dict]) -> dict | None:
        if r_count < MIN_RECENT_COUNT:
            return None
        b_rate = b_count / baseline_mins
        r_rate = r_count / RECENT_WINDOW_MINS
        ratio  = (r_rate / b_rate) if b_rate > 0 else float("inf")
        if ratio < SPIKE_RATIO:
            return None
        return {
            "key": key,
            "label": label,
            "recent_count": r_count,
            "baseline_avg": round(b_rate * RECENT_WINDOW_MINS, 1),
            "ratio": ratio,
            "events": matched,
        }

    spikes: list[dict] = []

    rt, bt = _count_themes(recent), _count_themes(baseline)
    for theme, r_count in rt.items():
        entry = _spike_entry(
            key     = f"theme/{theme}",
            label   = f"테마: {theme}",
            r_count = r_count,
            b_count = bt.get(theme, 0),
            matched = [e for e in recent if theme in (e.get("tags") or [])],
        )
        if entry:
            spikes.append(entry)

    rk, bk = _count_tickers(recent), _count_tickers(baseline)
    for ticker, r_count in rk.items():
        entry = _spike_entry(
            key     = f"ticker/{ticker}",
            label   = f"종목: {ticker}",
            r_count = r_count,
            b_count = bk.get(ticker, 0),
            matched = [e for e in recent if ticker in (e.get("tickers") or [])],
        )
        if entry:
            spikes.append(entry)

    return sorted(spikes, key=lambda s: (s["ratio"] != float("inf"), -s["ratio"] if s["ratio"] != float("inf") else 0))


# ── AI importance judgment ────────────────────────────────────────────────────

def judge_importance(spike: dict) -> tuple[int, str]:
    """Claude Haiku로 스파이크 중요도 판단.

    Returns:
        (score, reason) — score 1~10, reason 한 줄 한국어
        ANTHROPIC_API_KEY 없으면 (10, "AI 판단 건너뜀") 반환 (알림 통과)
    """
    if not ANTHROPIC_KEY:
        return 10, "AI 판단 건너뜀 (ANTHROPIC_API_KEY 미설정)"

    titles = [
        (e.get("title") or "")[:100]
        for e in spike["events"][:8]
        if e.get("title")
    ]
    if not titles:
        return 5, "제목 없음"

    titles_text = "\n".join(f"- {t}" for t in titles)
    ratio_str   = f"{spike['ratio']:.1f}배" if spike["ratio"] != float("inf") else "신규 급등"

    prompt = f"""주식 포트폴리오 투자자 관점에서 아래 뉴스 급증이 즉각적인 대응이 필요한 중요한 이벤트인지 판단해줘.

급증 정보:
- 분류: {spike['label']}
- 최근 {RECENT_WINDOW_MINS}분간 {spike['recent_count']}건 ({ratio_str})

뉴스 헤드라인:
{titles_text}

포트폴리오: MSFT, QQQI, ORCL, NVDA, GOOGL, SAP, UNH, SGOV, SPMO (미국 기술주 중심)

답변 형식 (반드시 이 형식만):
점수: [1-10]
이유: [한 줄, 50자 이내]

점수 기준:
9~10: 시장 충격 수준 (연준 긴급발표, 전쟁 확전, 반도체 수출금지 등)
7~8: 즉각 대응 고려 (실적 쇼크, 규제 발표, 핵심 종목 급등락 원인)
5~6: 모니터링 필요 (관련 업종 뉴스, 간접 영향)
1~4: 단순 노이즈 (반복 뉴스, 사소한 분석 글)"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        response = msg.content[0].text.strip()
        logger.info("AI 판단 원문: %s", response)

        score = 5
        reason = "파싱 실패"
        for line in response.splitlines():
            if line.startswith("점수:"):
                try:
                    score = int(line.split(":")[1].strip().split()[0])
                    score = max(1, min(10, score))
                except Exception:
                    pass
            elif line.startswith("이유:"):
                reason = line.split(":", 1)[1].strip()
        return score, reason

    except Exception as e:
        logger.warning("AI 판단 실패: %s — 기본값 사용", e)
        return 10, f"AI 오류: {e}"


# ── Alert formatting ──────────────────────────────────────────────────────────

def _format_alert(spike: dict, score: int, reason: str, now: datetime) -> str:
    ratio = spike["ratio"]
    ratio_str = f"{ratio:.0f}배" if ratio != float("inf") else "신규 급등"

    importance_bar = "🔴" if score >= 9 else "🟠" if score >= 7 else "🟡"

    lines = [
        "🔥 뉴스 급증 감지",
        "━━━━━━━━━━━━━━",
        spike["label"],
        f"최근 {RECENT_WINDOW_MINS}분: {spike['recent_count']}건  (평균 {spike['baseline_avg']}건 → {ratio_str})",
        f"{importance_bar} 중요도 {score}/10 — {reason}",
        f"감지: {now.strftime('%H:%M KST')}",
        "━━━━━━━━━━━━━━",
    ]
    for ev in spike["events"][:5]:
        title  = (ev.get("title") or "[제목 없음]")[:80]
        source = ev.get("source", "unknown")
        url    = ev.get("url") or ""
        line   = f"• [{source}] {title}"
        if url:
            line += f"\n  {url}"
        lines.append(line)
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(KST)
    logger.info("=== news_spike_detector [%s] ===", now.strftime("%Y-%m-%d %H:%M"))

    try:
        new_count = collect_news()
        logger.info("새 이벤트: %d건", new_count)
    except Exception as e:
        logger.error("수집 실패: %s", e)
        return

    try:
        recent, baseline = _split_windows(now)
    except Exception as e:
        logger.error("창 분리 실패: %s", e)
        return
    logger.info(
        "최근 %d분: %d건 / 베이스라인 %d분: %d건",
        RECENT_WINDOW_MINS, len(recent),
        BASELINE_WINDOW_MINS - RECENT_WINDOW_MINS, len(baseline),
    )

    spikes = detect_spikes(recent, baseline)
    if not spikes:
        logger.info("스파이크 없음")
        return

    logger.info("스파이크 %d건 감지", len(spikes))

    state       = _load_state()
    sent_count  = 0
    state_dirty = False

    for spike in spikes:
        if sent_count >= MAX_ALERTS_PER_RUN:
            break
        key = spike["key"]
        if not _is_cooled_down(state, key, now):
            logger.info("쿨다운 중: %s", key)
            continue

        # AI 중요도 판단
        score, reason = judge_importance(spike)
        logger.info("중요도 판단: %s → %d점 (%s)", key, score, reason)

        if score < AI_IMPORTANCE_THRESHOLD:
            logger.info("중요도 미달 (%d < %d) — 알림 건너뜀: %s", score, AI_IMPORTANCE_THRESHOLD, key)
            continue

        msg = _format_alert(spike, score, reason, now)
        if _send_telegram(msg):
            state[key] = now.isoformat()
            state_dirty = True
            sent_count += 1
            logger.info("알림 전송: %s (score=%d)", key, score)

    if state_dirty:
        _save_state(state)

    if sent_count == 0:
        logger.info("발송 없음 (쿨다운 또는 중요도 미달)")


if __name__ == "__main__":
    main()
