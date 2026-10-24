"""Final outgoing provider envelope, with trusted pins outside model JSON."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import run_public_wiki_rollout as runner
from test_wiki_source_notes import NOW, encoded
from test_wiki_topic_v3 import api, add_target, evidence, payload, propose, store


def envelope(evidence):
    captured = []
    propose(evidence, lambda prompt, **local: captured.append((prompt, local))
            or '{"targets":[],"refusal":"Synthetic refusal"}')
    prompt, local = captured[0]
    return json.loads(prompt.rsplit("\n", 1)[1]), local, prompt


def check(evidence, data, pins, **kwargs):
    inputs, journal, _ = evidence
    return runner.validate_topic_prompt(data, journal=journal,
        reserved_revision_id=kwargs.pop("reserved", inputs["source"]["revision_id"]), now=NOW,
        context_mode=kwargs.pop("mode", "source_note_snapshot"), pinned_context=pins, **kwargs)


def test_exact_envelope_and_local_pins_never_use_legacy_note(evidence, monkeypatch, tmp_path):
    monkeypatch.setattr("reports.wiki_topic_context._note",
                        lambda *a, **k: pytest.fail("legacy _note called"))
    data, local, prompt = envelope(evidence)
    assert set(data) == {"compiled", "source_map", "editable_topics", "read_only_source_context", "surface"}
    assert local == {"context_mode": "source_note_snapshot", "pinned_context": evidence[0]["context"]}
    assert check(evidence, data, local["pinned_context"]) == evidence[0]["source"]["revision_id"]
    assert all(word not in prompt for word in (str(tmp_path), "manifest_sha256", "dependencies", "omitted",
                                               "raw_ref", "compile_ref", "note_json_sha256"))


@pytest.mark.parametrize("damage", ["compiled", "map", "notes", "extra", "surface", "reservation",
    "missing_pins", "empty_pins", "pin_hash", "pin_note", "model_pins", "unknown_mode"])
def test_every_projection_and_pin_is_bound(evidence, damage):
    data, local, _ = envelope(evidence)
    pins = local["pinned_context"]
    kwargs = {}
    if damage == "compiled": data["compiled"]["claims"][0]["text"] = "PRIVATE"
    elif damage == "map": data["source_map"]["S1"]["canonical_url"] = "https://evil.example"
    elif damage == "notes": data["read_only_source_context"]["notes"][0]["title"] = "PRIVATE"
    elif damage == "extra": data["diagnostics"] = "PRIVATE"
    elif damage == "surface": data["surface"] = "portfolio"
    elif damage == "reservation": kwargs["reserved"] = "0" * 64
    elif damage == "missing_pins": pins = None
    elif damage == "empty_pins": pins = {"notes": [], "dependencies": [], "omitted": []}
    elif damage == "pin_hash": pins["dependencies"][0]["manifest_sha256"] = "0" * 64
    elif damage == "pin_note": pins["notes"][0]["claims"][0]["text"] = "PRIVATE"
    elif damage == "model_pins": data["pinned_context"] = pins
    else: kwargs["mode"] = "unknown"
    with pytest.raises(ValueError): check(evidence, data, pins, **kwargs)


@pytest.mark.parametrize("which", ["current", "support"])
def test_expiry_between_selection_and_provider(evidence, which):
    data, local, _ = envelope(evidence)
    inputs, journal, _ = evidence
    revision = (inputs["source"]["revision_id"] if which == "current"
                else inputs["context"]["notes"][0]["revision_id"])
    source = journal.load_source(revision)["source"]
    path = Path(source["manifest_ref"])
    value = json.loads(path.read_bytes()); value["expires_at"] = NOW.isoformat()
    path.write_bytes(encoded(value))
    with pytest.raises(ValueError): check(evidence, data, local["pinned_context"])


@pytest.mark.parametrize("damage", ["body", "before", "private", "budget", "duplicate"])
def test_editable_target_exactness_and_budget(evidence, damage):
    add_target(evidence)
    data, local, _ = envelope(evidence)
    if damage == "body": data["editable_topics"][0]["body"] = "PRIVATE"
    elif damage == "before": data["editable_topics"][0]["revision_hash"] = "forged"
    elif damage == "private": evidence[2][0]["body"] += "PRIVATE"
    elif damage == "budget": data["editable_topics"][0]["body"] = "X" * 100001
    else: data["editable_topics"] *= 2
    with pytest.raises(ValueError): check(evidence, data, local["pinned_context"])


def test_actual_checked_provider_receives_only_verified_prompt(evidence):
    inputs, journal, _ = evidence
    _, local, prompt = envelope(evidence)
    captured = []
    checked = runner.checked_topic_provider(lambda outgoing: captured.append(outgoing) or "synthetic response",
        journal=journal, reserved_revision_id=inputs["source"]["revision_id"], now=NOW, **local)
    assert checked(prompt) == "synthetic response"
    assert captured == [prompt]
    forged = prompt.rsplit("\n", 1)[0] + '\n{"compiled":{}}'
    with pytest.raises(ValueError): checked(forged)
    assert captured == [prompt]


@pytest.mark.parametrize("json_text", ['{"compiled":{},"compiled":{}}',
    '{"compiled":NaN}', '{"compiled":Infinity}', '{"compiled":1e999}'])
def test_checked_provider_rejects_nonfinite_and_duplicate_envelopes(evidence, json_text):
    inputs, journal, _ = evidence
    _, local, _ = envelope(evidence)
    checked = runner.checked_topic_provider(lambda _: pytest.fail("invalid prompt reached provider"),
        journal=journal, reserved_revision_id=inputs["source"]["revision_id"], now=NOW, **local)
    with pytest.raises(ValueError): checked("Propose append-only draft topic synthesis.\n" + json_text)


def test_checked_provider_rejects_unvalidated_text_outside_final_envelope(evidence):
    inputs, journal, _ = evidence
    _, local, prompt = envelope(evidence)
    captured = []
    checked = runner.checked_topic_provider(lambda outgoing: captured.append(outgoing),
        journal=journal, reserved_revision_id=inputs["source"]["revision_id"], now=NOW, **local)
    header, data = prompt.rsplit("\n", 1)
    with pytest.raises(ValueError): checked(header + " PRIVATE LOCAL PATH /tmp/secret\n" + data)
    assert captured == []


@pytest.mark.parametrize("proof", [{"private": True}, {"requires_login": True}, {"visibility": "private"}])
def test_final_current_policy_ignores_broad_private_opt_in(evidence, monkeypatch, proof):
    data, local, _ = envelope(evidence)
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setenv("WIKI_SOURCE_ALLOW_PRIVATE", "1")
    source = evidence[0]["source"]
    path = Path(source["manifest_ref"])
    manifest = json.loads(path.read_bytes()); manifest.update(proof)
    path.write_bytes(encoded(manifest))
    with pytest.raises(ValueError): check(evidence, data, local["pinned_context"])


def test_pin_closure_is_detached_and_clock_is_read_at_provider_call(evidence):
    inputs, journal, _ = evidence
    _, local, prompt = envelope(evidence)
    clock = [NOW]
    checked = runner.checked_topic_provider(lambda _: "synthetic", journal=journal,
        reserved_revision_id=inputs["source"]["revision_id"], now=lambda: clock[0], **local)
    local["pinned_context"]["notes"][0]["title"] = "MUTATED BY CALLER"
    assert checked(prompt) == "synthetic"
    from datetime import datetime
    manifest = json.loads(Path(inputs["source"]["manifest_ref"]).read_bytes())
    clock[0] = datetime.fromisoformat(manifest["expires_at"].replace("Z", "+00:00"))
    with pytest.raises(ValueError): checked(prompt)


def test_checked_provider_composes_with_v3_proposal_callback(evidence):
    inputs, journal, rows = evidence
    captured = []
    checked = runner.checked_topic_provider(
        lambda prompt: captured.append(prompt) or json.dumps(payload(inputs)), journal=journal,
        reserved_revision_id=inputs["source"]["revision_id"], now=NOW,
        context_mode="source_note_snapshot", pinned_context=inputs["context"])
    result = api().propose_topic_integration_v3(**inputs, journal=journal, records=rows,
                                              now=NOW, llm_fn=checked)
    assert result["protocol_version"] == 3 and result["targets"]
    assert len(captured) == 1
    with pytest.raises(ValueError):
        checked(captured[0], context_mode="legacy_pages", pinned_context=inputs["context"])
    assert len(captured) == 1
