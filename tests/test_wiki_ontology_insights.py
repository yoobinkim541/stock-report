from __future__ import annotations

import json
from pathlib import Path

from agent_console import wiki_ontology as wo
from reports import wiki_ontology_insights as job


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    from agent_console import wiki

    wiki._CACHE.clear()


def _source_page(index: int, claims: list[str]) -> dict:
    return {
        "id": f"src-{index}", "title": f"원유 수급 메모 {index}", "summary": "원유 수급 관련 공개 출처 정리",
        "body": "본문", "kind": "note", "surface": "market", "status": "draft",
        "source_refs": [f"https://t.me/s/example/{index}"],
        "source_knowledge": {"claims": [
            {"claim_id": f"c{index}-{n}", "source_revision_id": f"rev-{index}", "text": text, "as_of": "2026-11-1" + str(index),
             "competing_claim_ids": []} for n, text in enumerate(claims)]},
    }


SOURCES = [
    _source_page(1, ["이란 긴장으로 유가가 3% 올랐다.", "유가 상승으로 정유주가 강세였다."]),
    _source_page(2, ["OPEC 증산 합의로 유가 상단이 제한된다는 관측이 나왔다."]),
    _source_page(3, ["이란 제재 완화 논의로 유가가 하락 압력을 받았다."]),
    _source_page(4, ["유가 변동성 확대에 정유사 마진이 흔들렸다."]),
]


def _extraction_reply(prompt: str) -> str:
    # 묶음 안 페이지 순서는 저장 시각에 따라 달라지므로 문장으로 claim 키를 찾는다.
    key = next(line[3:line.index("]")] for line in prompt.splitlines() if line.startswith("- [c") and "3% 올랐다" in line)
    return json.dumps({"entities": [], "relations": [
        {"type": "positive_for", "from": "region:iran", "to": "asset:crude-oil", "claims": [key], "note": "긴장 고조"}]})


def _insight_reply(points: list[dict], **extra) -> str:
    return json.dumps({"action": "create", "title": "공급 변수의 줄다리기", "confidence": 0.6, "horizon": "weeks",
                       "thesis": "이란 긴장과 OPEC 증산이 맞서 유가 방향이 갈린다는 보도가 이어졌다.",
                       "points": points, "tensions": [], "implications": ["유가가 오르면 정유주에 유리할 수 있다."],
                       "watch": ["OPEC 회의 결과"], **extra})


def _keys(prompt: str) -> dict[str, str]:
    """프롬프트의 ``[k1|S1]`` 표기에서 claim 문장 → 키."""
    rows = {}
    for line in prompt.splitlines():
        if line.startswith("- [k"):
            key = line[3:line.index("|")]
            rows[line.split(") ", 1)[1]] = key
    return rows


def _key_for(prompt: str, fragment: str) -> str:
    return next(key for text, key in _keys(prompt).items() if fragment in text)


def test_job_extracts_compiles_and_writes_a_cross_source_insight(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    for page in SOURCES:
        wiki.upsert_page(page)
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        if "typed knowledge graph" in prompt:
            return _extraction_reply(prompt)
        if "원유" not in prompt.split("<entity>")[1].split("</entity>")[0]:
            return json.dumps({"action": "skip", "skip_reason": "test"})
        return _insight_reply([
            {"text": "이란 긴장은 유가를 3% 끌어올렸지만 OPEC 증산이 상단을 막는다는 관측이 함께 나왔다.",
             "claims": [_key_for(prompt, "3% 올랐다"), _key_for(prompt, "OPEC 증산")]},
            {"text": "제재 완화 논의와 변동성 확대가 겹쳐 정유사 마진이 흔들린 것으로 전해졌다.",
             "claims": [_key_for(prompt, "제재 완화"), _key_for(prompt, "마진이 흔들렸다")]},
            {"text": "유가가 12% 급등했다는 수치는 근거에 없다.",
             "claims": [_key_for(prompt, "3% 올랐다"), _key_for(prompt, "OPEC 증산")]},
            {"text": "한 출처만 인용한 판단은 인사이트가 아니다.",
             "claims": [_key_for(prompt, "3% 올랐다"), _key_for(prompt, "정유주가 강세")]},
        ])

    monkeypatch.setattr(job, "_held_tickers", lambda: set())
    report = job.run(apply=True, extract_calls=3, insight_limit=5, llm_fn=llm)

    assert report["extraction"]["pages"] == 4 and report["extraction"]["relations"] == 1
    assert report["ontology"]["relations"] == 1
    written = [row for row in report["insights"]["written"] if row["entity"] == "asset:crude-oil"]
    assert len(written) == 1
    page = wiki.get_page(job.insight_page_id("asset:crude-oil"))
    assert page["kind"] == "insight" and page["status"] == "draft"
    assert wo.insight_entity_id(page) == "asset:crude-oil"
    assert "ticker:cl=f" in page["tags"]  # 태그는 저장 시 소문자가 된다
    assert [row["id"] for row in wiki.for_report_targets(["CL=F"])["CL=F"]] == [page["id"]]
    assert "12%" not in page["body"] and "한 출처만" not in page["body"]
    assert "1. 이란 긴장은 유가를 3% 끌어올렸지만" in page["body"] and "2. 제재 완화 논의와" in page["body"]
    assert "## 시사점 (모델 추론)" in page["body"] and "이란 → 원유 [호재·상승 요인]" in page["body"]
    assert sorted(page["links"]) == ["src-1", "src-2", "src-3", "src-4"]
    meta = page["source_knowledge"]["insight"]
    assert sorted(meta["cited"]) == [["src-1", "c1-0"], ["src-2", "c2-0"], ["src-3", "c3-0"], ["src-4", "c4-0"]]

    index = (wiki.wiki_artifacts_dir() / "index.md").read_text(encoding="utf-8")
    assert index.index("## 인사이트 (Insights)") < index.index("원유 인사이트: 공급 변수의 줄다리기")
    context = wiki.build_context_section(query="유가 전망", surface="market", limit=3)
    assert "[위키 온톨로지]" in context and "이란 → 원유 [호재·상승 요인]" in context
    assert f"[[{page['id']}]]" in context
    assert not [issue for issue in wiki.lint_pages()["issues"] if issue["code"] == "insight_evidence_missing"]

    # 근거가 그대로면 다시 쓰지 않고, 추출도 다시 부르지 않는다.
    calls_before = len(prompts)
    again = job.run(apply=True, extract_calls=3, insight_limit=5, llm_fn=llm)
    assert again["pending_pages"] == 0 and again["extraction"]["calls"] == 0
    assert not [row for row in again["insights"]["written"] if row["entity"] == "asset:crude-oil"]
    assert not [prompt for prompt in prompts[calls_before:] if "(자산·지수, asset:crude-oil)" in prompt]


def test_insight_survives_plain_edit_and_flags_archived_evidence(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    for page in SOURCES:
        wiki.upsert_page(page)
    wiki.upsert_page({
        "id": "insight-x", "title": "원유 인사이트", "summary": "가설", "body": "본문", "kind": "insight",
        "surface": "market", "status": "draft", "tags": ["insight", "entity:asset:crude-oil"],
        "source_knowledge": {"insight": {"entity_id": "asset:crude-oil", "cited": [["src-1", "c1-0"], ["src-2", "c2-0"]]}},
    })

    wiki.upsert_page({**wiki.get_page("insight-x"), "body": "교차 링크가 덧붙은 본문"})
    assert wiki.get_page("insight-x")["source_knowledge"]["insight"]["cited"] == [["src-1", "c1-0"], ["src-2", "c2-0"]]

    wiki.upsert_page({**wiki.get_page("src-2"), "status": "archived"})
    issues = [issue for issue in wiki.lint_pages()["issues"] if issue["code"] == "insight_evidence_missing"]
    assert [issue["page_id"] for issue in issues] == ["insight-x"]


def _candidate(pages: list[dict], entity_id: str = "asset:crude-oil") -> tuple[dict, dict]:
    ontology = wo.compile_ontology(pages, generated_at="2026-11-15T00:00:00+00:00")
    candidate = next(item for item in job.insight_candidates(ontology, pages, now="2026-11-15T00:00:00+00:00")
                     if item["entity"]["id"] == entity_id)
    return ontology, candidate


def test_candidates_need_three_sources_and_refresh_only_on_new_evidence():
    ontology = wo.compile_ontology(SOURCES[:2], generated_at="2026-11-15T00:00:00+00:00")
    assert not [item for item in job.insight_candidates(ontology, SOURCES[:2]) if item["entity"]["id"] == "asset:crude-oil"]

    ontology, candidate = _candidate(SOURCES)
    assert not candidate["refresh"]
    existing = {"id": job.insight_page_id("asset:crude-oil"), "kind": "insight", "status": "draft",
                "tags": ["entity:asset:crude-oil"],
                "source_knowledge": {"insight": {
                    "input_hash": "older", "generated_at": "2026-11-14T00:00:00+00:00", "relations": [],
                    "evidence": [[row["page_id"], row["claim_id"]] for row in candidate["evidence"]]}}}

    def crude(pages: list[dict], now: str) -> list[dict]:
        graph = wo.compile_ontology(pages, generated_at=now)
        return [item for item in job.insight_candidates(graph, [*pages, existing], now=now)
                if item["entity"]["id"] == "asset:crude-oil"]

    # 근거 claim 이 그대로면 (해시만 다르고) 하루 지난 인사이트는 건드리지 않는다.
    assert crude(SOURCES, "2026-11-15T00:00:00+00:00") == []
    # 일주일이 지나면 다시 쓴다.
    assert [item["refresh"] for item in crude(SOURCES, "2026-11-22T00:00:00+00:00")] == [True]
    # 새 claim 3건이 붙으면 바로 다시 쓴다.
    more = [*SOURCES, _source_page(5, ["유가가 반등했다.", "유가 전망이 상향됐다.", "유가 선물 포지션이 늘었다."])]
    assert [item["refresh"] for item in crude(more, "2026-11-15T00:00:00+00:00")] == [True]


def test_validate_insight_rejects_single_source_and_ungrounded_numbers():
    ontology, candidate = _candidate(SOURCES)
    prompt, keymap = job.build_insight_request(ontology, candidate)
    a, b, c = (_key_for(prompt, text) for text in ("3% 올랐다", "정유주가 강세", "OPEC 증산"))
    entity = candidate["entity"]

    same_source = json.loads(_insight_reply([{"text": "같은 출처 두 문장을 이은 판단이다.", "claims": [a, b]}] * 2))
    assert job.validate_insight(same_source, keymap, entity=entity) == (None, "cross_source_points_missing")

    bad_thesis = json.loads(_insight_reply(
        [{"text": "긴장과 증산이 맞선다는 보도가 함께 나왔다.", "claims": [a, c]},
         {"text": "정유주 강세와 상단 제한 관측이 함께 전해졌다.", "claims": [b, c]}],
        thesis="유가가 배럴당 150달러까지 오를 수 있다는 판단이다."))
    assert job.validate_insight(bad_thesis, keymap, entity=entity) == (None, "thesis_number_ungrounded")

    assert job.validate_insight({"action": "skip", "skip_reason": "잡담"}, keymap, entity=entity) == (None, "model_skip")
    assert job.validate_insight(None, keymap, entity=entity) == (None, "payload_invalid")

    good = json.loads(_insight_reply(
        [{"text": "긴장과 증산이 맞선다는 보도가 함께 나왔다.", "claims": [a, c, "k999"]},
         {"text": "정유주 강세와 상단 제한 관측이 함께 전해졌다.", "claims": [b, c]}],
        implications=["유가가 70% 오른다.", "정유주 변동성이 커질 수 있다."], horizon="forever", confidence="높음"))
    insight, reason = job.validate_insight(good, keymap, entity=entity)
    assert reason == "" and insight["points"][0]["claims"] == [a, c]
    assert insight["implications"] == ["정유주 변동성이 커질 수 있다."]
    assert (insight["horizon"], insight["confidence"]) == ("weeks", 0.4)


def test_failed_llm_calls_leave_pages_pending_and_stop_early():
    extractions = {"pages": {}}
    calls = []

    def down(prompt: str) -> None:
        calls.append(prompt)

    monkey_pages = [_source_page(n, [f"이란 긴장으로 유가가 올랐다 {n}. " + "가" * 590] * 20) for n in range(1, 6)]
    summary = job.extract_relations(monkey_pages, extractions, llm_fn=down, max_calls=5)

    assert len(job.batches(monkey_pages)) == 5
    assert (summary["calls"], summary["failed_calls"], summary["pages"]) == (2, 2, 0)
    assert extractions == {"pages": {}} and len(job.pending_pages(monkey_pages, extractions)) == 5


def test_prune_drops_cache_for_archived_pages():
    extractions = {"pages": {"src-1": {"input_hash": "x"}, "gone": {"input_hash": "y"}}}

    assert job.prune_extractions(SOURCES, extractions) == 1
    assert list(extractions["pages"]) == ["src-1"]
