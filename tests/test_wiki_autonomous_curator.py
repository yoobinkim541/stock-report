from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def _page(wiki, *, title, body, kind="playbook", surface="market", **extra):
    payload = {
        "title": title,
        "summary": title,
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": "draft",
        "source_refs": [],
    }
    payload.update(extra)
    return wiki.upsert_page(payload)


def test_find_management_pairs_keeps_kind_and_surface_boundaries(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_autonomous_curator as curator

    first = _page(wiki, title="금리 상승과 성장주 밸류에이션", body="금리 상승 할인율 성장주 밸류에이션 위험")
    second = _page(wiki, title="성장주 금리와 할인율 위험", body="성장주 밸류에이션 금리 상승 할인율 위험")
    _page(wiki, title="금리 상승 개념", body="금리 할인율 성장주 위험", kind="concept")
    _page(wiki, title="다른 화면의 금리 위험", body="금리 할인율 성장주 위험", surface="ticker")

    pairs = curator.find_management_pairs(wiki._all_wiki_pages(), min_similarity=0.2)

    assert any({a["id"], b["id"]} == {first["id"], second["id"]} for a, b, _ in pairs)
    assert all(a["kind"] == b["kind"] and a["surface"] == b["surface"] for a, b, _ in pairs)


def test_find_split_candidates_uses_readability_threshold_and_skips_children(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_autonomous_curator as curator

    long_page = _page(wiki, title="긴 판단", body="긴 본문 " * 700)
    _page(wiki, title="이미 자식인 문서", body="긴 본문 " * 700, parent_page_id="parent-1")
    _page(wiki, title="원문 근거 문서", body="긴 본문 " * 700, source_refs=["https://example.com/source"])

    candidates = curator.find_split_candidates(wiki._all_wiki_pages(), readability_limit=1000)

    assert [page["id"] for page in candidates] == [long_page["id"]]


def test_parse_management_decision_is_fail_closed():
    from reports import wiki_autonomous_curator as curator

    assert curator._parse_management_decision('{"action":"merge","reason":"same"}') == {
        "action": "merge",
        "reason": "same",
    }
    assert curator._parse_management_decision('{"action":"delete"}') is None
    assert curator._parse_management_decision("자연어 지시") is None


def test_valid_split_decision_rejects_unpaired_empty_items():
    from reports import wiki_autonomous_curator as curator

    source = {"id": "source-1"}
    decision = {
        "source_page_id": "source-1",
        "new_titles": ["배경", "", "적용"],
        "new_bodies": ["배경 판단", "중간 판단", ""],
    }

    assert curator._valid_split_decision(decision, source) is None


def test_run_dry_run_reports_merge_and_split_without_writes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_autonomous_curator as curator

    first = _page(wiki, title="금리 성장주 위험 A", body="금리 상승 할인율 성장주 위험")
    second = _page(wiki, title="금리 성장주 위험 B", body="성장주 위험 금리 상승 할인율")
    long_page = _page(wiki, title="분할 대상", body=("## 배경\n" + "배경 문장 " * 700) + "\n\n## 적용\n" + "적용 문장 " * 700)

    def llm(prompt):
        if "<operation>merge" in prompt:
            return '{"action":"merge","target_page_id":"%s","source_page_ids":["%s"],"body":"통합 판단","reason":"같은 판단"}' % (first["id"], second["id"])
        return '{"action":"split","source_page_id":"%s","new_titles":["배경","적용"],"new_bodies":["배경 판단","적용 판단"],"reason":"의미 단위"}' % long_page["id"]

    result = curator.run(dry_run=True, llm_fn=llm, limit=10, readability_limit=1000)

    assert len(result["merged"]) == 1
    assert len(result["split"]) == 1
    assert wiki.get_page(first["id"])["status"] != "archived"
    assert wiki.get_page(long_page["id"])["status"] != "archived"


def test_run_reserves_split_budget_when_merge_candidates_exceed_limit(monkeypatch, tmp_path):
    """실측(2026-09-06): 실제 배포 로그에서 5일 연속 '병합 6건, 분할 0건'이 나왔다 —
    merge 루프가 공유 max_operations 예산을 먼저 다 써버려, 가독성 임계치를 넘는
    분할 후보(실측 시점에 2건 존재)가 구조적으로 영원히 굶었다. 병합 후보가 예산을
    넘게 많아도 분할 후보가 있으면 최소 1건은 이번 실행에서 처리돼야 한다."""
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_autonomous_curator as curator

    # merge 후보가 예산(limit=2)보다 훨씬 많게 — 서로 다른 4쌍(8페이지)을 만든다.
    merge_pages = []
    for i in range(8):
        merge_pages.append(_page(wiki, title=f"금리 성장주 위험 {i}", body="금리 상승 할인율 성장주 위험 공통 판단"))
    long_page = _page(wiki, title="분할 대상", body=("## 배경\n" + "배경 문장 " * 700) + "\n\n## 적용\n" + "적용 문장 " * 700)

    import re

    def llm(prompt):
        if "<operation>merge" in prompt:
            ids = re.findall(r"^id: (\S+)$", prompt, flags=re.MULTILINE)
            target_id, source_id = ids[0], ids[1]
            return '{"action":"merge","target_page_id":"%s","source_page_ids":["%s"],"body":"통합 판단","reason":"같은 판단"}' % (target_id, source_id)
        return '{"action":"split","source_page_id":"%s","new_titles":["배경","적용"],"new_bodies":["배경 판단","적용 판단"],"reason":"의미 단위"}' % long_page["id"]

    result = curator.run(dry_run=True, llm_fn=llm, limit=2, readability_limit=1000)

    assert len(result["split"]) >= 1, "병합 후보가 넘쳐도 분할 후보는 최소 1건 처리돼야 한다"


def test_run_split_persists_parent_page_id(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_autonomous_curator as curator

    source = _page(wiki, title="분할 대상", body=("## 배경\n" + "배경 문장 " * 700) + "\n\n## 적용\n" + "적용 문장 " * 700)
    llm = lambda prompt: '{"action":"split","source_page_id":"%s","new_titles":["배경","적용"],"new_bodies":["배경 판단","적용 판단"],"reason":"의미 단위"}' % source["id"]

    result = curator.run(dry_run=False, llm_fn=llm, limit=10, readability_limit=1000)

    assert len(result["split"]) == 1
    for child_id in result["split"][0]["created"]:
        assert wiki.get_page(child_id)["parent_page_id"] == source["id"]
