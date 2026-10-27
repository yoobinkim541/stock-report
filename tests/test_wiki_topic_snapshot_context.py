"""Task2 contracts exercised against genuine synthetic Task1 artifacts."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from test_wiki_source_notes import (
    NOW, encoded, inventory, rewrite_compilation, rewrite_receipt,
)
from reports import wiki_source_notes


CURRENT = {"revision_id": "0" * 64, "terms": ["capacity", "demand"]}
NOTE_FIELDS = {
    "note_id", "revision_id", "projection_version", "canonical_url", "title",
    "provider", "scope", "provenance_class", "published_at", "collected_at",
    "terms", "verified_aliases", "claims", "limitations",
}
DEPENDENCY_FIELDS = {
    "kind", "note_id", "revision_id", "projection_version", "identity_version",
    "source_protocol_version", "canonical_url", "manifest_sha256", "raw_sha256",
    "text_sha256", "compile_sha256", "note_json_sha256", "note_md_sha256", "surface",
}


@pytest.fixture
def api():
    class API:
        def __getattr__(self, name):
            assert importlib.util.find_spec("reports.wiki_topic_snapshot_context") is not None, "Task2 APIs missing"
            return getattr(importlib.import_module("reports.wiki_topic_snapshot_context"), name)
    return API()


def expected_note(pin):
    return {"note_id": pin["note_id"], **{
        key: deepcopy(pin["note"][key]) for key in NOTE_FIELDS - {"note_id"}
    }}


@pytest.fixture
def notes(store):
    made = []

    def make(*, terms=None, aliases=None, size=None, chunked=False):
        body = "A" * 40000 + "TAIL" if chunked else "Capacity rose; demand is uncertain."
        data = store(url=f"https://t.me/public_channel/{len(made) + 1}",
                     chunked=chunked, body=body + f"\nSynthetic original {len(made) + 1}")
        def update(compiled):
            if terms is not None:
                compiled["terms"] = terms
            if aliases is not None:
                compiled["verified_aliases"] = aliases
        rewrite_compilation(data, update)
        if size is not None:
            preview = wiki_source_notes.materialize_source_note(data[1]["revision_id"], journal=data[0], now=NOW)
            # Size the independent whitelist without weakening compiler validation.
            compiled = data[0].load_compilation(data[0].load_source(data[1]["revision_id"]))
            source = data[1]
            note = {"note_id": preview["note_id"], "projection_version": 1,
                **{key: source.get(key, "") for key in ("revision_id", "canonical_url", "provider",
                    "scope", "provenance_class", "published_at", "collected_at")},
                **{key: compiled[key] for key in ("title", "terms", "verified_aliases", "claims", "limitations")}}
            padding = size - len(encoded(note).decode())
            assert padding >= 0
            def pad(value):
                update(value)
                value["limitations"][0] += "X" * padding
            rewrite_compilation(data, pad)
        result = wiki_source_notes.materialize_source_note(data[1]["revision_id"], journal=data[0], now=NOW, apply=True)
        pin = wiki_source_notes.pin_source_note(result["note_id"], journal=data[0], now=NOW)
        if size is not None:
            assert len(encoded(expected_note(pin)).decode()) == size
        made.append((data, pin))
        return data, pin

    return make


def select(api, data, pins, *, compiled=None, surface="market", now=NOW):
    return api.select_snapshot_context(compiled or CURRENT,
        note_ids=[pin["note_id"] for pin in pins], journal=data[0], now=now, surface=surface)


@pytest.mark.parametrize("edit", ["rename", "body", "private", "archive", "split", "delete"])
def test_editorial_changes_do_not_change_snapshot(api, notes, tmp_path, monkeypatch, edit):
    from agent_console import wiki
    from reports import wiki_topic_context
    data, pin = notes()
    sentinel = tmp_path / "editorial" / "untouched.json"
    sentinel.write_bytes(encoded({"id": "independent-page", "title": "EDITORIAL TITLE",
        "body": "EDITORIAL BODY", "tags": ["compiled_source", "surface:private"]}))
    context = select(api, data, [pin])
    row = json.loads(sentinel.read_bytes())
    if edit == "delete":
        sentinel.unlink()
    else:
        if edit == "rename": row["title"] = "RENAMED PRIVATE TITLE"
        elif edit == "body": row["body"] = "REPLACED EDITORIAL BODY"
        elif edit == "private":
            row["body"] += " PRIVATE ADDITION"
            row["private"] = "SECRET"
        elif edit == "archive": row["tags"].append("status:archived")
        else: row.update(parent_page_id="split-parent", children=["private-child"])
        sentinel.write_bytes(encoded(row))
    def forbidden(*args, **kwargs):
        pytest.fail("mutable wiki used as source authority")
    monkeypatch.setattr(wiki_topic_context, "_note", forbidden)
    monkeypatch.setattr(wiki, "semantic_snapshot", forbidden)
    assert select(api, data, [pin]) == context
    api.revalidate_snapshot_context(context, journal=data[0], now=NOW)
    projection = api.snapshot_context_model_input(context)
    assert projection == {"notes": [expected_note(pin)]}
    assert not any(word in json.dumps(projection) for word in ("EDITORIAL", "PRIVATE", "SECRET", "split-parent"))


def test_exact_contract_projection_is_detached_and_local_only(api, notes, tmp_path):
    data, pin = notes()
    before = inventory(tmp_path)
    context = select(api, data, [pin])
    assert set(context) == {"notes", "dependencies", "omitted"}
    assert set(context["notes"][0]) == NOTE_FIELDS
    assert context["notes"][0] == expected_note(pin)
    dependency = context["dependencies"][0]
    assert set(dependency) == DEPENDENCY_FIELDS
    assert dependency == {"kind": "source_note_snapshot", "surface": "market", **{
        key: pin[key] for key in DEPENDENCY_FIELDS - {"kind", "surface"}
    }}
    assert "surface" not in pin["note"]
    api.revalidate_snapshot_context(context, journal=data[0], now=NOW)
    projection = api.snapshot_context_model_input({**context,
        "omitted": [{"reason": "PRIVATE DIAGNOSTIC", "path": str(tmp_path)}]})
    assert set(projection) == {"notes"}
    assert not any(key in json.dumps(projection) for key in (
        "manifest", "sha256", "dependencies", "omitted", "page_id", "page_fingerprint", str(tmp_path)))
    projection["notes"][0]["claims"][0]["anchor"]["quote"] = "CHANGED"
    projection["notes"][0]["verified_aliases"].append("CHANGED")
    assert context["notes"][0] == expected_note(pin)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("damage", ["raw", "text", "compile", "note", "markdown", "manifest", "expiry", "policy", "unknown_policy"])
def test_evidence_tampering_or_policy_revocation_invalidates_context(api, notes, damage):
    data, pin = notes()
    context = select(api, data, [pin])
    if damage in {"raw", "text"}:
        Path(data[1][f"{damage}_ref"]).write_bytes(b"CORRUPT ORIGINAL")
    elif damage == "compile":
        rewrite_compilation(data, lambda value: value.update(title="Different compilation"))
    elif damage in {"note", "markdown", "manifest"}:
        name = {"note": "note.json", "markdown": "note.md", "manifest": "manifest.json"}[damage]
        (data[0].root / "source-notes" / pin["note_id"] / name).write_bytes(b"CORRUPT ARTIFACT")
    elif damage == "expiry":
        path = Path(data[1]["manifest_ref"])
        value = json.loads(path.read_bytes()); value["expires_at"] = NOW.isoformat()
        path.write_bytes(encoded(value))
    else:
        rewrite_receipt(data, lambda value: value["source"].update(
            {"scope": "personal_conversation"} if damage == "policy" else {"provider": "unknown"}))
    with pytest.raises(ValueError, match="snapshot_context_changed"):
        api.revalidate_snapshot_context(context, journal=data[0], now=NOW)
    fresh = select(api, data, [pin])
    assert fresh["notes"] == []
    assert fresh["omitted"] == [{"note_id": pin["note_id"], "reason": "unverified"}]


def test_expiry_is_rechecked_at_supplied_use_time(api, notes):
    data, pin = notes()
    context = select(api, data, [pin])
    expires = json.loads(Path(data[1]["manifest_ref"]).read_bytes())["expires_at"]
    with pytest.raises(ValueError, match="snapshot_context_changed"):
        api.revalidate_snapshot_context(context, journal=data[0], now=datetime.fromisoformat(expires.replace("Z", "+00:00")))


def test_rank_cap_revision_dedup_current_and_explicit_ids(api, notes):
    ordinary = [notes(terms=["capacity"], aliases=[]) for _ in range(6)]
    data, top = notes()
    _, unrelated = notes(terms=["news", "market"], aliases=[])
    pins = [pin for _, pin in ordinary] + [top, ordinary[0][1], unrelated]
    result = select(api, data, pins)
    ordered = sorted((pin for _, pin in ordinary), key=lambda pin: (pin["note_id"], pin["revision_id"]))
    assert [note["note_id"] for note in result["notes"]] == [top["note_id"], *[pin["note_id"] for pin in ordered[:3]]]
    assert {item["reason"] for item in result["omitted"]} == {"duplicate_revision", "note_limit", "unrelated"}
    assert select(api, data, list(reversed(pins))) == result
    current = select(api, data, [top], compiled={**CURRENT, "revision_id": top["revision_id"]})
    assert current["notes"] == []
    assert current["omitted"][0]["reason"] == "current_revision"
    assert select(api, data, [])["notes"] == []  # No implicit latest selection.


def test_existing_financial_alias_overlap_and_generic_exclusion(api, notes):
    data, pin = notes(terms=["Federal Reserve"], aliases=[])
    assert len(select(api, data, [pin], compiled={**CURRENT, "terms": ["fed"]})["notes"]) == 1
    assert select(api, data, [pin], compiled={**CURRENT, "terms": ["news", "market"]})["notes"] == []
    data, pin = notes(terms=["neutral"], aliases=["Federal Reserve"])
    assert len(select(api, data, [pin], compiled={**CURRENT, "terms": [], "verified_aliases": ["fed"]})["notes"]) == 1


def test_whole_notes_and_exact_canonical_character_budgets(api, notes):
    data, exact = notes(size=12000)
    _, oversize = notes(size=12001)
    _, small = notes(size=11997)
    result = select(api, data, [exact, oversize, small])
    assert len(result["notes"]) == 2
    assert len(encoded(result["notes"]).decode()) == 24000
    assert result["omitted"] == [{"note_id": oversize["note_id"], "revision_id": oversize["revision_id"], "reason": "note_budget"}]
    assert {note["note_id"] for note in result["notes"]} == {exact["note_id"], small["note_id"]}
    assert all(note["limitations"][0].endswith("X") for note in result["notes"])
    api.revalidate_snapshot_context(result, journal=data[0], now=NOW)


def test_aggregate_budget_omits_whole_note_and_keeps_tail(api, notes):
    made = [notes(size=8000) for _ in range(3)]
    result = select(api, made[0][0], [pin for _, pin in made])
    assert len(result["notes"]) == 2
    assert result["omitted"][0]["reason"] == "total_budget"
    assert all(note["limitations"][0].endswith("X") for note in result["notes"])


def test_large_resumed_note_is_omitted_not_clipped(api, notes):
    data, pin = notes(chunked=True)
    assert len(pin["note"]["claims"]) == 243
    result = select(api, data, [pin])
    assert result["notes"] == []
    assert result["omitted"][0]["reason"] == "note_budget"
    assert pin["note"]["claims"][-1]["anchor"]["start"] == 40000


@pytest.mark.parametrize("surface", ["", "all", "private", "portfolio", "ticker", "paper", "lab", "wiki", "MARKET", " market", None, [], 1])
def test_only_explicit_market_routing_is_supported(api, notes, surface):
    data, pin = notes()
    with pytest.raises(ValueError, match="routing_surface"):
        select(api, data, [pin], surface=surface)


@pytest.mark.parametrize("ids", [["f" * 64], ["../escape"], ["A" * 64]])
def test_unknown_or_invalid_note_ids_are_diagnostic_only(api, notes, ids):
    data, _ = notes()
    context = api.select_snapshot_context(CURRENT, note_ids=ids, journal=data[0], now=NOW, surface="market")
    assert context == {"notes": [], "dependencies": [], "omitted": [{"note_id": ids[0], "reason": "unverified"}]}
    assert api.snapshot_context_model_input(context) == {"notes": []}


@pytest.mark.parametrize("damage", ["note_text", "note_identity", "note_extra", "claim_extra", "anchor_extra",
    "dependency_hash", "dependency_extra", "kind", "projection_bool", "projection_float", "surface",
    "duplicate", "missing_dependency", "reordered", "tuple_notes", "nonfinite"])
def test_mutated_local_context_fails_closed(api, notes, damage):
    data, pin = notes()
    _, other = notes()
    context = select(api, data, [pin, other])
    note, dep = context["notes"][0], context["dependencies"][0]
    if damage == "note_text": note["claims"][0]["text"] = "Forged"
    elif damage == "note_identity": note["note_id"] = "0" * 64
    elif damage == "note_extra": note["private_body"] = "PRIVATE"
    elif damage == "claim_extra": note["claims"][0]["private"] = "PRIVATE"
    elif damage == "anchor_extra": note["claims"][0]["anchor"]["private"] = "PRIVATE"
    elif damage == "dependency_hash": dep["compile_sha256"] = "0" * 64
    elif damage == "dependency_extra": dep["page_fingerprint"] = "forged"
    elif damage == "kind": dep["kind"] = "page"
    elif damage == "projection_bool": dep["projection_version"] = True
    elif damage == "projection_float": dep["projection_version"] = 1.0
    elif damage == "surface": dep["surface"] = "portfolio"
    elif damage == "duplicate":
        context["notes"].append(deepcopy(note))
        context["dependencies"].append(deepcopy(dep))
    elif damage == "missing_dependency": context["dependencies"].pop()
    elif damage == "reordered": context["dependencies"].reverse()
    elif damage == "tuple_notes": context["notes"] = tuple(context["notes"])
    else: note["claims"][0]["anchor"]["start"] = float("nan")
    with pytest.raises(ValueError, match="snapshot_context_changed"):
        api.revalidate_snapshot_context(context, journal=data[0], now=NOW)


def test_no_materialization_compiler_model_reservation_or_editorial_calls(api, notes, tmp_path, monkeypatch):
    from reports import wiki_source_compiler, wiki_source_chunks, wiki_public_budget
    data, pin = notes()
    # Materialization may create safety locks; selection/pinning must not.
    locks = data[0].root / "source-locks"
    for path in locks.iterdir():
        path.unlink()
    locks.rmdir()
    before = inventory(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("selection performed operational work")
    monkeypatch.setattr(wiki_source_notes, "materialize_source_note", forbidden)
    monkeypatch.setattr(wiki_source_compiler, "compile_source", forbidden)
    monkeypatch.setattr(wiki_source_compiler, "structured_llm_call", forbidden)
    monkeypatch.setattr(wiki_source_chunks, "compile_chunks", forbidden)
    for name in dir(wiki_public_budget):
        if "reserve" in name and callable(getattr(wiki_public_budget, name)):
            monkeypatch.setattr(wiki_public_budget, name, forbidden)
    result = select(api, data, [pin])
    api.revalidate_snapshot_context(result, journal=data[0], now=NOW + timedelta(minutes=1))
    assert api.snapshot_context_model_input(result) == {"notes": [expected_note(pin)]}
    assert inventory(tmp_path) == before
    assert not locks.exists()
