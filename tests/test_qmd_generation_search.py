import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import stat
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from agent_console import qmd_generation as qg, wiki, wiki_publication


@pytest.fixture(autouse=True)
def trusted_synthetic_executable(monkeypatch):
    """Synthetic Runners never start a process, but production still verifies a real
    executable path and byte hash first. Bind that check to this interpreter so the
    tests do not depend on an installed qmd; the opt-in real-QMD test keeps its binary."""
    if os.getenv("AGENT_CONSOLE_TEST_REAL_QMD") != "1":
        monkeypatch.setenv("AGENT_CONSOLE_QMD_BIN", sys.executable)



@pytest.fixture
def candidate(tmp_path, monkeypatch):
    records = [{"id": "page-a", "title": "Synthetic", "body": "synthetic knowledge",
                "tags": ["wiki", "status:stable"]}]
    snapshot = {"records": records, "captured_at": "2026-10-16T00:00:00+00:00", "schema_text": "",
                "schema_sha256": hashlib.sha256(b"").hexdigest()}
    snapshot["snapshot_sha256"] = wiki_publication._snapshot_hash(snapshot)
    publication = dict(generation_id="a" * 64, snapshot_sha256=snapshot["snapshot_sha256"],
                       manifest_sha256="b" * 64)
    directory = tmp_path / "generations" / publication["generation_id"]
    directory.mkdir(parents=True)
    manifest = b'{"synthetic":true}'
    (directory / "manifest.json").write_bytes(manifest)
    publication["manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    for name, data in wiki_publication.render_candidate(snapshot).items():
        path = directory / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    def pin(*, root, generation_id=None):
        assert Path(root) == tmp_path
        assert generation_id in (None, publication["generation_id"])
        return SimpleNamespace(**publication, read=lambda name: (directory / name).read_bytes())
    monkeypatch.setattr(wiki_publication, "pin_generation", pin, raising=False)
    (tmp_path / "current.json").write_text(json.dumps(publication))
    return tmp_path, publication, records


def capability():
    return {"supported": True, "recipe": {
        "build_argv": ["{executable}", "--index", "{index_name}", "collection", "add",
                       "{collection_path}", "--name", "{collection_name}"],
        "query_argv": ["{executable}", "--index", "{index_name}", "search", "{query}",
                       "--format", "json", "--full-path", "--full", "-n", "10", "-c", "{collection_name}"],
        "environment": {key: "{probe_root}/" + value for key, value in
                        {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                         "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp"}.items()}}}


class Runner:
    failure = None

    def __call__(self, argv, **kwargs):
        env = kwargs["env"]
        assert set(env) == {"PATH", "LANG", "NO_COLOR", "HOME", "XDG_CONFIG_HOME",
                            "XDG_CACHE_HOME", "TMPDIR", "TMP", "TEMP"}
        assert argv[1] == "--index"
        assert not {"embed", "query", "vsearch", "update"}.intersection(argv)
        base = Path(env["HOME"]).parent
        db = Path(env["XDG_CACHE_HOME"]) / "qmd" / (argv[2] + ".sqlite")
        if "--help" in argv:
            return SimpleNamespace(returncode=0, stdout=f"Index: {db}\n", stderr="")
        if "add" in argv:
            self.collection = Path(argv[5])
            db.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("CREATE TABLE indexed_notes(path TEXT PRIMARY KEY, body TEXT)")
                connection.executemany("INSERT INTO indexed_notes VALUES (?, ?)",
                                       [(str(path), path.read_text()) for path in self.collection.glob("*.md")])
                connection.commit()
            config = Path(env["XDG_CONFIG_HOME"]) / "qmd/wiki_generation.yml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(yaml.safe_dump({"collections": {"wiki-generation": {
                "path": str(self.collection), "pattern": "**/*.md"}}}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if self.failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        query = argv[4]
        rows = [{"file": str(path), "content": path.read_text(), "score": 1}
                for path in self.collection.glob("*.md") if query in path.read_text()]
        if self.failure == "wrong":
            rows = [{"file": str(base / "wrong.md"), "content": query}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")


def test_build_search_health_and_corruption(candidate):
    root, publication, records = candidate
    runner = Runner()
    result = qg.build_index(publication, root=root, capability=capability(), runner=runner)
    assert result["status"] == "ready"
    descriptor = result["descriptor"]
    hits = qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=runner)
    assert [hit["page_id"] for hit in hits] == ["page-a"]
    assert hits[0]["source_semantic_revision"] == wiki.semantic_snapshot("page-a", records=records)["revision_hash"]
    assert hits[0]["generation_id"] == publication["generation_id"]
    assert qg.generation_health(root=root)["index_fresh"] is True
    Path(descriptor["database_path"]).write_bytes(b"corrupt")
    assert qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=runner) == []
    assert qg.generation_health(root=root)["index_fresh"] is False


@pytest.mark.parametrize("failure", ["wrong", "timeout"])
def test_failed_build_preserves_previous_descriptor(candidate, failure):
    root, publication, _ = candidate
    previous = b'{"previous":true}'
    (root / "search-current.json").write_bytes(previous)
    runner = Runner()
    runner.failure = failure
    assert qg.build_index(publication, root=root, capability=capability(), runner=runner)["status"] == "search_pending"
    assert (root / "search-current.json").read_bytes() == previous


def test_unsupported_never_invokes_runner(candidate):
    root, publication, _ = candidate
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported capability invoked QMD")
    assert qg.build_index(publication, root=root, capability={"supported": False}, runner=forbidden)["status"] == "search_pending"


def test_stale_unknown_hits_rejected(candidate, monkeypatch):
    _, _, records = candidate
    monkeypatch.setattr(wiki.qmd_search, "status", lambda: {"installed": True})
    monkeypatch.setattr(wiki.qmd_search, "search", lambda *a, **k: [
        {"page_id": "page-a", "generation_id": "old", "source_semantic_revision": "stale"},
        {"page_id": "unknown", "generation_id": "old", "source_semantic_revision": "stale"}])
    assert wiki._qmd_ranked_pages(records, query="synthetic", surface="all", status="all", limit=5) == []


def test_unverified_recipe_never_runs(candidate):
    root, publication, _ = candidate
    cap = capability()
    cap["recipe"]["build_argv"] = ["qmd", "update"]
    def forbidden(*args, **kwargs):
        pytest.fail("unverified recipe was executed")
    assert qg.build_index(publication, root=root, capability=cap, runner=forbidden)["status"] == "search_pending"


def test_descriptor_pages_cannot_forge_revision(candidate):
    root, publication, _ = candidate
    runner = Runner()
    descriptor = qg.build_index(publication, root=root, capability=capability(), runner=runner)["descriptor"]
    next(iter(descriptor["pages"].values()))["source_semantic_revision"] = "forged"
    assert qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=runner) == []


def test_query_pins_descriptor_across_pointer_switch(candidate):
    root, publication, _ = candidate
    runner = Runner()
    descriptor = qg.build_index(publication, root=root, capability=capability(), runner=runner)["descriptor"]
    def switching(argv, **kwargs):
        (root / "current.json").write_text(json.dumps({"generation_id": "new"}))
        (root / "search-current.json").write_text('{}')
        return runner(argv, **kwargs)
    hits = qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=switching)
    assert hits[0]["generation_id"] == publication["generation_id"]
    assert qg.generation_health(root=root)["index_fresh"] is False


def test_bad_preflight_never_builds(candidate):
    root, publication, _ = candidate
    def runner(argv, **kwargs):
        assert argv[-1] == "--help"
        return SimpleNamespace(returncode=0, stdout="Index: /outside/global.sqlite", stderr="")
    assert qg.build_index(publication, root=root, capability=capability(), runner=runner)["status"] == "search_pending"


def test_generation_feature_default_off(monkeypatch):
    from agent_console import qmd_search
    monkeypatch.delenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", raising=False)
    assert qmd_search.generation_enabled() is False


def test_real_reader_and_feature_gated_search(tmp_path, monkeypatch):
    from agent_console import qmd_search
    snapshot = {"records": [{"id": "page-a", "title": "Synthetic", "body": "synthetic knowledge",
                             "tags": ["wiki", "status:stable"]}],
                "captured_at": "2026-10-16T00:00:00+00:00", "schema_text": "",
                "schema_sha256": hashlib.sha256(b"").hexdigest()}
    snapshot["snapshot_sha256"] = wiki_publication._snapshot_hash(snapshot)
    publication = wiki_publication.publish_snapshot(snapshot, root=tmp_path, operation_id="task4-synthetic")
    runner = Runner()
    result = qg.build_index(publication, root=tmp_path, capability=capability(), runner=runner)
    assert result["status"] == "ready"
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setattr(qmd_search, "generation_root", lambda: tmp_path)
    hits = qmd_search.search("synthetic", runner=runner)
    assert hits[0]["generation_id"] == publication["generation_id"]
    assert qmd_search.health(runner=runner)["index_fresh"] is True
    monkeypatch.setattr(qmd_search, "search", lambda *args, **kwargs: hits)
    monkeypatch.setattr(qmd_search, "status", lambda: {"installed": True})
    pages = wiki._qmd_ranked_pages(snapshot["records"], query="synthetic", surface="all", status="all", limit=5)
    assert [page["id"] for page in pages] == ["page-a"]
    assert "qmd_snippet" not in pages[0]


def test_smoke_queries_are_bounded(candidate):
    root, publication, records = candidate
    directory = root / "generations" / publication["generation_id"]
    snapshot = json.loads((directory / "snapshot.json").read_bytes())
    snapshot["records"] = [{**records[0], "id": f"page-{number:03d}"} for number in range(31)]
    snapshot["snapshot_sha256"] = wiki_publication._snapshot_hash(snapshot)
    publication["snapshot_sha256"] = snapshot["snapshot_sha256"]
    for name, data in wiki_publication.render_candidate(snapshot).items():
        (directory / name).write_bytes(data)
    runner, calls = Runner(), []
    def counted(argv, **kwargs):
        calls.append(argv)
        return runner(argv, **kwargs)
    result = qg.build_index(publication, root=root, capability=capability(), runner=counted)
    assert result["status"] == "ready"
    assert len(result["descriptor"]["pages"]) == 31
    queries = [argv[4] for argv in calls if argv[3] == "search"]
    assert queries[1:] == ["page-000", "page-015", "page-030"]
    assert len(calls) == 6  # preflight, build, sentinel and three note samples


def test_health_validates_published_pointer(candidate, monkeypatch):
    root, publication, _ = candidate
    assert qg.build_index(publication, root=root, capability=capability(), runner=Runner())["status"] == "ready"
    old_pin = wiki_publication.pin_generation
    def pin(*, root, generation_id=None):
        if generation_id is None:
            raise ValueError("corrupt published generation")
        return old_pin(root=root, generation_id=generation_id)
    monkeypatch.setattr(wiki_publication, "pin_generation", pin)
    assert qg.generation_health(root=root)["index_fresh"] is False


def test_descriptor_directory_flush_failure_restores_previous(candidate, monkeypatch):
    root, publication, _ = candidate
    previous = b'{"previous":true}'
    (root / "search-current.json").write_bytes(previous)
    original = os.fsync
    failed = False
    def fsync(fd):
        nonlocal failed
        if (stat.S_ISDIR(os.fstat(fd).st_mode) and not failed
                and (root / "search-current.json").read_bytes() != previous):
            failed = True
            raise OSError("synthetic directory flush failure")
        return original(fd)
    monkeypatch.setattr(qg.os, "fsync", fsync)
    assert qg.build_index(publication, root=root, capability=capability(), runner=Runner())["status"] == "search_pending"
    assert (root / "search-current.json").read_bytes() == previous


@pytest.mark.parametrize("change", ["executable", "collection_name", "index_name", "config", "config_bytes"])
def test_forged_runtime_is_rejected_before_invocation(candidate, change):
    root, publication, _ = candidate
    descriptor = qg.build_index(publication, root=root, capability=capability(), runner=Runner())["descriptor"]
    if change in {"config", "config_bytes"}:
        config = Path(descriptor["build_root"]) / "config/qmd/wiki_generation.yml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("collections: {}\n# forged\n" if change == "config"
                          else config.read_text() + "# forged but semantically unchanged\n")
        descriptor["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
    else:
        descriptor[change] = "/tmp/forged-qmd" if change == "executable" else "forged"
    def forbidden(*args, **kwargs):
        pytest.fail("forged runtime reached subprocess")
    assert qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=forbidden) == []


def test_changed_executable_bytes_rejected_before_invocation(candidate, monkeypatch):
    from agent_console import qmd_search
    root, publication, _ = candidate
    executable = root / "synthetic-qmd"
    executable.write_bytes(b"synthetic trusted executable v1")
    monkeypatch.setattr(qmd_search, "qmd_bin", lambda: str(executable))
    descriptor = qg.build_index(publication, root=root, capability=capability(), runner=Runner())["descriptor"]
    executable.write_bytes(b"synthetic changed executable v2")
    def forbidden(*args, **kwargs):
        pytest.fail("changed executable reached subprocess")
    assert qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=forbidden) == []


def test_sqlite_startup_schema_rewrite_preserves_search(candidate):
    root, publication, _ = candidate
    class StartupRunner(Runner):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if "add" in argv or argv[3] == "search":
                db = Path(kwargs["env"]["XDG_CACHE_HOME"]) / "qmd/wiki_generation.sqlite"
                with closing(sqlite3.connect(db)) as connection:
                    connection.executescript("""
                        DROP TRIGGER IF EXISTS startup_trigger;
                        CREATE TRIGGER startup_trigger AFTER UPDATE ON indexed_notes
                        BEGIN SELECT 1; END;
                    """)
            return result
    runner = StartupRunner()
    descriptor = qg.build_index(publication, root=root, capability=capability(), runner=runner)["descriptor"]
    before = Path(descriptor["database_path"]).read_bytes()
    hits = qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=runner)
    assert before != Path(descriptor["database_path"]).read_bytes()
    assert [hit["page_id"] for hit in hits] == ["page-a"]
    assert qg.generation_health(root=root)["status"] == "ready"
    with closing(sqlite3.connect(descriptor["database_path"])) as connection:
        connection.execute("UPDATE indexed_notes SET body = 'tampered'")
        connection.commit()
    assert qg.search_generation("synthetic", descriptor=descriptor, limit=5, runner=runner) == []
    assert qg.generation_health(root=root)["status"] == "search_pending"


def _real_snapshot(body):
    snapshot = {"records": [{"id": "page-a", "title": "Synthetic publication", "body": body,
                             "tags": ["wiki", "status:stable"]}],
                "captured_at": "2026-10-16T00:00:00+00:00", "schema_text": "",
                "schema_sha256": hashlib.sha256(b"").hexdigest()}
    snapshot["snapshot_sha256"] = wiki_publication._snapshot_hash(snapshot)
    return snapshot


def test_older_build_cannot_replace_newer_search_pointer(tmp_path, monkeypatch):
    publication_a = wiki_publication.publish_snapshot(_real_snapshot("publication A"), root=tmp_path, operation_id="A")
    activate = qg._activate_descriptor
    newer = {}
    def interleave(root, descriptor):
        if descriptor["generation_id"] == publication_a["generation_id"]:
            publication_b = wiki_publication.publish_snapshot(_real_snapshot("publication B"), root=root, operation_id="B")
            newer.update(qg.build_index(publication_b, root=root, capability=capability(), runner=Runner()))
            assert newer["status"] == "ready"
        return activate(root, descriptor)
    monkeypatch.setattr(qg, "_activate_descriptor", interleave)
    older = qg.build_index(publication_a, root=tmp_path, capability=capability(), runner=Runner())
    assert older["status"] == "search_pending"
    assert json.loads((tmp_path / "search-current.json").read_bytes()) == newer["descriptor"]
    assert qg.generation_health(root=tmp_path)["status"] == "ready"


@pytest.mark.skipif(os.getenv("AGENT_CONSOLE_TEST_REAL_QMD") != "1", reason="opt-in isolated real QMD")
def test_real_qmd_queries_preserve_verified_generation(tmp_path):
    from agent_console import qmd_search
    executable = qmd_search.qmd_bin()
    capability_result = qg.probe_isolation(executable=executable, workspace=tmp_path, runner=subprocess.run)
    assert capability_result["supported"], capability_result["reason"]
    publication = wiki_publication.publish_snapshot(_real_snapshot("Synthetic publication knowledge"),
                                                     root=tmp_path, operation_id="real-qmd-synthetic")
    result = qg.build_index(publication, root=tmp_path, capability=capability_result, runner=subprocess.run)
    assert result["status"] == "ready"
    descriptor = result["descriptor"]
    qg._verify_descriptor(descriptor)
    physical_before = Path(descriptor["database_path"]).read_bytes()
    for _ in range(2):
        hits = qg.search_generation("publication", descriptor=descriptor, limit=5, runner=subprocess.run)
        assert [hit["page_id"] for hit in hits] == ["page-a"]
        qg._verify_descriptor(descriptor)
        assert qg.generation_health(root=tmp_path)["status"] == "ready"
    assert physical_before != Path(descriptor["database_path"]).read_bytes()
    with closing(sqlite3.connect(descriptor["database_path"])) as connection:
        connection.execute("UPDATE content SET doc = doc || ' tampered'")
        connection.commit()
    assert qg.search_generation("publication", descriptor=descriptor, limit=5, runner=subprocess.run) == []
    assert qg.generation_health(root=tmp_path)["status"] == "search_pending"


@pytest.mark.parametrize("alias", [False, True])
def test_routing_rejects_copied_root_before_verify_or_invoke(tmp_path, monkeypatch, alias):
    from agent_console import qmd_search
    root_a, root_b = tmp_path / "A", tmp_path / "B"
    snapshot = _real_snapshot("identical synthetic publication")
    publication_a = wiki_publication.publish_snapshot(snapshot, root=root_a, operation_id="A")
    publication_b = wiki_publication.publish_snapshot(snapshot, root=root_b, operation_id="B")
    assert publication_a == publication_b
    built = qg.build_index(publication_b, root=root_b, capability=capability(), runner=Runner())
    assert built["status"] == "ready"
    (root_a / "search-current.json").write_bytes((root_b / "search-current.json").read_bytes())
    if alias:
        linked_root = tmp_path / "alias-B"
        linked_root.symlink_to(root_b, target_is_directory=True)
        descriptor = dict(built["descriptor"], root=str(linked_root))
        (root_a / "search-current.json").write_text(json.dumps(descriptor))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setattr(qmd_search, "generation_root", lambda: root_a)
    def forbidden(*args, **kwargs):
        pytest.fail("cross-root descriptor reached verification or invocation")
    monkeypatch.setattr(qg, "_verify_descriptor", forbidden)
    assert qg.generation_health(root=root_a)["status"] == "search_pending"
    assert qmd_search.search("publication", runner=forbidden) == []


def test_routing_rejects_symlinked_trusted_root(tmp_path, monkeypatch):
    from agent_console import qmd_search
    root = tmp_path / "real"
    publication = wiki_publication.publish_snapshot(_real_snapshot("synthetic publication"), root=root, operation_id="real")
    assert qg.build_index(publication, root=root, capability=capability(), runner=Runner())["status"] == "ready"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "1")
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "1")
    monkeypatch.setattr(qmd_search, "generation_root", lambda: alias)
    def forbidden(*args, **kwargs):
        pytest.fail("symlinked root reached verification or invocation")
    monkeypatch.setattr(qg, "_verify_descriptor", forbidden)
    assert qg.generation_health(root=alias)["status"] == "search_pending"
    assert qmd_search.search("publication", runner=forbidden) == []
