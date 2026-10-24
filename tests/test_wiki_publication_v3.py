"""Offline historical effects against actual canonical v3 commits."""
import base64
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from datetime import timedelta

import pytest

from agent_console import shared_memory, wiki, wiki_publication as publication
from reports import wiki_publication_effects as effects, wiki_topic_transaction_v3 as transaction
from reports import wiki_public_budget
from reports.wiki_source_notes import materialize_source_note
from test_wiki_source_notes import NOW, store
from test_wiki_topic_v3 import evidence, propose


def forbidden(*args, **kwargs):
    pytest.fail("historical recovery accessed fresh evidence/model/quota")


@pytest.fixture
def committed(evidence, tmp_path, monkeypatch):
    _, journal, rows = evidence
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(tmp_path / "qmd"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setattr(effects, "publication_root", lambda: tmp_path / "publication")
    proposal = propose(evidence)
    for item in proposal["source_map"].values():
        materialize_source_note(item["revision_id"], journal=journal, now=NOW, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=NOW)
    assert transaction._apply_with_witness_v3(batch, transaction.topic_commit_record_v3(batch))["status"] == "applied"
    journal.transition_topic_v3(batch["operation_id"], stage="committed")
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: transaction._rows())
    return journal, batch, tmp_path / "publication"


def assets(batch):
    return {asset["note_id"]: deepcopy(asset) for asset in batch["public_proof"]["source_assets"].values()}


def recover(journal, batch):
    return transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=NOW + timedelta(days=400))


def test_optional_assets_preserve_legacy_snapshot_bytes(committed, monkeypatch):
    monkeypatch.setattr(wiki, "_now", lambda: "2026-10-20T00:00:00+00:00")
    legacy = publication.capture_snapshot()
    assert set(legacy) == {"records", "captured_at", "schema_text", "schema_sha256", "snapshot_sha256"}
    assert publication.capture_snapshot(source_note_assets=None) == legacy
    assert publication.render_candidate(legacy) == publication.render_candidate(publication.capture_snapshot())
    empty = publication.capture_snapshot(source_note_assets={})
    assert empty["snapshot_sha256"] != legacy["snapshot_sha256"]


@pytest.mark.parametrize("mutation", ["delete", "alter"])
def test_binding_and_generation_ignore_mutable_receipts_and_live_assets(committed, monkeypatch, mutation):
    from reports import wiki_source_compiler, wiki_topic_snapshot_context
    journal, batch, root = committed
    receipt = journal.load_topic_operation_v3(batch["operation_id"])
    binding = effects._binding(receipt, journal)
    for revision in batch["revision_ids"]:
        source_receipt = journal.load_source(revision)
        live = [source_receipt["compile_ref"], *[source_receipt["source"][key]
                for key in ("raw_ref", "text_ref", "manifest_ref")]]
        for name in live:
            live_path = Path(name)
            if live_path.exists():
                if mutation == "delete":
                    live_path.unlink()
                else:
                    live_path.write_bytes(b"changed live archive/compilation bytes")
        path = journal._path("sources", revision)
        if mutation == "delete":
            path.unlink()
        else:
            path.write_text('{"role":"conflict","routing":{},"evidence_by_role":{}}')
    for path in (journal.root / "source-notes").rglob("*"):
        if path.is_file() and path.suffix != ".lock":
            if mutation == "delete":
                path.unlink()
            else:
                path.write_bytes(b"changed live bytes")
    monkeypatch.setattr(journal, "load_source", forbidden)
    monkeypatch.setattr(wiki_public_budget, "reserve", forbidden)
    monkeypatch.setattr(wiki_source_compiler, "compile_source", forbidden)
    monkeypatch.setattr(wiki_topic_snapshot_context, "select_snapshot_context", forbidden)
    monkeypatch.setattr(transaction, "_freeze", forbidden)
    assert effects._binding(receipt, journal) == binding
    before = shared_memory._paths()["events"].read_bytes()
    result = recover(journal, batch)
    assert result["stage"] == "committed" and not result["newly_committed"]
    assert result["effects"] == {"artifacts": "done", "search": "failed", "log": "done"}
    assert shared_memory._paths()["events"].read_bytes() == before
    reader = publication.pin_generation(root=root)
    for asset in assets(batch).values():
        note_id = asset["note_id"]
        assert reader.read(f"source-notes/{note_id}.json") == base64.b64decode(asset["note_assets"]["note_json_bytes"])
        assert reader.read(f"source-notes/{note_id}.md") == base64.b64decode(asset["note_assets"]["note_md_bytes"])
        topic = reader.read(f"notes/{batch['target_ids'][0]}.md").decode()
        assert f"../source-notes/{note_id}.md" in topic
    assert recover(journal, batch)["effects"] == result["effects"]


def test_frozen_assets_are_detached_hash_checked_and_required_for_links(committed):
    _, batch, _ = committed
    supplied = assets(batch)
    snapshot = publication.capture_snapshot(source_note_assets=supplied)
    files = publication.render_candidate(snapshot)
    assert publication.validate_candidate(files)["ok"]
    note_id = next(iter(supplied))
    supplied[note_id]["note_assets"]["note_md_bytes"] = "dGFtcGVy"
    assert publication.render_candidate(snapshot) == files
    changed = deepcopy(snapshot)
    changed["source_note_assets"] = supplied
    with pytest.raises(ValueError, match="snapshot_hash_mismatch"):
        publication.render_candidate(changed)
    changed["snapshot_sha256"] = publication._snapshot_hash(changed)
    with pytest.raises(ValueError, match="source_note"):
        publication.render_candidate(changed)
    missing = dict(files)
    del missing[f"source-notes/{note_id}.json"]
    assert not publication.validate_candidate(missing)["ok"]
    stripped = deepcopy(snapshot)
    stripped["source_note_assets"].pop(note_id)
    stripped["records"][0]["body"] += f"\n[Evidence](../source-notes/{note_id}.md)"
    stripped["snapshot_sha256"] = publication._snapshot_hash(stripped)
    assert not publication.validate_candidate(publication.render_candidate(stripped))["ok"]


def test_default_off_fallback_uses_frozen_metadata_without_generation(committed, monkeypatch, tmp_path):
    journal, batch, root = committed
    monkeypatch.delenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED")
    for revision in batch["revision_ids"]:
        journal._path("sources", revision).unlink()
    monkeypatch.setattr(journal, "load_source", forbidden)
    monkeypatch.setattr(wiki_public_budget, "reserve", forbidden)
    result = recover(journal, batch)
    assert result["stage"] == "committed"
    assert result["effects"] == {"artifacts": "done", "search": "done", "log": "done"}
    assert not root.exists()
    for note_id in assets(batch):
        assert (tmp_path / "wiki" / "source-notes" / f"{note_id}.json").is_file()


@pytest.mark.parametrize("generation", [True, False])
def test_target_drift_without_intent_stays_committed_and_unavailable(committed, monkeypatch, generation):
    journal, batch, root = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", str(int(generation)))
    with shared_memory._events_lock():
        rows = transaction._rows()
        next(row for row in rows if row["id"] == batch["target_ids"][0])["body"] = "Newer editorial knowledge"
        shared_memory._write_jsonl_locked(rows)
    before = shared_memory._paths()["events"].read_bytes()
    monkeypatch.setattr(journal, "load_source", forbidden)
    result = recover(journal, batch)
    assert result["stage"] == "committed"
    assert all(value != "done" for value in result["effects"].values())
    assert result["reason"] == "publication_target_mismatch"
    assert not (root / "current.json").exists()
    assert shared_memory._paths()["events"].read_bytes() == before


@pytest.mark.parametrize("boundary", ["publication", "pointer", "search", "log", "sidecar"])
def test_crashes_repair_same_frozen_generation(committed, monkeypatch, boundary):
    journal, batch, root = committed
    owner, name = {
        "publication": (publication, "publish_snapshot"),
        "pointer": (publication, "_activate_locked"),
        "search": (effects.qmd_generation, "build_index"),
        "log": (wiki, "_append_wiki_log"),
        "sidecar": (effects, "_save"),
    }[boundary]
    original = getattr(owner, name)
    def crash(*args, **kwargs):
        if boundary == "sidecar" and (not args[1].get("activated") or args[0].parent.name != "publication-effects"):
            return original(*args, **kwargs)
        if boundary == "pointer":
            value = original(*args, **kwargs)
            if args[1] != root:
                return value
        raise OSError("injected " + boundary)
    monkeypatch.setattr(owner, name, crash)
    first = recover(journal, batch)
    assert first["stage"] == "committed"
    assert first["effects"][{"publication": "artifacts", "pointer": "artifacts", "sidecar": "artifacts"}.get(boundary, boundary)] == "failed"
    sidecar = json.loads(journal._path("publication-effects", batch["operation_id"]).read_bytes())
    identity = sidecar["publication"]
    monkeypatch.setattr(owner, name, original)
    monkeypatch.setattr(journal, "load_source", forbidden)
    again = recover(journal, batch)
    assert again["effects"]["artifacts"] == again["effects"]["log"] == "done"
    assert publication.pin_generation(root=root).generation_id == identity["generation_id"]
    assert json.loads(journal._path("publication-effects", batch["operation_id"]).read_bytes())["snapshot"] == sidecar["snapshot"]


def test_real_index_adapter_binds_search_to_same_generation(committed, monkeypatch):
    from test_qmd_generation_search import Runner, capability
    journal, batch, root = committed
    runner = Runner()
    monkeypatch.setattr(wiki.qmd_search, "qmd_bin", lambda: "/bin/true")
    monkeypatch.setattr(effects, "search_dependencies", lambda: (capability(), runner))
    result = recover(journal, batch)
    assert result["effects"] == {"artifacts": "done", "search": "done", "log": "done"}
    reader = publication.pin_generation(root=root)
    descriptor = json.loads((root / "search-current.json").read_bytes())
    assert descriptor["generation_id"] == reader.generation_id
    assert descriptor["snapshot_sha256"] == reader.snapshot_sha256
    monkeypatch.setattr(effects.qmd_generation, "build_index", forbidden)
    assert recover(journal, batch)["effects"] == result["effects"]


def test_newer_active_generation_never_reactivates_old_effects(committed, monkeypatch):
    journal, batch, root = committed
    old = recover(journal, batch)
    frozen = json.loads(journal._path("publication-effects", batch["operation_id"]).read_bytes())["snapshot"]
    newer = deepcopy(frozen)
    newer["captured_at"] = "2026-10-21T00:00:00+00:00"
    newer["snapshot_sha256"] = publication._snapshot_hash(newer)
    publication.publish_snapshot(newer, root=root, operation_id="newer")
    pointer = (root / "current.json").read_bytes()
    monkeypatch.setattr(publication, "_activate_locked", forbidden)
    monkeypatch.setattr(effects.qmd_generation, "build_index", forbidden)
    monkeypatch.setattr(journal, "load_source", forbidden)
    assert recover(journal, batch)["effects"] == old["effects"]
    assert (root / "current.json").read_bytes() == pointer


def test_rehashed_sidecar_cannot_drop_witnessed_assets(committed):
    journal, batch, _ = committed
    recover(journal, batch)
    path = journal._path("publication-effects", batch["operation_id"])
    sidecar = json.loads(path.read_bytes())
    sidecar["snapshot"]["source_note_assets"] = {}
    sidecar["snapshot"]["snapshot_sha256"] = publication._snapshot_hash(sidecar["snapshot"])
    sidecar["publication"] = effects._identity_for(sidecar["snapshot"])
    sidecar["activated"] = False
    path.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="publication_snapshot_assets_mismatch"):
        effects.repair_publication(batch["operation_id"], journal=journal)


@pytest.mark.parametrize("mutation", ["missing_proof", "asset", "source_input", "witness"])
def test_frozen_proof_or_witness_tamper_never_certifies_success(committed, monkeypatch, mutation):
    journal, batch, _ = committed
    receipt = journal.load_topic_operation_v3(batch["operation_id"])
    changed = deepcopy(receipt)
    if mutation == "missing_proof":
        changed["batch"].pop("public_proof")
    elif mutation == "asset":
        asset = next(iter(changed["batch"]["public_proof"]["source_assets"].values()))
        asset["note_assets"]["note_md_bytes"] = "dGFtcGVy"
    elif mutation == "source_input":
        next(iter(changed["batch"]["source_inputs"].values()))["role"] = "conflict"
    else:
        with shared_memory._events_lock():
            rows = transaction._rows()
            next(row for row in rows if row["id"].startswith("topic-commit-v3:"))["topic_protocol_version"] = 3.0
            shared_memory._write_jsonl_locked(rows)
    monkeypatch.setattr(journal, "load_source", forbidden)
    with pytest.raises(ValueError, match="publication_receipt_mismatch|topic_commit_witness_mismatch"):
        effects._binding(changed, journal)
    assert journal.load_topic_operation_v3(batch["operation_id"])["stage"] == "committed"


@pytest.mark.parametrize("version", [None, 0, 4, 3.0, True])
def test_unknown_binding_version_rejected_before_dependencies(version):
    with pytest.raises(ValueError, match="publication_protocol_invalid"):
        effects._binding({"schema_version": version}, None)


def test_legacy_success_is_not_upgraded_when_generation_policy_changes(committed, monkeypatch):
    journal, batch, root = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    legacy = recover(journal, batch)
    assert legacy["effects"]["artifacts"] == "done"
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    assert recover(journal, batch)["effects"] == legacy["effects"]
    assert not (root / "current.json").exists()


def rehashed_asset(asset, mutate):
    """Independently forge a consistent identity, not just a bad checksum."""
    asset = deepcopy(asset)
    note = json.loads(base64.b64decode(asset["note_assets"]["note_json_bytes"]))
    manifest = json.loads(base64.b64decode(asset["note_assets"]["manifest_bytes"]))
    mutate(note, manifest)
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    note_bytes = encode(note)
    md = ((f"# {note['title']}\n\n" if note["title"] else "") + str(note["body"]) +
          "\n\n## Retained Source Knowledge\n\n```json\n" + note_bytes.decode() + "\n```\n").encode()
    manifest["note_json_sha256"] = hashlib.sha256(note_bytes).hexdigest()
    manifest["note_md_sha256"] = hashlib.sha256(md).hexdigest()
    identity = {key: value for key, value in manifest.items() if key not in {"schema_version", "note_id"}}
    note_id = hashlib.sha256(encode(identity)).hexdigest()
    manifest["note_id"] = note_id
    manifest_bytes = encode(manifest)
    asset["note_id"] = note_id
    asset["note_assets"] = {key: base64.b64encode(value).decode() for key, value in (
        ("note_json_bytes", note_bytes), ("note_md_bytes", md), ("manifest_bytes", manifest_bytes))}
    asset["note_hashes"].update(note_json_sha256=manifest["note_json_sha256"],
        note_md_sha256=manifest["note_md_sha256"], manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    return {note_id: asset}


@pytest.mark.parametrize("damage", ["extra", "body", "claims", "claim_extra", "anchor_bool",
    "anchor_quote", "terms", "claim_state", "protocol", "projection"])
def test_self_consistent_assets_still_require_exact_public_note_schema(committed, damage):
    _, batch, _ = committed
    def mutate(note, manifest):
        if damage == "extra": note["private_path"] = "/private/operator/secret"
        if damage == "body": note["body"] = {"private": "content"}
        if damage == "claims": note["claims"] = "not claims"
        if damage == "claim_extra": note["claims"][0]["private"] = True
        if damage == "anchor_bool": note["claims"][0]["anchor"]["start"] = False
        if damage == "anchor_quote": note["claims"][0]["anchor"]["quote"] = ["C"]
        if damage == "terms": note["terms"] = [{"path": "/private"}]
        if damage == "claim_state": note["claims"][0]["knowledge_state"] = "verified"
        if damage == "protocol": note["source_protocol_version"] = manifest["source_protocol_version"] = 99
        if damage == "projection": note["projection_version"] = manifest["projection_version"] = 99
    forged = rehashed_asset(next(iter(assets(batch).values())), mutate)
    with pytest.raises(ValueError, match="source_note_asset_invalid"):
        publication.capture_snapshot(source_note_assets=forged)


def test_legacy_fallback_freezes_one_intent_across_effects_and_retries(committed, monkeypatch):
    journal, batch, _ = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    captures = []
    capture = publication.capture_snapshot
    def once(**kwargs):
        snapshot = capture(**kwargs)
        captures.append(deepcopy(snapshot))
        return snapshot
    monkeypatch.setattr(publication, "capture_snapshot", once)
    sync = wiki.qmd_search.sync_pages
    def search_failure(*args, **kwargs):
        raise OSError("/private/operator/secret source body")
    monkeypatch.setattr(wiki.qmd_search, "sync_pages", search_failure)
    first = recover(journal, batch)
    assert first["effects"] == {"artifacts": "done", "search": "failed", "log": "done"}
    assert first["reason"] == "search_refresh_failed"
    assert len(captures) == 1
    intent_path = journal._path("publication-effects-v3-legacy", batch["operation_id"])
    intent_bytes = intent_path.read_bytes()
    assert json.loads(intent_bytes)["snapshot"] == captures[0]
    with shared_memory._events_lock():
        rows = transaction._rows()
        rows.append(shared_memory.normalize_record({"id": "later-page", "title": "Later display page",
            "body": "Not in the frozen intent", "tags": ["wiki", "surface:market", "status:draft"]}))
        shared_memory._write_jsonl_locked(rows)
    monkeypatch.setattr(publication, "capture_snapshot", forbidden)
    monkeypatch.setattr(journal, "load_source", forbidden)
    monkeypatch.setattr(wiki.qmd_search, "sync_pages", sync)
    assert recover(journal, batch)["effects"]["search"] == "done"
    assert intent_path.read_bytes() == intent_bytes
    assert not (wiki.qmd_search.wiki_docs_dir() / "later-page.md").exists()


def test_old_legacy_intent_cannot_replace_newer_generation(committed, monkeypatch):
    journal, batch, root = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    write = effects.safe_io.atomic_write_text
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk")))
    first = recover(journal, batch)
    assert first["effects"]["artifacts"] == "failed"
    newer = publication.capture_snapshot(source_note_assets=assets(batch))
    publication.publish_snapshot(newer, root=root, operation_id="newer")
    pointer = (root / "current.json").read_bytes()
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", write)
    monkeypatch.setattr(publication, "capture_snapshot", forbidden)
    retry = recover(journal, batch)
    assert retry["stage"] == "committed" and retry["effects"]["artifacts"] == "failed"
    assert retry["reason"] == "publication_predecessor_changed"
    assert (root / "current.json").read_bytes() == pointer


def test_error_results_never_echo_private_exception_content(committed, monkeypatch):
    journal, batch, _ = committed
    monkeypatch.setattr(effects, "repair_publication", lambda *a, **kw: (_ for _ in ()).throw(
        OSError("/private/operator/raw-content secret")))
    result = recover(journal, batch)
    assert result["reason"] == "publication_effects_unavailable"
    assert "private" not in json.dumps(journal.load_topic_operation_v3(batch["operation_id"]))


@pytest.mark.parametrize("field", ["note_assets", "note_hashes"])
def test_malformed_asset_containers_fail_closed_without_live_reads(committed, monkeypatch, field):
    _, batch, _ = committed
    supplied = assets(batch)
    asset = next(iter(supplied.values()))
    asset[field] = list(asset[field])
    monkeypatch.setattr(shared_memory, "_read_jsonl_checked", forbidden)
    with pytest.raises(ValueError, match="source_note_asset_invalid"):
        publication._source_note_files(supplied)


def test_legacy_intent_remains_legacy_across_policy_flip(committed, monkeypatch):
    journal, batch, root = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    write = effects.safe_io.atomic_write_text
    sync = wiki.qmd_search.sync_pages
    log = wiki._append_wiki_log
    def crash(*args, **kwargs):
        raise OSError("interrupted")
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", crash)
    monkeypatch.setattr(wiki.qmd_search, "sync_pages", crash)
    monkeypatch.setattr(wiki, "_append_wiki_log", crash)
    assert set(recover(journal, batch)["effects"].values()) == {"failed"}
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", write)
    monkeypatch.setattr(wiki.qmd_search, "sync_pages", sync)
    monkeypatch.setattr(wiki, "_append_wiki_log", log)
    monkeypatch.setattr(publication, "capture_snapshot", forbidden)
    monkeypatch.setattr(publication, "publish_snapshot", forbidden)
    assert set(recover(journal, batch)["effects"].values()) == {"done"}
    assert not root.exists()


def test_newer_flat_export_blocks_old_legacy_artifact_retry(committed, monkeypatch, tmp_path):
    journal, batch, _ = committed
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")
    write = effects.safe_io.atomic_write_text
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk")))
    assert recover(journal, batch)["effects"]["artifacts"] == "failed"
    target = tmp_path / "wiki" / "index.md"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"A newer flat publication")
    monkeypatch.setattr(effects.safe_io, "atomic_write_text", write)
    monkeypatch.setattr(publication, "capture_snapshot", forbidden)
    retry = recover(journal, batch)
    assert retry["effects"]["artifacts"] == "failed"
    assert retry["reason"] == "publication_predecessor_changed"
    assert target.read_bytes() == b"A newer flat publication"


def test_witnessed_batch_without_frozen_evidence_reports_unavailable(committed, monkeypatch):
    journal, original, _ = committed
    batch = deepcopy(original)
    proposal = {"protocol_version": 3, "revision_id": original["proposal"]["revision_id"], "role": "support",
        "observed_evidence_ids": [], "context": {"notes": [], "dependencies": [], "omitted": []},
        "dependencies": [], "targets": [], "refusal": "insufficient", "source_map": {}}
    batch.update(operation_id=hashlib.sha256(publication._json_bytes(proposal)).hexdigest(), proposal=proposal,
        revision_ids=[], source_map={}, dependencies=[], source_inputs={}, original_claims={}, target_ids=[],
        expected_before={}, expected_after={}, upserts=[], snapshots=[],
        public_proof={"export_metadata": {"protocol_version": 3, "source_note_ids": [], "target_ids": []},
                      "source_assets": {}})
    journal.prepare_topic_v3(batch)
    assert transaction._apply_with_witness_v3(batch, transaction.topic_commit_record_v3(batch))["status"] == "applied"
    journal.transition_topic_v3(batch["operation_id"], stage="committed")
    monkeypatch.setattr(journal, "load_source", forbidden)
    result = recover(journal, batch)
    assert result["stage"] == "committed" and result["reason"] == "publication_frozen_proof_unavailable"
    assert set(result["effects"].values()) == {"failed"}
