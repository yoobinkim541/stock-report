import json
from pathlib import Path

from agent_console import shared_memory, wiki, qmd_search


def test_compiled_split_roundtrip_retains_claims_and_provenance_in_markdown(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(tmp_path / "markdown"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()

    supporting = {
        "claim_id": "capacity-support",
        "source_revision_id": "revision-support",
        "text": "Reported capacity increased.",
        "as_of": "2026-10-09",
        "scope": "announced capacity, not realized output",
        "knowledge_state": "draft",
        "competing_claim_ids": ["demand-counterclaim"],
    }
    competing = {
        "claim_id": "demand-counterclaim",
        "source_revision_id": "revision-demand",
        "text": "Demand remains uncertain.",
        "as_of": "2026-10-10",
        "scope": "orders, not capacity announcements",
        "knowledge_state": "draft",
        "competing_claim_ids": ["capacity-support"],
    }
    limitation = "Announced capacity is not evidence of realized orders."
    relation = {
        "type": "compete",
        "from_claim_id": "capacity-support",
        "to_claim_id": "demand-counterclaim",
        "scope_explanation": "Capacity and demand measure different conditions.",
    }
    refs = ["https://example.org/capacity", "https://example.org/demand"]
    body = "\n\n".join(f"Detailed compiled paragraph {i:04d}." for i in range(180))
    page = wiki.upsert_page({
        "id": "compiled-long-parent",
        "title": "Compiled capacity and demand",
        "kind": "concept",
        "surface": "market",
        "body": body,
        "source_refs": refs,
        "source_knowledge": {
            "claims": [supporting, competing],
            "limitations": [limitation],
            "relations": [relation],
            "integrations": [
                {"revision_id": "revision-support", "role": "support", "evidence_ids": ["evidence-s"]},
                {"revision_id": "revision-demand", "role": "conflict", "evidence_ids": ["evidence-c"]},
            ],
        },
    })

    wiki.split_for_readability(page["id"], max_chars=700)
    snapshot = wiki.semantic_snapshot(page["id"])

    assert len(snapshot["children"]) > 1
    for child in snapshot["children"]:
        target = qmd_search._safe_file_stem(qmd_search._clean(child["id"], 100))
        assert f"[[{target}|" in snapshot["page"]["body"]
    assert {claim["claim_id"]: claim for claim in snapshot["claims"]} == {
        "capacity-support": supporting,
        "demand-counterclaim": competing,
    }
    assert snapshot["source_knowledge"]["limitations"] == [limitation]
    assert snapshot["source_knowledge"]["relations"] == [relation]
    assert snapshot["source_knowledge"]["integrations"] == [
        {"revision_id": "revision-support", "role": "support", "evidence_ids": ["evidence-s"]},
        {"revision_id": "revision-demand", "role": "conflict", "evidence_ids": ["evidence-c"]},
    ]
    assert snapshot["source_refs"] == refs

    export_page = {
        **snapshot["page"],
        "source_refs": snapshot["source_refs"],
        "source_knowledge": {
            **snapshot["source_knowledge"],
            "claims": snapshot["claims"],
        },
    }
    result = qmd_search.export_pages([export_page])
    markdown = Path(result["files"][0]).read_text(encoding="utf-8")
    exported = json.loads(markdown.split("## Source Knowledge\n\n```json\n", 1)[1].split("\n```", 1)[0])

    assert exported["claims"] == snapshot["claims"]
    assert exported["limitations"] == [limitation]
    assert exported["relations"] == [relation]
    assert exported["integrations"] == snapshot["source_knowledge"]["integrations"]
    assert all(ref in markdown for ref in refs)
    wiki._CACHE.clear()
