"""Detached wiki candidates and durable immutable-generation publication."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit

from contextlib import contextmanager
from pathlib import Path
import os
import stat
import tempfile
import uuid

import safe_io

from . import qmd_search, shared_memory, wiki


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot_hash(snapshot: dict) -> str:
    return _sha(_json_bytes({key: value for key, value in snapshot.items()
                             if key != "snapshot_sha256"}))


def capture_snapshot() -> dict:
    """Read checked canonical wiki rows under the existing store lock, once."""
    with shared_memory._events_lock():
        records = deepcopy([row for row in shared_memory._read_jsonl_checked(
            shared_memory._paths()["events"]) if wiki._is_wiki_record(row)])
        schema_text = wiki.wiki_contract.maintenance_schema()
        snapshot = {"records": records, "captured_at": wiki._now(),
                    "schema_text": schema_text,
                    "schema_sha256": _sha(schema_text.encode("utf-8"))}
    snapshot["snapshot_sha256"] = _snapshot_hash(snapshot)
    return snapshot


def _identities(records: list[dict]) -> dict[str, str]:
    stems: dict[str, str] = {}
    ids: set[str] = set()
    for row in records:
        identity = row.get("id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("missing_page_id")
        if identity in ids:
            raise ValueError(f"duplicate_page_id: {identity}")
        ids.add(identity)
        # Match the existing Markdown/index naming contract, including its limits.
        stem = qmd_search._safe_file_stem(qmd_search._clean(identity, 100))
        if stem in stems:
            raise ValueError(f"filename_collision: {stems[stem]} / {identity}")
        stems[stem] = identity
    return stems


def _check_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_snapshot")
    if not isinstance(snapshot.get("records"), list) or any(
            not isinstance(row, dict) for row in snapshot["records"]):
        raise ValueError("invalid_snapshot_records")
    if any(not isinstance(snapshot.get(key), str) for key in (
            "schema_text", "schema_sha256", "snapshot_sha256", "captured_at")):
        raise ValueError("invalid_snapshot_metadata")
    if snapshot["schema_sha256"] != _sha(snapshot["schema_text"].encode("utf-8")):
        raise ValueError("schema_hash_mismatch")
    if snapshot["snapshot_sha256"] != _snapshot_hash(snapshot):
        raise ValueError("snapshot_hash_mismatch")


_WIKI_REFERENCE_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
_MARKDOWN_CODE_START_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*(?:\n|$)|(?<!`)(?P<inline>`+)(?!`)",
    re.MULTILINE)
_PREVIEW_CODE_START_RE = re.compile(
    r"(?P<fence>`{3,}|~{3,})|(?<!`)(?P<inline>`+)(?!`)")


def _markdown_chunks(text: str, *, flattened_preview: bool = False) -> list[tuple[bool, str]]:
    chunks = []
    cursor = 0
    # Index summaries flatten original fences onto a single line.
    start_re = _PREVIEW_CODE_START_RE if flattened_preview else _MARKDOWN_CODE_START_RE
    while match := start_re.search(text, cursor):
        chunks.append((False, text[cursor:match.start()]))
        fence = match.group("fence")
        if fence:
            marker = re.escape(fence[0])
            closing = rf"{marker}{{{len(fence)},}}"
            if flattened_preview:
                closing = rf"(?<!{marker}){closing}(?!{marker})"
            else:
                closing = rf"^ {{0,3}}{closing}[ \t]*\r?(?:\n|$)"
            end = re.compile(closing, re.MULTILINE).search(text, match.end())
            stop = end.end() if end else len(text)
            is_code = True
        else:
            closing = rf"(?<!`){re.escape(match.group('inline'))}(?!`)"
            end = re.search(closing, text[match.end():])
            stop = match.end() + end.end() if end else match.end()
            is_code = end is not None
        chunks.append((is_code, text[match.start():stop]))
        cursor = stop
    chunks.append((False, text[cursor:]))
    return chunks


def _markdown_prose(text: str, *, flattened_preview: bool = False) -> str:
    return "".join(" " if is_code else chunk for is_code, chunk in
                   _markdown_chunks(text, flattened_preview=flattened_preview))


def _reference_lookup(records: list[dict]) -> tuple[dict, dict]:
    by_id = {row["id"]: row for row in records}
    by_title: dict[str, list[dict]] = {}
    for row in records:
        title = str(row.get("title") or "").strip()
        if title:
            by_title.setdefault(title, []).append(row)
    return by_id, by_title


def _resolve_reference(token: str, by_id: dict, by_title: dict) -> tuple[dict | None, str]:
    token = token.strip()
    if "|" in token:
        identity, label = (part.strip() for part in token.split("|", 1))
        return by_id.get(identity), label
    page = by_id.get(token)
    if page is not None:
        return page, str(page.get("title") or token)
    matches = by_title.get(token, [])
    return (matches[0] if len(matches) == 1 else None), token


def _export_file_stem(target: str, stems: dict[str, str]) -> str | None:
    if not target.startswith("notes/") and not target.endswith(".md"):
        return None
    stem = target.removeprefix("notes/").removesuffix(".md")
    return stem if stem in stems else None


def _export_markdown(text: str, by_id: dict, by_title: dict, stems_by_id: dict,
                     *, flattened_preview: bool = False) -> str:
    def replace(match: re.Match[str]) -> str:
        page, label = _resolve_reference(match.group(1), by_id, by_title)
        if page is None:
            return match.group(0)
        stem = stems_by_id[page["id"]]
        if match.group(1).split("|", 1)[0].strip() == stem:
            return match.group(0)
        return f"[[{stem}|{label}]]"

    return "".join(chunk if is_code else _WIKI_REFERENCE_RE.sub(replace, chunk)
                   for is_code, chunk in _markdown_chunks(
                       text, flattened_preview=flattened_preview))


def render_candidate(snapshot: dict) -> dict[str, bytes]:
    """Return all candidate bytes using only supplied, hash-checked inputs."""
    _check_snapshot(snapshot)
    records = deepcopy(snapshot["records"])
    stems = _identities(records)
    by_id, by_title = _reference_lookup(records)
    stems_by_id = {identity: stem for stem, identity in stems.items()}
    pages = wiki._apply_backlinks([wiki._record_to_page(row) for row in records], records)
    lint = wiki.collect_lint(pages, captured_at=snapshot["captured_at"])
    files = {
        "index.md": wiki._render_index_md(pages, captured_at=snapshot["captured_at"]),
        "open-questions.md": wiki._render_open_questions_md(pages),
        "lint.md": wiki._render_lint_md(lint),
        "schema.md": snapshot["schema_text"],
    }
    for name in files:
        if name != "schema.md":
            files[name] = _export_markdown(files[name], by_id, by_title, stems_by_id,
                                           flattened_preview=name == "index.md")
    for (stem, identity), page, record in zip(stems.items(), pages, records):
        # Canonical metadata preserves fields and refs that view normalization limits.
        page["source_refs"] = deepcopy(record.get("artifacts") or [])
        body = (str(record.get("body") or "") if record.get("parent_page_id") or any(
            str(tag).startswith("split_from:") for tag in record.get("tags") or [])
            else page["body"])
        note = qmd_search._page_markdown({**page, "body": ""}, identity)
        if body:
            note += "\n## Body\n\n" + body + "\n"
        note = _export_markdown(note, by_id, by_title, stems_by_id)
        note += "\n## Canonical Record\n\n```json\n" + _json_bytes(record).decode("utf-8") + "\n```\n"
        files[f"notes/{stem}.md"] = note
    result = {name: body.encode("utf-8") for name, body in files.items()}
    result["snapshot.json"] = _json_bytes(snapshot)
    return result


def _relationship_errors(records: list[dict], stems: dict[str, str]) -> list[dict]:
    ids = set(stems.values())
    by_id, by_title = _reference_lookup(records)
    errors = []
    for row in records:
        identity = row["id"]
        parents = {row["parent_page_id"]} if row.get("parent_page_id") else set()
        parents.update(tag[len("split_from:"):] for tag in row.get("tags") or []
                       if isinstance(tag, str) and tag.startswith("split_from:"))
        if len(parents) > 1:
            errors.append({"code": "conflicting_parents", "page_id": identity,
                           "target_ids": sorted(parents)})
        targets = [(target, "missing_link") for target in row.get("links") or []]
        if row.get("parent_page_id"):
            targets.append((row["parent_page_id"], "missing_parent"))
        for tag in row.get("tags") or []:
            for prefix in ("split_into:", "split_from:"):
                if isinstance(tag, str) and tag.startswith(prefix):
                    target = tag[len(prefix):]
                    targets.append((target, "missing_child" if prefix == "split_into:" else "missing_parent"))
                    if prefix == "split_into:" and target in by_id:
                        child = by_id[target]
                        parents = {child["parent_page_id"]} if child.get("parent_page_id") else set()
                        parents.update(t[len("split_from:"):] for t in child.get("tags") or []
                                       if isinstance(t, str) and t.startswith("split_from:"))
                        if parents != {identity}:
                            errors.append({"code": "child_parent_mismatch", "page_id": identity,
                                           "target_id": target})
        for target, code in targets:
            if target not in ids:
                errors.append({"code": code, "page_id": identity, "target_id": target})
        texts = [str(row.get("body") or ""), str(row.get("summary") or ""),
                 *[str(item) for item in row.get("openQuestions") or []],
                 *[str(item) for item in row.get("decisions") or []]]
        for text in texts:
            for match in _WIKI_REFERENCE_RE.finditer(_markdown_prose(text)):
                page, _ = _resolve_reference(match.group(1), by_id, by_title)
                if page is None:
                    token = match.group(1).strip()
                    target = token.split("|", 1)[0].strip()
                    # File syntax is publication-only, never a labelled title alias.
                    ambiguous_title = "|" not in token and bool(by_title.get(target))
                    if ambiguous_title or _export_file_stem(target, stems) is None:
                        errors.append({"code": "missing_markdown_link", "page_id": identity,
                                       "target_id": target})
    return errors


def validate_candidate(files: dict[str, bytes]) -> dict:
    """Validate byte identity and complete internal targets, without side effects."""
    errors = []
    try:
        for name, content in files.items():
            path = PurePosixPath(name)
            if (not isinstance(content, bytes) or path.is_absolute() or ".." in path.parts
                    or "\\" in name or str(path) != name):
                raise ValueError(f"unsafe_candidate_path: {name}")
        snapshot = json.loads(files["snapshot.json"].decode("utf-8"))
        expected = render_candidate(snapshot)
        for name in sorted(set(files) | set(expected)):
            if files.get(name) != expected.get(name):
                errors.append({"code": "file_mismatch", "path": name})
        stems = _identities(snapshot["records"])
        errors.extend(_relationship_errors(snapshot["records"], stems))
        for name, content in files.items():
            if not name.endswith(".md") or name == "schema.md":
                continue
            text = content.decode("utf-8").split("\n## Canonical Record\n", 1)[0]
            text = _markdown_prose(text, flattened_preview=name == "index.md")
            for token in _WIKI_REFERENCE_RE.findall(text):
                target = token.split("|", 1)[0].strip()
                stem = target if target in stems else _export_file_stem(target, stems)
                if stem is None or f"notes/{stem}.md" not in files:
                    errors.append({"code": "missing_markdown_link", "path": name,
                                   "page_id": stems.get(PurePosixPath(name).stem),
                                   "target_id": stem or target})
            for target in re.findall(r"\[[^\]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)", text):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                resolved = posixpath.normpath(posixpath.join(
                    str(PurePosixPath(name).parent), unquote(parsed.path)))
                if resolved not in files:
                    errors.append({"code": "missing_markdown_link", "path": name,
                                   "page_id": stems.get(PurePosixPath(name).stem),
                                   "target_id": target})
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        errors.append({"code": "invalid_candidate", "message": str(exc)})
    return {"ok": not errors, "errors": errors}


def _relative(name: str) -> str:
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name
            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name or name == "."):
        raise ValueError("unsafe_relative_path")
    return name


def _generation_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid_generation_id")
    return value


def _no_symlinks(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("symlink_path")
    return path


def _fsync_directory(path: Path) -> None:
    fd = os.open(_no_symlinks(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_regular(path: Path) -> bytes:
    fd = os.open(_no_symlinks(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not_regular_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _write_generation_file(path: Path, data: bytes) -> None:
    with open(path, "xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _publication_lock(root: Path):
    # Store capture must finish before this lock; no store locks below it.
    root = _no_symlinks(root)
    missing = []
    ancestor = root
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    root.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _fsync_directory(directory.parent)
    _fsync_directory(root.parent)
    for name in (".staging", "generations", "publication.lock", "current.json"):
        _no_symlinks(root / name)
    with safe_io.file_write_lock(str(root / "publication")):
        for name in (".staging", "generations"):
            directory = _no_symlinks(root / name)
            directory.mkdir(exist_ok=True)
            if directory.stat().st_dev != root.stat().st_dev:
                raise ValueError("cross_device_publication")
        alias = root / "current"
        if alias.is_symlink():
            target = os.readlink(alias)
            if not re.fullmatch(r"generations/[0-9a-f]{64}", target):
                raise ValueError("unsafe_current_alias")
        elif alias.exists():
            raise ValueError("conflicting_current_alias")
        _fsync_directory(root)
        yield root


class GenerationReader:
    """One validated immutable directory, never a re-resolved current alias."""

    def __init__(self, directory: Path, publication: dict, files: dict[str, bytes]):
        self.directory = directory
        self.generation_id = publication["generation_id"]
        self.snapshot_sha256 = publication["snapshot_sha256"]
        self.manifest_sha256 = publication["manifest_sha256"]
        # Detached bytes also prevent later out-of-contract disk mutation mixing reads.
        self._files = files.copy()

    def read(self, relative_path: str) -> bytes:
        name = _relative(relative_path)
        if name not in self._files:
            raise ValueError("unlisted_generation_file")
        return self._files[name]


def _load_generation(root: Path, generation_id: str) -> GenerationReader:
    directory = _no_symlinks(root / "generations" / _generation_id(generation_id))
    raw = _read_regular(directory / "manifest.json")
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or set(manifest) != {
            "version", "snapshot_sha256", "schema_sha256", "files"}
            or manifest["version"] != 1 or not isinstance(manifest["files"], dict)
            or _sha(_json_bytes(manifest)) != generation_id
            or raw != _json_bytes(manifest)):
        raise ValueError("invalid_generation_manifest")
    files = {}
    for name, digest in manifest["files"].items():
        _relative(name)
        if name == "manifest.json" or not isinstance(digest, str):
            raise ValueError("invalid_manifest_file")
        data = _read_regular(directory / name)
        if _sha(data) != digest:
            raise ValueError("file_digest_mismatch")
        files[name] = data
    expected_dirs = {str(parent) for name in files for parent in PurePosixPath(name).parents
                     if str(parent) != "."}
    actual_files, actual_dirs = set(), set()
    for base, dirs, names in os.walk(directory, followlinks=False):
        for name in dirs + names:
            entry = Path(base) / name
            mode = entry.lstat().st_mode
            relative = entry.relative_to(directory).as_posix()
            if stat.S_ISDIR(mode):
                actual_dirs.add(relative)
            elif stat.S_ISREG(mode):
                actual_files.add(relative)
            else:
                raise ValueError("unsafe_generation_entry")
    if actual_files != set(files) | {"manifest.json"} or actual_dirs != expected_dirs:
        raise ValueError("unexpected_generation_entries")
    if not validate_candidate(files)["ok"]:
        raise ValueError("invalid_generation_candidate")
    snapshot = json.loads(files["snapshot.json"])
    if (snapshot["snapshot_sha256"] != manifest["snapshot_sha256"]
            or snapshot["schema_sha256"] != manifest["schema_sha256"]):
        raise ValueError("manifest_identity_mismatch")
    publication = {"generation_id": generation_id,
                   "snapshot_sha256": manifest["snapshot_sha256"],
                   "manifest_sha256": _sha(raw)}
    return GenerationReader(directory, publication, files)


def pin_generation(*, root: Path, generation_id: str | None = None) -> GenerationReader:
    """Resolve current.json exactly once; explicit IDs pin retained versions."""
    root = _no_symlinks(root)
    pointer = None
    if generation_id is None:
        pointer = json.loads(_read_regular(root / "current.json"))
        generation_id = pointer["generation_id"]
    reader = _load_generation(root, generation_id)
    if pointer is not None and any(pointer.get(key) != getattr(reader, key) for key in (
            "generation_id", "snapshot_sha256", "manifest_sha256")):
        raise ValueError("pointer_identity_mismatch")
    return reader


def _activate_locked(generation_id: str, root: Path, operation_id: str) -> dict:
    _repair_activation_history(root)
    reader = _load_generation(root, generation_id)
    # Also complete a prior rename whose caller crashed before flushing its parent.
    _fsync_directory(root / "generations")
    publication = {key: getattr(reader, key) for key in (
        "generation_id", "snapshot_sha256", "manifest_sha256")}
    _no_symlinks(root / "current.json")
    safe_io.atomic_write_json(str(root / "current.json"), {
        **publication, "operation_id": operation_id})
    # atomic_write_json fsyncs only its file, not the replaced directory entry.
    _fsync_directory(root)
    _replace_current_alias(root, generation_id)
    return publication


def _replace_current_alias(root: Path, generation_id: str):
    # Navigation only: independent external opens do NOT pin a multi-file read.
    temporary_alias = root / (".current-" + uuid.uuid4().hex)
    try:
        temporary_alias.symlink_to("generations/" + generation_id)
        os.replace(temporary_alias, root / "current")
        _fsync_directory(root)
    finally:
        if temporary_alias.is_symlink():
            temporary_alias.unlink()


def _activation_pointer(root: Path):
    path = _no_symlinks(root / 'current.json')
    return json.loads(_read_regular(path)) if path.exists() else None


def _durable_activation_record(path: Path, value: dict):
    _no_symlinks(path)
    if path.exists():
        if json.loads(_read_regular(path)) != value:
            raise ValueError('activation_record_conflict')
    else:
        safe_io.atomic_write_json(str(path), value)
    _fsync_directory(path.parent)
    _fsync_directory(path.parent.parent)


def _repair_activation_history(root: Path):
    directory = _no_symlinks(root / 'activation-intents')
    if not directory.exists():
        return
    current = _activation_pointer(root)
    for path in sorted(directory.glob('*.json')):
        intent = json.loads(_read_regular(path))
        if current == intent['pointer']:
            _replace_current_alias(root, current['generation_id'])
            _durable_activation_record(
                root / 'activation-history' / path.name, intent)


def activate_generation(generation_id: str, *, root: Path, operation_id: str) -> dict:
    """Activate or roll back to an intact retained version; never prune history."""
    _generation_id(generation_id)
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("invalid_operation_id")
    with _publication_lock(root) as locked_root:
        reader = _load_generation(locked_root, generation_id)
        _fsync_directory(locked_root / 'generations')
        identity = {key: getattr(reader, key) for key in (
            'generation_id', 'snapshot_sha256', 'manifest_sha256')}
        name = _sha(operation_id.encode('utf-8')) + '.json'
        path = _no_symlinks(locked_root / 'activation-intents' / name)
        pointer = {**identity, 'operation_id': operation_id}
        _repair_activation_history(locked_root)
        if path.exists():
            intent = json.loads(_read_regular(path))
            if intent['pointer'] != pointer:
                raise ValueError('activation_operation_conflict')
            history = _no_symlinks(locked_root / 'activation-history' / name)
            if history.exists():
                _durable_activation_record(history, intent)
                return identity
            if _activation_pointer(locked_root) != intent['predecessor']:
                return identity
        else:
            intent = {'event': 'rollback', 'operation_id': operation_id,
                      'pointer': pointer, 'predecessor': _activation_pointer(locked_root)}
            _durable_activation_record(path, intent)
        result = _activate_locked(generation_id, locked_root, operation_id)
        _repair_activation_history(locked_root)
        return result


def publish_snapshot(snapshot: dict, *, root: Path, operation_id: str) -> dict:
    """Durably stage a complete candidate before switching the active pointer."""
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("invalid_operation_id")
    files = render_candidate(snapshot)
    if not validate_candidate(files)["ok"]:
        raise ValueError("invalid_publication_candidate")
    manifest = {"version": 1, "snapshot_sha256": snapshot["snapshot_sha256"],
                "schema_sha256": snapshot["schema_sha256"],
                "files": {name: _sha(data) for name, data in sorted(files.items())}}
    manifest_bytes = _json_bytes(manifest)
    generation_id = _sha(manifest_bytes)
    with _publication_lock(root) as locked_root:
        destination = locked_root / "generations" / generation_id
        if destination.exists() or destination.is_symlink():
            _load_generation(locked_root, generation_id)
        else:
            stage = Path(tempfile.mkdtemp(dir=locked_root / ".staging"))
            # Failed staging trees are retained, never reused as complete versions.
            for name, data in {**files, "manifest.json": manifest_bytes}.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_generation_file(target, data)
            for directory in sorted((path for path in stage.rglob("*") if path.is_dir()),
                                    key=lambda path: len(path.parts), reverse=True):
                _fsync_directory(directory)
            _fsync_directory(stage)
            _fsync_directory(locked_root / ".staging")
            os.rename(stage, destination)
            _fsync_directory(locked_root / "generations")
            _fsync_directory(locked_root / ".staging")
        return _activate_locked(generation_id, locked_root, operation_id)
