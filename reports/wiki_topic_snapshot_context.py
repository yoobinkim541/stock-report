"""Bounded immutable source context, independent of mutable editorial pages.

Only trusted caller routing ``market`` is supported by this public v3 mode.
Routing lives in dependencies, never in immutable source notes. Callers must
revalidate immediately before model exposure and again before commit; editable
target eligibility/surface and CAS are separate consumer responsibilities.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import TypedDict

from reports.wiki_source_integration import financial_terms
from reports.wiki_source_journal import Journal, _identity
from reports.wiki_source_notes import pin_source_note


class SnapshotNote(TypedDict):
    note_id: str
    revision_id: str
    projection_version: int
    canonical_url: str
    title: str
    provider: str
    scope: str
    provenance_class: str
    published_at: str
    collected_at: str
    terms: list[str]
    verified_aliases: list[str]
    claims: list[dict]
    limitations: list[str]


class SnapshotDependency(TypedDict):
    kind: str
    note_id: str
    revision_id: str
    projection_version: int
    identity_version: int
    source_protocol_version: int
    canonical_url: str
    manifest_sha256: str
    raw_sha256: str
    text_sha256: str
    compile_sha256: str
    note_json_sha256: str
    note_md_sha256: str
    surface: str


class SnapshotContext(TypedDict):
    notes: list[SnapshotNote]
    dependencies: list[SnapshotDependency]
    omitted: list[dict]


_NOTE_FIELDS = (
    "note_id", "revision_id", "projection_version", "canonical_url", "title",
    "provider", "scope", "provenance_class", "published_at", "collected_at",
    "terms", "verified_aliases", "claims", "limitations",
)
_DEPENDENCY_FIELDS = (
    "note_id", "revision_id", "projection_version", "identity_version",
    "source_protocol_version", "canonical_url", "manifest_sha256", "raw_sha256",
    "text_sha256", "compile_sha256", "note_json_sha256", "note_md_sha256",
)
_CLAIM_FIELDS = (
    "claim_id", "source_revision_id", "text", "as_of", "scope", "anchor",
    "competing_claim_ids", "knowledge_state",
)


def _encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _surface(surface: str) -> None:
    if type(surface) is not str or surface != "market":
        raise ValueError("routing_surface_unsupported")


def _strings(value: object) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item.strip() for item in value):
        raise ValueError("snapshot_context_fields_invalid")
    return value


def _same(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return left.keys() == right.keys() and all(_same(left[key], value) for key, value in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _model_note(note: dict) -> SnapshotNote:
    projected = {key: deepcopy(note[key]) for key in _NOTE_FIELDS}
    claims = []
    for claim in note["claims"]:
        copied = {key: deepcopy(claim[key]) for key in _CLAIM_FIELDS}
        if claim["anchor"] is not None:
            copied["anchor"] = {key: claim["anchor"][key] for key in ("start", "end", "quote")}
        claims.append(copied)
    projected["claims"] = claims
    return projected


def _candidate(pin: dict, surface: str) -> tuple[SnapshotNote, SnapshotDependency]:
    note = _model_note({**pin["note"], "note_id": pin["note_id"]})
    dependency = {key: pin[key] for key in _DEPENDENCY_FIELDS}
    dependency.update(kind="source_note_snapshot", surface=surface)
    return note, dependency


def snapshot_context_model_input(context: SnapshotContext) -> dict:
    """Return detached ``{notes: [...]}``, not local guards or diagnostics.

    This projection is not proof of eligibility. Immediately beforehand call
    revalidate_snapshot_context with the trusted journal and current time.
    Canonical JSON is sorted, finite, ensure_ascii=False, separators=(',', ':');
    limits count characters, not UTF-8 bytes (including the aggregate list).
    """
    return {"notes": [_model_note(note) for note in context["notes"]]}


def select_snapshot_context(compiled: dict, *, note_ids: list[str], journal: Journal,
                            now: datetime, surface: str) -> SnapshotContext:
    """Select only explicitly pinned, currently eligible immutable artifacts.

    Rank by descending meaningful financial-term overlap, then note/revision ID.
    Never clip: omit whole notes above 12000 canonical JSON characters, a 24000
    character aggregate list, or four selected notes. Unknown IDs are diagnostics.
    No editorial reads, artifact creation, compilation, reservations or providers.
    """
    _surface(surface)
    if type(note_ids) is not list or any(type(item) is not str for item in note_ids):
        raise ValueError("snapshot_note_ids_invalid")
    current = _identity(compiled["revision_id"])
    wanted = financial_terms([*_strings(compiled["terms"]),
                              *_strings(compiled.get("verified_aliases", []))])
    context: SnapshotContext = {"notes": [], "dependencies": [], "omitted": []}
    ranked = []
    for note_id in sorted(note_ids):
        omitted = {"note_id": note_id}
        try:
            pin = pin_source_note(note_id, journal=journal, now=now)
            note, dependency = _candidate(pin, surface)
            omitted["revision_id"] = pin["revision_id"]
            if pin["revision_id"] == current:
                reason = "current_revision"
            else:
                overlap = len(wanted & financial_terms([*note["terms"], *note["verified_aliases"]]))
                if overlap:
                    ranked.append((-overlap, note_id, pin["revision_id"], note, dependency))
                    continue
                reason = "unrelated"
        except (ValueError, OSError, KeyError, TypeError, UnicodeError):
            reason = "unverified"
        context["omitted"].append({**omitted, "reason": reason})
    seen = set()
    for _, note_id, revision_id, note, dependency in sorted(ranked):
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
        context["omitted"].append({"note_id": note_id, "revision_id": revision_id, "reason": reason})
    context["omitted"].sort(key=lambda item: (item["note_id"], item.get("revision_id", ""), item["reason"]))
    return context


def revalidate_snapshot_context(context: SnapshotContext, *, journal: Journal, now: datetime) -> None:
    """Re-pin exact Task1 bytes under current evidence policy; reject any drift.

    No pin bytes enter the context: immutable identities/hashes bind fresh pins.
    Callers supply trusted local context, not model-supplied dependencies. For
    commit, retain the outer operation/source/target/store lock ordering.
    """
    try:
        if type(context) is not dict or set(context) != {"notes", "dependencies", "omitted"}:
            raise ValueError()
        notes, dependencies = context["notes"], context["dependencies"]
        if (type(notes) is not list or type(dependencies) is not list or type(context["omitted"]) is not list
                or len(notes) != len(dependencies) or len(notes) > 4 or len(_encoded(notes)) > 24000):
            raise ValueError()
        seen = set()
        for note, dependency in zip(notes, dependencies):
            if type(note) is not dict or type(dependency) is not dict:
                raise ValueError()
            _surface(dependency["surface"])
            pin = pin_source_note(dependency["note_id"], journal=journal, now=now)
            current_note, current_dependency = _candidate(pin, dependency["surface"])
            if (pin["revision_id"] in seen or len(_encoded(note)) > 12000
                    or not _same(note, current_note) or not _same(dependency, current_dependency)):
                raise ValueError()
            seen.add(pin["revision_id"])
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("snapshot_context_changed") from exc
