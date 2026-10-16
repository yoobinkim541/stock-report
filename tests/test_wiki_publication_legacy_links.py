import copy
import json

import pytest

from agent_console import wiki_publication as publication


def row(identity, **fields):
    return {"id": identity, "title": identity, "body": "Stored body",
            "tags": ["wiki"], "source": {"surface": "wiki"}, **fields}


def snapshot(*records):
    value = {"records": list(records), "captured_at": "2026-10-16T12:00:00+00:00",
             "schema_text": "Schema\n"}
    value["schema_sha256"] = publication._sha(b"Schema\n")
    value["snapshot_sha256"] = publication._snapshot_hash(value)
    return value


def prose(files, name="notes/source.md"):
    return files[name].decode().split("\n## Canonical Record\n", 1)[0]


@pytest.mark.parametrize("field", ["body", "summary", "openQuestions", "decisions"])
def test_unique_stripped_title_exports_stable_link_in_all_prose(field):
    text = "See [[  Legacy Page  ]]."
    value = snapshot(row("source", **{field: [text] if field in (
        "openQuestions", "decisions") else text}),
        row("stable-target", title="  Legacy Page  "))
    files = publication.render_candidate(value)

    assert "[[stable-target|Legacy Page]]" in prose(files)
    if field == "openQuestions":
        assert b"[[stable-target|Legacy Page]]" in files["open-questions.md"]
    if field in ("body", "summary"):
        assert b"[[stable-target|Legacy Page]]" in files["index.md"]
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


@pytest.mark.parametrize("duplicate_title", [False, True])
def test_id_precedes_unique_or_duplicate_title(duplicate_title):
    value = snapshot(row("source", body="[[token]] [[token|Chosen label]]"),
                     row("token", title="ID winner"),
                     row("other", title="token"),
                     row("another", title="token" if duplicate_title else "Other title"))
    files = publication.render_candidate(value)

    assert "[[token]] [[token|Chosen label]]" in prose(files)
    assert "[[other|token]]" not in prose(files)
    assert publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("reference", ["[[Unknown]]", "[[Repeated]]",
                                          "[[Legacy Page|Label]]", "[[legacy page]]"])
def test_unknown_ambiguous_and_labelled_title_refs_remain_blocked(reference):
    value = snapshot(row("source", body=reference),
                     row("target", title="Legacy Page"),
                     row("duplicate-a", title="Repeated"),
                     row("duplicate-b", title=" Repeated "))
    files = publication.render_candidate(value)

    assert reference in prose(files)
    result = publication.validate_candidate(files)
    assert not result["ok"]
    assert any(error["code"] == "missing_markdown_link"
               and error.get("page_id") == "source" for error in result["errors"])


@pytest.mark.parametrize("wrapper", ["`{text}`", "``{text}``",
                                     "```md\n{text}\n```", "~~~~md\n{text}\n~~~~"])
@pytest.mark.parametrize("text", ["[[Legacy Page]] [[missing|Unknown]]",
                                  "[missing](missing.md) [relative](../missing.md)"])
def test_code_examples_are_unchanged_and_not_validated_as_prose(wrapper, text):
    code = wrapper.format(text=text)
    value = snapshot(row("source", body="See [[Legacy Page]].\n\n" + code),
                     row("target", title="Legacy Page"))
    files = publication.render_candidate(value)

    assert code in prose(files)
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}
    assert "See [[target|Legacy Page]]." in prose(files)


def test_ordinary_markdown_links_in_prose_still_require_actual_files():
    files = publication.render_candidate(snapshot(
        row("source", summary="Source summary",
            body="[target](target.md) [missing](missing.md)"), row("target")))
    result = publication.validate_candidate(files)

    assert not result["ok"]
    targets = {error.get("target_id") for error in result["errors"]
               if error["code"] == "missing_markdown_link"}
    assert "missing.md" in targets
    assert "target.md" not in targets


@pytest.mark.parametrize("reference", ["[[Legacy Page]]", "[[\uD55C\uAE00/path?target]]",
                                      "[[\uD55C\uAE00/path?target|Visible]]"])
def test_unicode_path_identity_links_use_actual_sanitized_export_target(reference):
    files = publication.render_candidate(snapshot(
        row("source", body=reference), row("\uD55C\uAE00/path?target", title="Legacy Page")))
    label = "Visible" if "|" in reference else "Legacy Page"

    assert f"[[\uD55C\uAE00-path-target|{label}]]" in prose(files)
    assert "notes/\uD55C\uAE00-path-target.md" in files
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


def test_sanitized_target_collisions_still_reject_candidate():
    value = snapshot(row("source", body="[[Legacy Page]]"),
                     row("\uD55C\uAE00/a", title="Legacy Page"), row("\uD55C\uAE00?a"))
    with pytest.raises(ValueError, match="filename_collision"):
        publication.render_candidate(value)


def test_resolved_title_does_not_excuse_missing_exported_target():
    files = publication.render_candidate(snapshot(
        row("source", body="[[Legacy Page]]"), row("target", title="Legacy Page")))
    del files["notes/target.md"]
    result = publication.validate_candidate(files)

    assert not result["ok"]
    assert any(error["code"] == "missing_markdown_link" and error.get("target_id") == "target"
               for error in result["errors"])


def test_render_and_validation_preserve_snapshot_and_canonical_json_exactly():
    records = [row("source", body="[[Legacy Page]]", source_knowledge={
        "limitations": ["[[Legacy Page]] [literal](absent.md)"]}),
        row("target", title="Legacy Page")]
    value = snapshot(*records)
    before = copy.deepcopy(value)
    files = publication.render_candidate(value)
    canonical = files["notes/source.md"].split(b"\n## Canonical Record\n\n```json\n", 1)[1]
    expected = json.dumps(records[0], ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()
    expected_snapshot = json.dumps(before, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()

    assert canonical == expected + b"\n```\n"
    assert files["snapshot.json"] == expected_snapshot
    assert b'"[[Legacy Page]] [literal](absent.md)"' in files["notes/source.md"]
    assert json.loads(files["snapshot.json"]) == before
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}
    assert value == before
    assert publication.render_candidate(value) == files


def test_metadata_relationships_do_not_gain_title_aliases():
    files = publication.render_candidate(snapshot(
        row("source", links=["Legacy Page"]), row("target", title="Legacy Page")))
    result = publication.validate_candidate(files)

    assert not result["ok"]
    assert {"code": "missing_link", "page_id": "source",
            "target_id": "Legacy Page"} in result["errors"]


@pytest.mark.parametrize("marker", ["`", "~"])
def test_unclosed_fence_preserves_code_through_eof(marker):
    code = marker * 3 + "md\n[[Legacy Page]] [[missing]] [example](absent.md)"
    value = snapshot(row("source", body="See [[Legacy Page]].\n\n" + code),
                     row("target", title="Legacy Page"))
    files = publication.render_candidate(value)

    assert code in prose(files)
    assert "See [[target|Legacy Page]]." in prose(files)
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


@pytest.mark.parametrize("marker", ["`", "~"])
@pytest.mark.parametrize("closing_length", [4, 5])
def test_fence_requires_matching_marker_and_at_least_opener_length(marker, closing_length):
    other = "~" if marker == "`" else "`"
    code = (marker * 4 + "md\n" + marker * 3 + "\n" + other * 4
            + "\n[[Legacy Page]] [[missing]] [example](absent.md)\n"
            + marker * closing_length)
    value = snapshot(row("source", summary="Source summary",
                         body=code + "\nAfter [[Legacy Page]] and `[[missing-inline]]`."),
                     row("target", title="Legacy Page"))
    files = publication.render_candidate(value)

    assert code in prose(files)
    assert "After [[target|Legacy Page]] and `[[missing-inline]]`." in prose(files)
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


@pytest.mark.parametrize("marker", ["`", "~"])
def test_fence_marker_inside_content_line_does_not_close_fence(marker):
    code = (marker * 3 + "md\ncontent " + marker * 3
            + "\n[[Legacy Page]] [[missing]]\n" + marker * 3)
    files = publication.render_candidate(snapshot(
        row("source", summary="Source summary", body=code + "\nAfter [[Legacy Page]]."),
        row("target", title="Legacy Page")))

    assert code in prose(files)
    assert "After [[target|Legacy Page]]." in prose(files)
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


@pytest.mark.parametrize("reference", ["[[target.md]]", "[[notes/target.md]]",
                                      "[[target.md|Label]]", "[[notes/target.md|Label]]",
                                      "[[notes/target]]", "[[notes/target|Label]]"])
def test_existing_export_file_references_remain_supported(reference):
    files = publication.render_candidate(snapshot(
        row("source", body=reference), row("target", title="Target title")))

    assert reference in prose(files)
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}
    del files["notes/target.md"]
    assert not publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("reference", ["[[unknown.md]]", "[[notes/unknown.md|Label]]",
                                      "[[other/target.md]]", "[[notes/../target.md]]",
                                      "[[Legacy Page.md|Label]]", "[[notes/unknown]]",
                                      "[[other/target]]", "[[notes/../target]]"])
def test_file_reference_compatibility_does_not_accept_unknown_paths_or_titles(reference):
    files = publication.render_candidate(snapshot(
        row("source", body=reference), row("target", title="Legacy Page.md")))

    assert not publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("labelled", [False, True])
def test_exact_id_wins_over_export_file_reference(labelled):
    reference = "[[target.md|Chosen]]" if labelled else "[[target.md]]"
    files = publication.render_candidate(snapshot(
        row("source", body=reference), row("target.md", title="ID winner"),
        row("target", title="File fallback")))

    assert reference in prose(files)
    del files["notes/target.md.md"]
    result = publication.validate_candidate(files)
    assert not result["ok"]
    assert any(error.get("path") == "notes/source.md"
               and error["code"] == "missing_markdown_link" for error in result["errors"])


@pytest.mark.parametrize("target", ["target.md", "notes/target"])
def test_unique_title_wins_over_export_file_reference(target):
    files = publication.render_candidate(snapshot(
        row("source", body=f"[[{target}]]"), row("title-winner", title=target),
        row("target", title="File fallback")))

    assert f"[[title-winner|{target}]]" in prose(files)
    assert publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("target", ["target.md", "notes/target"])
def test_duplicate_title_is_not_rescued_by_export_file_reference(target):
    files = publication.render_candidate(snapshot(
        row("source", body=f"[[{target}]]"), row("first", title=target),
        row("second", title=target), row("target", title="File fallback")))

    assert not publication.validate_candidate(files)["ok"]


def test_file_reference_uses_sanitized_unicode_stem_not_title():
    files = publication.render_candidate(snapshot(
        row("source", body="[[notes/\uD55C\uAE00-target.md|Visible]]"),
        row("\uD55C\uAE00/target", title="Different title")))

    assert publication.validate_candidate(files) == {"ok": True, "errors": []}


def test_bare_sanitized_stem_does_not_gain_publication_alias():
    files = publication.render_candidate(snapshot(
        row("source", body="[[path-target]]"), row("path/target", title="Different title")))

    assert not publication.validate_candidate(files)["ok"]
