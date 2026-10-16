import copy
import json

import pytest

from reports.wiki_topic_integration import propose_topic_integration
from scripts import run_public_wiki_rollout as runner
from test_wiki_topic_integration import evidence, NOW


def envelope(evidence):
    data, journal, rows = evidence
    captured = []
    propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
        llm_fn=lambda prompt: captured.append(json.loads(prompt.rsplit("\n", 1)[1]))
        or json.dumps({"targets": [], "refusal": "Synthetic refusal"}))
    return captured[0], data, journal


def test_verified_topic_prompt_matches_reserved_source(evidence, monkeypatch):
    monkeypatch.setattr("agent_console.shared_memory.inspect_records", lambda: evidence[2])
    payload, data, journal = envelope(evidence)
    assert runner.validate_topic_prompt(payload, journal=journal,
        reserved_revision_id=data["source"]["revision_id"], now=NOW) == data["source"]["revision_id"]


@pytest.mark.parametrize("damage", ["reservation", "current", "note", "mapping", "extra", "surface"])
def test_topic_prompt_tampering_rejected_before_provider(evidence, damage, monkeypatch):
    monkeypatch.setattr("agent_console.shared_memory.inspect_records", lambda: evidence[2])
    payload, data, journal = envelope(evidence)
    payload = copy.deepcopy(payload)
    reserved = data["source"]["revision_id"]
    if damage == "reservation": reserved = "forged"
    elif damage == "current": payload["compiled"]["claims"][0]["text"] = "Private content"
    elif damage == "note": payload["read_only_source_context"]["notes"][0]["claims"][0]["text"] = "Private content"
    elif damage == "mapping": payload["source_map"]["S1"]["canonical_url"] = "https://example.org/forged"
    elif damage == "extra": payload["omitted"] = ["private-page-id"]
    else: payload["surface"] = "portfolio"
    with pytest.raises(ValueError):
        runner.validate_topic_prompt(payload, journal=journal, reserved_revision_id=reserved, now=NOW)
