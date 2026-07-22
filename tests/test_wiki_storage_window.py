"""위키가 최근 100 레코드 창에 갇히던 회귀를 고정한다.

shared_memory.list_records 는 limit 을 100 으로 클램프한다. 위키는 지식층이라
전수를 봐야 하므로 all_records() 를 쓴다. 이 테스트는 '100번째보다 오래된
위키 페이지'가 조회·통계·갱신에서 살아있는지 확인한다.
"""
import json
from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "shared-memory"))


def _seed(count: int, surface: str = "market") -> None:
    """채팅 레코드를 count 건 쌓아 위키 페이지를 100 창 밖으로 밀어낸다."""
    from agent_console import shared_memory

    for i in range(count):
        shared_memory.append_record({
            "title": f"chat-{i:04d}",
            "summary": f"본문 {i}",
            "tags": ["chat"],
            "source": {"surface": surface, "screen": surface},
        })


def test_old_wiki_page_survives_beyond_100_records(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "summary": "손실한도 1% 규칙",
        "body": "손실한도는 1% 로 고정한다.",
    })
    page_id = page["id"]

    _seed(110)   # 위키 페이지를 최근 100 창 밖으로 밀어냄

    assert wiki.get_page(page_id) is not None, "get_page 가 오래된 페이지를 못 찾음"
    titles = [p["title"] for p in wiki.list_pages(limit=50)]
    assert "오래된 손실한도 규칙" in titles, "list_pages 에서 사라짐"
    assert wiki.stats()["total"] >= 1, "stats 가 오래된 페이지를 못 셈"


def test_upsert_old_page_does_not_duplicate_id(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import shared_memory, wiki

    page = wiki.upsert_page({
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "summary": "v1",
        "body": "v1 본문",
    })
    page_id = page["id"]

    _seed(110)

    wiki.upsert_page({
        "id": page_id,
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "summary": "v2",
        "body": "v2 본문",
    })

    events = Path(shared_memory.shared_memory_dir()) / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    matching = [r for r in rows if r.get("id") == page_id]
    assert len(matching) == 1, f"같은 id 레코드가 {len(matching)}개 — 중복 누적"
    assert matching[0]["summary"] == "v2"
