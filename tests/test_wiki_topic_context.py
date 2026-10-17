"""Synthetic, archive-backed public context contracts."""
import copy
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_console import wiki
from reports import raw_archive
from reports.wiki_source_compiler import validate_compilation
from reports.wiki_source_journal import Journal
from reports.wiki_source_revision import build_revision

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
CURRENT = {"revision_id": "0" * 64, "terms": ["Hormuz", "energy", "news"]}


def api():
    return importlib.import_module("reports.wiki_topic_context")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    journal, rows = Journal(tmp_path / "journal"), []

    def add(page_id, *, terms=None, text="Reported shipping uncertainty", source=None):
        if source is None:
            url = f"https://t.me/public_channel/{len(rows) + 1}"
            record = raw_archive.save_raw_artifact("telegram", "text", NOW, f"Public report {page_id}", url,
                                                   "Synthetic complete original", ".txt")
            record.update(body_coverage="full", text_complete=True, extraction_scope="public_message")
            raw_archive.save_extracted_text(record, "Synthetic complete original")
            source = build_revision({"source": "telegram:public_channel", "url": url,
                "collected_at": NOW.isoformat(), "extraction_scope": "public_message",
                **{key: record[key] for key in ("raw_path", "text_path", "manifest_path")}}, now=NOW)
            compiled = validate_compilation({"action": "create", "body": "Report [S1]",
                "claims": [{"text": text, "as_of": "2026-10-15", "scope": "shipping"}],
                "terms": terms or ["Hormuz"], "limitations": ["Unverified report"]}, source)
            journal.save_compilation(source, compiled)
        row = {"id": page_id, "title": "Public report", "body": "source note",
               "tags": ["wiki", "surface:market", "kind:concept", "status:draft", "compiled_source"],
               "source_knowledge": {"claims": []}}
        rows.append(row)
        operation = {"schema_version": 1, "stage": "committed", "revision_id": source["revision_id"],
                     "batch": {"target_ids": [page_id], "expected_after": {
                         page_id: wiki._source_fingerprint(page_id, rows)}}}
        path = journal.root / "operations" / f"{page_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(operation))
        return source

    return journal, rows, add


def select(store, compiled=None):
    journal, rows, _ = store
    return api().select_topic_context(compiled or CURRENT, journal=journal, records=rows, now=NOW, surface="market")


def test_rank_cap_dedup_current_and_read_only(store):
    _, rows, add = store
    source = add("b")
    add("a", source=source)
    for name in ("f", "e", "d", "c"):
        add(name)
    add("top", terms=["Hormuz", "energy"])
    before = copy.deepcopy(rows)
    result = select(store)
    assert [note["page_id"] for note in result["notes"]] == ["top", "a", "c", "d"]
    assert {item["reason"] for item in result["omitted"]} >= {"duplicate_revision", "note_limit"}
    assert rows == before
    assert source["revision_id"] not in {n["revision_id"] for n in select(store, {**CURRENT, "revision_id": source["revision_id"]})["notes"]}
    rows.reverse()
    assert select(store) == result


def test_whole_notes_and_serialized_budgets(store):
    _, _, add = store
    add("a", text="X" * 12000 + "END")
    for name in "bcde":
        add(name, text="Y" * 8500 + "COMPLETE")
    result = select(store)
    encoded = [json.dumps(n, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for n in result["notes"]]
    assert all(len(n) <= 12000 for n in encoded)
    assert len(json.dumps(result["notes"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 24000
    assert len(result["notes"]) == 2
    assert all(n["claims"][0]["text"].endswith("COMPLETE") for n in result["notes"])
    assert {o["reason"] for o in result["omitted"]} >= {"note_budget", "total_budget"}


@pytest.mark.parametrize("damage", ["private", "changed", "archived", "missing", "expired", "raw", "text", "compiled", "identity", "surface", "uncommitted"])
def test_invalid_dependency_never_exposed(store, damage):
    journal, rows, add = store
    source = add("a")
    if damage in {"private", "identity"}:
        path = journal._path("sources", source["revision_id"])
        receipt = json.loads(path.read_text())
        receipt["source"]["scope" if damage == "private" else "raw_sha256"] = "FORBIDDEN"
        path.write_text(json.dumps(receipt))
    elif damage in {"changed", "archived", "surface"}:
        rows[0]["title"] = "FORBIDDEN"
        if damage != "changed":
            rows[0]["tags"].append("status:archived" if damage == "archived" else "surface:private")
    elif damage == "missing":
        Path(source["raw_ref"]).unlink()
    elif damage == "expired":
        path = Path(source["manifest_ref"])
        data = json.loads(path.read_text())
        data["expires_at"] = NOW.isoformat()
        path.write_text(json.dumps(data))
    elif damage in {"raw", "text"}:
        Path(source[f"{damage}_ref"]).write_text("FORBIDDEN")
    elif damage == "compiled":
        Path(journal.load_source(source["revision_id"])["compile_ref"]).write_text("FORBIDDEN")
    else:
        path = next((journal.root / "operations").glob("*.json"))
        data = json.loads(path.read_text())
        data["stage"] = "prepared"
        path.write_text(json.dumps(data))
    result = select(store)
    assert result["notes"] == []
    assert result["omitted"]
    assert "FORBIDDEN" not in json.dumps(result)


def test_nested_metadata_is_not_prompt_data(store):
    journal, rows, add = store
    source = add("a")
    receipt = journal.load_source(source["revision_id"])
    compiled = journal.load_compilation(receipt)
    compiled["claims"][0]["private"] = {"secret": "FORBIDDEN"}
    compiled["claims"][0]["anchor"] = {"start": 0, "end": 9, "quote": "Synthetic", "private": "FORBIDDEN"}
    compiled["private"] = {"secret": "FORBIDDEN"}
    # Recreate the synthetic receipt with an internally consistent artifact hash.
    path = journal._path("sources", source["revision_id"])
    path.unlink()
    journal.save_compilation(source, compiled)
    result = select(store)
    assert len(result["notes"]) == 1
    assert "FORBIDDEN" not in json.dumps(result)
    assert set(result["notes"][0]["claims"][0]["anchor"]) == {"start", "end", "quote"}


def test_archive_bundles_and_precommit_revalidation(store):
    journal, rows, add = store
    source = add("a")
    raw_archive.compact_raw_artifacts(now=NOW + timedelta(days=3), min_age_days=0)
    assert not Path(source["raw_ref"]).exists()
    context = select(store)
    assert len(context["notes"]) == 1
    api().revalidate_topic_context(context, journal=journal, records=rows, now=NOW)
    rows[0]["body"] += " changed"
    with pytest.raises(ValueError, match="topic_context_changed"):
        api().revalidate_topic_context(context, journal=journal, records=rows, now=NOW)


@pytest.mark.parametrize("damage", ["expiry", "compilation", "note", "dependency"])
def test_revalidation_rejects_changed_dependencies_and_projection(store, damage):
    journal, rows, add = store
    source = add("a")
    context = select(store)
    now = NOW
    if damage == "expiry":
        now += timedelta(days=400)
    elif damage == "compilation":
        Path(journal.load_source(source["revision_id"])["compile_ref"]).write_text("changed")
    elif damage == "note":
        context["notes"][0]["claims"][0]["text"] = "forged"
    else:
        context["dependencies"].clear()
    with pytest.raises(ValueError, match="topic_context_changed"):
        api().revalidate_topic_context(context, journal=journal, records=rows, now=now)


def test_generic_overlap_is_not_related(store):
    store[2]("a", terms=["news", "market"])
    assert select(store)["notes"] == []


def test_verified_aliases_rank_but_arbitrary_metadata_does_not(store):
    journal, rows, add = store
    source = add("a", terms=["unrelated"])
    receipt = journal.load_source(source["revision_id"])
    compiled = journal.load_compilation(receipt)
    compiled["verified_aliases"] = ["Hormuz", {"private": "FORBIDDEN"}]
    journal._path("sources", source["revision_id"]).unlink()
    journal.save_compilation(source, compiled)
    rows[0]["verified_aliases"] = ["energy", {"private": "FORBIDDEN"}]
    rows[0]["private"] = {"term": "energy", "secret": "FORBIDDEN"}
    path = next((journal.root / "operations").glob("*.json"))
    operation = json.loads(path.read_text())
    operation["batch"]["expected_after"]["a"] = wiki._source_fingerprint("a", rows)
    path.write_text(json.dumps(operation))
    result = select(store)
    assert [note["page_id"] for note in result["notes"]] == ["a"]
    assert "energy" not in result["notes"][0]["terms"]
    assert "FORBIDDEN" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["raw", "text", "private", "uncommitted", "removed", "child"])
def test_precommit_checks_every_public_dependency_again(store, damage):
    journal, rows, add = store
    source = add("a")
    context = select(store)
    if damage in {"raw", "text"}:
        Path(source[f"{damage}_ref"]).write_text("changed")
    elif damage == "private":
        path = journal._path("sources", source["revision_id"])
        data = json.loads(path.read_text())
        data["source"]["canonical_url"] = "https://t.me/c/123/4"
        path.write_text(json.dumps(data))
    elif damage == "uncommitted":
        next((journal.root / "operations").glob("*.json")).unlink()
    elif damage == "removed":
        rows.clear()
    else:
        rows.append({"id": "child", "title": "Child", "body": "changed",
                     "parent_page_id": "a", "tags": ["wiki", "status:draft"]})
    with pytest.raises(ValueError, match="topic_context_changed"):
        api().revalidate_topic_context(context, journal=journal, records=rows, now=NOW)


def test_model_projection_excludes_local_diagnostics_dependencies_and_extras(store):
    store[2]("public")
    context = select(store)
    expected = copy.deepcopy(context["notes"])
    context["omitted"].append({"page_id": "PRIVATE_OMITTED_SENTINEL", "reason": "private"})
    context["dependencies"][0]["raw_sha256"] = "DEPENDENCY_HASH_SENTINEL"
    context["private"] = {"secret": "ROOT_PRIVATE_SENTINEL"}
    note = context["notes"][0]
    note["private"] = {"secret": "NOTE_PRIVATE_SENTINEL"}
    claim = note["claims"][0]
    claim["private"] = {"secret": "CLAIM_PRIVATE_SENTINEL"}
    claim["anchor"] = {"start": 0, "end": 9, "quote": "Synthetic", "private": "ANCHOR_PRIVATE_SENTINEL"}
    expected[0]["claims"][0]["anchor"] = {"start": 0, "end": 9, "quote": "Synthetic"}
    projected = api().topic_context_model_input(context)
    assert projected == {"notes": expected}
    serialized = json.dumps(projected)
    assert "SENTINEL" not in serialized
    assert "dependencies" not in serialized
    assert "omitted" not in serialized
    assert "raw_sha256" not in serialized


def test_model_projection_has_no_mutable_aliases_in_either_direction(store):
    store[2]("public")
    context = select(store)
    claim = context["notes"][0]["claims"][0]
    claim["anchor"] = {"start": 0, "end": 9, "quote": "Synthetic"}
    projected = api().topic_context_model_input(context)
    before = copy.deepcopy(context)
    note = projected["notes"][0]
    note["terms"].append("changed")
    note["limitations"].append("changed")
    note["claims"][0]["competing_claim_ids"].append("changed")
    note["claims"][0]["anchor"]["quote"] = "changed"
    note["claims"].append({"text": "changed"})
    projected["notes"].append({"title": "changed"})
    assert context == before
    fresh = api().topic_context_model_input(context)
    expected = copy.deepcopy(fresh)
    context["notes"][0]["terms"].append("source changed")
    claim["anchor"]["quote"] = "source changed"
    context["notes"].clear()
    assert fresh == expected


def test_model_projection_empty_notes_does_not_expose_private_omissions():
    context = {"notes": [], "dependencies": [{"raw_sha256": "PRIVATE_HASH"}],
               "omitted": [{"page_id": "PRIVATE_ID", "reason": "unverified"}]}
    assert api().topic_context_model_input(context) == {"notes": []}


@pytest.mark.parametrize("current,previous", [
    ("엔비디아", "NVIDIA"), ("연준", "Federal Reserve"), ("미국채", "Treasury"),
])
def test_cross_language_related_sources_seed_context(store, current, previous):
    store[2]("prior", terms=[previous])
    result = select(store, {**CURRENT, "terms": [current]})
    assert [note["page_id"] for note in result["notes"]] == ["prior"]
