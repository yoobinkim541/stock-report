import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports import source_wiki_curator as swc

KST = timezone(timedelta(hours=9))


def test_build_wiki_pages_from_events_groups_source_backed_topic():
    events = [
        {
            "source": "saveticker",
            "title": "엔비디아 AI 서버 수요 확대",
            "url": "https://saveticker.com/nvda",
            "body_raw": "AI 서버와 반도체 수요가 확대됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "text_path": "/tmp/nvda.txt",
            "raw_path": "/tmp/nvda.json",
            "classification": {"kind": "article", "topic": "기술/AI", "trust": "B"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "AI 데이터센터 전력 수요 증가",
            "url": "https://t.me/insidertracking/1",
            "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "text_path": "/tmp/tg.txt",
            "raw_path": "/tmp/tg.html",
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 23, 10, 0, tzinfo=KST))

    assert {page["id"] for page in pages} == {"source-topic-기술-ai", "source-ticker-nvda"}
    page = next(page for page in pages if page["id"] == "source-topic-기술-ai")
    assert page["surface"] == "market"
    assert page["kind"] == "source_digest"
    assert page["status"] == "reviewed"
    assert "기술/AI" in page["title"]
    assert "NVDA 2건" in page["summary"]
    assert "엔비디아 AI 서버 수요 확대" in page["body"]
    assert "source:saveticker" in page["tags"]
    assert "source:telegram" in page["tags"]
    assert "/tmp/nvda.txt" in page["source_refs"]
    assert "https://t.me/insidertracking/1" in page["source_refs"]


def test_build_wiki_pages_from_events_dedupes_source_refs_and_skips_weak_groups():
    events = [
        {
            "source": "telegram:insidertracking",
            "title": "짧은 단독 뉴스",
            "url": "https://t.me/insidertracking/1",
            "body_raw": "짧다",
            "topic": "잡담",
            "tags": ["잡담"],
            "classification": {"kind": "community_signal", "topic": "잡담", "trust": "C"},
        },
        {
            "source": "saveticker",
            "title": "금리와 채권 시장 변동",
            "url": "https://saveticker.com/rates",
            "body_raw": "연준과 국채 금리가 시장 변동성을 키웠다.",
            "topic": "금리/채권",
            "tags": ["금리/채권"],
            "text_path": "/tmp/rates.txt",
            "raw_path": "/tmp/rates.json",
            "classification": {"kind": "article", "topic": "금리/채권", "trust": "B"},
        },
        {
            "source": "saveticker",
            "title": "국채 금리 재상승",
            "url": "https://saveticker.com/rates",
            "body_raw": "국채 금리가 다시 상승했다.",
            "topic": "금리/채권",
            "tags": ["금리/채권"],
            "text_path": "/tmp/rates.txt",
            "raw_path": "/tmp/rates.json",
            "classification": {"kind": "article", "topic": "금리/채권", "trust": "B"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 23, 10, 0, tzinfo=KST))

    assert [page["id"] for page in pages] == ["source-topic-금리-채권"]
    refs = pages[0]["source_refs"]
    assert refs.count("https://saveticker.com/rates") == 1
    assert refs.count("/tmp/rates.txt") == 1
    assert refs.count("/tmp/rates.json") == 1
