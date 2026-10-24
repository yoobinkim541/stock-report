"""Content-addressed public source derivatives, never mutable wiki authority."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from datetime import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import uuid

from reports import raw_archive, wiki_distillation
from reports.wiki_source_chunks import _aggregate, source_chunks
from reports.wiki_source_compiler import validate_compilation
from reports.wiki_source_journal import Journal, _encoded, _identity
from reports.wiki_source_revision import PROTOCOL_VERSION, _canonical_url, _provider
from scripts.run_public_wiki_pilot import is_public as _is_public_message, source_allowed


PROJECTION_VERSION = 1
_FILES = {"note.json", "note.md", "manifest.json"}
_CLAIM_FIELDS = {"claim_id", "source_revision_id", "text", "as_of", "scope", "anchor",
                 "competing_claim_ids", "knowledge_state"}
_COMPILED_FIELDS = {"revision_id", "body", "claims", "terms", "limitations", "skip_reason", "title"}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bytes(value: dict) -> bytes:
    return _encoded(value).encode("utf-8")


def _object(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("source_note_duplicate_key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("source_note_nonfinite_json")

    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if type(result) is not dict:
        raise ValueError("source_note_object_required")
    # Also reject overflowed JSON floats (e.g. 1e999).
    _bytes(result)
    return result


def _text(value) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("source_note_text_invalid")
    return value


def _strings(value) -> list[str]:
    if (type(value) is not list or any(type(item) is not str or not item.strip() for item in value)
            or len(value) != len(set(value))):
        raise ValueError("source_note_list_invalid")
    return value


def _absolute(value) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError("source_note_path_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise ValueError("source_note_path_invalid")
    return path


@contextmanager
def _directory(path: Path):
    """Walk from / with no-follow descriptors, including the configured root."""
    path = _absolute(path)
    fd = os.open("/", _DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _subdirectory(root: int, components, *, create=False):
    fd = os.dup(root)
    try:
        for component in components:
            if not component or component in {".", ".."} or "/" in component:
                raise ValueError("source_note_path_invalid")
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_file(parent: int, name: str) -> bytes:
    fd = os.open(name, _READ, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("source_note_regular_file_required")
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("source_note_file_changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _relative_read(root: int, relative: Path) -> bytes:
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("source_note_path_invalid")
    with _subdirectory(root, relative.parts[:-1]) as parent:
        return _read_file(parent, relative.name)


def _archive_read(root: int, path: Path, category: str) -> bytes:
    try:
        return _relative_read(root, Path(category) / path)
    except FileNotFoundError:
        pass
    # Cold archive lookup is the existing deterministic member contract, but
    # both the bundle and its ancestors are opened without following symlinks.
    if len(path.parts) != 5:
        raise ValueError("source_note_archive_unavailable")
    provider, year, month, day, filename = path.parts
    if (not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"\d{2}", month)
            or not re.fullmatch(r"\d{2}", day)):
        raise ValueError("source_note_archive_unavailable")
    member = f'{"manifest" if filename.endswith(".manifest.json") else category}/{filename}'
    directory = ("raw", raw_archive.BUNDLE_DIRNAME, provider, year, month)
    with _subdirectory(root, directory) as bundles:
        for name in sorted(os.listdir(bundles)):
            if not re.fullmatch(rf"{day}-ttl\d+\.tar\.gz", name):
                continue
            blob = _read_file(bundles, name)
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
                found = []
                for info in archive:
                    if info.name == member:
                        if not info.isfile():
                            raise ValueError("source_note_archive_member_invalid")
                        with archive.extractfile(info) as stream:
                            found.append(stream.read())
                if len(found) > 1:
                    raise ValueError("source_note_archive_member_ambiguous")
                if found:
                    return found[0]
    raise ValueError("source_note_archive_unavailable")


def _receipt(root: int, revision_id: str) -> tuple[dict, dict]:
    receipt = _object(_relative_read(root, Path("sources") / f"{_identity(revision_id)}.json"))
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt.get("revision_id") != revision_id
            or receipt.get("compile_state") not in {"compiled", "skipped"}
            or type(receipt.get("source")) is not dict
            or type(receipt.get("observed_evidence_ids")) is not list
            or type(receipt.get("evidence_by_role")) is not dict
            or type(receipt.get("routing")) is not dict):
        raise ValueError("source_note_receipt_invalid")
    return receipt, receipt["source"]


def _public(source: dict) -> None:
    provider = source.get("provider")
    if (type(provider) is not str or not (
            provider.startswith("telegram:") or provider in {"arca", "fred", "saveticker", "saveticker_report_pdf"})
            or source.get("scope") == "authorized_article" or not source_allowed(source)):
        raise ValueError("source_note_policy_unavailable")


def _source(source: dict, revision_id: str, *, now: datetime) -> dict:
    _public(source)
    root = _absolute(raw_archive.reports_root())
    relative = {}
    for key, category in (("raw_ref", "raw"), ("text_ref", "text"), ("manifest_ref", "raw")):
        relative[key] = _absolute(source[key]).relative_to(root / category)
    with _directory(root) as archive:
        manifest = _object(_archive_read(archive, relative["manifest_ref"], "raw"))
        for key, field in (("raw_ref", "raw_path"), ("text_ref", "text_path"),
                           ("manifest_ref", "manifest_path")):
            if _absolute(manifest[field]) != _absolute(source[key]):
                raise ValueError("source_note_manifest_path_mismatch")
        for owner in (source, manifest):
            if (owner.get("visibility", "public") != "public" or owner.get("requires_login", False) is not False
                    or owner.get("private", False) is not False):
                raise ValueError("source_note_nonpublic")
        # Private opt-in is not public proof. Only the strict public-message
        # contract can stand without explicit retained no-login metadata.
        if not _is_public_message(source) and not (
                manifest.get("visibility") == "public" and manifest.get("requires_login") is False
                and manifest.get("private") is False):
            raise ValueError("source_note_public_proof_unavailable")
        expires = datetime.fromisoformat(_text(manifest.get("expires_at")).replace("Z", "+00:00"))
        if expires.tzinfo is None or now.tzinfo is None or expires <= now:
            raise ValueError("source_note_expired")
        if (not (manifest.get("text_complete") is True or manifest.get("body_coverage") in {"full", "complete"}
                 or manifest.get("fulltext_status") == "full")
                or manifest.get("text_complete") is False
                or manifest.get("body_coverage") in {"preview", "partial", "truncated"}):
            raise ValueError("source_note_body_incomplete")
        raw = _archive_read(archive, relative["raw_ref"], "raw")
        text = _archive_read(archive, relative["text_ref"], "text")
    body = text.decode("utf-8")
    _text(body)
    if (manifest.get("sha256") != _sha(raw) or source.get("raw_sha256") != _sha(raw)
            or source.get("text_sha256") != _sha(text)
            or manifest.get("text_sha256", _sha(text)) != _sha(text)):
        raise ValueError("source_note_archive_hash_mismatch")
    scope = manifest.get("source_text_scope") or manifest.get("extraction_scope")
    provider = _provider(manifest.get("source"))
    url = _canonical_url(manifest.get("source_url") or manifest.get("url"))
    if (source.get("scope") != scope or source.get("canonical_url") != url
            or not (source["provider"] == provider or source["provider"].startswith("telegram:") and provider == "telegram")):
        raise ValueError("source_note_archive_identity_mismatch")
    scan = body.lower() + "\n" + raw[:100000].decode("utf-8", errors="ignore").lower()
    if re.search(r"cf-chl-|cf-mitigated|cloudflare challenge|just a moment|"
                 r"(?:sign|log)\s*in to (continue|read)|"
                 r"\uB85C\uADF8\uC778\s*\uD6C4\s*(\uC774\uC6A9|\uC5F4\uB78C)", scan):
        raise ValueError("source_note_login_or_challenge")
    article = {"text": body, "url": url, "provenance": source["provider"], "text_complete": True,
               "content_kind": _text(source.get("content_kind")),
               "source_text_scope": "article_fulltext" if scope in {"authorized_article", "rendered_article"} else scope}
    if not wiki_distillation._article_is_eligible(article):
        raise ValueError("source_note_source_class_invalid")
    if source["provider"] in {"saveticker", "saveticker_report_pdf"}:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if (parsed.hostname not in {"saveticker.com", "www.saveticker.com"} or parsed.query
                or source["provider"] == "saveticker" and not re.fullmatch(r"/news/[A-Za-z0-9_-]+/?", parsed.path)):
            raise ValueError("source_note_source_class_invalid")
    provenance = wiki_distillation._full_body_source_class(article) or (
        "ocr_report" if scope == "ocr_extracted_text" else "editorial")
    identity = {"protocol_version": PROTOCOL_VERSION, "provider": source["provider"],
        "canonical_url": url, "raw_sha256": _sha(raw), "text_sha256": _sha(text),
        "scope": scope, "provenance_class": provenance}
    if (_sha(_bytes(identity)) != revision_id or source.get("revision_id") != revision_id
            or type(source.get("protocol_version")) is not int
            or any(source.get(key) != value for key, value in identity.items())):
        raise ValueError("source_note_revision_mismatch")
    return {**source, "body": body}


def _compilation(root: int, journal_root: Path, receipt: dict) -> dict:
    digest = _identity(receipt["compile_sha256"])
    path = _absolute(receipt["compile_ref"])
    if path != journal_root / "compiled" / f"{digest}.json":
        raise ValueError("source_note_compilation_path_invalid")
    blob = _relative_read(root, path.relative_to(journal_root))
    compiled = _object(blob)
    if _sha(blob) != digest or compiled.get("revision_id") != receipt["revision_id"]:
        raise ValueError("source_note_compilation_hash_mismatch")
    return compiled


def _validate_single(compiled: dict, source: dict) -> dict:
    if set(compiled) - _COMPILED_FIELDS or compiled.get("revision_id") != source["revision_id"]:
        raise ValueError("source_note_compilation_fields_invalid")
    if compiled.get("skip_reason"):
        checked = validate_compilation({"action": "skip", "skip_reason": compiled["skip_reason"]}, source)
    else:
        claims = compiled.get("claims")
        if type(claims) is not list or not claims:
            raise ValueError("source_note_claims_missing")
        for claim in claims:
            if type(claim) is not dict or set(claim) != _CLAIM_FIELDS:
                raise ValueError("source_note_claim_fields_invalid")
            _strings(claim["competing_claim_ids"])
        payload = {key: compiled[key] for key in ("body", "terms", "limitations")}
        payload.update(action="create", claims=[{key: claim[key] for key in
            ("text", "as_of", "scope", "anchor", "competing_claim_ids")} for claim in claims])
        if "title" in compiled:
            payload["title"] = compiled["title"]
        checked = validate_compilation(payload, source)
    if _bytes(checked) != _bytes(compiled):
        raise ValueError("source_note_compilation_invalid")
    return checked


def _validated(root: int, journal_root: Path, compiled: dict, source: dict) -> dict:
    aliases = compiled.get("verified_aliases", [])
    _strings(aliases)
    core = {key: value for key, value in compiled.items() if key != "verified_aliases"}
    claims = core.get("claims")
    if type(claims) is not list or not claims or any(type(claim) is not dict for claim in claims):
        raise ValueError("source_note_claims_missing")
    normal_ids = [_sha(f'{source["revision_id"]}:{index}'.encode()) for index in range(len(claims))]
    # Stored claim identity, not body length, distinguishes the two existing
    # formats. Neither path may discard or normalize an invalid stored field.
    if [claim.get("claim_id") for claim in claims] != normal_ids:
        parts = source_chunks(source)
        outputs = []
        child_root = journal_root / "chunks" / source["revision_id"]
        with _subdirectory(root, ("chunks", source["revision_id"])) as child:
            for part in parts:
                receipt, stored = _receipt(child, part["revision_id"])
                for field in ("revision_id", "chunk_parent_revision_id", "source_span", "text_sha256"):
                    if stored.get(field) != part.get(field):
                        raise ValueError("source_note_chunk_identity_invalid")
                output = _validate_single(_compilation(child, child_root, receipt), part)
                if (receipt["compile_state"] == "skipped") != bool(output["skip_reason"]):
                    raise ValueError("source_note_chunk_state_invalid")
                outputs.append(output)
        if _bytes(_aggregate(source, parts, outputs)) != _bytes(core):
            raise ValueError("source_note_chunk_aggregate_invalid")
    else:
        _validate_single(core, source)
    if core.get("skip_reason") or not core.get("claims"):
        raise ValueError("source_note_knowledge_unavailable")
    return {**core, "verified_aliases": aliases}


def _build(root: int, journal_root: Path, revision_id: str, now: datetime) -> dict:
    receipt, stored = _receipt(root, revision_id)
    if receipt["compile_state"] != "compiled":
        raise ValueError("source_note_knowledge_unavailable")
    source = _source(stored, revision_id, now=now)
    compiled = _validated(root, journal_root, _compilation(root, journal_root, receipt), source)
    note = {key: source[key] for key in ("revision_id", "canonical_url", "provider", "scope", "provenance_class")}
    for key in ("published_at", "collected_at"):
        value = source.get(key, "")
        if type(value) is not str:
            raise ValueError("source_note_date_invalid")
        note[key] = value
    note.update(projection_version=PROJECTION_VERSION, source_protocol_version=source["protocol_version"],
        title=compiled.get("title", ""), body=compiled["body"], claims=compiled["claims"],
        terms=compiled["terms"], verified_aliases=compiled["verified_aliases"], limitations=compiled["limitations"])
    if type(note["title"]) is not str:
        raise ValueError("source_note_title_invalid")
    note_json = _bytes(note)
    heading = f'# {note["title"]}\n\n' if note["title"] else ""
    note_md = (f'{heading}{note["body"]}\n\n'
               f'## Retained Source Knowledge\n\n```json\n{note_json.decode("utf-8")}\n```\n').encode("utf-8")
    identity = {"identity_version": 1, "source_protocol_version": source["protocol_version"],
        "revision_id": revision_id, "projection_version": PROJECTION_VERSION, "canonical_url": source["canonical_url"],
        "raw_sha256": source["raw_sha256"], "text_sha256": source["text_sha256"],
        "compile_sha256": receipt["compile_sha256"], "note_json_sha256": _sha(note_json),
        "note_md_sha256": _sha(note_md)}
    note_id = _sha(_bytes(identity))
    manifest = _bytes({"schema_version": 1, "note_id": note_id, **identity})
    return {**identity, "note_id": note_id, "manifest_sha256": _sha(manifest), "note": note,
            "note_json_bytes": note_json, "note_md_bytes": note_md, "manifest_bytes": manifest}


def _verify(directory: int, expected: dict) -> None:
    if set(os.listdir(directory)) != _FILES:
        raise ValueError("source_note_artifacts_invalid")
    for filename, key in (("note.json", "note_json_bytes"), ("note.md", "note_md_bytes"),
                          ("manifest.json", "manifest_bytes")):
        if _read_file(directory, filename) != expected[key]:
            raise ValueError("source_note_artifact_changed")


@contextmanager
def _source_lock(root: int, revision_id: str):
    # Same sidecar and flock as Journal.source_lock, without path reopening or
    # truncation. This interoperates with existing workers' lock ordering.
    with _subdirectory(root, ("source-locks",), create=True) as locks:
        fd = os.open(f"{revision_id}.json.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=locks)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("source_note_lock_invalid")
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("source_note_lock_timeout")
                    time.sleep(0.1)
            yield
        finally:
            os.close(fd)


def _rename_noreplace(parent: int, staging: str, note_id: str) -> None:
    # Linux renameat2 provides atomic publication without replacing even an
    # empty destination directory introduced by another writer.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(staging), parent, os.fsencode(note_id), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _write(directory: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("source_note_short_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(root: int, pin: dict) -> None:
    with _subdirectory(root, ("source-notes",), create=True) as notes:
        os.fsync(root)
        note_id = pin["note_id"]
        try:
            with _subdirectory(notes, (note_id,)) as existing:
                _verify(existing, pin)
                os.fsync(existing)
            os.fsync(notes)
            return
        except FileNotFoundError:
            # Only a missing final directory is creatable; missing files in an
            # existing artifact must never trigger replacement.
            if note_id in os.listdir(notes):
                raise ValueError("source_note_artifact_incomplete")
        staging = ".staging-" + uuid.uuid4().hex
        os.mkdir(staging, mode=0o700, dir_fd=notes)
        with _subdirectory(notes, (staging,)) as directory:
            for name, key in (("note.json", "note_json_bytes"), ("note.md", "note_md_bytes"),
                              ("manifest.json", "manifest_bytes")):
                _write(directory, name, pin[key])
            _verify(directory, pin)
            os.fsync(directory)
        try:
            _rename_noreplace(notes, staging, note_id)
        except FileExistsError:
            with _subdirectory(notes, (note_id,)) as existing:
                _verify(existing, pin)
        os.fsync(notes)
        with _subdirectory(notes, (note_id,)) as final:
            _verify(final, pin)


def _result(pin: dict) -> dict:
    return {key: value for key, value in pin.items()
            if key not in {"note", "note_json_bytes", "note_md_bytes", "manifest_bytes"}}


def _same(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return left.keys() == right.keys() and all(_same(left[key], value) for key, value in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def materialize_source_note(revision_id: str, *, journal: Journal, now: datetime,
                            apply: bool = False) -> dict:
    """Validate retained evidence; optionally publish its deterministic derivative.

    Dry runs create neither locks nor staging. Results are local identity/hash
    descriptors and are identical on preview, publication and verified reuse.
    """
    _identity(revision_id)
    try:
        journal_root = _absolute(journal.root)
        with _directory(journal_root) as root:
            if apply:
                with _source_lock(root, revision_id):
                    pin = _build(root, journal_root, revision_id, now)
                    _publish(root, pin)
            else:
                pin = _build(root, journal_root, revision_id, now)
                try:
                    with _subdirectory(root, ("source-notes", pin["note_id"])) as existing:
                        _verify(existing, pin)
                except FileNotFoundError:
                    # A preview may describe an unpublished artifact, but does
                    # not silently accept incomplete published artifacts.
                    with _subdirectory(root, ()) as parent:
                        try:
                            with _subdirectory(parent, ("source-notes",)) as notes:
                                if pin["note_id"] in os.listdir(notes):
                                    raise ValueError("source_note_artifact_incomplete")
                        except FileNotFoundError:
                            pass
            return _result(pin)
    except (OSError, KeyError, TypeError, UnicodeError, tarfile.TarError) as exc:
        raise ValueError("source_note_unavailable") from exc


def pin_source_note(note_id: str, *, journal: Journal, now: datetime) -> dict:
    """Return detached parsed knowledge plus exact verified bytes and hashes.

    No page IDs, local paths, mutable editorial records or provider calls enter
    the pin. Call revalidate_source_note immediately before each new use.
    """
    _identity(note_id)
    try:
        journal_root = _absolute(journal.root)
        with _directory(journal_root) as root:
            with _subdirectory(root, ("source-notes", note_id)) as directory:
                manifest = _object(_read_file(directory, "manifest.json"))
                if (manifest.get("note_id") != note_id or type(manifest.get("projection_version")) is not int
                        or manifest["projection_version"] != PROJECTION_VERSION):
                    raise ValueError("source_note_manifest_invalid")
                revision_id = _identity(manifest["revision_id"])
                pin = _build(root, journal_root, revision_id, now)
                if pin["note_id"] != note_id:
                    raise ValueError("source_note_identity_changed")
                _verify(directory, pin)
                return pin
    except (OSError, KeyError, TypeError, UnicodeError, tarfile.TarError) as exc:
        raise ValueError("source_note_unavailable") from exc


def revalidate_source_note(pin: dict, *, journal: Journal, now: datetime) -> None:
    """Fail closed on any pin mutation, evidence change, expiry or revocation."""
    try:
        if type(pin) is not dict:
            raise ValueError("source_note_pin_invalid")
        current = pin_source_note(pin["note_id"], journal=journal, now=now)
        if (not _same(pin, current)
                or _bytes(pin["note"]) != current["note_json_bytes"]):
            raise ValueError("source_note_pin_changed")
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("source_note_pin_invalid") from exc
