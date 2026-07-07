"""reports/social_sentiment.py — 텔레그램 채널(insidertracking 등) 소셜/속보 포스트 구조화.

레딧(WSB) 게시물 분석·breaking news·프리마켓 뉴스처럼 **형식이 있는 장문 포스트**를
분류·파싱해 대시보드 카드·리포트 다이제스트·(게이트 하) 판단 보조에 쓴다. 전부 순수
함수(무네트워크) — 수집은 source_collector 텔레그램 경로(body 필드)가 담당.

정직 규율: 소셜 심리는 **표시·컨텍스트**다. 판단 반영은 news_labels LLM 라벨 →
news 축(기본 가중 0·신규 축 게이트 통과 시만 승격) 단일 경로 — 직접 신호 아님.
"""
from __future__ import annotations

import re
from collections import Counter

# 포스트 유형 분류 키워드 (제목/본문 선두부)
_REDDIT_PAT = re.compile(r"레딧|reddit|wsb", re.I)
_BREAKING_PAT = re.compile(r"속보|breaking", re.I)
_PREMARKET_PAT = re.compile(r"프리마켓|pre-?market", re.I)

# 섹션 헤더: 이모지 + 티커/주제 (예: "💾 MU / SNDK - AI 메모리", "🧠 NVDA / AI 반도체")
_SECTION_RE = re.compile(
    r"^\s*([\U0001F300-\U0001FAFF☀-➿\U0001F1E6-\U0001F1FF]{1,4})\s*(.+)$")
_BULLET_RE = re.compile(r"^\s*[·\-•ㆍ*]\s*(.+)$")
_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
_TICKER_STOP = {"AI", "ATH", "FUD", "FOMO", "YOLO", "DD", "ETF", "SPY", "WSB", "CEO",
                "IPO", "GDP", "CPI", "PER", "USD", "KRW", "BER", "FUK", "II", "III"}
# SPY 는 지수 ETF 로 유의미하지만 시장 섹션 전용 — 티커 추출에선 제외(노이즈), 시장 섹션 헤딩으로 잡힘


def classify_post(text: str) -> str:
    """포스트 유형 — 'reddit_analysis' | 'breaking' | 'premarket' | 'other'."""
    head = (text or "")[:200]
    if _REDDIT_PAT.search(head) and ("분석" in head or "analysis" in head.lower()):
        return "reddit_analysis"
    if _BREAKING_PAT.search(head):
        return "breaking"
    if _PREMARKET_PAT.search(head):
        return "premarket"
    return "other"


def _heading_tickers(heading: str) -> list[str]:
    """섹션 헤딩에서 티커 후보 추출 (대문자 2~5·불용어 제외·순서 보존)."""
    out = []
    for t in _TICKER_RE.findall(heading):
        if t not in _TICKER_STOP and t not in out:
            out.append(t)
    return out


def parse_reddit_sections(text: str) -> list[dict]:
    """레딧 분석 포스트 → [{emoji, heading, tickers, bullets}] (섹션 구조 보존).

    형식: 이모지 헤더 줄 + '·' 불릿들. 형식 밖 줄은 무시(관대한 파서 — 포맷 드리프트 내성).
    """
    sections: list[dict] = []
    cur: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        b = _BULLET_RE.match(line)
        if b and cur is not None:
            cur["bullets"].append(b.group(1).strip())
            continue
        m = _SECTION_RE.match(line)
        if m and not _BULLET_RE.match(line):
            heading = m.group(2).strip()
            if len(heading) < 2:
                continue
            cur = {"emoji": m.group(1), "heading": heading,
                   "tickers": _heading_tickers(heading), "bullets": []}
            sections.append(cur)
    return [s for s in sections if s["bullets"]]


def sentiment_summary(events: list[dict]) -> dict | None:
    """최근 이벤트 중 최신 레딧 분석 포스트 요약 — 대시보드 카드·다이제스트용.

    반환: {title, published_at, url, sections, top_tickers, mood_bullets} | None(분석 포스트 없음).
    top_tickers: 섹션 헤딩 티커 언급 빈도순. mood_bullets: '전체 시장 심리' 섹션 불릿.
    """
    candidates = []
    for e in events or []:
        body = e.get("body") or e.get("title") or ""
        if classify_post(body) == "reddit_analysis":
            candidates.append((str(e.get("published_at") or ""), e, body))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, ev, body = candidates[0]
    sections = parse_reddit_sections(body)
    counts: Counter = Counter()
    mood: list[str] = []
    for s in sections:
        for t in s["tickers"]:
            counts[t] += 1
        if "심리" in s["heading"] or "시장" in s["heading"]:
            mood = s["bullets"][:5]
    return {
        "title": (ev.get("title") or "")[:120],
        "published_at": ev.get("published_at") or "",
        "url": ev.get("url") or "",
        "sections": sections,
        "top_tickers": [t for t, _ in counts.most_common(8)],
        "mood_bullets": mood,
    }


def digest_line(summary: dict | None) -> str | None:
    """다이제스트 한 줄 — '레딧/WSB 심리: MU·SNDK·NVDA 중심 (섹션 8개)'. 없으면 None."""
    if not summary or not summary.get("sections"):
        return None
    tks = "·".join(summary.get("top_tickers", [])[:5]) or "종목 특정 없음"
    mood = summary["mood_bullets"][0][:60] if summary.get("mood_bullets") else ""
    tail = f" — {mood}" if mood else ""
    return f"레딧/WSB 심리({str(summary.get('published_at'))[:10]}): {tks}{tail}"
