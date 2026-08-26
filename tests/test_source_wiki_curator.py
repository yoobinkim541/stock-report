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
    assert page["surface"] == "market"           # topic 그룹 — 시장 전반 주제
    ticker_page = next(page for page in pages if page["id"] == "source-ticker-nvda")
    assert ticker_page["surface"] == "ticker"     # ticker 그룹 — 개별 종목(위키 그래프 분산)
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


def test_build_wiki_pages_from_events_links_pages_sharing_events():
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
    by_id = {page["id"]: page for page in pages}

    assert by_id["source-topic-기술-ai"]["links"] == ["source-ticker-nvda"]
    assert by_id["source-ticker-nvda"]["links"] == ["source-topic-기술-ai"]


def test_build_wiki_pages_from_events_attaches_evidence_metadata():
    events = [
        {
            "source": "telegram:insidertracking",
            "title": "AI 데이터센터 전력 수요 증가",
            "url": "https://t.me/insidertracking/1",
            "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "AI CAPEX 과열 우려",
            "url": "https://t.me/insidertracking/2",
            "body_raw": "CAPEX 부담과 마진 둔화 가능성이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 28, 10, 0, tzinfo=KST))
    page = next(page for page in pages if page["id"] == "source-topic-기술-ai")

    assert page["status"] == "draft"
    assert page["confidence"] == 0.45
    assert len(page["evidence_ids"]) == 2
    assert page["conflicting_evidence_ids"] == []
    assert page["staleness_policy"] == "refresh_after_12h"
    assert page["answer_hints"] == [
        "커뮤니티/텔레그램 단독 신호는 가격·수급·공식 자료와 교차확인 전에는 보조 근거로 둡니다.",
        "최신성은 evidence freshness를 우선 확인합니다.",
    ]


def test_build_wiki_pages_from_events_body_adds_insights_and_followups():
    events = [
        {
            "source": "saveticker",
            "title": "AI 서버 수요 확대",
            "url": "https://saveticker.com/1",
            "body_raw": "AI 서버와 반도체 수요가 확대됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "article", "topic": "기술/AI", "trust": "B"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "반도체 전력 수요 증가",
            "url": "https://t.me/insidertracking/2",
            "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "메모리 CAPEX 확대",
            "url": "https://t.me/insidertracking/3",
            "body_raw": "CAPEX 확대와 공급 제약이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["AMD"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 23, 10, 0, tzinfo=KST))
    page = next(page for page in pages if page["id"] == "source-topic-기술-ai")

    assert "핵심 관찰:" in page["body"]
    assert "출처 분포:" in page["body"]
    assert "반복 티커:" in page["body"]
    assert "후속 질문:" in page["body"]
    assert "NVDA" in page["body"]
    assert "AMD" in page["body"]


def test_curate_recent_source_wiki_limit_zero_saves_all_groups(monkeypatch):
    """2026-07-25 회귀방지 — 소수 거시 주제가 이벤트 수에서 항상 상위권을 독점해,
    limit 캡이 있으면 종목별/유형별 신규 페이지가 영구히 저장되지 않는 문제가 있었음.
    limit<=0 은 계산된 그룹을 전부 저장(무제한)해야 한다."""
    events = [
        {"source": "saveticker", "title": f"이벤트 {i}", "url": f"https://saveticker.com/{i}",
         "body_raw": "내용", "topic": topic, "tags": [topic],
         "classification": {"kind": "article", "topic": topic, "trust": "B"}}
        for i, topic in enumerate(["금리/채권", "금리/채권", "유가/원자재", "유가/원자재",
                                    "기술/AI", "기술/AI", "정책/재정", "정책/재정",
                                    "인플레/고용", "인플레/고용"])
    ]

    import reports.source_collector as source_collector
    from agent_console import wiki

    monkeypatch.setattr(source_collector, "load_recent_events", lambda hours: events)
    saved_ids = []
    monkeypatch.setattr(wiki, "upsert_page", lambda page: saved_ids.append(page["id"]) or page)
    monkeypatch.setattr(
        wiki,
        "list_pages",
        lambda **_kwargs: [{"id": page_id, "title": page_id} for page_id in saved_ids],
    )
    sync_calls = []
    monkeypatch.setattr(wiki.qmd_search, "sync_pages", lambda pages: sync_calls.append(list(pages)) or {
        "ok": True, "exported_count": len(pages), "error": "",
    })

    result = swc.curate_recent_source_wiki(hours=48, limit=0)
    assert result["pages"] == 5
    assert result["saved"] == 5
    assert len(saved_ids) == 5
    assert len(sync_calls) == 1
    assert len(sync_calls[0]) == 5
    assert result["qmd_exported_count"] == 5

    saved_ids.clear()
    result = swc.curate_recent_source_wiki(hours=48, limit=2)
    assert result["saved"] == 2
    assert len(saved_ids) == 2
    assert len(sync_calls) == 2
