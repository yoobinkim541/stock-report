"""Synthetic worker/rollout composition against real notes and canonical CAS."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_console import shared_memory, wiki
from reports import wiki_source_pipeline as pipeline, wiki_topic_transaction_v3 as transaction
from reports.wiki_source_notes import materialize_source_note
from reports.wiki_topic_snapshot_context import select_snapshot_context
from scripts import run_public_wiki_rollout as rollout
from test_wiki_source_notes import NOW, encoded, inventory, store
from test_wiki_topic_v3 import evidence, payload, propose


@pytest.fixture
def worker(evidence, tmp_path, monkeypatch):
    data, journal, _ = evidence
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", "1")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked([])
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: shared_memory._read_jsonl_checked(
        shared_memory._paths()["events"]))
    materialize_source_note(data["source"]["revision_id"], journal=journal, now=NOW, apply=True)
    monkeypatch.setattr(pipeline, "discover_targets", lambda *a, **kw: {"candidate_ids": []})
    return data, journal


def run(worker, provider, **kwargs):
    data, journal = worker
    return pipeline.run_public_topic(data["source"], journal=journal, llm_fn=provider,
        reserved_revision_id=data["source"]["revision_id"], now=NOW,
        context_mode="source_note_snapshot",
        note_ids=[n["note_id"] for n in data["context"]["notes"]], **kwargs)


@pytest.mark.parametrize("flag", [None, "0", "true", "01", "2"])
def test_snapshot_exact_flag_disabled_is_readonly(worker, monkeypatch, tmp_path, flag):
    if flag is None:
        monkeypatch.delenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED")
    else:
        monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", flag)
    before = inventory(tmp_path)
    assert run(worker, lambda *a, **kw: pytest.fail("disabled provider")) == {"enabled": False}
    assert inventory(tmp_path) == before


def test_snapshot_requires_old_gate_and_matching_reservation(worker, monkeypatch, tmp_path):
    data, journal = worker
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "0")
    before = inventory(tmp_path)
    assert run(worker, None) == {"enabled": False}
    assert inventory(tmp_path) == before
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "1")
    with pytest.raises(ValueError, match="unreserved_source"):
        pipeline.run_public_topic(data["source"], journal=journal, llm_fn=None,
            reserved_revision_id="forged", now=NOW, context_mode="source_note_snapshot", note_ids=[])
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("mode,notes", [("unknown", []), ("source_note_snapshot", None)])
def test_unknown_mode_or_implicit_note_selection_is_rejected(worker, mode, notes, tmp_path):
    data, journal = worker
    before = inventory(tmp_path)
    with pytest.raises(ValueError):
        pipeline.run_public_topic(data["source"], journal=journal, llm_fn=None,
            reserved_revision_id=data["source"]["revision_id"], now=NOW,
            context_mode=mode, note_ids=notes)
    assert inventory(tmp_path) == before


def test_seed_retry_is_frozen_effects_only_without_provider_or_quota(worker, monkeypatch):
    data, journal = worker
    calls = []
    def provider(prompt, **local):
        calls.append((prompt, local))
        return json.dumps(payload(data))
    result = run(worker, provider)
    assert result["stage"] == "committed" and result["newly_committed"]
    assert result["reason"] == ""
    assert len(calls) == 1 and calls[0][1]["context_mode"] == "source_note_snapshot"
    assert calls[0][1]["pinned_context"] == data["context"]
    assert not list((journal.root / "topic-operations").glob("*.json"))
    receipt = journal.load_topic_operation_v3(result["operation_id"])
    assert receipt["schema_version"] == receipt["batch"]["protocol_version"] == 3
    assert len(wiki.semantic_snapshot(receipt["batch"]["target_ids"][0])["claims"]) == 6
    before = shared_memory.inspect_records()
    from reports import wiki_public_budget
    monkeypatch.setattr(wiki_public_budget, "reserve", lambda *a, **kw: pytest.fail("retry quota"))
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("historical source lookup"))
    retry = run(worker, lambda *a, **kw: pytest.fail("retry provider"))
    assert retry["stage"] == "committed" and not retry["newly_committed"]
    assert shared_memory.inspect_records() == before


@pytest.mark.parametrize("damage", ["expiry", "role"])
def test_prepared_retry_revalidates_without_new_model_work(worker, monkeypatch, damage):
    data, journal = worker
    batch = transaction.prepare_topic_batch_v3(propose((data, journal, [])),
        journal=journal, records=[], now=NOW)
    if damage == "expiry":
        path = Path(data["source"]["manifest_ref"])
        manifest = json.loads(path.read_bytes()); manifest["expires_at"] = NOW.isoformat()
        path.write_bytes(encoded(manifest))
    else:
        path = journal._path("sources", data["source"]["revision_id"])
        receipt = json.loads(path.read_bytes()); receipt["evidence_by_role"]["support"] = []
        path.write_bytes(encoded(receipt))
    result = run(worker, lambda *a, **kw: pytest.fail("prepared retry provider"))
    assert result["operation_id"] == batch["operation_id"] and result["stage"] == "stale-base"
    assert shared_memory.inspect_records() == []


def test_explicit_missing_notes_never_materialize_or_call_provider(worker, tmp_path):
    data, journal = worker
    before = inventory(tmp_path)
    result = pipeline.run_public_topic(data["source"], journal=journal, llm_fn=None,
        reserved_revision_id=data["source"]["revision_id"], now=NOW,
        context_mode="source_note_snapshot", note_ids=["0" * 64])
    assert result["stage"] == "insufficient_context"
    assert inventory(tmp_path) == before


def test_actual_process_snapshot_provider_receives_only_checked_prompt(worker, monkeypatch):
    data, journal = worker
    monkeypatch.setattr(pipeline, "run", lambda **kw: {"data_committed": 0})
    monkeypatch.setattr(rollout, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: NOW)}))
    from reports import wiki_topic_context
    monkeypatch.setattr(wiki_topic_context, "_note", lambda *a, **kw: pytest.fail("snapshot parsed as legacy"))
    calls = []
    def engine(prompt):
        calls.append(prompt)
        envelope = json.loads(prompt.rsplit("\n", 1)[1])
        assert "notes" in envelope["read_only_source_context"]
        assert "dependencies" not in prompt and str(journal.root) not in prompt
        assert "note_json_bytes" not in prompt and "manifest_sha256" not in prompt
        return json.dumps(payload(data))
    monkeypatch.setattr(rollout, "structured_llm_call", engine)
    reservation = {"id": "synthetic", "revision_id": data["source"]["revision_id"]}
    result = rollout.process(data["source"], [], journal, reservation,
        context_mode="source_note_snapshot", note_ids=[n["note_id"] for n in data["context"]["notes"]])
    from reports.wiki_topic_v3 import snapshot_topic_prompt
    assert calls == [snapshot_topic_prompt(json.loads(calls[0].rsplit("\n", 1)[1]))]
    assert result["topic"]["newly_committed"] and len(calls) == 1
    assert result["topic"]["stage"] == "committed"
    saved = json.loads((journal.root / "automatic-attempts" / "synthetic.json").read_text())
    assert saved["reservation"] == reservation and len(saved["model_calls"]) == 1
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("process history source read"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("process history fresh work"))
    retry = rollout.process(data["source"], [], journal, reservation)
    assert retry["topic"]["stage"] == "committed" and not retry["topic"]["newly_committed"]
    assert len(calls) == 1


def test_real_seed_then_third_source_conflict_updates_same_public_topic(worker, monkeypatch):
    data, journal = worker
    data["context"]["notes"] = data["context"]["notes"][:1]
    data["context"]["dependencies"] = data["context"]["dependencies"][:1]
    seed = run(worker, lambda prompt, **local: json.dumps(payload(data)))
    batch = journal.load_topic_operation_v3(seed["operation_id"])["batch"]
    topic_id = batch["target_ids"][0]
    before = wiki.semantic_snapshot(topic_id)
    later_receipt = next(journal.load_source(path.stem) for path in (journal.root / "sources").glob("*.json")
                         if path.stem not in batch["revision_ids"])
    from reports.wiki_topic_context import _verified_source
    source, compiled, _ = _verified_source(journal, later_receipt["revision_id"], NOW)
    source.update(role="conflict", observed_evidence_ids=["third-source-conflict"])
    journal.save_compilation(source, compiled)
    note_ids = [item["note_id"] for item in batch["source_inputs"].values()]
    context = select_snapshot_context(compiled, note_ids=note_ids, journal=journal, now=NOW, surface="market")
    data.update(source=source, compiled=compiled, context=context, targets=[before])
    monkeypatch.setattr(pipeline, "discover_targets", lambda *a, **kw: {"candidate_ids": [topic_id]})
    def update(prompt, **local):
        envelope = json.loads(prompt.rsplit("\n", 1)[1])
        assert envelope["editable_topics"][0]["body"] == before["body"]
        value = payload(data)
        target = value["targets"][0]
        for key in ("title", "kind", "surface", "purpose"):
            target.pop(key)
        target.update(page_id=topic_id, before_hash=before["revision_hash"])
        return json.dumps(value)
    appended = run(worker, update)
    assert appended["newly_committed"] and appended["operation_id"] != seed["operation_id"]
    after = wiki.semantic_snapshot(topic_id)
    assert after["body"].startswith(before["body"])
    assert all(claim in after["claims"] for claim in before["claims"])
    assert len({claim["source_revision_id"] for claim in after["claims"]}) == 3
    assert "third-source-conflict" in after["conflicting_evidence_ids"]
    assert before["source_knowledge"]["limitations"] == after["source_knowledge"]["limitations"]


def test_process_persists_explicit_v3_stage_for_resume(worker, monkeypatch):
    data, journal = worker
    notes = [n["note_id"] for n in data["context"]["notes"]]
    monkeypatch.setattr(pipeline, "run", lambda **kw: {"data_committed": 0})
    monkeypatch.setattr(rollout, "structured_llm_call", lambda prompt: json.dumps(payload(data)))
    rollout.process(data["source"], [], journal, {"id": "stage", "revision_id": data["source"]["revision_id"]},
                    context_mode="source_note_snapshot", note_ids=notes)
    stage = json.loads(rollout._topic_stage_path(data["source"], journal).read_text())
    assert stage == {"schema_version": 3, "revision_id": data["source"]["revision_id"],
        "role": "support", "state": "done", "context_mode": "source_note_snapshot", "note_ids": notes}


def test_unwitnessed_v3_stage_resumes_exact_local_mode_and_note_ids(worker):
    data, journal = worker
    source = data["source"]
    notes = [n["note_id"] for n in data["context"]["notes"]]
    path = rollout._topic_stage_path(source, journal)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded({"schema_version": 3, "revision_id": source["revision_id"],
        "role": "support", "state": "pending", "context_mode": "source_note_snapshot", "note_ids": notes}))
    sources, reasons = rollout.resume_topic_sources(journal, NOW)
    assert reasons == [] and len(sources) == 1
    assert sources[0]["context_mode"] == "source_note_snapshot" and sources[0]["note_ids"] == notes


@pytest.mark.parametrize("state", ["prepared", "applied", "committed"])
def test_rollout_witness_history_never_reserves_or_calls_source_worker(worker, monkeypatch, state):
    data, journal = worker
    result = run(worker, lambda prompt, **local: json.dumps(payload(data)))
    path = journal._path("topic-operations-v3", result["operation_id"])
    receipt = json.loads(path.read_bytes()); receipt["stage"] = state
    path.write_bytes(encoded(receipt))
    # A missing source receipt and later target edits cannot turn history into fresh work.
    journal._path("sources", data["source"]["revision_id"]).unlink()
    rows = shared_memory.inspect_records()
    next(row for row in rows if row["id"] in receipt["batch"]["target_ids"])["body"] += " Later edit"
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    stage_path = rollout._topic_stage_path(data["source"], journal)
    stage_path.parent.mkdir(exist_ok=True)
    stage_path.write_bytes(encoded({"revision_id": data["source"]["revision_id"], "role": "support", "state": "failed"}))
    monkeypatch.setattr(rollout.quota, "reserve", lambda *a, **kw: pytest.fail("historical reservation"))
    monkeypatch.setattr(pipeline, "_resume_compilations", lambda *a: ([], []))
    monkeypatch.setattr(rollout, "process", lambda *a, **kw: pytest.fail("historical fresh process"))
    batch = ([data["source"]], lambda source: [], lambda source: False, {"pending_sources": 1})
    code = rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal,
        journal.root / "automatic-budget.json", NOW, batch)
    saved = json.loads((journal.root / "automatic-latest.json").read_text())
    assert code == 0 and saved["attempted"] == 0
    if state == "committed":
        assert "topic_recovery" not in saved
    else:
        assert saved["topic_recovery"][0]["stage"] == "committed"
        assert saved["topic_recovery"][0]["reason"] == ""
    assert shared_memory.inspect_records() == rows


@pytest.mark.parametrize("directory,version", [("topic-operations", 3), ("topic-operations-v3", 2),
    ("topic-operations-v3", 99)])
def test_worker_rejects_unknown_or_misplaced_versions_before_source_access(worker, monkeypatch, directory, version):
    data, journal = worker
    path = journal._path(directory, "f" * 64)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded({"schema_version": version, "batch": {"protocol_version": version}}))
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("version not validated first"))
    with pytest.raises(ValueError):
        run(worker, lambda *a, **kw: pytest.fail("unknown version provider"))


def test_process_disabled_snapshot_has_no_writes_or_provider(worker, monkeypatch, tmp_path):
    data, journal = worker
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", "0")
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("disabled legacy work"))
    before = inventory(tmp_path)
    assert rollout.process(data["source"], [], journal,
        {"id": "off", "revision_id": data["source"]["revision_id"]},
        context_mode="source_note_snapshot", note_ids=[]) == {"topic": {"enabled": False}}
    assert inventory(tmp_path) == before


def _legacy_done(journal):
    for path in (journal.root / "sources").glob("*.json"):
        receipt = journal.load_source(path.stem)
        for role, ids in receipt["evidence_by_role"].items():
            if ids:
                journal.save_routing(path.stem, role, {"integrated": True, "unexamined": [],
                                                       "integrated_evidence_ids": ids})


def test_rollout_shared_reservation_is_spent_once_and_history_spends_none(worker, monkeypatch):
    data, journal = worker
    _legacy_done(journal)
    monkeypatch.delenv("WIKI_SOURCE_DAILY_LIMIT", raising=False)
    monkeypatch.setattr(pipeline, "run", lambda **kw: {"data_committed": 0})
    calls = []
    monkeypatch.setattr(rollout, "structured_llm_call", lambda prompt: calls.append(prompt) or json.dumps(payload(data)))
    budget = journal.root / "automatic-budget.json"
    assert rollout.quota.reserve(budget, "a" * 64) is not None
    notes = [n["note_id"] for n in data["context"]["notes"]]
    source = {**data["source"], "context_mode": "source_note_snapshot", "note_ids": notes}
    batch = ([source], lambda source: [], lambda source: False, {"pending_sources": 1})
    assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal, budget, NOW, batch) == 0
    assert len(calls) == 1
    assert rollout.quota.status(budget)["count"] == 2 and rollout.quota.status(budget)["limit"] == 20
    budget_bytes = budget.read_bytes()
    rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal, budget, NOW, batch)
    assert len(calls) == 1 and budget.read_bytes() == budget_bytes


def test_disabled_explicit_snapshot_candidate_never_spends_shared_reservation(worker, monkeypatch):
    data, journal = worker
    _legacy_done(journal)
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", "0")
    source = {**data["source"], "context_mode": "source_note_snapshot", "note_ids": []}
    monkeypatch.setattr(rollout.quota, "reserve", lambda *a, **kw: pytest.fail("disabled reservation"))
    batch = ([source], lambda source: [], lambda source: False, {"pending_sources": 1})
    assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal,
        journal.root / "automatic-budget.json", NOW, batch) == 0


@pytest.mark.parametrize("v3_flag", ["0", "1"])
def test_bootstrap_disabled_persisted_v3_retry_never_spends_quota(worker, monkeypatch, tmp_path, v3_flag):
    data, journal = worker
    _legacy_done(journal)
    source = data["source"]
    journal.save_routing(source["revision_id"], "support", {
        "integrated": False, "unexamined": [], "integrated_evidence_ids": []})
    stage_path = rollout._topic_stage_path(source, journal)
    stage_path.parent.mkdir(exist_ok=True)
    stage_path.write_bytes(encoded({"schema_version": 3, "revision_id": source["revision_id"],
        "role": "support", "state": "pending", "context_mode": "source_note_snapshot",
        "note_ids": [note["note_id"] for note in data["context"]["notes"]]}))
    budget = journal.root / "automatic-budget.json"
    budget.write_bytes(encoded({"schema": 1, "days": {}}))
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "0")
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", v3_flag)
    calls = []
    monkeypatch.setattr(rollout, "structured_llm_call", lambda prompt: calls.append(prompt))
    before = inventory(tmp_path)
    batch = ([source], lambda source: [], lambda source: False, {"pending_sources": 1})
    for _ in range(2):
        assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal, budget, NOW, batch) == 0
        assert rollout.quota.status(budget)["count"] == 0
        assert all((tmp_path / path).read_bytes() == blob for path, blob in before.items())
        assert not list((journal.root / "automatic-attempts").glob("*.json"))
        saved = json.loads((journal.root / "automatic-latest.json").read_text())
        assert saved["attempted"] == 0
        assert calls == []


def _prepare_legacy_topic(worker):
    from reports.wiki_topic_context import _verified_source, select_topic_context
    from reports.wiki_topic_integration import validate_topic_integration
    from reports.wiki_topic_transaction import prepare_topic_batch
    data, journal = worker
    notes = sorted(data["context"]["notes"], key=lambda note: note["revision_id"])
    source, compiled, receipt = _verified_source(journal, notes[0]["revision_id"], NOW)
    source.update(role="support", observed_evidence_ids=receipt["evidence_by_role"]["support"])
    row = {"id": "legacy-source-note", "title": "Capacity report", "body": "Earlier attribution",
           "tags": ["wiki", "surface:market", "kind:concept", "status:draft", "compiled_source"]}
    before = shared_memory.inspect_records()
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked([*before, row])
    rows = shared_memory.inspect_records()
    operation_id = "c" * 64
    journal.prepare({"operation_id": operation_id, "revision_id": notes[1]["revision_id"],
        "target_ids": [row["id"]], "upserts": [row], "expected_before": {row["id"]: None},
        "expected_after": {row["id"]: wiki._source_fingerprint(row["id"], rows)}})
    journal.transition(operation_id, stage="applied")
    journal.transition(operation_id, stage="committed", effects={key: "done" for key in ("artifacts", "search", "log")})
    context = select_topic_context(compiled, journal=journal, records=rows, now=NOW, surface="market")
    legacy = {"source": source, "compiled": compiled, "context": context, "targets": []}
    value = payload(legacy)
    value["targets"][0]["title"] = "Legacy capacity topic"
    proposal = validate_topic_integration(value, **legacy)
    return prepare_topic_batch(proposal, journal=journal, records=rows, now=NOW)


def test_mixed_actual_v1_v2_v3_discovery_and_retry_dispatch(worker, monkeypatch):
    data, journal = worker
    v2 = _prepare_legacy_topic(worker)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kw: {"ok": True})
    monkeypatch.setattr(wiki, "sync_qmd", lambda: {"ok": True})
    monkeypatch.setattr(wiki, "_append_wiki_log", lambda *a, **kw: None)
    v3 = run(worker, lambda prompt, **local: json.dumps(payload(data)))
    v3_batch = journal.load_topic_operation_v3(v3["operation_id"])["batch"]
    pending = pipeline._public_topic_operations(journal)
    assert [r["schema_version"] for r in pending] == [2, 3]
    assert all("source_inputs" not in r["batch"] for r in pending if r["schema_version"] == 2)
    assert pipeline._recover_public_topic(pending[0], journal=journal, now=NOW)["stage"] == "committed"
    assert pipeline._recover_public_topic(pending[1], journal=journal, now=NOW)["stage"] == "committed"
    from scripts.run_public_wiki_pilot import public_target_ids, hydrate_public_target
    allowed = public_target_ids(journal, rollout.public_revisions(journal))
    assert {"legacy-source-note", *v2["target_ids"], *v3_batch["target_ids"]} <= allowed
    for identity in allowed:
        assert hydrate_public_target(identity, journal, rollout.public_revisions(journal))["page_id"] == identity


@pytest.mark.parametrize("topic_flag,v3_flag", [("1", "0"), ("0", "1"), ("0", "0")])
def test_default_process_cannot_dispatch_v3_history_when_either_gate_disabled(
        worker, monkeypatch, tmp_path, topic_flag, v3_flag):
    data, journal = worker
    run(worker, lambda prompt, **local: json.dumps(payload(data)))
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", topic_flag)
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", v3_flag)
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("disabled historical source read"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("disabled history became legacy work"))
    before = inventory(tmp_path)
    result = rollout.process(data["source"], [], journal,
        {"id": "disabled-history", "revision_id": data["source"]["revision_id"]})
    assert result == {"topic": {"enabled": False}}
    assert inventory(tmp_path) == before
    assert pipeline.run_public_topic(data["source"], journal=journal, llm_fn=None,
        reserved_revision_id=data["source"]["revision_id"], now=NOW) == {"enabled": False}
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("damage", ["expired", "deleted"])
def test_default_process_and_resume_recognize_history_before_mutable_source_reads(worker, monkeypatch, damage):
    data, journal = worker
    result = run(worker, lambda prompt, **local: json.dumps(payload(data)))
    if damage == "expired":
        path = Path(data["source"]["manifest_ref"])
        value = json.loads(path.read_bytes()); value["expires_at"] = NOW.isoformat()
        path.write_bytes(encoded(value))
    else:
        journal._path("sources", data["source"]["revision_id"]).unlink()
    path = rollout._topic_stage_path(data["source"], journal)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded({"revision_id": data["source"]["revision_id"], "role": "support", "state": "failed"}))
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("historical source read"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("history became legacy work"))
    monkeypatch.setattr(rollout, "structured_llm_call", lambda *a: pytest.fail("historical model"))
    assert not rollout.pending_public_source(data["source"], journal)
    assert rollout.resume_topic_sources(journal, NOW) == ([], [])
    recovered = rollout.process(data["source"], [], journal,
        {"id": "history", "revision_id": data["source"]["revision_id"]})
    assert recovered["topic"]["operation_id"] == result["operation_id"]
    assert recovered["topic"]["stage"] == "committed"
    assert recovered["topic"]["reason"] == ""


def test_disabled_rollout_does_not_repair_v3_receipts(worker, monkeypatch):
    data, journal = worker
    _legacy_done(journal)
    run(worker, lambda prompt, **local: json.dumps(payload(data)))
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", "0")
    monkeypatch.setattr(transaction, "recover_topic_operation_v3", lambda *a, **kw: pytest.fail("disabled v3 recovery"))
    batch = ([data["source"]], lambda source: [], lambda source: False, {"pending_sources": 1})
    assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal,
        journal.root / "automatic-budget.json", NOW, batch) == 0


def test_rollout_history_does_not_revalidate_mutable_source_during_selection(worker, monkeypatch):
    data, journal = worker
    _legacy_done(journal)
    run(worker, lambda prompt, **local: json.dumps(payload(data)))
    original = journal.load_source
    def read(identity):
        if identity == data["source"]["revision_id"]:
            pytest.fail("historical source read during selection")
        return original(identity)
    monkeypatch.setattr(journal, "load_source", read)
    monkeypatch.setattr(rollout.quota, "reserve", lambda *a, **kw: pytest.fail("historical quota"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("historical legacy worker"))
    batch = ([data["source"]], lambda source: [], lambda source: False, {"pending_sources": 1})
    assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal,
        journal.root / "automatic-budget.json", NOW, batch) == 0


@pytest.mark.parametrize("v3_flag", ["0", "1"])
def test_default_process_does_not_downgrade_persisted_snapshot_stage(worker, monkeypatch, tmp_path, v3_flag):
    data, journal = worker
    path = rollout._topic_stage_path(data["source"], journal)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded({"schema_version": 3, "revision_id": data["source"]["revision_id"],
        "role": "support", "state": "pending", "context_mode": "source_note_snapshot",
        "note_ids": [n["note_id"] for n in data["context"]["notes"]]}))
    monkeypatch.setenv("AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED", v3_flag)
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("snapshot stage downgraded"))
    before = inventory(tmp_path)
    if v3_flag == "0":
        assert rollout.process(data["source"], [], journal,
            {"id": "stage", "revision_id": data["source"]["revision_id"]}) == {"topic": {"enabled": False}}
    else:
        with pytest.raises(ValueError, match="topic_context_mode_changed"):
            rollout.process(data["source"], [], journal,
                {"id": "stage", "revision_id": data["source"]["revision_id"]})
    assert inventory(tmp_path) == before


def test_expired_prepared_rollout_retry_never_falls_through_to_new_reservation(worker, monkeypatch):
    data, journal = worker
    _legacy_done(journal)
    transaction.prepare_topic_batch_v3(propose((data, journal, [])), journal=journal, records=[], now=NOW)
    path = Path(data["source"]["manifest_ref"])
    value = json.loads(path.read_bytes()); value["expires_at"] = NOW.isoformat()
    path.write_bytes(encoded(value))
    monkeypatch.setattr(rollout.quota, "reserve", lambda *a, **kw: pytest.fail("stale retry new reservation"))
    batch = ([data["source"]], lambda source: [], lambda source: False, {"pending_sources": 1})
    assert rollout._run_batch(SimpleNamespace(apply=True, source_limit=1), journal,
        journal.root / "automatic-budget.json", NOW, batch) == 2


@pytest.mark.parametrize("source_metadata", ["absent", "revoked"])
def test_witnessed_history_needs_no_current_source_body_or_public_policy(worker, monkeypatch, source_metadata):
    data, journal = worker
    result = run(worker, lambda prompt, **local: json.dumps(payload(data)))
    source = {"revision_id": data["source"]["revision_id"], "role": "support"}
    if source_metadata == "revoked":
        source.update(provider="private", scope="personal_conversation", canonical_url="private")
    monkeypatch.setattr(journal, "load_source", lambda *a: pytest.fail("historical policy lookup"))
    monkeypatch.setattr(pipeline, "run", lambda **kw: pytest.fail("historical source body required"))
    direct = pipeline.run_public_topic(source, journal=journal, llm_fn=None,
        reserved_revision_id=source["revision_id"], now=NOW)
    assert direct["stage"] == "committed" and direct["operation_id"] == result["operation_id"]
    actual = rollout.process(source, [], journal, {"id": "frozen", "revision_id": source["revision_id"]})
    assert actual["topic"]["stage"] == "committed"
