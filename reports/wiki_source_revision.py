"""Verified source identity, independent of collection deliveries and wiki routing."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit

from reports import raw_archive, wiki_distillation
from reports.evidence_cards import event_to_evidence_card

PROTOCOL_VERSION = 1


class SourceRevision(TypedDict):
    revision_id: str
    protocol_version: int
    provider: str
    canonical_url: str
    raw_sha256: str
    text_sha256: str
    scope: str
    provenance_class: str
    body: str
    title: str
    published_at: str
    collected_at: str
    ingested_at: str
    raw_ref: str
    text_ref: str
    manifest_ref: str
    observed_evidence_ids: list[str]
    role: str
    content_kind: str


class DiscoveryResult(TypedDict):
    eligible: list[SourceRevision]
    unavailable: list[dict]
    pending_chunking: list[dict]


class SourceGateError(ValueError):
    def __init__(self, reason: str, *, details: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def _hash_json(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _provider(value: object) -> str:
    provider = str(value or "").strip().lower()
    return {"saveticker_article": "saveticker", "arca:stock": "arca"}.get(provider, provider)


def _canonical_url(value: object) -> str:
    try:
        url = urlsplit(str(value or ""))
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.fragment or url.port not in (None, 443)):
            raise ValueError()
        return urlunsplit(("https", url.netloc.lower(), url.path, url.query, ""))
    except ValueError as exc:
        raise SourceGateError("source_url_invalid") from exc


def _archive_path(value: object, directory: str) -> Path:
    try:
        path = Path(str(value or "")).expanduser().resolve()
        path.relative_to((raw_archive.reports_root() / directory).resolve())
        return path
    except (ValueError, OSError, RuntimeError) as exc:
        raise SourceGateError("archive_path_invalid") from exc


def build_revision(event: dict, *, now: datetime, allow_chunking: bool = False) -> SourceRevision:
    raw_path = _archive_path(event.get("raw_path"), "raw")
    text_path = _archive_path(event.get("text_path"), "text")
    manifest_path = _archive_path(event.get("manifest_path"), "raw")
    manifest_text = raw_archive.read_raw_text(manifest_path)
    try:
        manifest = json.loads(manifest_text) if manifest_text else None
        if not isinstance(manifest, dict):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise SourceGateError("manifest_unavailable") from exc
    for field, path, directory in (("raw_path", raw_path, "raw"),
                                   ("text_path", text_path, "text"),
                                   ("manifest_path", manifest_path, "raw")):
        if _archive_path(manifest.get(field), directory) != path:
            raise SourceGateError("manifest_path_mismatch")

    provider = _provider(event.get("source"))
    manifest_provider = _provider(manifest.get("source"))
    if not provider or not (provider == manifest_provider or
                            provider.startswith("telegram:") and manifest_provider == "telegram"):
        raise SourceGateError("provider_mismatch")
    url = _canonical_url(event.get("url"))
    if url != _canonical_url(manifest.get("source_url") or manifest.get("url")):
        raise SourceGateError("url_mismatch")
    scope = str(manifest.get("source_text_scope") or manifest.get("extraction_scope") or "")
    event_scope = str(event.get("source_text_scope") or event.get("extraction_scope") or scope)
    if not scope or event_scope != scope:
        raise SourceGateError("scope_mismatch")
    complete = (manifest.get("text_complete") is True or
                manifest.get("body_coverage") in {"full", "complete"} or
                manifest.get("fulltext_status") == "full")
    if (not complete or manifest.get("text_complete") is False or
            manifest.get("body_coverage") in {"preview", "partial", "truncated"}):
        raise SourceGateError("body_incomplete")
    try:
        expires = datetime.fromisoformat(str(manifest["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None or now.tzinfo is None:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise SourceGateError("expiry_unverifiable") from exc
    if expires <= now:
        raise SourceGateError("raw_expired")

    raw = raw_archive.read_raw_artifact(raw_path)
    if not isinstance(raw, bytes):
        raise SourceGateError("raw_unavailable")
    raw_hash = hashlib.sha256(raw).hexdigest()
    if manifest.get("sha256") != raw_hash or event.get("raw_sha256", raw_hash) != raw_hash:
        raise SourceGateError("raw_hash_mismatch")
    text_bytes = raw_archive.read_raw_artifact(text_path)
    if not isinstance(text_bytes, bytes):
        raise SourceGateError("text_unavailable")
    try:
        body = text_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise SourceGateError("text_encoding_invalid") from exc
    if not body.strip():
        raise SourceGateError("text_unavailable")
    text_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if any(owner.get("text_sha256", text_hash) != text_hash for owner in (event, manifest)):
        raise SourceGateError("text_hash_mismatch")
    scan = body.lower() + "\n" + raw[:100000].decode("utf-8", errors="ignore").lower()
    if re.search(r"cf-chl-|cf-mitigated|cloudflare challenge|just a moment|"
                 r"(?:sign|log)\s*in to (continue|read)|"
                 r"\uB85C\uADF8\uC778\s*\uD6C4\s*(\uC774\uC6A9|\uC5F4\uB78C)", scan):
        raise SourceGateError("reauth_required")

    classification = event.get("classification")
    classified_kind = classification.get("kind") if isinstance(classification, dict) else None
    default_kind = "community_signal" if provider.startswith("telegram:") and scope == "public_message" else "article"
    content_kind = str(event.get("content_kind") or event.get("kind") or classified_kind or default_kind)
    parsed_url = urlsplit(url)
    if provider in {"saveticker", "saveticker_report_pdf"} and (
            parsed_url.hostname not in {"saveticker.com", "www.saveticker.com"} or
            parsed_url.query or
            provider == "saveticker" and not re.fullmatch(r"/news/[A-Za-z0-9_-]+/?", parsed_url.path)):
        raise SourceGateError("source_class_ineligible")
    article_scope = "article_fulltext" if scope in {"authorized_article", "rendered_article"} else scope
    article = {"text": body, "url": url, "provenance": provider,
               "text_complete": True, "content_kind": content_kind,
               "source_text_scope": article_scope}
    if provider == "fred" and content_kind == "series_notes":
        article["content_kind"] = "series_notes"
    if not wiki_distillation._article_is_eligible(article):
        raise SourceGateError("source_class_ineligible")
    source_class = wiki_distillation._full_body_source_class(article) or (
        "ocr_report" if scope == "ocr_extracted_text" else "editorial")
    identity = {"protocol_version": PROTOCOL_VERSION, "provider": provider,
                "canonical_url": url, "raw_sha256": raw_hash, "text_sha256": text_hash,
                "scope": scope, "provenance_class": source_class}
    revision: SourceRevision = {
        **identity, "revision_id": _hash_json(identity), "body": body,
        "title": str(event.get("title") or manifest.get("title") or ""),
        "published_at": str(event.get("published_at") or ""),
        "collected_at": str(event.get("source_collected_at") or event.get("collected_at") or ""),
        "ingested_at": str(event.get("collected_at") or manifest.get("fetched_at") or ""),
        "raw_ref": str(raw_path), "text_ref": str(text_path), "manifest_ref": str(manifest_path),
        "observed_evidence_ids": [event_to_evidence_card(event, now=now).id], "role": "support",
        "content_kind": content_kind,
    }
    if len(body) > wiki_distillation.MAX_SOURCE_ARTICLE_CHARS and not allow_chunking:
        raise SourceGateError("source_budget_exceeded", details={k: v for k, v in revision.items() if k != "body"})
    return revision


def discover_revisions(events: list[dict], *, now: datetime,
                       evidence_roles: dict[str, str] | None = None,
                       allow_chunking: bool = False) -> DiscoveryResult:
    result: DiscoveryResult = {"eligible": [], "unavailable": [], "pending_chunking": []}
    groups: dict[tuple[str, str], SourceRevision] = {}
    for event in events:
        evidence_id = event_to_evidence_card(event, now=now).id
        try:
            revision = build_revision(event, now=now, allow_chunking=allow_chunking)
        except SourceGateError as exc:
            item = {"discovery_id": evidence_id, "reason": exc.reason,
                    "observed_evidence_ids": [evidence_id], **exc.details}
            result["pending_chunking" if exc.reason == "source_budget_exceeded" else "unavailable"].append(item)
            continue
        except (OSError, UnicodeError) as exc:
            result["unavailable"].append({"discovery_id": evidence_id,
                                          "observed_evidence_ids": [evidence_id],
                                          "reason": "archive_read_failed"})
            continue
        role = (evidence_roles or {}).get(evidence_id, "support")
        if role not in {"support", "conflict"}:
            raise ValueError("evidence_role_invalid")
        revision["role"] = role
        key = (revision["revision_id"], role)
        if key in groups:
            groups[key]["observed_evidence_ids"] = list(dict.fromkeys(
                [*groups[key]["observed_evidence_ids"], evidence_id]))
        else:
            groups[key] = revision
    result["eligible"] = list(groups.values())
    return result
