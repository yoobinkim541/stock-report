"""Task 8: synthetic authorization, migration and actual v3 pilot contracts."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from agent_console import shared_memory, wiki
from reports import wiki_public_budget as quota
from reports.wiki_source_notes import materialize_source_note
from test_wiki_source_notes import NOW, encoded, inventory, store


def api():
    assert importlib.util.find_spec("reports.wiki_topic_pilot_state") is not None, "Task8 state API missing"
    return importlib.import_module("reports.wiki_topic_pilot_state")


def runner():
    return importlib.import_module("scripts.run_public_topic_pilot")


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


@pytest.fixture
def authorized(store, tmp_path, monkeypatch):
    sources = [store(url=f"https://t.me/public_channel/{i}", body=f"Capacity report {i}")
               for i in range(1, 4)]
    journal = sources[0][0]
    revisions = tuple(s[1]["revision_id"] for s in sources)
    monkeypatch.setattr(runner(), "APPROVED_REVISIONS", revisions)
    if importlib.util.find_spec("reports.wiki_topic_pilot_state"):
        monkeypatch.setattr(api(), "_now", lambda: NOW)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked([])
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: shared_memory._read_jsonl_checked(
        shared_memory._paths()["events"]))
    pins = {r: materialize_source_note(r, journal=journal, now=NOW, apply=True)["note_id"]
            for r in revisions}
    # This is a synthetic, explicitly retained operator decision, NOT a production initializer.
    approval = {"schema_version": 1, "approval_ref": "original-operator-approval",
                "revision_ids": list(revisions), "max_attempts": 2, "daily_limit": 20,
                "decision": "approved"}
    identity = sha(encoded(approval))
    state = {"schema_version": 1, "revisions": list(revisions), "attempts": [],
             "topic_ids": [], "baseline": None}
    path = journal.root / "topic-pilot.json"
    old = b" \n" + json.dumps(state, indent=3).encode() + b"\n"
    path.write_bytes(old)
    proof = journal.root / "topic-pilot-history" / "original.json"
    put(proof, {"approval": approval, "decision": "untouched", "state_path": str(path)})
    put(journal.root / "automatic-budget.json", {"schema": 1, "days": {}})
    guard = {"schema_version": 1, "authorization_id": identity, "approval": approval,
             "state_path": str(path), "state_sha256": sha(old),
             "history_proofs": {str(proof.relative_to(journal.root)): sha(proof.read_bytes())},
             "lifecycle": "initialized", "attempts": []}
    guard_path = journal.root / "topic-pilot-authorizations" / f"{identity}.json"
    put(guard_path, guard)
    for target in ("automatic-run", "topic-pilot-authorizations", "topic-pilot.json"):
        (journal.root / (target + ".lock")).touch()
    return {"journal": journal, "path": path, "old": old, "guard": guard,
            "guard_path": guard_path, "id": identity, "pins": pins, "sources": sources,
            "revisions": revisions}


def inspect(env):
    return api().inspect_pilot_authorization(journal=env["journal"])


def migrate(env, apply=True, **kwargs):
    return api().migrate_empty_pilot_state(journal=env["journal"],
        expected_sha256=kwargs.get("expected_sha256", sha(env["old"])),
        authorization_id=kwargs.get("authorization_id", env["id"]), apply=apply)


def replace_state(env, mutate):
    state = json.loads(env["path"].read_bytes())
    mutate(state)
    put(env["path"], state)
    guard = json.loads(env["guard_path"].read_bytes())
    guard.update(state_sha256=sha(env["path"].read_bytes()), attempts=state["attempts"])
    put(env["guard_path"], guard)


def test_inspect_and_dry_migration_are_readonly(authorized, tmp_path):
    before = inventory(tmp_path)
    assert inspect(authorized)["status"] == "ready"
    result = migrate(authorized, apply=False)
    assert result["status"] == "migration_ready"
    assert result["state"]["note_ids"] == authorized["pins"]
    assert inventory(tmp_path) == before


def test_migration_same_path_retains_exact_bytes_and_is_idempotent(authorized):
    result = migrate(authorized)
    assert result["status"] == "ready"
    state = json.loads(authorized["path"].read_bytes())
    assert set(state) == {"schema_version", "authorization_id", "context_mode", "revision_ids",
                          "note_ids", "baseline", "attempts", "topic_ids"}
    assert type(state["schema_version"]) is int and state["schema_version"] == 2
    assert state["note_ids"] == authorized["pins"]
    record = authorized["journal"].root / "topic-pilot-migrations" / authorized["id"]
    assert (record / "old-state.bin").read_bytes() == authorized["old"]
    intent = json.loads((record / "intent.json").read_bytes())
    assert intent["before_sha256"] == sha(authorized["old"])
    assert intent["after_sha256"] == sha(authorized["path"].read_bytes())
    assert migrate(authorized)["status"] == "ready"
    assert inspect(authorized)["attempts_used"] == 0
    assert not list(authorized["journal"].root.glob("topic-pilot-v*.json"))


@pytest.mark.parametrize("field,value", [("attempts", [{"status": "failed"}]),
    ("topic_ids", ["old-topic"]), ("baseline", []), ("schema_version", True),
    ("schema_version", 1.0), ("extra", 1)])
def test_nonempty_or_non_strict_legacy_state_cannot_migrate(authorized, field, value):
    replace_state(authorized, lambda s: s.update({field: value}))
    assert migrate(authorized)["status"] == "needs_review"


@pytest.mark.parametrize("damage", ["missing_state", "corrupt_state", "missing_guard", "corrupt_guard",
    "extra_guard", "bool_guard", "float_guard", "extra_approval", "bool_limit", "float_limit",
    "revision_order", "approval_identity", "daily_limit", "proof_missing", "proof_changed",
    "budget_missing", "budget_corrupt", "orphan_reservation", "orphan_operation", "orphan_witness",
    "duplicate_guard", "relocated", "state_symlink", "guard_symlink"])
def test_uncertain_history_never_becomes_zero_allowance(authorized, damage, tmp_path):
    env = authorized
    guard = deepcopy(env["guard"])
    if damage == "missing_state": env["path"].unlink()
    elif damage == "corrupt_state": env["path"].write_bytes(b"{")
    elif damage == "missing_guard": env["guard_path"].unlink()
    elif damage == "corrupt_guard": env["guard_path"].write_bytes(b"{")
    elif damage == "extra_guard": guard["extra"] = 1
    elif damage == "bool_guard": guard["schema_version"] = True
    elif damage == "float_guard": guard["schema_version"] = 1.0
    elif damage == "extra_approval": guard["approval"]["extra"] = 1
    elif damage == "bool_limit": guard["approval"]["max_attempts"] = True
    elif damage == "float_limit": guard["approval"]["max_attempts"] = 2.0
    elif damage == "revision_order": guard["approval"]["revision_ids"].reverse()
    elif damage == "approval_identity": guard["approval"]["approval_ref"] = "new-random-approval"
    elif damage == "daily_limit": guard["approval"]["daily_limit"] = 21
    elif damage == "proof_missing": next((env["journal"].root / "topic-pilot-history").iterdir()).unlink()
    elif damage == "proof_changed": next((env["journal"].root / "topic-pilot-history").iterdir()).write_bytes(b"new")
    elif damage == "budget_missing": (env["journal"].root / "automatic-budget.json").unlink()
    elif damage == "budget_corrupt": (env["journal"].root / "automatic-budget.json").write_bytes(b"{")
    elif damage == "orphan_reservation": quota.reserve(env["journal"].root / "automatic-budget.json", env["revisions"][1], NOW)
    elif damage == "orphan_operation": put(env["journal"].root / "topic-operations-v3" / ("a" * 64 + ".json"), {})
    elif damage == "orphan_witness":
        with shared_memory._events_lock():
            shared_memory._write_jsonl_locked([{"id": "topic-commit-v3:" + "a" * 64,
                "topic_operation_id": "a" * 64, "tags": ["topic-commit"]}])
    elif damage == "duplicate_guard": put(env["guard_path"].with_name("f" * 64 + ".json"), guard)
    elif damage == "relocated":
        destination = tmp_path / "relocated"
        shutil.copytree(env["journal"].root, destination)
        env["journal"].root = destination
    elif damage in {"state_symlink", "guard_symlink"}:
        path = env["path"] if damage == "state_symlink" else env["guard_path"]
        other = tmp_path / "outside.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    if guard != env["guard"]:
        put(env["guard_path"], guard)
    before = inventory(tmp_path)
    assert inspect(env)["status"] == "needs_review"
    assert migrate(env)["status"] == "needs_review"
    result = runner().run(env["journal"], now=NOW, apply=True,
                          llm_fn=lambda _: pytest.fail("uncertain authorization called provider"))
    assert result["status"] == "needs_review"
    assert inventory(tmp_path) == before


def test_migration_cas_and_changed_authorization(authorized):
    before = authorized["path"].read_bytes()
    assert migrate(authorized, expected_sha256="0" * 64)["status"] == "needs_review"
    assert migrate(authorized, authorization_id="0" * 64)["status"] == "needs_review"
    assert authorized["path"].read_bytes() == before


@pytest.mark.parametrize("point", ["record_before", "record_after", "state_before", "state_after", "guard_after"])
def test_migration_crash_reconciles_without_new_allowance(authorized, monkeypatch, point):
    module = api()
    publish, atomic = module._publish_record, module._atomic
    def crash_publish(*args):
        if point == "record_before": raise KeyboardInterrupt(point)
        result = publish(*args)
        if point == "record_after": raise KeyboardInterrupt(point)
        return result
    def crash_atomic(journal, relative, blob):
        if relative == "topic-pilot.json" and point == "state_before": raise KeyboardInterrupt(point)
        result = atomic(journal, relative, blob)
        if relative == "topic-pilot.json" and point == "state_after": raise KeyboardInterrupt(point)
        if relative.startswith("topic-pilot-authorizations/") and point == "guard_after":
            raise KeyboardInterrupt(point)
        return result
    monkeypatch.setattr(module, "_publish_record", crash_publish)
    monkeypatch.setattr(module, "_atomic", crash_atomic)
    with pytest.raises(KeyboardInterrupt): migrate(authorized)
    monkeypatch.setattr(module, "_publish_record", publish)
    monkeypatch.setattr(module, "_atomic", atomic)
    assert migrate(authorized)["status"] == "ready"
    assert inspect(authorized)["attempts_used"] == 0
    assert quota.status(authorized["journal"].root / "automatic-budget.json", NOW)["count"] == 0


def test_missing_or_ambiguous_artifacts_are_not_materialized(authorized):
    note = authorized["journal"].root / "source-notes" / next(iter(authorized["pins"].values()))
    shutil.rmtree(note)
    before = inventory(authorized["journal"].root)
    assert migrate(authorized)["status"] == "needs_review"
    assert inventory(authorized["journal"].root) == before


def model(calls, *, refuse=False, fail=False, interrupt=False):
    def respond(prompt):
        calls.append(prompt)
        if interrupt: raise KeyboardInterrupt("synthetic interruption")
        if fail: raise RuntimeError("synthetic failure")
        if refuse: return '{"targets":[],"refusal":"Need more verification"}'
        envelope = json.loads(prompt.rsplit("\n", 1)[1])
        current = envelope["compiled"]
        target = next(iter(envelope["editable_topics"]), None)
        units = [(current["revision_id"], current["claims"][0])]
        if target is None:
            note = envelope["read_only_source_context"]["notes"][0]
            units.append((note["revision_id"], note["claims"][0]))
        labels = {v["revision_id"]: k for k, v in envelope["source_map"].items()}
        proposed = {"page_id": target["page_id"] if target else None,
            "before_hash": target["revision_hash"] if target else None,
            "contributing_revision_ids": [r for r, _ in units], "scope_explanation": "Dated reports",
            "relations": [], "additions": [{"claim_ids": [c["claim_id"]], "revision_ids": [r],
                "as_of": c["as_of"], "scope": c["scope"], "body": f"The author reports [{labels[r]}]."}
                for r, c in units]}
        if target is None:
            proposed.update(title="Capacity topic", kind="concept", surface="market", purpose="topic_synthesis")
        return json.dumps({"targets": [proposed], "refusal": None})
    return respond


def test_real_seed_and_same_topic_followup_two_total_calls(authorized, tmp_path):
    assert migrate(authorized)["status"] == "ready"
    sources_before = {str(p): p.read_bytes() for directory in ("sources", "compiled", "source-notes")
                      for p in (authorized["journal"].root / directory).rglob("*") if p.is_file()}
    calls = []
    result = runner().run(authorized["journal"], now=NOW, apply=True, llm_fn=model(calls))
    assert result["status"] == "awaiting_semantic_review"
    assert result["attempts_used"] == 2 and result["quota"]["count"] == 2 and len(calls) == 2
    state = json.loads(authorized["path"].read_bytes())
    assert len(state["topic_ids"]) == 1
    topic = wiki.semantic_snapshot(state["topic_ids"][0])
    assert topic["page"]["status"] == "draft"
    assert {c["source_revision_id"] for c in topic["claims"]} == set(authorized["revisions"])
    envelopes = [json.loads(p.rsplit("\n", 1)[1]) for p in calls]
    assert not envelopes[0]["editable_topics"]
    assert envelopes[1]["editable_topics"][0]["page_id"] == state["topic_ids"][0]
    assert envelopes[1]["editable_topics"][0]["body"] in topic["page"]["body"]
    assert inspect(authorized)["status"] == "awaiting_semantic_review"
    before = inventory(tmp_path)
    assert runner().run(authorized["journal"], now=NOW + timedelta(days=1), apply=True,
                        llm_fn=model(calls))["status"] == "awaiting_semantic_review"
    assert len(calls) == 2 and inventory(tmp_path) == before
    assert all(Path(p).read_bytes() == blob for p, blob in sources_before.items())
    assert not list((authorized["journal"].root / "topic-operations").glob("*.json"))
    assert migrate(authorized)["status"] == "needs_review"


@pytest.mark.parametrize("outcome", ["refused", "failed", "interrupted"])
def test_bad_seed_consumes_attempt_and_no_followup(authorized, outcome):
    migrate(authorized)
    calls = []
    provider = model(calls, refuse=outcome == "refused", fail=outcome == "failed", interrupt=outcome == "interrupted")
    if outcome == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            runner().run(authorized["journal"], now=NOW, apply=True, llm_fn=provider)
    else:
        assert runner().run(authorized["journal"], now=NOW, apply=True, llm_fn=provider)["status"] == outcome
    again = runner().run(authorized["journal"], now=NOW, apply=True, llm_fn=provider)
    assert again["status"] == outcome and again["attempts_used"] == 1
    assert len(calls) == 1
    assert quota.status(authorized["journal"].root / "automatic-budget.json", NOW)["count"] == 1
    assert migrate(authorized)["status"] == "needs_review"


def test_daily_quota_uses_shared_twenty_and_kst(authorized, monkeypatch):
    migrate(authorized)
    path = authorized["journal"].root / "automatic-budget.json"
    instant = NOW.replace(hour=16)
    for index in range(20): quota.reserve(path, format(index, "064x"), instant)
    monkeypatch.setenv("WIKI_SOURCE_DAILY_LIMIT", "100")
    calls = []
    result = runner().run(authorized["journal"], now=instant, apply=True, llm_fn=model(calls))
    assert result["status"] == "quota_exhausted" and result["attempts_used"] == 0
    assert result["quota"]["day"] == "2026-10-21" and result["quota"]["limit"] == 20
    assert not calls


def test_locks_fixed_order_and_inspection_never_initializes(tmp_path, authorized, monkeypatch):
    import fcntl
    import os
    acquired = []
    flock = fcntl.flock
    def record(fd, operation):
        if operation & fcntl.LOCK_EX:
            acquired.append(Path(os.readlink(f"/proc/self/fd/{fd}")).name)
        return flock(fd, operation)
    monkeypatch.setattr(fcntl, "flock", record)
    inspect(authorized)
    assert acquired[:3] == ["automatic-run.lock", "topic-pilot-authorizations.lock", "topic-pilot.json.lock"]
    journal = type(authorized["journal"])(tmp_path / "never-initialized")
    assert api().inspect_pilot_authorization(journal=journal)["status"] == "needs_review"
    assert not journal.root.exists()


@pytest.mark.parametrize("flag", ["--now", "--date", "--revision", "--revision-ids"])
def test_actual_cli_has_no_time_or_revision_override(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["run_public_topic_pilot", flag, "invented"])
    with pytest.raises(SystemExit) as exc: runner().main()
    assert exc.value.code == 2
