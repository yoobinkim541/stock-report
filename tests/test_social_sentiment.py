#!/usr/bin/env python3
"""test_social_sentiment.py — WSB/속보/프리마켓 포스트 구조화 (무네트워크·실포맷 샘플)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reports import social_sentiment as SS

# 실제 insidertracking 포맷 축약 샘플 (사용자 제공 포스트 기반)
SAMPLE = """미국 레딧 게시물 분석
(2026년 7월 5일 00:00~09:30 기준)

💾 MU / SNDK - AI 메모리

· "월요일 메모리 갭업" 기대감 폭발
· MU ATH, SNDK ATH 반복 언급
· 2027까지 홀드 선언도 등장

🧠 NVDA / AI 반도체

· "반도체 버블 끝났다"는 FUD 무시
· AI 사이클 아직 초반이라는 DD 반복

🚗 TSLA

· 예상보다 좋은 인도량으로 강세론 회복

📈 SPY / 시장

· "월요일 V자 반등" 기대감 강함

🔥 현재 WSB 전체 시장 심리

· 메모리(MU·SNDK)가 압도적인 주인공
· YOLO 콜옵션 심리 매우 강함
· AI 버블 경고는 거의 무시되는 분위기

작성자: 미국 주식 인사이더
t.me/insidertracking"""


def test_classify_post_types():
    assert SS.classify_post(SAMPLE) == "reddit_analysis"
    assert SS.classify_post("🚨 Breaking news: 연준 긴급 회의") == "breaking"
    assert SS.classify_post("속보 - 관세 발표") == "breaking"
    assert SS.classify_post("프리마켓 뉴스 요약 7/7") == "premarket"
    assert SS.classify_post("일반 종목 코멘트") == "other"
    assert SS.classify_post("") == "other"


def test_parse_reddit_sections_structure():
    secs = SS.parse_reddit_sections(SAMPLE)
    heads = [s["heading"] for s in secs]
    assert any("MU / SNDK" in h for h in heads)
    assert any("TSLA" in h for h in heads)
    mu = next(s for s in secs if "MU / SNDK" in s["heading"])
    assert mu["tickers"] == ["MU", "SNDK"]                    # 불용어(AI) 제외·순서 보존
    assert len(mu["bullets"]) == 3 and "갭업" in mu["bullets"][0]
    nvda = next(s for s in secs if "NVDA" in s["heading"])
    assert nvda["tickers"] == ["NVDA"]                        # FUD·DD·AI 불용어 제외
    assert all(s["bullets"] for s in secs)                    # 불릿 없는 유령 섹션 없음


def test_sentiment_summary_picks_latest_and_mood():
    events = [
        {"source": "telegram:insidertracking", "title": "미국 레딧 게시물 분석 (old)",
         "body": SAMPLE, "published_at": "2026-07-04T10:00:00+09:00", "url": "https://t.me/i/1"},
        {"source": "telegram:insidertracking", "title": "미국 레딧 게시물 분석",
         "body": SAMPLE, "published_at": "2026-07-05T10:00:00+09:00", "url": "https://t.me/i/2"},
        {"source": "telegram:insidertracking", "title": "일반 뉴스", "body": "짧은 글",
         "published_at": "2026-07-06T10:00:00+09:00", "url": ""},
    ]
    s = SS.sentiment_summary(events)
    assert s is not None and s["url"] == "https://t.me/i/2"    # 최신 분석 포스트 선택
    assert "MU" in s["top_tickers"] and "SNDK" in s["top_tickers"]
    assert s["mood_bullets"] and "주인공" in s["mood_bullets"][0]


def test_sentiment_summary_none_when_no_analysis():
    assert SS.sentiment_summary([]) is None
    assert SS.sentiment_summary([{"title": "그냥 뉴스", "body": "내용"}]) is None


def test_digest_line():
    s = SS.sentiment_summary([{"title": "미국 레딧 게시물 분석", "body": SAMPLE,
                               "published_at": "2026-07-05T10:00:00+09:00"}])
    line = SS.digest_line(s)
    assert line.startswith("레딧/WSB 심리(2026-07-05)")
    assert "MU" in line
    assert SS.digest_line(None) is None


def test_collector_tags_post_type():
    """수집기가 포스트 유형 태그(레딧분석 등)를 붙이는지 — build_digest 배선 포함."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "reports"))
    import source_collector as sc
    events = [{"source": "telegram:insidertracking", "title": "미국 레딧 게시물 분석",
               "body": SAMPLE, "published_at": "2026-07-05T10:00:00+09:00",
               "tags": ["레딧분석"], "tickers": ["NVDA"]}]
    digest = sc.build_digest(events)
    assert "레딧/WSB 심리(2026-07-05)" in digest               # 다이제스트 한 줄 배선
