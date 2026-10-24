"""Immutable derivatives must never borrow authority from mutable wiki pages."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from reports import raw_archive
from reports.wiki_source_chunks import _aggregate, source_chunks
from reports.wiki_source_compiler import validate_compilation
from reports.wiki_source_journal import Journal
from reports.wiki_source_revision import build_revision


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def api():
    class API:
        def __getattr__(self, name):
            assert importlib.util.find_spec("reports.wiki_source_notes") is not None, "Task 1 APIs missing"
            return getattr(importlib.import_module("reports.wiki_source_notes"), name)
    if importlib.util.find_spec("reports.wiki_source_notes") is not None:
        return importlib.import_module("reports.wiki_source_notes")
    return API()


def compilation(source, *, count=2):
    ids = [digest(f"{source['revision_id']}:{i}".encode()) for i in range(count)]
    return validate_compilation({
        "action": "create", "title": "Attributed capacity and demand",
        "body": "The author reports capacity, not realized demand [S1].",
        "claims": [{"text": f"The author reports claim {i}", "as_of": "unknown",
                    "scope": "Reported outlook, not realized demand",
                    "anchor": {"start": 0, "end": 1, "quote": source["body"][0]},
                    "competing_claim_ids": [ids[1 - i]] if count == 2 else []}
                   for i in range(count)],
        "terms": ["capacity", "demand"], "limitations": ["Forecast uncertainty"],
        "skip_reason": None}, source)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))

    def make(*, body="Capacity rose; demand is uncertain.\n", chunked=False,
             provider="telegram:public_channel", archive_provider="telegram",
             url="https://t.me/public_channel/42", scope="public_message",
             kind="community_signal", public_proof=None):
        record = raw_archive.save_raw_artifact(archive_provider, "text", NOW, "Original title",
            url, body, ".txt")
        record.update(body_coverage="full", text_complete=True, extraction_scope=scope)
        raw_archive.save_extracted_text(record, body)
        if public_proof is not None:
            manifest_path = Path(record["manifest_path"])
            manifest = json.loads(manifest_path.read_bytes())
            manifest.update(public_proof)
            manifest_path.write_bytes(encoded(manifest))
        source = build_revision({"source": provider,
            "url": url, "content_kind": kind,
            "collected_at": NOW.isoformat(), "published_at": "2026-10-19",
            "extraction_scope": scope,
            **{key: record[key] for key in ("raw_path", "text_path", "manifest_path")}},
            now=NOW, allow_chunking=True)
        journal = Journal(tmp_path / "journal")
        if chunked:
            parts = source_chunks(source)
            outputs = []
            child = Journal(journal.root / "chunks" / source["revision_id"])
            for part in parts:
                output = compilation(part, count=81)
                child.save_compilation(part, output)
                outputs.append(output)
            compiled = _aggregate(source, parts, outputs)
        else:
            compiled = compilation(source)
        compiled["verified_aliases"] = ["capacity outlook"]
        journal.save_compilation(source, compiled)
        for directory in ("editorial", "operations", "topic-operations"):
            path = tmp_path / directory / "untouched.json"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'{"synthetic":"mutable editorial history"}')
        return journal, source, compiled

    return make


def materialize(api, data, *, apply=False, now=NOW):
    journal, source, _ = data
    return api.materialize_source_note(source["revision_id"], journal=journal, now=now, apply=apply)


def rewrite_receipt(data, mutate):
    journal, source, _ = data
    path = journal._path("sources", source["revision_id"])
    receipt = json.loads(path.read_bytes())
    mutate(receipt)
    path.write_bytes(encoded(receipt))


def rewrite_compilation(data, mutate):
    journal, source, compiled = data
    value = deepcopy(compiled)
    mutate(value)
    blob = encoded(value)
    path = journal._path("compiled", digest(blob))
    path.write_bytes(blob)
    rewrite_receipt(data, lambda receipt: receipt.update(compile_ref=str(path), compile_sha256=digest(blob)))


def inventory(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_identity_is_id_free_and_deterministic(api, store, tmp_path):
    data = store()
    before = inventory(tmp_path)
    preview = materialize(api, data)
    assert inventory(tmp_path) == before
    assert materialize(api, data, now=NOW + timedelta(minutes=1)) == preview
    result = materialize(api, data, apply=True)
    journal, source, compiled = data
    pin = api.pin_source_note(result["note_id"], journal=journal, now=NOW)
    identity = {"identity_version": 1, "source_protocol_version": 1,
        "revision_id": source["revision_id"], "projection_version": 1,
        "canonical_url": source["canonical_url"], "raw_sha256": source["raw_sha256"],
        "text_sha256": source["text_sha256"], "compile_sha256": journal.load_source(source["revision_id"])["compile_sha256"],
        "note_json_sha256": digest(pin["note_json_bytes"]), "note_md_sha256": digest(pin["note_md_bytes"])}
    assert result["note_id"] == digest(encoded(identity))
    assert pin["note_json_bytes"] == encoded(pin["note"])
    assert pin["note"]["title"] == compiled["title"]
    assert pin["note"]["claims"] == compiled["claims"]
    assert pin["note"]["limitations"] == compiled["limitations"]
    assert pin["note"]["verified_aliases"] == ["capacity outlook"]
    assert pin["note"]["published_at"] == "2026-10-19"
    assert pin["note"]["collected_at"] == NOW.isoformat()
    assert pin["note"]["provider"] == "telegram:public_channel"
    assert "page_id" not in pin["note"]
    for blob in (pin["note_json_bytes"], pin["note_md_bytes"]):
        assert result["note_id"].encode() not in blob
        assert str(tmp_path).encode() not in blob
        assert pin["manifest_sha256"].encode() not in blob
    root = journal.root / "source-notes" / result["note_id"]
    assert set(os.listdir(root)) == {"note.json", "note.md", "manifest.json"}
    assert digest((root / "manifest.json").read_bytes()) == pin["manifest_sha256"]
    assert all((tmp_path / path).read_bytes() == blob for path, blob in before.items())
    assert materialize(api, data, apply=True) == result
    api.revalidate_source_note(pin, journal=journal, now=NOW)


@pytest.mark.parametrize("change", ["title", "body", "alias", "version"])
def test_output_and_compiler_changes_have_new_identity(api, store, monkeypatch, change):
    data = store()
    first = materialize(api, data)["note_id"]
    if change == "version":
        monkeypatch.setattr(api, "PROJECTION_VERSION", 2)
    else:
        rewrite_compilation(data, lambda value: value.update(**{
            "title": {"title": "Another compiler title"},
            "body": {"body": "Another attributed synthesis [S1](https://t.me/public_channel/42)."},
            "alias": {"verified_aliases": ["demand outlook"]}}[change]))
    assert materialize(api, data)["note_id"] != first


def test_missing_compiler_title_and_dates_are_not_invented(api, store):
    data = store()
    rewrite_compilation(data, lambda value: value.pop("title"))
    rewrite_receipt(data, lambda value: [value["source"].pop(key) for key in ("published_at", "collected_at")])
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    assert pin["note"]["title"] == ""
    assert pin["note"]["published_at"] == pin["note"]["collected_at"] == ""
    assert b"Original title" not in pin["note_md_bytes"]
    assert b"Source synthesis" not in pin["note_md_bytes"]


@pytest.mark.parametrize("damage", ["private", "unknown", "revision", "protocol", "raw", "text",
    "expired", "expiry_missing", "login", "incomplete", "manifest_url", "compile_hash", "pending"])
def test_invalid_sources_fail_closed(api, store, damage):
    data = store()
    journal, source, _ = data
    if damage in {"private", "unknown", "revision", "protocol", "pending", "compile_hash"}:
        changes = {"private": {"scope": "personal_conversation"}, "unknown": {"provider": "unknown"},
                   "revision": {"revision_id": "0" * 64}, "protocol": {"protocol_version": 99}}
        rewrite_receipt(data, lambda receipt: receipt.update(
            **({"compile_state": "pending"} if damage == "pending" else
               {"compile_sha256": "0" * 64} if damage == "compile_hash" else
               {"source": {**receipt["source"], **changes[damage]}})))
    elif damage in {"raw", "text", "login"}:
        blob = b"Sign in to read this article" if damage == "login" else b"corrupt"
        Path(source["text_ref" if damage == "text" else "raw_ref"]).write_bytes(blob)
        if damage == "login":
            path = Path(source["manifest_ref"])
            manifest = json.loads(path.read_bytes())
            manifest["sha256"] = digest(blob)
            path.write_bytes(encoded(manifest))
            rewrite_receipt(data, lambda value: value["source"].update(raw_sha256=digest(blob)))
    else:
        path = Path(source["manifest_ref"])
        manifest = json.loads(path.read_bytes())
        if damage == "expired":
            manifest["expires_at"] = NOW.isoformat()
        elif damage == "expiry_missing":
            manifest.pop("expires_at")
        elif damage == "incomplete":
            manifest["text_complete"] = False
        else:
            manifest["source_url"] = "https://t.me/other_channel/42"
        path.write_bytes(encoded(manifest))
    with pytest.raises(ValueError, match="login_or_challenge" if damage == "login" else None):
        materialize(api, data)


@pytest.mark.parametrize("scope,proof", [
    ("article_fulltext", {}),
    ("rendered_article", {"visibility": "public"}),
    ("article_fulltext", {"visibility": "public", "private": False}),
    ("article_fulltext", {"visibility": "public", "requires_login": False}),
    ("authorized_article", {"visibility": "public", "requires_login": False, "private": False}),
    ("article_fulltext", {"visibility": "public", "requires_login": True, "private": False}),
])
def test_private_opt_in_never_authorizes_unknown_or_login_articles(api, store, monkeypatch, scope, proof):
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setenv("WIKI_SOURCE_ALLOW_PRIVATE", "1")
    data = store(provider="saveticker", archive_provider="saveticker_article",
        url="https://saveticker.com/news/synthetic", scope=scope, kind="article", public_proof=proof)
    with pytest.raises(ValueError): materialize(api, data, apply=True)
    assert not (data[0].root / "source-notes").exists()


def test_nontelegram_requires_retained_public_proof_at_every_new_use(api, store, monkeypatch):
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setenv("WIKI_SOURCE_ALLOW_PRIVATE", "1")
    data = store(provider="saveticker", archive_provider="saveticker_article",
        url="https://saveticker.com/news/synthetic", scope="article_fulltext", kind="article",
        public_proof={"visibility": "public", "requires_login": False, "private": False})
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    path = Path(data[1]["manifest_ref"])
    manifest = json.loads(path.read_bytes()); manifest.pop("requires_login")
    path.write_bytes(encoded(manifest))
    with pytest.raises(ValueError): api.revalidate_source_note(pin, journal=data[0], now=NOW)


@pytest.mark.parametrize("damage", ["claim_id", "source_id", "anchor", "anchor_bool", "anchor_extra",
    "duplicate", "competitor", "self_competitor", "duplicate_competitor", "state", "date", "scope",
    "text", "unknown_field", "title", "terms", "limitations", "aliases", "body", "skip"])
def test_all_stored_compiler_fields_are_validated(api, store, damage):
    data = store()
    def mutate(value):
        first = value["claims"][0]
        if damage == "claim_id": first["claim_id"] = "0" * 64
        elif damage == "source_id": first["source_revision_id"] = "0" * 64
        elif damage == "anchor": first["anchor"]["quote"] = "wrong"
        elif damage == "anchor_bool": first["anchor"]["start"] = False
        elif damage == "anchor_extra": first["anchor"]["path"] = "/private"
        elif damage == "duplicate": value["claims"].append(deepcopy(first))
        elif damage == "competitor": first["competing_claim_ids"] = ["0" * 64]
        elif damage == "self_competitor": first["competing_claim_ids"] = [first["claim_id"]]
        elif damage == "duplicate_competitor": first["competing_claim_ids"] *= 2
        elif damage == "state": first["knowledge_state"] = "verified"
        elif damage in {"date", "scope", "text"}: first[{"date": "as_of"}.get(damage, damage)] = ""
        elif damage == "unknown_field": value["private_path"] = "/private"
        elif damage == "title": value["title"] = ""
        elif damage == "terms": value["terms"] = [1]
        elif damage == "limitations": value["limitations"] = []
        elif damage == "aliases": value["verified_aliases"] = [{"private": "text"}]
        elif damage == "body": value["body"] = "Unattributed [S2]"
        elif damage == "skip": value["skip_reason"] = "Not reusable"
    rewrite_compilation(data, mutate)
    with pytest.raises(ValueError):
        materialize(api, data)


def test_full_resumed_chunk_claims_and_global_tail_anchors_survive(api, store):
    data = store(body="A" * 20000 + "B" * 20000 + "TAIL", chunked=True)
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    assert len(pin["note"]["claims"]) == 243
    assert pin["note"]["claims"] == data[2]["claims"]
    assert pin["note"]["claims"][-1]["anchor"] == {"start": 40000, "end": 40001, "quote": "T"}
    assert "40000:40004" in pin["note"]["body"]
    assert pin["note"]["claims"][-1]["claim_id"] == digest(
        f"{data[1]['revision_id']}:chunk:40000:claim:80".encode())
    rewrite_compilation(data, lambda value: value["claims"][-1].update(text="Forged tail"))
    with pytest.raises(ValueError): materialize(api, data)


def test_long_normal_compilation_retains_tail_without_assuming_chunk_ids(api, store):
    data = store(body="A" * 40000 + "TAIL")
    def tail(value):
        value["claims"][-1]["anchor"] = {"start": 40000, "end": 40004, "quote": "TAIL"}
    rewrite_compilation(data, tail)
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    assert pin["note"]["claims"][-1]["anchor"] == {"start": 40000, "end": 40004, "quote": "TAIL"}


def test_saved_one_span_aggregate_retains_chunk_identity(api, store):
    data = store(body="Single complete span", chunked=True)
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    assert pin["note"]["claims"] == data[2]["claims"]


@pytest.mark.parametrize("mutation", ["bool_identity", "float_projection", "bytearray", "tuple_claims"])
def test_pin_equality_is_type_strict(api, store, mutation):
    data = store()
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    if mutation == "bool_identity": pin["identity_version"] = True
    elif mutation == "float_projection": pin["projection_version"] = 1.0
    elif mutation == "bytearray": pin["note_md_bytes"] = bytearray(pin["note_md_bytes"])
    else: pin["note"]["claims"] = tuple(pin["note"]["claims"])
    with pytest.raises(ValueError): api.revalidate_source_note(pin, journal=data[0], now=NOW)


@pytest.mark.parametrize("artifact", ["note.json", "note.md", "manifest.json", "extra", "missing"])
def test_corrupt_existing_artifacts_are_never_overwritten(api, store, artifact):
    data = store()
    result = materialize(api, data, apply=True)
    root = data[0].root / "source-notes" / result["note_id"]
    if artifact == "missing": (root / "note.md").unlink()
    else: (root / artifact).write_bytes(b"corrupt")
    before = inventory(root)
    with pytest.raises(ValueError): materialize(api, data, apply=True)
    with pytest.raises(ValueError): api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    assert inventory(root) == before


@pytest.mark.parametrize("damage", ["expiry", "policy", "raw", "compile", "manifest", "pin", "bytes"])
def test_pins_recheck_exact_bytes_and_current_original_policy(api, store, damage):
    data = store()
    result = materialize(api, data, apply=True)
    pin = api.pin_source_note(result["note_id"], journal=data[0], now=NOW)
    if damage == "expiry":
        path = Path(data[1]["manifest_ref"])
        value = json.loads(path.read_bytes()); value["expires_at"] = NOW.isoformat()
        path.write_bytes(encoded(value))
    elif damage == "policy": rewrite_receipt(data, lambda value: value["source"].update(scope="private"))
    elif damage == "raw": Path(data[1]["raw_ref"]).unlink()
    elif damage == "compile": rewrite_compilation(data, lambda value: value.update(title="Changed"))
    elif damage == "manifest": pin["manifest_sha256"] = "0" * 64
    elif damage == "pin": pin["note"]["claims"][0]["text"] = "Forged"
    else: pin["note_json_bytes"] += b" "
    with pytest.raises(ValueError): api.revalidate_source_note(pin, journal=data[0], now=NOW)


@pytest.mark.parametrize("target", ["raw", "text", "manifest", "receipt", "compiled", "root", "note", "notes_root", "lock"])
def test_symlinks_are_rejected_even_when_bytes_match(api, store, tmp_path, target):
    data = store()
    result = materialize(api, data, apply=True)
    journal, source, _ = data
    receipt = journal.load_source(source["revision_id"])
    paths = {"raw": Path(source["raw_ref"]), "text": Path(source["text_ref"]),
        "manifest": Path(source["manifest_ref"]), "receipt": journal._path("sources", source["revision_id"]),
        "compiled": Path(receipt["compile_ref"]), "root": journal.root,
        "note": journal.root / "source-notes" / result["note_id"] / "note.json",
        "notes_root": journal.root / "source-notes",
        "lock": journal.root / "source-locks" / (source["revision_id"] + ".json.lock")}
    path = paths[target]
    saved = tmp_path / "substituted"
    path.rename(saved); path.symlink_to(saved, target_is_directory=saved.is_dir())
    with pytest.raises(ValueError): materialize(api, data, apply=True)


@pytest.mark.parametrize("target", ["raw", "compiled", "note", "ancestor"])
def test_open_time_symlink_substitution_cannot_read_outside_bytes(api, store, tmp_path, monkeypatch, target):
    data = store()
    result = materialize(api, data, apply=True)
    journal, source, _ = data
    receipt = journal.load_source(source["revision_id"])
    path = {"raw": Path(source["raw_ref"]), "compiled": Path(receipt["compile_ref"]),
        "note": journal.root / "source-notes" / result["note_id"] / "note.json",
        "ancestor": Path(source["raw_ref"]).parent}[target]
    outside = tmp_path / "outside"
    if path.is_dir(): outside.mkdir()
    else: outside.write_bytes(b"PRIVATE MUST NEVER BE READ")
    original = os.open
    fired = False
    def swap(name, flags, *args, **kwargs):
        nonlocal fired
        if str(name) == path.name and not fired:
            fired = True
            path.rename(path.with_name(path.name + ".old"))
            path.symlink_to(outside, target_is_directory=outside.is_dir())
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(api.os, "open", swap)
    with pytest.raises(ValueError): api.pin_source_note(result["note_id"], journal=journal, now=NOW)
    assert fired


@pytest.mark.parametrize("bad", ["../escape", "/tmp/escape", "A" * 64, "0" * 63])
def test_untrusted_identifiers_do_not_select_paths(api, store, bad):
    data = store()
    with pytest.raises(ValueError): api.materialize_source_note(bad, journal=data[0], now=NOW)
    with pytest.raises(ValueError): api.pin_source_note(bad, journal=data[0], now=NOW)


def test_archive_and_compilation_escape_paths_fail(api, store, tmp_path):
    data = store()
    external = tmp_path / "outside.json"
    external.write_bytes(encoded(data[2]))
    rewrite_receipt(data, lambda value: value.update(compile_ref=str(external)))
    with pytest.raises(ValueError): materialize(api, data)
    rewrite_receipt(data, lambda value: value["source"].update(raw_ref=str(external)))
    with pytest.raises(ValueError): materialize(api, data)


def test_no_compiler_provider_reservation_or_editorial_authority(api, store, monkeypatch):
    data = store()
    from reports import wiki_source_compiler, wiki_source_chunks, wiki_topic_context, wiki_public_budget
    def forbidden(*args, **kwargs): pytest.fail("artifact creation invoked operational work")
    monkeypatch.setattr(wiki_source_compiler, "compile_source", forbidden)
    monkeypatch.setattr(wiki_source_compiler, "structured_llm_call", forbidden)
    monkeypatch.setattr(wiki_source_chunks, "compile_chunks", forbidden)
    monkeypatch.setattr(wiki_topic_context, "_note", forbidden)
    for name in dir(wiki_public_budget):
        if "reserve" in name and callable(getattr(wiki_public_budget, name)):
            monkeypatch.setattr(wiki_public_budget, name, forbidden)
    materialize(api, data, apply=True)


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("step", range(1, 8))
def test_crash_at_each_file_directory_fsync_and_rename_is_retryable(api, store, monkeypatch, phase, step):
    data = store()
    preview = materialize(api, data)
    journal = data[0]
    fsync = api.os.fsync
    rename = api._rename_noreplace
    calls = 0
    def interrupt(operation, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == step and phase == "before": raise OSError("synthetic crash")
        result = operation(*args, **kwargs)
        if calls == step and phase == "after": raise OSError("synthetic crash")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(api.os, "fsync", lambda *args: interrupt(fsync, *args))
        patch.setattr(api, "_rename_noreplace", lambda *args: interrupt(rename, *args))
        with pytest.raises((OSError, ValueError)): materialize(api, data, apply=True)
    final = journal.root / "source-notes" / preview["note_id"]
    if final.exists():
        api.pin_source_note(preview["note_id"], journal=journal, now=NOW)
    else:
        with pytest.raises(ValueError): api.pin_source_note(preview["note_id"], journal=journal, now=NOW)
    assert materialize(api, data, apply=True)["note_id"] == preview["note_id"]


def test_compacted_archive_members_are_verified_without_extraction(api, store):
    data = store()
    expected = materialize(api, data)
    source = data[1]
    paths = [(Path(source[key]), category) for key, category in
             (("raw_ref", "raw"), ("text_ref", "text"), ("manifest_ref", "manifest"))]
    bundle = raw_archive.reports_root() / "raw" / raw_archive.BUNDLE_DIRNAME / "telegram" / "2026" / "10" / "20-ttl30.tar.gz"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle, "w:gz") as archive:
        for path, category in paths:
            blob = path.read_bytes()
            info = tarfile.TarInfo(f"{category}/{path.name}"); info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
    for path, _ in paths: path.unlink()
    assert materialize(api, data) == expected
    assert all(not path.exists() for path, _ in paths)
