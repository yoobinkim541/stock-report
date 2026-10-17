"""Bounded, read-only public source notes and precommit dependency validation."""
from __future__ import annotations

import json
from datetime import datetime
from typing import TypedDict

from agent_console import wiki
from reports.wiki_source_integration import financial_terms
from reports.wiki_source_journal import Journal
from reports.wiki_source_revision import build_revision
from scripts.run_public_wiki_pilot import source_allowed as is_public, public_target_ids


class TopicNote(TypedDict):
    page_id: str
    revision_id: str
    title: str
    claims: list[dict]
    terms: list[str]
    limitations: list[str]
    canonical_url: str


class TopicDependency(TypedDict):
    page_id: str
    revision_id: str
    surface: str
    page_fingerprint: str
    raw_sha256: str
    text_sha256: str
    compile_sha256: str


class TopicContext(TypedDict):
    """Local selection state; never serialize wholesale into a model prompt."""

    notes: list[TopicNote]
    dependencies: list[TopicDependency]
    omitted: list[dict]


def _encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str or not item.strip() for item in value):
        raise ValueError("context_fields_invalid")
    return list(dict.fromkeys(value))


def _text(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("context_fields_invalid")
    return value


def _terms(terms: list[str]) -> set[str]:
    return financial_terms(terms)


def topic_context_model_input(context: TopicContext) -> dict:
    """Project a trusted selection result into detached, notes-only model data.

    This is not a verification/proof substitute. Task 2 callers must first call
    revalidate_topic_context with current records/time, then use this projection,
    never serialize context itself. Dependencies and omission diagnostics are local.
    """
    notes = []
    for note in context["notes"]:
        projected = {key: _text(note[key]) for key in
                     ("page_id", "revision_id", "title", "canonical_url")}
        projected["terms"] = _strings(note["terms"])
        projected["limitations"] = _strings(note["limitations"])
        claims = []
        for claim in note["claims"]:
            copied = {key: _text(claim[key]) for key in
                      ("claim_id", "source_revision_id", "text", "as_of", "scope", "knowledge_state")}
            copied["competing_claim_ids"] = _strings(claim["competing_claim_ids"])
            anchor = claim["anchor"]
            if anchor is not None:
                if type(anchor["start"]) is not int or type(anchor["end"]) is not int:
                    raise ValueError("context_fields_invalid")
                anchor = {"start": anchor["start"], "end": anchor["end"], "quote": _text(anchor["quote"])}
            copied["anchor"] = anchor
            claims.append(copied)
        projected["claims"] = claims
        notes.append(projected)
    return {"notes": notes}


def _verified_source(journal: Journal, revision_id: str, now: datetime) -> tuple[dict, dict, dict]:
    receipt = journal.load_source(revision_id)
    if not receipt or receipt["compile_state"] != "compiled" or not is_public(receipt["source"]):
        raise ValueError("context_source_unavailable")
    source = receipt["source"]
    # Rebuild the identity from retained archive bytes, not from receipt assertions.
    verified = build_revision({"source": source["provider"], "url": source["canonical_url"],
        "raw_path": source["raw_ref"], "text_path": source["text_ref"],
        "manifest_path": source["manifest_ref"], "raw_sha256": source["raw_sha256"],
        "text_sha256": source["text_sha256"], "extraction_scope": source["scope"],
        "content_kind": source.get("content_kind"), "collected_at": source.get("collected_at", "")},
        now=now, allow_chunking=True)
    for key in ("revision_id", "protocol_version", "provider", "canonical_url", "raw_sha256",
                "text_sha256", "scope", "provenance_class"):
        if verified[key] != source.get(key):
            raise ValueError("context_source_changed")
    if verified["revision_id"] != revision_id or not is_public(verified):
        raise ValueError("context_source_changed")
    return verified, journal.load_compilation(receipt), receipt


def _note(page_id: str, revision_id: str, *, journal: Journal, records: list[dict],
          now: datetime, surface: str) -> tuple[TopicNote, TopicDependency]:
    source, compiled, receipt = _verified_source(journal, revision_id, now)
    if page_id not in public_target_ids(journal, {revision_id}, records=records):
        raise ValueError("context_page_changed")
    snapshot = wiki.semantic_snapshot(page_id, records=records)
    page = snapshot["page"]
    if (page["surface"] != surface or page["status"] == "archived"
            or page.get("parent_page_id") or "compiled_source" not in page["tags"]):
        raise ValueError("context_page_unavailable")
    claims = []
    for claim in compiled["claims"]:
        if not isinstance(claim, dict) or claim.get("source_revision_id") != revision_id:
            raise ValueError("context_claim_invalid")
        projected = {key: _text(claim.get(key)) for key in
                     ("claim_id", "source_revision_id", "text", "as_of", "scope")}
        projected["knowledge_state"] = "draft"
        projected["competing_claim_ids"] = _strings(claim.get("competing_claim_ids", []))
        anchor = claim.get("anchor")
        if anchor is not None:
            start, end, quote = anchor["start"], anchor["end"], anchor["quote"]
            if (type(start) is not int or type(end) is not int or type(quote) is not str
                    or not 0 <= start < end <= len(source["body"])
                    or source["body"][start:end] != quote):
                raise ValueError("context_anchor_invalid")
            anchor = {"start": start, "end": end, "quote": quote}
        projected["anchor"] = anchor
        claims.append(projected)
    ids = [claim["claim_id"] for claim in claims]
    if not claims or len(ids) != len(set(ids)) or any(
            other not in ids or other == claim["claim_id"]
            for claim in claims for other in claim["competing_claim_ids"]):
        raise ValueError("context_claim_invalid")
    aliases = compiled.get("verified_aliases", [])
    if not isinstance(aliases, list):
        raise ValueError("context_fields_invalid")
    terms = _strings(compiled["terms"])
    terms = list(dict.fromkeys([*terms, *[alias for alias in aliases if type(alias) is str and alias.strip()]]))
    note: TopicNote = {"page_id": page_id, "revision_id": revision_id,
        "title": _text(compiled.get("title") or page["title"]), "claims": claims,
        "terms": terms, "limitations": _strings(compiled["limitations"]),
        "canonical_url": source["canonical_url"]}
    dependency: TopicDependency = {"page_id": page_id, "revision_id": revision_id, "surface": surface,
        "page_fingerprint": snapshot["revision_hash"], "raw_sha256": source["raw_sha256"],
        "text_sha256": source["text_sha256"], "compile_sha256": receipt["compile_sha256"]}
    return note, dependency


def select_topic_context(compiled: dict, *, journal: Journal, records: list[dict],
                         now: datetime, surface: str) -> TopicContext:
    if not surface or surface == "all":
        raise ValueError("routing_surface_required")
    context: TopicContext = {"notes": [], "dependencies": [], "omitted": []}
    candidates = set()
    for path in sorted((journal.root / "operations").glob("*.json")):
        operation = journal._read(path)
        if operation and operation.get("stage") == "committed":
            for page_id in operation["batch"]["target_ids"]:
                candidates.add((page_id, operation["revision_id"]))
    # Also account for source notes whose public receipt was removed/uncommitted.
    represented = {page_id for page_id, _ in candidates}
    for row in records:
        if "compiled_source" in row.get("tags", []) and row.get("id") not in represented:
            context["omitted"].append({"page_id": row["id"], "reason": "unverified"})
    ranked = []
    wanted = _terms([*_strings(compiled["terms"]), *[
        alias for alias in compiled.get("verified_aliases", []) if type(alias) is str and alias.strip()]])
    for page_id, revision_id in sorted(candidates):
        reason = "current_revision" if revision_id == compiled["revision_id"] else ""
        if not reason:
            try:
                note, dependency = _note(page_id, revision_id, journal=journal, records=records,
                                         now=now, surface=surface)
                overlap = len(wanted & _terms(note["terms"]))
                if overlap:
                    ranked.append((-overlap, page_id, revision_id, note, dependency))
                    continue
                reason = "unrelated"
            except (ValueError, OSError, KeyError, TypeError, UnicodeError):
                reason = "unverified"
        context["omitted"].append({"page_id": page_id, "revision_id": revision_id, "reason": reason})
    seen = set()
    for _, page_id, revision_id, note, dependency in sorted(ranked):
        if revision_id in seen:
            reason = "duplicate_revision"
        else:
            seen.add(revision_id)
            if len(_encoded(note)) > 12000:
                reason = "note_budget"
            elif len(context["notes"]) >= 4:
                reason = "note_limit"
            elif len(_encoded([*context["notes"], note])) > 24000:
                reason = "total_budget"
            else:
                context["notes"].append(note)
                context["dependencies"].append(dependency)
                continue
        context["omitted"].append({"page_id": page_id, "revision_id": revision_id, "reason": reason})
    context["omitted"].sort(key=lambda item: (item["page_id"], item.get("revision_id", ""), item["reason"]))
    return context


def revalidate_topic_context(context: TopicContext, *, journal: Journal, records: list[dict],
                             now: datetime) -> None:
    """Caller must hold ordered dependency/target locks and pass freshly read rows."""
    try:
        notes, dependencies = context["notes"], context["dependencies"]
        if len(notes) != len(dependencies) or len(notes) > 4 or len(_encoded(notes)) > 24000:
            raise ValueError()
        seen = set()
        for note, dependency in zip(notes, dependencies):
            if note["revision_id"] in seen or len(_encoded(note)) > 12000:
                raise ValueError()
            seen.add(note["revision_id"])
            current_note, current_dependency = _note(note["page_id"], note["revision_id"], journal=journal,
                records=records, now=now, surface=dependency["surface"])
            if note != current_note or dependency != current_dependency:
                raise ValueError()
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("topic_context_changed") from exc
