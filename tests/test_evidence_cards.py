from datetime import datetime, timedelta, timezone

from reports.evidence_cards import cards_to_source_refs, event_to_evidence_card


KST = timezone(timedelta(hours=9))


def test_event_to_evidence_card_preserves_raw_text_and_payload():
    event = {
        "source": "telegram:insidertracking",
        "title": "AI 데이터센터 전력 수요 증가",
        "url": "https://t.me/insidertracking/1",
        "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
        "topic": "기술/AI",
        "tags": ["기술/AI"],
        "tickers": ["NVDA"],
        "published_at": "2026-07-28T09:30:00+09:00",
        "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
    }

    card = event_to_evidence_card(event, now=datetime(2026, 7, 28, 10, 0, tzinfo=KST))
    data = card.to_dict()

    assert data["id"].startswith("evidence-")
    assert data["source_type"] == "telegram"
    assert data["source_name"] == "telegram:insidertracking"
    assert data["source_url"] == "https://t.me/insidertracking/1"
    assert data["raw_text"] == "AI 데이터센터 전력 수요 증가\n반도체와 데이터센터 전력 병목이 같이 언급됐다."
    assert data["raw_payload"]["classification"]["trust"] == "C"
    assert data["symbols"] == ["NVDA"]
    assert data["topics"] == ["기술/AI"]
    assert data["event_type"] == "rumor"
    assert data["confidence"] == 0.45
    assert data["freshness"] == "intraday"
    assert "growth" in data["impact_axes"]
    assert "AI 데이터센터 전력 수요 증가" in data["summary"]


def test_cards_to_source_refs_keeps_url_and_raw_paths_deduped():
    first = event_to_evidence_card({
        "source": "saveticker",
        "title": "엔비디아 AI 서버 수요 확대",
        "url": "https://saveticker.com/nvda",
        "body_raw": "AI 서버 수요 확대",
        "raw_path": "/tmp/nvda.json",
        "text_path": "/tmp/nvda.txt",
        "topic": "기술/AI",
        "tickers": ["NVDA"],
    })
    second = event_to_evidence_card({
        "source": "saveticker",
        "title": "엔비디아 AI 서버 수요 확대",
        "url": "https://saveticker.com/nvda",
        "body_raw": "AI 서버 수요 확대",
        "raw_path": "/tmp/nvda.json",
        "text_path": "/tmp/nvda.txt",
        "topic": "기술/AI",
        "tickers": ["NVDA"],
    })

    assert cards_to_source_refs([first, second]) == [
        "https://saveticker.com/nvda",
        "/tmp/nvda.txt",
        "/tmp/nvda.json",
    ]
