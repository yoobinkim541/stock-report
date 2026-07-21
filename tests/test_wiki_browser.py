from __future__ import annotations

from dashboard import wiki_browser


PAGES = [
    {
        "id": "p1",
        "title": "손실한도와 레버리지",
        "summary": "QQQ와 TQQQ를 손실한도 1% 안에서 비교한다.",
        "body": "QQQ는 기본, TQQQ는 예산을 더 크게 써야 한다.",
        "tags": ["risk", "portfolio", "leverage"],
        "status": "stable",
        "surface": "portfolio",
        "kind": "playbook",
        "confidence": 0.9,
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T01:00:00+00:00",
        "source_refs": ["conversation:001", "news:alpha"],
    },
    {
        "id": "p2",
        "title": "AI 콘솔 위키 브라우저",
        "summary": "문서 브라우저와 관련 문서를 보여준다.",
        "body": "문서 브라우저는 대화와 메모를 다시 읽게 한다.",
        "tags": ["wiki", "browser"],
        "status": "reviewed",
        "surface": "portfolio",
        "kind": "concept",
        "confidence": 0.7,
        "created_at": "2026-07-13T00:05:00+00:00",
        "updated_at": "2026-07-13T02:00:00+00:00",
        "source_refs": ["conversation:002"],
    },
    {
        "id": "p3",
        "title": "중동 재교전 시나리오",
        "summary": "유가와 달러가 같이 흔들리는 국면이다.",
        "body": "지정학 꼬리위험을 먼저 본다.",
        "tags": ["geo", "risk"],
        "status": "draft",
        "surface": "market",
        "kind": "risk",
        "confidence": 0.6,
        "created_at": "2026-07-13T00:10:00+00:00",
        "updated_at": "2026-07-13T02:30:00+00:00",
        "source_refs": ["news:middleeast", "market_event:1"],
    },
    {
        "id": "p4",
        "title": "손실한도와 레버리지 보강",
        "summary": "TQQQ는 변동성 예산을 크게 잡아야 한다.",
        "body": "현금 완충이 중요하다.",
        "tags": ["risk", "portfolio"],
        "status": "draft",
        "surface": "market",
        "kind": "playbook",
        "confidence": 0.8,
        "created_at": "2026-07-13T00:20:00+00:00",
        "updated_at": "2026-07-13T03:00:00+00:00",
        "source_refs": ["conversation:001"],
    },
]


def test_build_browser_model_prefers_selected_page_and_filters_surface():
    model = wiki_browser.build_browser_model(
        PAGES,
        selected_page_id="p1",
        query="손실",
        surface="portfolio",
        status="all",
    )

    assert model["selected_id"] == "p1"
    assert model["selected"]["id"] == "p1"
    assert model["visible_count"] == 2
    assert [page["id"] for page in model["visible"]] == ["p1", "p2"]


def test_related_pages_uses_tags_and_source_refs():
    related = wiki_browser.related_pages(PAGES[0], PAGES, limit=3)

    assert related[0]["id"] == "p4"
    assert any(page["id"] == "p2" for page in related)


def test_select_page_id_falls_back_to_first_visible_page():
    selected = wiki_browser.select_page_id(
        PAGES,
        selected_page_id="missing",
        query="AI",
        surface="portfolio",
        status="all",
    )

    assert selected == "p2"


def test_build_browser_model_related_pages_use_full_corpus_not_visible_slice():
    model = wiki_browser.build_browser_model(
        PAGES,
        selected_page_id="p1",
        query="손실",
        surface="portfolio",
        status="stable",
    )

    assert model["visible_count"] == 1
    assert model["visible"][0]["id"] == "p1"
    assert model["related"][0]["id"] == "p4"


def test_aliases_are_available():
    filtered = wiki_browser.filter_pages(PAGES, query="레버리지", surface="portfolio", status="all")
    picked = wiki_browser.pick_selected_page(PAGES, selected_page_id="p4", query="", surface="all", status="all")

    assert [page["id"] for page in filtered] == ["p1", "p4"]
    assert picked == "p4"
