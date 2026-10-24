"""Read-only public citations, separate from authorization for new evidence use."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re

from agent_console import wiki_publication as publication
from reports.wiki_source_journal import Journal
from reports.wiki_source_notes import pin_source_note


def valid_note_id(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unavailable(note_id: object, *, expired: bool = False) -> dict:
    result = {"availability": "expired" if expired else "unavailable"}
    if valid_note_id(note_id):
        result["note_id"] = note_id
    return result


def _citation(note_id: str, note: dict, availability: str) -> dict:
    publication._check_source_note(note)
    return {**deepcopy(note), "note_id": note_id, "availability": availability,
            "viewer_url": f"?source_note={note_id}"}


def source_note_citation(note_id: str, *, journal: Journal, now: datetime) -> dict:
    """Recheck current availability without consulting any editorial wiki page."""
    if not valid_note_id(note_id):
        return _unavailable(note_id)
    try:
        pin = pin_source_note(note_id, journal=journal, now=now)
        return {**_citation(note_id, pin["note"], "available"),
                "markdown": pin["note_md_bytes"].decode("utf-8")}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        return _unavailable(note_id, expired=str(exc) == "source_note_expired")


def frozen_source_note_citation(note_id: str, *, reader: publication.GenerationReader) -> dict:
    """Read exact paired assets from this reader only, never current/live evidence.

    Historical display says nothing about current expiry, policy or eligibility.
    The snapshot's hash-bound asset map binds both outputs to the note identity.
    """
    if not valid_note_id(note_id):
        return _unavailable(note_id)
    try:
        snapshot = json.loads(reader.read("snapshot.json"))
        publication._check_snapshot(snapshot)
        if snapshot["snapshot_sha256"] != reader.snapshot_sha256:
            raise ValueError("snapshot_identity_mismatch")
        asset = snapshot["source_note_assets"][note_id]
        expected = publication._source_note_files({note_id: asset})
        md_path, json_path = f"source-notes/{note_id}.md", f"source-notes/{note_id}.json"
        markdown, metadata = reader.read(md_path), reader.read(json_path)
        if markdown != expected[md_path] or metadata != expected[json_path]:
            raise ValueError("source_note_asset_mismatch")
        result = _citation(note_id, json.loads(metadata), "historical")
        return {**result, "current_eligibility": "not_checked",
                "viewer_url": result["viewer_url"] + f"&generation={reader.generation_id}",
                "markdown_bytes": markdown, "json_bytes": metadata}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return _unavailable(note_id)
