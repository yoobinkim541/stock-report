"""Default-off topic stage, called after legacy source locks are released."""
import json

import pytest

from agent_console import shared_memory
from reports import wiki_source_pipeline as pipeline, wiki_topic_integration
from test_wiki_topic_transaction import NOW


def test_topic_stage_default_off_does_not_read_store(monkeypatch):
    monkeypatch.delenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", raising=False)
    result = pipeline.run_public_topic({}, journal=None, llm_fn=None,
                                       reserved_revision_id="", now=NOW)
    assert result == {"enabled": False}


def test_topic_stage_rejects_wrong_reservation_before_model(transaction, monkeypatch):
    data, journal, _ = transaction
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "1")
    with pytest.raises(ValueError, match="unreserved_source"):
        pipeline.run_public_topic(data["source"], journal=journal,
            llm_fn=lambda _: pytest.fail("unreserved model call"),
            reserved_revision_id="forged", now=NOW)


def test_topic_stage_commits_once_without_holding_legacy_lock(transaction, monkeypatch):
    data, journal, rows = transaction
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "1")
    monkeypatch.setattr(pipeline, "discover_targets", lambda *a, **kw: {"candidate_ids": []})
    calls = []

    def llm(prompt):
        # A fresh open must acquire this non-reentrant flock: no outer legacy
        # source lock may remain held while entering the topic stage.
        with journal.source_lock(data["source"]["revision_id"]):
            calls.append(prompt)
        envelope = json.loads(prompt.rsplit("\n", 1)[1])
        current = envelope["compiled"]
        note = envelope["read_only_source_context"]["notes"][0]
        revisions = [current["revision_id"], note["revision_id"]]
        labels = {v["revision_id"]: k for k, v in envelope["source_map"].items()}
        claims = [current["claims"][0], note["claims"][0]]
        return json.dumps({"refusal": None, "targets": [{"page_id": None,
            "before_hash": None, "title": "Shipping topic", "kind": "concept",
            "surface": "market", "purpose": "topic_synthesis",
            "contributing_revision_ids": revisions, "scope_explanation": "Dated shipping reports",
            "additions": [{"claim_ids": [c["claim_id"]], "revision_ids": [r],
                "as_of": c["as_of"], "scope": c["scope"],
                "body": f"Attributed report [{labels[r]}]."} for r, c in zip(revisions, claims)],
            "relations": []}]})

    result = pipeline.run_public_topic(data["source"], journal=journal, llm_fn=llm,
        reserved_revision_id=data["source"]["revision_id"], now=NOW)
    assert result["stage"] == "committed"
    assert len(calls) == 1
    after = shared_memory.inspect_records()
    assert [r for r in after if r["id"] in {row["id"] for row in rows}] == rows
    retry = pipeline.run_public_topic(data["source"], journal=journal,
        llm_fn=lambda _: pytest.fail("duplicate model call"),
        reserved_revision_id=data["source"]["revision_id"], now=NOW)
    assert retry["stage"] == "committed"
    assert shared_memory.inspect_records() == after
