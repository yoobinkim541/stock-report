"""Cross-reference maintenance leaves no orphan and no page without outbound links."""
import pytest

from agent_console import wiki
from reports import wiki_crosslinks, wiki_karpathy_audit as audit


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(wiki_crosslinks, "HUB_CHILD_SIZE", 1)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def seed():
    rows = [
        ("nvda-supply", "NVDA 공급망 병목", "risk", ["ticker:nvda"]),
        ("nvda-orders", "NVDA 수주 둔화 가능성", "risk", ["ticker:nvda"]),
        ("xom-capex", "XOM 설비투자 확대", "playbook", ["ticker:xom"]),
        ("lonely", "단독 개념 설명", "concept", []),
        ("archived", "보관 문서", "risk", ["ticker:nvda"]),
    ]
    for pid, title, kind, tags in rows:
        wiki.upsert_page({"id": pid, "title": title, "kind": kind, "tags": tags,
                          "body": f"{title}에 대한 원문 기반 설명과 한계. 수치·시점·범위를 함께 기록하고 반론 근거도 남긴다. "
                                  "단일 출처 주장은 교차 확인 전까지 초안으로 유지한다.",
                          "source_refs": ["https://example.com/" + pid],
                          "status": "archived" if pid == "archived" else "draft"})
    wiki._CACHE.clear()


def test_apply_links_related_pages_and_hubs_every_page():
    seed()
    assert wiki_crosslinks.apply(dry_run=True)["pages_relinked"] == 4
    wiki._CACHE.clear()
    assert wiki.get_page("nvda-supply")["links"] == []

    result = wiki_crosslinks.apply(dry_run=False)
    wiki._CACHE.clear()
    pages = wiki._all_wiki_pages()
    out, inbound, broken = audit.link_graph(pages)
    assert result["pages_relinked"] == 4
    assert not any(broken.values())
    assert [k for k, v in out.items() if not v] == []
    assert [k for k, v in inbound.items() if not v] == []
    assert "nvda-orders" in wiki.get_page("nvda-supply")["links"]
    assert "archived" not in wiki.get_page("nvda-supply")["links"]
    hubs = [p for p in pages if wiki_crosslinks.is_hub(p)]
    assert all(len(p["body"]) <= audit.LONG_PAGE_LIMIT for p in hubs)
    inp = audit.AuditInputs(pages=pages)
    assert audit.check_knowledge_not_links(inp)["pass"]
    assert audit.check_long_pages(inp)["pass"]


def test_reapply_is_idempotent_and_archives_obsolete_hubs(monkeypatch):
    seed()
    wiki_crosslinks.apply(dry_run=False)
    wiki._CACHE.clear()
    assert wiki_crosslinks.apply(dry_run=False)["pages_relinked"] == 0
    monkeypatch.setattr(wiki_crosslinks, "HUB_CHILD_SIZE", 50)
    wiki._CACHE.clear()
    wiki_crosslinks.apply(dry_run=False)
    wiki._CACHE.clear()
    active_hubs = [p["id"] for p in wiki._all_wiki_pages() if wiki_crosslinks.is_hub(p) and p["status"] != "archived"]
    assert not any(hub.endswith(("-01", "-02")) for hub in active_hubs)
    out, inbound, broken = audit.link_graph(wiki._all_wiki_pages())
    assert not any(broken.values()) and all(inbound.values())
