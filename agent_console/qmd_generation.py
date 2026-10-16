from __future__ import annotations

import json
import hashlib
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Callable

import yaml


_INDEX_NAMES = ("qmd_probe_a", "qmd_probe_b")
_QUERY = "qmd-generation-probe-common"


def _unsupported(reason: str) -> dict:
    return {"supported": False, "reason": reason, "recipe": None}


def probe_isolation(
    *,
    executable: str,
    workspace: Path,
    runner: Callable[..., Any],
) -> dict:
    """Prove that named QMD indexes build and query only synthetic documents."""
    try:
        base = Path(workspace).resolve()
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qmd-isolation-probe-", dir=base) as temp:
            root = Path(temp).resolve()
            home = root / "home"
            config = root / "config"
            cache = root / "cache"
            temp_dir = root / "tmp"
            synthetic = root / "workspace"
            a_dir, b_dir = synthetic / "a", synthetic / "b"
            for path in (home, config, cache, temp_dir, a_dir, b_dir):
                path.mkdir(parents=True, exist_ok=True)

            a_note, b_note = a_dir / "probe-a.md", b_dir / "probe-b.md"
            a_note.write_text(
                "# Synthetic A\n\nqmd-generation-probe-common sentinel-a\n", encoding="utf-8"
            )
            b_note.write_text(
                "# Synthetic B\n\nqmd-generation-probe-common sentinel-b\n", encoding="utf-8"
            )

            env = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "NO_COLOR": "1",
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(config),
                "XDG_CACHE_HOME": str(cache),
                "TMPDIR": str(temp_dir),
                "TMP": str(temp_dir),
                "TEMP": str(temp_dir),
            }
            index_paths = {}
            for name in _INDEX_NAMES:
                config_path = config / "qmd" / f"{name}.yml"
                database_path = cache / "qmd" / f"{name}.sqlite"
                if not all(path.resolve().is_relative_to(root) for path in (config_path, database_path)):
                    return _unsupported("isolated_index_path_escapes_temporary_root")
                if config_path.exists() or database_path.exists():
                    return _unsupported("temporary_index_path_already_exists")
                index_paths[name] = (config_path, database_path)

            def invoke(index_name: str, phase: str, *args: str):
                argv = [executable, "--index", index_name, *args]
                try:
                    completed = runner(
                        argv,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env=env,
                        cwd=str(synthetic),
                    )
                except subprocess.TimeoutExpired:
                    return None, f"qmd_{phase}_timeout"
                except (FileNotFoundError, OSError):
                    return None, f"qmd_{phase}_execution_failed"
                code = getattr(completed, "returncode", 1)
                if code != 0:
                    return None, f"qmd_{phase}_exit_{code}"
                return completed, ""

            for index_name in _INDEX_NAMES:
                for phase, args in (
                    ("version", ("--version",)),
                    ("help", ("--help",)),
                    ("collection_help", ("collection", "help")),
                ):
                    completed, error = invoke(index_name, phase, *args)
                    if error:
                        return _unsupported(error)
                    if phase == "help":
                        help_text = f"{completed.stdout}\n{completed.stderr}"
                        if "--index <name>" not in help_text:
                            return _unsupported("named_index_option_not_documented_by_local_help")
                        index_lines = [line.partition("Index:")[2].strip() for line in help_text.splitlines() if "Index:" in line]
                        expected_db = index_paths[index_name][1].resolve()
                        if len(index_lines) != 1 or Path(index_lines[0]).resolve() != expected_db:
                            return _unsupported("qmd_help_index_path_not_confined_to_temporary_cache")
                        if not expected_db.is_relative_to(cache.resolve()):
                            return _unsupported("isolated_index_path_escapes_temporary_root")
                    if phase == "collection_help":
                        help_text = f"{completed.stdout}\n{completed.stderr}"
                        if "add <path>" not in help_text or "--name" not in help_text:
                            return _unsupported("collection_add_recipe_not_documented_by_local_help")

            for index_name, collection_name, collection_dir in (
                (_INDEX_NAMES[0], "probe-a", a_dir),
                (_INDEX_NAMES[1], "probe-b", b_dir),
            ):
                # QMD collection add indexes Markdown lexically; it does not embed.
                _, error = invoke(
                    index_name,
                    "collection_add",
                    "collection",
                    "add",
                    str(collection_dir),
                    "--name",
                    collection_name,
                )
                if error:
                    return _unsupported(error)

            for index_name, collection_name, expected_note, expected_sentinel, wrong_sentinel in (
                (_INDEX_NAMES[0], "probe-a", a_note, "sentinel-a", "sentinel-b"),
                (_INDEX_NAMES[1], "probe-b", b_note, "sentinel-b", "sentinel-a"),
            ):
                completed, error = invoke(
                    index_name,
                    "synthetic_search",
                    "search",
                    _QUERY,
                    "--format",
                    "json",
                    "--full-path",
                    "--full",
                    "-n",
                    "10",
                    "-c",
                    collection_name,
                )
                if error:
                    return _unsupported(error)
                try:
                    payload = json.loads(completed.stdout)
                except (TypeError, json.JSONDecodeError):
                    return _unsupported("synthetic_query_returned_invalid_json")
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                result_paths = []

                def collect_files(value):
                    if isinstance(value, dict):
                        file_value = value.get("file")
                        if isinstance(file_value, str):
                            file_path = Path(file_value)
                            if not file_path.is_absolute():
                                file_path = synthetic / file_path
                            result_paths.append(file_path.resolve())
                        for child in value.values():
                            collect_files(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect_files(child)

                collect_files(payload)
                identity_ok = (
                    result_paths
                    and all(path == expected_note.resolve() and path.is_relative_to(root) for path in result_paths)
                    and expected_sentinel in serialized
                    and wrong_sentinel not in serialized
                )
                if not identity_ok:
                    reason = "synthetic_b_query_returned_wrong_identity" if index_name == _INDEX_NAMES[1] else "synthetic_a_query_returned_wrong_identity"
                    return _unsupported(reason)

            return {
                "supported": True,
                "reason": "",
                "recipe": {
                    "build_argv": [
                        "{executable}", "--index", "{index_name}", "collection", "add",
                        "{collection_path}", "--name", "{collection_name}",
                    ],
                    "query_argv": [
                        "{executable}", "--index", "{index_name}", "search", "{query}",
                        "--format", "json", "--full-path", "--full", "-n", "10",
                        "-c", "{collection_name}",
                    ],
                    "environment": {
                        "HOME": "{probe_root}/home",
                        "XDG_CONFIG_HOME": "{probe_root}/config",
                        "XDG_CACHE_HOME": "{probe_root}/cache",
                        "TMPDIR": "{probe_root}/tmp",
                        "TMP": "{probe_root}/tmp",
                        "TEMP": "{probe_root}/tmp",
                    },
                },
            }
    except OSError:
        return _unsupported("qmd_local_capability_probe_failed")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_data(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _confined(path: Path, root: Path) -> Path:
    if not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("search_path_outside_root")
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError("search_symlink")
    return path


def _environment(base: Path) -> dict:
    return {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "NO_COLOR": "1",
            **{key: str(base / value) for key, value in {
                "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp"}.items()}}


def _verified_recipe(recipe: dict) -> bool:
    return recipe == {
        "build_argv": ["{executable}", "--index", "{index_name}", "collection", "add",
                       "{collection_path}", "--name", "{collection_name}"],
        "query_argv": ["{executable}", "--index", "{index_name}", "search", "{query}",
                       "--format", "json", "--full-path", "--full", "-n", "10", "-c", "{collection_name}"],
        "environment": {key: "{probe_root}/" + value for key, value in {
            "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
            "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp"}.items()},
    }


def _trusted_executable() -> tuple[str, str]:
    from .qmd_search import qmd_bin
    configured = qmd_bin()
    executable = Path(shutil.which(configured) or configured).resolve(strict=True)
    return str(executable), _digest(executable.read_bytes())


def _runtime_config(base: Path) -> dict:
    # Installed QMD's default references are inert metadata, never model operations.
    return {"collections": {"wiki-generation": {"path": str(base / "notes"), "pattern": "**/*.md"}},
            "models": {
                "embed": "hf:ggml-org/embeddinggemma-300M-GGUF/embeddinggemma-300M-Q8_0.gguf",
                "generate": "hf:tobil/qmd-query-expansion-1.7B-gguf/qmd-query-expansion-1.7B-q4_k_m.gguf",
                "rerank": "hf:ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf",
            }}


def _config_bytes(base: Path) -> bytes:
    return yaml.safe_dump(_runtime_config(base), sort_keys=False, allow_unicode=True).encode("utf-8")


def _verify_runtime(descriptor: dict, *, initializing: bool = False) -> None:
    root = Path(descriptor["root"])
    base = _confined(Path(descriptor["build_root"]), root)
    if base.parent != root / "search-indexes" or not base.name.startswith("build-"):
        raise ValueError("wrong_build_root")
    executable, digest = _trusted_executable()
    if (descriptor["executable"] != executable or descriptor["executable_sha256"] != digest
            or descriptor["index_name"] != "wiki_generation"
            or descriptor["collection_name"] != "wiki-generation"):
        raise ValueError("untrusted_qmd_runtime")
    config = _confined(base / "config" / "qmd" / "wiki_generation.yml", root)
    if initializing:
        if config.exists():
            raise ValueError("unexpected_initial_config")
    elif config.read_bytes() != _config_bytes(base):
        raise ValueError("untrusted_qmd_config")


def _database_fingerprint(path: Path) -> str:
    """Hash all logical schema/data, not mutable SQLite headers or page layout."""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("index_corruption")
            digest = hashlib.sha256()
            for statement in connection.iterdump():
                digest.update(statement.encode("utf-8"))
                digest.update(b"\n")
            return digest.hexdigest()
    except sqlite3.Error as exc:
        raise ValueError("index_corruption") from exc


def _invoke(descriptor: dict, args: list[str], runner, *, initializing: bool = False):
    _verify_runtime(descriptor, initializing=initializing)
    result = runner([descriptor["executable"], "--index", descriptor["index_name"], *args],
                    capture_output=True, text=True, timeout=30,
                    env=_environment(Path(descriptor["build_root"])), cwd=descriptor["build_root"])
    if getattr(result, "returncode", 1) != 0:
        raise ValueError("qmd_failed")
    return result


def _query(descriptor: dict, query: str, runner) -> list[dict]:
    result = _invoke(descriptor, ["search", query, "--format", "json", "--full-path",
                                "--full", "-n", "10", "-c", descriptor["collection_name"]], runner)
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("invalid_search_payload")
    return payload


def _hit_path(hit: dict, descriptor: dict) -> str:
    path = Path(hit["file"])
    if not path.is_absolute():
        path = Path(descriptor["build_root"]) / path
    return str(path.resolve())


def _activate_descriptor(root: Path, descriptor: dict) -> None:
    import safe_io
    from .wiki_publication import _publication_lock, pin_generation

    def prepare(data: bytes) -> Path:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".search-", delete=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)

    def flush_directory():
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    pointer = root / "search-current.json"
    # Same order as publication: publication lock, then search-descriptor lock.
    with _publication_lock(root), safe_io.file_write_lock(str(pointer)):
        published = pin_generation(root=root)
        if any(getattr(published, key) != descriptor[key] for key in (
                "generation_id", "snapshot_sha256", "manifest_sha256")):
            raise ValueError("superseded_generation_build")
        previous = pointer.read_bytes() if pointer.exists() else None
        temporary = prepare(_json_data(descriptor))
        os.replace(temporary, pointer)
        try:
            flush_directory()
        except OSError:
            if previous is None:
                pointer.unlink()
            else:
                os.replace(prepare(previous), pointer)
            flush_directory()
            raise


def _verify_descriptor(descriptor: dict) -> None:
    from . import wiki
    from .wiki_publication import pin_generation, _check_snapshot, _identities
    _verify_runtime(descriptor)
    root = Path(descriptor["root"])
    base = _confined(Path(descriptor["build_root"]), root)
    db = _confined(Path(descriptor["database_path"]), base)
    if db != base / "cache" / "qmd" / (descriptor["index_name"] + ".sqlite"):
        raise ValueError("wrong_database_path")
    if _database_fingerprint(db) != descriptor["database_content_sha256"]:
        raise ValueError("index_corruption")
    reader = pin_generation(root=root, generation_id=descriptor["generation_id"])
    snapshot = json.loads(reader.read("snapshot.json"))
    _check_snapshot(snapshot)
    if snapshot["snapshot_sha256"] != descriptor["snapshot_sha256"]:
        raise ValueError("wrong_snapshot")
    generation = _confined(root / "generations" / descriptor["generation_id"], root)
    if _digest((generation / "manifest.json").read_bytes()) != descriptor["manifest_sha256"]:
        raise ValueError("wrong_manifest")
    expected_pages = {}
    for stem, identity in _identities(snapshot["records"]).items():
        try:
            revision = wiki.semantic_snapshot(identity, records=snapshot["records"])["revision_hash"]
        except ValueError:
            continue
        path = base / "notes" / f"{stem}.md"
        if path.read_bytes() != reader.read(f"notes/{stem}.md"):
            raise ValueError("search_note_snapshot_mismatch")
        expected_pages[str(path)] = {"page_id": identity, "source_semantic_revision": revision}
    if descriptor["pages"] != expected_pages:
        raise ValueError("search_revision_mismatch")
    for path, expected in descriptor["file_sha256"].items():
        if _digest(_confined(Path(path), base).read_bytes()) != expected:
            raise ValueError("search_file_corruption")


def build_index(publication: dict, *, root: Path, capability: dict, runner) -> dict:
    """Build only the verified lexical recipe; publish no descriptor on failure."""
    from . import wiki
    from .wiki_publication import pin_generation, _check_snapshot, _identities
    result = {"status": "search_pending", "generation_id": publication.get("generation_id"),
              "snapshot_sha256": publication.get("snapshot_sha256"), "descriptor": None}
    if capability.get("supported") is not True or not _verified_recipe(capability.get("recipe")):
        return result
    try:
        if set(publication) != {"generation_id", "snapshot_sha256", "manifest_sha256"}:
            raise ValueError("invalid_publication")
        if any(not isinstance(value, str) or len(value) != 64 or
               any(char not in "0123456789abcdef" for char in value) for value in publication.values()):
            raise ValueError("invalid_publication_identity")
        root = Path(root).absolute()
        generation = _confined(root / "generations" / publication["generation_id"], root)
        reader = pin_generation(root=root, generation_id=publication["generation_id"])
        snapshot = json.loads(reader.read("snapshot.json"))
        _check_snapshot(snapshot)
        if snapshot["snapshot_sha256"] != publication["snapshot_sha256"] or _digest(
                (generation / "manifest.json").read_bytes()) != publication["manifest_sha256"]:
            raise ValueError("publication_identity_mismatch")
        search_root = _confined(root / "search-indexes", root)
        search_root.mkdir(exist_ok=True)
        base = Path(tempfile.mkdtemp(prefix="build-", dir=search_root))
        env = _environment(base)
        for key, value in env.items():
            if key not in {"PATH", "LANG", "NO_COLOR"}:
                _confined(Path(value), root).mkdir(exist_ok=True)
        collection = base / "notes"
        collection.mkdir()
        executable, executable_sha256 = _trusted_executable()
        descriptor = {**publication, "root": str(root), "build_root": str(base),
                      "index_name": "wiki_generation", "collection_name": "wiki-generation",
                      "executable": executable, "executable_sha256": executable_sha256,
                      "pages": {}, "file_sha256": {}}
        for stem, identity in _identities(snapshot["records"]).items():
            try:
                revision = wiki.semantic_snapshot(identity, records=snapshot["records"])["revision_hash"]
            except ValueError:
                continue  # Archived pages are not searchable.
            data = reader.read(f"notes/{stem}.md")
            path = collection / f"{stem}.md"
            path.write_bytes(data)
            descriptor["pages"][str(path)] = {"page_id": identity, "source_semantic_revision": revision}
        sentinel = "qmdgeneration" + publication["generation_id"]
        sentinel_path = collection / "publication-sentinel.md"
        if sentinel_path.exists():
            raise ValueError("sentinel_filename_collision")
        sentinel_path.write_text(sentinel, encoding="utf-8")
        db = base / "cache" / "qmd" / "wiki_generation.sqlite"
        config = base / "config" / "qmd" / "wiki_generation.yml"
        for path in (db, config):
            _confined(path, root)
            if path.exists():
                raise ValueError("index_path_exists")
        descriptor["database_path"] = str(db)
        help_result = _invoke(descriptor, ["--help"], runner, initializing=True)
        advertised = [line.partition("Index:")[2].strip() for line in
                      (help_result.stdout + "\n" + help_result.stderr).splitlines() if "Index:" in line]
        if advertised != [str(db)]:
            raise ValueError("index_preflight_failed")
        _invoke(descriptor, ["collection", "add", str(collection), "--name", descriptor["collection_name"]],
                runner, initializing=True)
        actual_config = yaml.safe_load(config.read_bytes())
        expected_config = _runtime_config(base)
        if actual_config not in (expected_config, {"collections": expected_config["collections"]}):
            raise ValueError("untrusted_built_config")
        config.write_bytes(_config_bytes(base))
        smoke = _query(descriptor, sentinel, runner)
        if not smoke or any(_hit_path(hit, descriptor) != str(sentinel_path) or
                            sentinel not in json.dumps(hit) for hit in smoke):
            raise ValueError("sentinel_mismatch")
        # Bounded CLI smoke coverage; every note/revision is verified locally below.
        paths = sorted(descriptor["pages"])
        sampled = sorted({0, len(paths) // 2, len(paths) - 1}) if paths else []
        for ordinal in sampled:
            path = paths[ordinal]
            page = descriptor["pages"][path]
            hits = _query(descriptor, page["page_id"], runner)
            if not hits or not any(_hit_path(hit, descriptor) == path for hit in hits) or any(
                    _hit_path(hit, descriptor) not in descriptor["pages"] for hit in hits):
                raise ValueError("note_identity_mismatch")
        descriptor["file_sha256"] = {str(path): _digest(path.read_bytes()) for path in collection.glob("*.md")}
        descriptor["database_content_sha256"] = _database_fingerprint(db)
        _verify_descriptor(descriptor)
        _activate_descriptor(root, descriptor)
        return {**result, "status": "ready", "descriptor": descriptor}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, subprocess.TimeoutExpired):
        return result


def search_generation(query: str, *, descriptor: dict, limit: int, runner) -> list[dict]:
    from .qmd_search import _clean
    try:
        descriptor = json.loads(json.dumps(descriptor))
        _verify_descriptor(descriptor)
        hits = _query(descriptor, _clean(query, 600), runner) if query.strip() else []
        output, seen = [], set()
        for hit in hits:
            page = descriptor["pages"].get(_hit_path(hit, descriptor))
            if page and page["page_id"] not in seen:
                seen.add(page["page_id"])
                output.append({**page, "id": page["page_id"], "generation_id": descriptor["generation_id"],
                               "score": hit.get("score", 0), "provider": "qmd"})
        _verify_descriptor(descriptor)
        return output[:max(1, min(int(limit), 10))]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
        return []


def _load_descriptor(*, root: Path) -> dict:
    """Pin search state only beneath the caller's non-symlink publication root."""
    root = Path(os.path.abspath(root))
    for component in (*reversed(root.parents), root):
        if component.is_symlink():
            raise ValueError("symlink_publication_root")
    pointer = _confined(root / "search-current.json", root)
    descriptor = json.loads(pointer.read_bytes())
    if not isinstance(descriptor, dict) or descriptor.get("root") != str(root):
        raise ValueError("descriptor_publication_root_mismatch")
    return descriptor


def generation_health(*, root: Path) -> dict:
    from .wiki_publication import pin_generation
    result = {"published": None, "indexed": None, "index_fresh": False,
              "status": "search_pending", "fallback_available": True}
    try:
        published = pin_generation(root=Path(root))
        result["published"] = {key: getattr(published, key) for key in (
            "generation_id", "snapshot_sha256", "manifest_sha256")}
        descriptor = _load_descriptor(root=Path(root))
        result["indexed"] = {key: descriptor[key] for key in ("generation_id", "snapshot_sha256", "manifest_sha256")}
        _verify_descriptor(descriptor)
        result["index_fresh"] = all(result["published"].get(key) == value for key, value in result["indexed"].items())
        if result["index_fresh"]:
            result["status"] = "ready"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return result
