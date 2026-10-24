"""Task 7: offline citation navigation must preserve immutable evidence identity."""
import base64
from copy import deepcopy
from datetime import timedelta
import importlib
import importlib.util
import json
import posixpath
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_console import shared_memory, wiki, wiki_publication as publication
from dashboard import wiki_browser
from reports.wiki_source_notes import materialize_source_note, pin_source_note
from test_wiki_source_notes import NOW, digest, encoded, rewrite_compilation, store


@pytest.fixture
def viewer():
    class API:
        def __getattr__(self, name):
            assert importlib.util.find_spec("reports.wiki_source_note_viewer"), "Task 7 citation viewer missing"
            return getattr(importlib.import_module("reports.wiki_source_note_viewer"), name)
    return API()


@pytest.fixture
def note(store):
    data = store()
    journal, source, _ = data
    result = materialize_source_note(source["revision_id"], journal=journal, now=NOW, apply=True)
    return data, pin_source_note(result["note_id"], journal=journal, now=NOW)


def frozen(pin, tmp_path, *, records=None):
    asset = {"note_id": pin["note_id"],
        "note_hashes": {key: pin[key] for key in (
            "manifest_sha256", "raw_sha256", "text_sha256", "compile_sha256",
            "note_json_sha256", "note_md_sha256")},
        "note_assets": {key: base64.b64encode(pin[key]).decode() for key in (
            "note_json_bytes", "note_md_bytes", "manifest_bytes")}}
    snapshot = {"records": records if records is not None else [{"id": "topic", "type": "wiki",
        "title": "Editorial topic", "body": "Separate synthesis", "links": [],
        "source_knowledge": {"claims": deepcopy(pin["note"]["claims"])}}],
        "captured_at": NOW.isoformat(), "schema_text": "synthetic schema",
        "schema_sha256": digest(b"synthetic schema"), "source_note_assets": {pin["note_id"]: asset}}
    snapshot["snapshot_sha256"] = digest(encoded(snapshot))
    files = publication.render_candidate(snapshot)
    assert publication.validate_candidate(files)["ok"]
    manifest = {"version": 1, "snapshot_sha256": snapshot["snapshot_sha256"],
        "schema_sha256": snapshot["schema_sha256"],
        "files": {name: digest(blob) for name, blob in files.items()}}
    generation_id = digest(encoded(manifest))
    root = tmp_path / "publication"
    directory = root / "generations" / generation_id
    for name, blob in {**files, "manifest.json": encoded(manifest)}.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return publication.pin_generation(root=root, generation_id=generation_id), snapshot, files


def forbidden(*args, **kwargs):
    raise AssertionError("citation viewer consulted a live/current/model/store fallback")


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch, tmp_path):
    import socket
    from reports import wiki_public_budget, wiki_source_compiler
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(wiki_source_compiler, "compile_source", forbidden)
    monkeypatch.setattr(wiki_public_budget, "reserve", forbidden)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED", "0")


def test_live_citation_uses_compiler_identity_not_private_editorial_text(viewer, note, monkeypatch, tmp_path):
    (journal, source, compiled), pin = note
    monkeypatch.setattr(wiki, "get_page", forbidden)
    for body in ('{"title":"Renamed","body":"PRIVATE_EDITORIAL"}', '{}'):
        (tmp_path / "editorial" / "untouched.json").write_text(body)
        citation = viewer.source_note_citation(pin["note_id"], journal=journal, now=NOW)
        assert citation["availability"] == "available"
        assert citation["revision_id"] == source["revision_id"]
        assert citation["canonical_url"] == "https://t.me/public_channel/42"
        assert citation["title"] == compiled["title"]
        assert citation["claims"] == compiled["claims"]
        assert citation["limitations"] == ["Forecast uncertainty",
            "Community attribution is unverified; retain the author's reported claim rather than treating it as fact."]
        assert citation["published_at"] == "2026-10-19"
        assert citation["collected_at"] == NOW.isoformat()
        assert citation["viewer_url"] == f"?source_note={pin['note_id']}"
        assert "PRIVATE_EDITORIAL" not in json.dumps(citation)
        assert str(tmp_path) not in json.dumps(citation)


@pytest.mark.parametrize("damage", ["expired", "missing", "private", "symlink"])
def test_live_unavailable_status_is_explicit_and_does_not_leak(viewer, note, tmp_path, damage):
    (journal, source, _), pin = note
    now = NOW
    if damage == "expired":
        now += timedelta(days=400)
    elif damage == "missing":
        journal._path("sources", source["revision_id"]).unlink()
    elif damage == "private":
        path = journal._path("sources", source["revision_id"])
        receipt = json.loads(path.read_bytes())
        receipt["source"]["visibility"] = "private"
        path.write_bytes(encoded(receipt))
    else:
        path = journal.root / "source-notes" / pin["note_id"] / "note.json"
        path.unlink()
        secret = tmp_path / "secret.json"
        secret.write_text('{"private":"DO_NOT_EXPOSE"}')
        path.symlink_to(secret)
    result = viewer.source_note_citation(pin["note_id"], journal=journal, now=now)
    assert result["availability"] == ("expired" if damage == "expired" else "unavailable")
    assert "claims" not in result
    assert str(tmp_path) not in json.dumps(result)
    assert "DO_NOT_EXPOSE" not in json.dumps(result)


@pytest.mark.parametrize("bad_id", ["../secret", "/tmp/private", "a" * 63, "A" * 64,
    "a" * 64 + ".md", "%2e%2e%2fsecret", "a" * 64 + "\n", None, ["a" * 64]])
def test_ids_are_not_paths_or_coerced_values(viewer, bad_id):
    class NoJournal:
        @property
        def root(self):
            forbidden()
    assert viewer.source_note_citation(bad_id, journal=NoJournal(), now=NOW)["availability"] == "unavailable"


@pytest.mark.parametrize("damage", ["changed", "removed"])
def test_emitted_frozen_links_read_exact_same_reader_bytes(viewer, note, tmp_path, monkeypatch, damage):
    (journal, _, _), pin = note
    reader, snapshot, files = frozen(pin, tmp_path)
    for path in journal.root.rglob("*"):
        if path.is_file():
            path.unlink() if damage == "removed" else path.write_bytes(b"changed live bytes")
    monkeypatch.setattr(publication, "pin_generation", forbidden)
    monkeypatch.setattr(journal, "load_source", forbidden)
    topic = reader.read("notes/topic.md").decode()
    paths = re.findall(r"\]\(\.\./(source-notes/[^)]+)\)", topic)
    assert paths == [f"source-notes/{pin['note_id']}.md"]
    result = viewer.frozen_source_note_citation(pin["note_id"], reader=reader)
    assert result["availability"] == "historical"
    assert result["current_eligibility"] == "not_checked"
    assert result["markdown_bytes"] == reader.read(paths[0]) == pin["note_md_bytes"]
    assert result["json_bytes"] == reader.read(paths[0][:-3] + ".json") == pin["note_json_bytes"]
    assert result["claims"] == pin["note"]["claims"]
    assert snapshot["records"][0]["links"] == []
    assert [name for name in files if name.startswith("notes/")] == ["notes/topic.md"]


@pytest.mark.parametrize("missing", ["md", "json", "snapshot", "mismatched_json"])
def test_frozen_missing_or_mismatched_assets_never_fall_back(viewer, note, tmp_path, monkeypatch, missing):
    (journal, _, _), pin = note
    reader, _, files = frozen(pin, tmp_path)
    changed = dict(files)
    name = "snapshot.json" if missing == "snapshot" else f"source-notes/{pin['note_id']}.{missing}"
    if missing == "mismatched_json":
        changed[f"source-notes/{pin['note_id']}.json"] = encoded({**pin["note"], "title": "OTHER_GENERATION"})
    else:
        changed.pop(name)
    broken = publication.GenerationReader(reader.directory, {
        key: getattr(reader, key) for key in ("generation_id", "snapshot_sha256", "manifest_sha256")}, changed)
    monkeypatch.setattr(publication, "pin_generation", forbidden)
    monkeypatch.setattr(journal, "load_source", forbidden)
    result = viewer.frozen_source_note_citation(pin["note_id"], reader=broken)
    assert result["availability"] == "unavailable"
    assert "claims" not in result and "markdown_bytes" not in result


def test_query_links_without_frozen_assets_are_rejected(note, tmp_path):
    _, pin = note
    _, snapshot, _ = frozen(pin, tmp_path)
    snapshot["source_note_assets"] = {}
    snapshot["records"][0]["body"] += f"\n[Source](?source_note={pin['note_id']})"
    snapshot["snapshot_sha256"] = publication._snapshot_hash(snapshot)
    result = publication.validate_candidate(publication.render_candidate(snapshot))
    assert not result["ok"], "query citations need the same asset guarantee as Markdown citations"


def test_browser_emits_clickable_query_and_renders_same_pin(note, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    _, pin = note
    reader, _, _ = frozen(pin, tmp_path)
    monkeypatch.setattr(publication, "pin_generation", forbidden)
    script = """
import streamlit as st
from dashboard import wiki_browser
reader = st.session_state['reader']
wiki_browser.render_source_note_links(st.session_state['evidence'], reader=reader)
if st.query_params.get('source_note') is not None:
    wiki_browser.render_source_note_query(st.query_params, reader=reader)
"""
    app = AppTest.from_string(script, default_timeout=15)
    app.session_state["reader"] = reader
    app.session_state["evidence"] = {"source_knowledge": {"claims": pin["note"]["claims"]}}
    app.run()
    assert not app.exception
    links = re.findall(r"\]\((\?source_note=[^)]+)\)", "\n".join(item.value for item in app.markdown))
    assert len(links) == 1
    params = parse_qs(urlsplit(links[0]).query)
    assert params["source_note"] == [pin["note_id"]]
    assert params["generation"] == [reader.generation_id]
    app.query_params.update({key: value[0] for key, value in params.items()})
    app.run()
    assert not app.exception
    # Streamlit removes one terminal newline from code display, not reader bytes.
    assert any(item.value + "\n" == pin["note_md_bytes"].decode() for item in app.code)
    assert "historical" in " ".join(item.value for item in app.caption)
    assert not app.text_input and not app.text_area


def test_untrusted_compiler_text_is_not_executable_markdown(store, tmp_path, viewer):
    from streamlit.testing.v1 import AppTest
    data = store()
    attack = '<script>alert(1)</script>'
    body = attack + '<img src=x onerror=alert(1)> [x](javascript:alert(2)) [S1](https://t.me/public_channel/42)'
    rewrite_compilation(data, lambda value: value.update(title=attack, body=body))
    journal, source, _ = data
    result = materialize_source_note(source["revision_id"], journal=journal, now=NOW, apply=True)
    pin = pin_source_note(result["note_id"], journal=journal, now=NOW)
    reader, _, _ = frozen(pin, tmp_path)
    app = AppTest.from_string("""
import streamlit as st
from dashboard import wiki_browser
wiki_browser.render_source_note_query(st.query_params, reader=st.session_state['reader'])
""")
    app.session_state["reader"] = reader
    app.query_params["source_note"] = pin["note_id"]
    app.run()
    assert not app.exception
    assert any(attack in item.value for item in app.code)
    assert all(attack not in item.value for item in app.markdown)
    assert all(not item.proto.allow_html for item in app.markdown)


def test_query_route_precedes_editorial_and_context_reads(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(wiki, "stats", forbidden)
    monkeypatch.setattr(wiki, "list_pages", forbidden)
    monkeypatch.setattr(wiki, "build_context_section", forbidden)
    app = AppTest.from_string("from dashboard.wiki_browser import render_wiki_tab\nrender_wiki_tab('market')")
    app.query_params["source_note"] = "../secret"
    app.run()
    assert not app.exception
    assert "unavailable" in " ".join(item.value for item in app.warning)


@pytest.mark.parametrize("query", ["source_note=../secret", "source_note=", "source_note={id}&source_note={id}",
    "source_note={id}&generation=../other", "source_note={id}&generation=",
    "source_note={id}&generation=" + "a" * 64 + "&generation=" + "b" * 64])
def test_export_rejects_ambiguous_or_path_shaped_citation_queries(note, tmp_path, query):
    _, pin = note
    _, snapshot, _ = frozen(pin, tmp_path)
    snapshot["records"][0]["body"] += "\n[Source](?" + query.format(id=pin["note_id"]) + ")"
    snapshot["snapshot_sha256"] = publication._snapshot_hash(snapshot)
    assert not publication.validate_candidate(publication.render_candidate(snapshot))["ok"]


def test_browser_ignores_fabricated_revision_values_without_crashing(note, tmp_path):
    from streamlit.testing.v1 import AppTest
    _, pin = note
    reader, _, _ = frozen(pin, tmp_path)
    app = AppTest.from_string("""
import streamlit as st
from dashboard.wiki_browser import render_source_note_links
render_source_note_links({'source_knowledge': {'claims': [{'source_revision_id': ['../private']}] }},
                         reader=st.session_state['reader'])
""")
    app.session_state["reader"] = reader
    app.run()
    assert not app.exception
    assert not app.markdown


@pytest.mark.parametrize("damage", ["rename", "delete"])
def test_actual_export_and_viewer_survive_editorial_page_changes(viewer, note, tmp_path, monkeypatch, damage):
    (journal, _, _), pin = note
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    wiki._CACHE.clear()
    _, original, _ = frozen(pin, tmp_path)
    display = wiki.upsert_page({"id": "display-source", "title": "Display title", "body": "Display prose"})
    topic = wiki.upsert_page({"id": "topic", "title": "Separate topic", "body": "Earlier synthesis",
        "source_knowledge": {"claims": pin["note"]["claims"]}, "links": []})
    if damage == "rename":
        wiki.upsert_page({**display, "title": "Renamed editorial title", "body": "PRIVATE_EDITORIAL_ADDITION"})
    else:
        assert wiki.delete_page(display["id"])
    captured = publication.capture_snapshot(source_note_assets=original["source_note_assets"])
    reader, _, files = frozen(pin, tmp_path, records=captured["records"])
    before = shared_memory._paths()["events"].read_bytes()
    source_path = re.search(r"\]\(\.\./(source-notes/[^)]+)\)", reader.read("notes/topic.md").decode()).group(1)
    result = viewer.frozen_source_note_citation(source_path.split("/")[1][:-3], reader=reader)
    assert result["markdown_bytes"] == pin["note_md_bytes"]
    assert result["title"] == "Attributed capacity and demand"
    assert b"PRIVATE_EDITORIAL_ADDITION" not in result["markdown_bytes"]
    assert str(tmp_path) not in repr(result)
    assert captured["records"][0]["id"] != pin["note_id"]
    assert all(pin["note_id"] not in row.get("links", []) for row in captured["records"])
    assert all(not name.startswith("notes/" + pin["note_id"]) for name in files)
    assert shared_memory._paths()["events"].read_bytes() == before
    assert topic["links"] == []
    wiki._CACHE.clear()


@pytest.mark.parametrize("query_damage", ["duplicate", "bad_generation", "wrong_generation", "missing_generation"])
def test_browser_bad_or_unavailable_generation_never_uses_current_or_live(note, tmp_path, monkeypatch, query_damage):
    from streamlit.testing.v1 import AppTest
    _, pin = note
    reader, _, _ = frozen(pin, tmp_path)
    monkeypatch.setattr(publication, "pin_generation", forbidden)
    if query_damage == "missing_generation":
        from reports import wiki_source_note_viewer
        def missing(*, root, generation_id):
            assert generation_id == "b" * 64
            raise ValueError("missing synthetic generation")
        monkeypatch.setattr(publication, "pin_generation", missing)
        monkeypatch.setattr(wiki_source_note_viewer, "pin_source_note", forbidden)
    app = AppTest.from_string("""
import streamlit as st
from dashboard.wiki_browser import render_source_note_query
render_source_note_query(st.query_params, reader=st.session_state.get('reader'))
""")
    app.session_state["reader"] = None if query_damage == "missing_generation" else reader
    app.query_params["source_note"] = [pin["note_id"], pin["note_id"]] if query_damage == "duplicate" else pin["note_id"]
    app.query_params["generation"] = "../private" if query_damage == "bad_generation" else "b" * 64
    app.run()
    assert not app.exception
    assert "unavailable" in " ".join(item.value for item in app.warning)
    assert not app.code


def test_expired_live_query_has_status_without_exposing_evidence(note):
    from streamlit.testing.v1 import AppTest
    (journal, _, _), pin = note
    app = AppTest.from_string("""
import streamlit as st
from dashboard.wiki_browser import render_source_note_query
render_source_note_query(st.query_params, journal=st.session_state['journal'], now=st.session_state['now'])
""")
    app.session_state["journal"] = journal
    app.session_state["now"] = NOW + timedelta(days=400)
    app.query_params["source_note"] = pin["note_id"]
    app.run()
    assert not app.exception
    assert "expired" in " ".join(item.value for item in app.warning)
    assert not app.code


@pytest.mark.parametrize("generation", ["", "&generation=" + "a" * 64])
@pytest.mark.parametrize("syntax", ["inline", "reference", "angle_entity"])
def test_exported_query_click_uses_paired_assets_not_current_or_foreign_generation(
        viewer, note, tmp_path, monkeypatch, generation, syntax):
    from markdown_it import MarkdownIt
    (journal, _, _), pin = note
    query = f"?source_note={pin['note_id']}" + generation
    if syntax == "reference":
        body = f'[Open retained source][retained]\n\n[retained]: {query} "Source citation"'
    elif syntax == "angle_entity":
        body = f'[Open retained source](<{query.replace("&", "&amp;")}> "Source citation")'
    else:
        body = f'[Open retained source]({query} "Source citation")'
    body += f"\n\n`[Example]({query})`\n\n```md\n[Example]({query})\n```"
    record = {"id": "topic", "title": "Frozen topic", "body": body, "links": []}
    reader, snapshot, files = frozen(pin, tmp_path, records=[record])
    markdown = reader.read("notes/topic.md").decode()
    targets = [child.attrGet("href") for token in MarkdownIt().parse(markdown)
               for index, child in enumerate(token.children or [])
               if child.type == "link_open" and token.children[index + 1].content == "Open retained source"]
    assert targets == [f"../source-notes/{pin['note_id']}.md"]
    assert reader.generation_id != "a" * 64
    monkeypatch.setattr(publication, "pin_generation", forbidden)
    monkeypatch.setattr(journal, "load_source", forbidden)
    for path in (journal.root / "source-notes" / pin["note_id"]).iterdir():
        path.unlink()
    clicked_path = posixpath.normpath(posixpath.join("notes", targets[0]))
    assert reader.read(clicked_path) == pin["note_md_bytes"]
    assert reader.read(clicked_path[:-3] + ".json") == pin["note_json_bytes"]
    citation = viewer.frozen_source_note_citation(clicked_path.split("/")[1][:-3], reader=reader)
    assert citation["markdown_bytes"] == reader.read(clicked_path)
    assert citation["json_bytes"] == reader.read(clicked_path[:-3] + ".json")
    assert f"`[Example]({query})`" in markdown
    assert f"```md\n[Example]({query})\n```" in markdown
    assert snapshot["records"] == [record]
    assert files[f"source-notes/{pin['note_id']}.md"] == pin["note_md_bytes"]
    canonical = markdown.split("\n## Canonical Record\n\n```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(canonical) == record


@pytest.mark.parametrize("generation", ["", "&generation=" + "b" * 64])
def test_root_export_query_click_is_relative_to_its_own_pinned_reader(note, tmp_path, generation):
    from markdown_it import MarkdownIt
    _, pin = note
    query = f"?source_note={pin['note_id']}" + generation
    record = {"id": "topic", "title": "Topic", "body": "Synthesis", "links": [],
              "summary": f"[Retained]({query})", "openQuestions": [f"[Retained]({query})"]}
    reader, _, _ = frozen(pin, tmp_path, records=[record])
    for name in ("index.md", "open-questions.md"):
        targets = [child.attrGet("href") for token in MarkdownIt().parse(reader.read(name).decode())
                   for child in token.children or [] if child.type == "link_open"]
        assert targets == [f"source-notes/{pin['note_id']}.md"]
        assert reader.read(targets[0]) == pin["note_md_bytes"]
        assert reader.read(targets[0][:-3] + ".json") == pin["note_json_bytes"]


@pytest.mark.parametrize("indent", ["    ", "\t"], ids=["four_spaces", "tab"])
@pytest.mark.parametrize("newline", ["\n", "\r"], ids=["lf", "cr"])
def test_export_normalizes_prose_but_preserves_same_query_in_indented_code(note, tmp_path, indent, newline):
    from markdown_it import MarkdownIt
    _, pin = note
    query = f"?source_note={pin['note_id']}"
    code = f"{indent}[Example]({query})\n{indent}[retained]: {query}\n"
    body = (f"[Open retained source]({query})\n\n" + code
            + f"\n[Reference][retained]\n\n[retained]: {query}\n\n"
            + f"`[Inline example]({query})`\n\n```md\n[Fenced example]({query})\n```\n")
    code, body = code.replace("\n", newline), body.replace("\n", newline)
    record = {"id": "topic", "title": "Code examples", "summary": "Retained source examples",
              "body": body, "links": []}
    reader, snapshot, files = frozen(pin, tmp_path, records=[record])
    markdown = reader.read("notes/topic.md").decode()
    tokens = MarkdownIt().parse(markdown)
    assert any(token.type == "code_block" for token in tokens)
    assert code.encode() in files["notes/topic.md"], "indented examples must retain their exact source bytes"
    targets = [child.attrGet("href") for token in tokens for child in token.children or []
               if child.type == "link_open"]
    assert targets == [f"../source-notes/{pin['note_id']}.md"] * 2
    assert f"`[Inline example]({query})`" in markdown
    assert f"```md\n[Fenced example]({query})\n```".replace("\n", newline) in markdown
    assert snapshot["records"] == [record]
    canonical = markdown.split("\n## Canonical Record\n\n```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(canonical) == record
    assert files[f"source-notes/{pin['note_id']}.md"] == pin["note_md_bytes"]
    assert files[f"source-notes/{pin['note_id']}.json"] == pin["note_json_bytes"]
