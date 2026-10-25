"""Synthetic end-to-end acceptance for generation publication contracts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest

from agent_console import shared_memory, wiki, wiki_publication, qmd_generation
from reports import wiki_publication_effects, wiki_public_budget
from reports.wiki_topic_transaction import prepare_topic_batch, recover_topic_operation
from test_wiki_topic_transaction import transaction as transaction_fixture, proposal, validate, NOW
from test_wiki_topic_integration import evidence as evidence_fixture


@pytest.fixture
def transaction(evidence_fixture, tmp_path, monkeypatch):
    return transaction_fixture.__wrapped__(evidence_fixture, tmp_path, monkeypatch)


class SyntheticQmdRunner:
    """Injected lexical index; it never starts a process or touches a real index."""

    def __init__(self):
        self.docs = {}

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace
        import yaml

        env = kwargs["env"]
        assert all(key in env for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"))
        assert kwargs["timeout"] <= 30
        if "--help" in argv:
            db = __import__("pathlib").Path(env["XDG_CACHE_HOME"]) / "qmd" / (argv[2] + ".sqlite")
            return SimpleNamespace(returncode=0, stdout=f"Index: {db}\n", stderr="")
        if "add" in argv:
            directory = __import__("pathlib").Path(argv[5])
            self.docs = {str(path): path.read_text(encoding="utf-8")
                         for path in directory.rglob("*.md")}
            db = __import__("pathlib").Path(env["XDG_CACHE_HOME"]) / "qmd" / (argv[2] + ".sqlite")
            db.parent.mkdir(parents=True, exist_ok=True)
            import sqlite3
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE synthetic_documents(path TEXT, body TEXT)")
                connection.executemany("INSERT INTO synthetic_documents VALUES (?, ?)",
                                       list(self.docs.items()))
            config = __import__("pathlib").Path(env["XDG_CONFIG_HOME"]) / "qmd/wiki_generation.yml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(yaml.safe_dump({"collections": {"wiki-generation": {
                "path": str(directory), "pattern": "**/*.md"}}}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        query = argv[4]
        hits = [{"file": path, "content": body, "score": 1}
                for path, body in self.docs.items()
                if query.casefold() in (body + " " + __import__("pathlib").Path(path).stem).casefold()]
        return SimpleNamespace(returncode=0, stdout=json.dumps(hits), stderr="")


def test_synthetic_topic_publication_pinning_search_rollback_and_recovery(
        transaction, tmp_path, monkeypatch):
    data, journal, rows = transaction
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()

    payload = proposal(data)
    payload["targets"][0]["additions"][0]["body"] = (
        "Attributed synthetic account; limitations remain explicit.\n\n" * 450
        + "TAIL_ACCEPTANCE_UNCERTAINTY [S1]")
    checked = validate(data, payload)
    batch = prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    # The transaction fixture's canonical rows are the committed source/topic base.
    committed = recover_topic_operation(batch["operation_id"], journal=journal, now=NOW)
    assert committed["stage"] == "committed"

    rows = shared_memory.inspect_records()
    topic_id = batch["target_ids"][0]
    semantic = wiki.semantic_snapshot(topic_id, records=rows)
    assert len({claim["source_revision_id"] for claim in semantic["claims"]}) >= 2
    assert semantic["source_knowledge"]["limitations"]
    assert all(claim.get("text") and claim.get("scope") for claim in semantic["claims"])
    assert semantic["children"]
    assert "TAIL_ACCEPTANCE_UNCERTAINTY" in semantic["body"]
    assert len(wiki._context_body_excerpt(semantic["page"])) <= 1600
    source_paths = set()
    for receipt_path in (journal.root / "sources").glob("*.json"):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        source = receipt.get("source", {})
        source_paths.update(source.get(key) for key in ("raw_ref", "text_ref", "manifest_ref")
                            if source.get(key))
    assert source_paths
    assert all(Path(path).is_file() for path in source_paths)
    source_hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                     for path in source_paths}
    budget_path = journal.root / "automatic-budget.json"
    wiki_public_budget.reserve(budget_path, next(iter(semantic["claims"]))["source_revision_id"], now=NOW)
    budget_before = budget_path.read_bytes()
    assert budget_before
    journal_files = {path: path.read_bytes() for path in journal.root.rglob("*")
                     if path.is_file() and not path.name.endswith(".lock")}

    # Rendering owns its detached snapshot even when canonical state changes mid-render.
    snapshot = wiki_publication.capture_snapshot()
    candidate_files = wiki_publication.render_candidate(snapshot)
    validation = wiki_publication.validate_candidate(candidate_files)
    assert validation["ok"], validation["errors"]
    snapshot_topic = next(row for row in snapshot["records"] if row["id"] == topic_id)
    captured_body = snapshot_topic["body"]
    real_render = wiki_publication.render_candidate
    mutated = False

    def render_then_mutate(value):
        nonlocal mutated
        result = real_render(value)
        if not mutated:
            mutated = True
            wiki.upsert_page({"id": "acceptance-concurrent", "title": "Concurrent",
                              "body": "writer during render", "kind": "note", "surface": "market"})
        return result

    monkeypatch.setattr(wiki_publication, "render_candidate", render_then_mutate)
    root = tmp_path / "publication"
    first = wiki_publication.publish_snapshot(snapshot, root=root, operation_id="acceptance-first")
    pinned = wiki_publication.pin_generation(root=root)
    topic_stem = next(stem for stem, identity in wiki_publication._identities(snapshot["records"]).items()
                      if identity == topic_id)
    assert captured_body.encode() in pinned.read(f"notes/{topic_stem}.md")
    assert json.loads(pinned.read("snapshot.json"))["snapshot_sha256"] == first["snapshot_sha256"]
    rendered = pinned.read(f"notes/{topic_stem}.md").decode("utf-8")
    for claim in semantic["claims"]:
        assert claim["claim_id"] in rendered and claim["source_revision_id"] in rendered
    for limitation in semantic["source_knowledge"]["limitations"]:
        assert limitation in rendered
    assert all(ref in rendered for ref in semantic["source_refs"] if str(ref).startswith("https://"))

    # Publish a second complete version; the first reader retains its own bytes.
    monkeypatch.setattr(wiki_publication, "render_candidate", real_render)
    newer = copy.deepcopy(snapshot)
    newer["records"][0]["body"] += "\nNew synthetic revision."
    newer["captured_at"] = "2026-10-16T12:01:00+00:00"
    newer["snapshot_sha256"] = wiki_publication._snapshot_hash(newer)
    second = wiki_publication.publish_snapshot(newer, root=root, operation_id="acceptance-second")
    assert second["generation_id"] != first["generation_id"]
    assert pinned.generation_id == first["generation_id"]
    assert pinned.read("index.md")

    # The synthetic runner never starts a process; production still pins a real
    # executable path/hash first, so bind it to this interpreter (no installed qmd).
    monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", sys.executable)
    runner = SyntheticQmdRunner()
    capability = {"supported": True, "recipe": {
        "build_argv": ["{executable}", "--index", "{index_name}", "collection", "add",
                       "{collection_path}", "--name", "{collection_name}"],
        "query_argv": ["{executable}", "--index", "{index_name}", "search", "{query}",
                       "--format", "json", "--full-path", "--full", "-n", "10", "-c", "{collection_name}"],
        "environment": {key: "{probe_root}/" + value for key, value in {
            "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
            "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp"}.items()}}}
    indexed = qmd_generation.build_index(second, root=root, capability=capability, runner=runner)
    assert indexed["status"] == "ready", indexed
    matches = qmd_generation.search_generation("TAIL_ACCEPTANCE_UNCERTAINTY",
        descriptor=indexed["descriptor"], limit=20, runner=runner)
    child_ids = {child["id"] for child in semantic["children"]}
    assert any(hit["page_id"] in child_ids for hit in matches)
    assert wiki.semantic_snapshot(topic_id)["revision_hash"] == semantic["revision_hash"]

    wiki_publication.activate_generation(first["generation_id"], root=root,
                                          operation_id="acceptance-rollback")
    assert wiki_publication.pin_generation(root=root).generation_id == first["generation_id"]
    assert (root / "generations" / second["generation_id"]).is_dir()

    # Recovery is exercised through the real Task 5 sidecar entrypoint, but the
    # fixture's canonical topic commit is not claimed to own our isolated root.
    # It must be inert for a legacy receipt already marked done.
    monkeypatch.setattr(wiki_publication_effects, "publication_root", lambda: root)
    before_records = copy.deepcopy(shared_memory.inspect_records())
    recovered = wiki_publication_effects.repair_publication(batch["operation_id"], journal=journal)
    # This topic receipt already says legacy effects are done; Task 5 must not
    # fabricate generation proof or reinterpret it during migration.
    assert recovered["generation_id"] is None
    assert recovered["artifacts"] == recovered["search"] == recovered["log"] == "failed"
    assert shared_memory.inspect_records() == before_records
    assert {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in source_paths} == source_hashes
    assert budget_path.read_bytes() == budget_before
    assert {path: path.read_bytes() for path in journal.root.rglob("*")
            if path.is_file() and not path.name.endswith(".lock")} == journal_files


def test_task5_crash_repair_reuses_generation_with_isolated_search(
        transaction, tmp_path, monkeypatch):
    from pathlib import Path
    from reports import wiki_source_compiler
    from test_qmd_generation_search import Runner, capability

    data, journal, rows = transaction
    payload = proposal(data)
    checked = validate(data, payload)
    batch = prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    root = tmp_path / "recovery-publication"
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setattr(wiki_publication_effects, "publication_root", lambda: root)
    runner = Runner()
    monkeypatch.setattr(wiki.qmd_search, "qmd_bin", lambda: "/bin/true")
    monkeypatch.setattr(wiki_publication_effects, "search_dependencies",
                        lambda: (capability(), runner))

    raw_paths = set()
    for receipt_path in (journal.root / "sources").glob("*.json"):
        source = json.loads(receipt_path.read_text(encoding="utf-8")).get("source", {})
        raw_paths.update(source.get(key) for key in ("raw_ref", "text_ref", "manifest_ref")
                         if source.get(key))
    assert raw_paths and all(Path(path).is_file() for path in raw_paths)
    raw_hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in raw_paths}

    budget_path = journal.root / "automatic-budget.json"
    wiki_public_budget.reserve(budget_path, batch["revision_ids"][0], now=NOW)
    budget_before = budget_path.read_bytes()
    events_path = shared_memory._paths()["events"]

    activate = wiki_publication._activate_locked
    crashed = False

    def crash_after_activation(generation_id, activation_root, operation_id):
        nonlocal crashed
        result = activate(generation_id, activation_root, operation_id)
        if Path(activation_root) == root and not crashed:
            crashed = True
            raise OSError("injected crash after pointer activation")
        return result

    monkeypatch.setattr(wiki_publication, "_activate_locked", crash_after_activation)
    initial = recover_topic_operation(batch["operation_id"], journal=journal, now=NOW)
    assert initial["stage"] == "committed"
    assert initial["effects"]["artifacts"] == "failed"
    sidecar_path = journal._path("publication-effects", batch["operation_id"])
    assert sidecar_path.is_file()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    generation_id = sidecar["publication"]["generation_id"]
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert pointer["generation_id"] == generation_id
    canonical_before_repair = events_path.read_bytes()

    monkeypatch.setattr(wiki_source_compiler, "compile_source",
                        lambda *a, **k: pytest.fail("repair must not invoke compilation/model path"))
    monkeypatch.setattr(wiki_publication, "_activate_locked", activate)
    repaired = wiki_publication_effects.repair_publication(batch["operation_id"], journal=journal)
    assert repaired == {"generation_id": generation_id, "artifacts": "done",
                        "search": "done", "log": "done"}
    assert wiki_publication.pin_generation(root=root).generation_id == generation_id
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["activated"] is True
    assert sidecar["descriptor"]["generation_id"] == generation_id
    assert sidecar["descriptor_sha256"] == wiki_publication_effects._digest(sidecar["descriptor"])
    assert qmd_generation.generation_health(root=root)["indexed"]["generation_id"] == generation_id
    assert events_path.read_bytes() == canonical_before_repair
    assert budget_path.read_bytes() == budget_before and budget_before
    assert {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in raw_paths} == raw_hashes
