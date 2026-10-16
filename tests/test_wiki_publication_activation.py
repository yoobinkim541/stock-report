import errno
import json
import os
import stat

import pytest

from agent_console import wiki_publication as p


def snapshot(body="first"):
    value = {"records": [{"id": "one", "title": "One", "body": body,
                           "tags": ["wiki"], "source": {"surface": "wiki"}}],
             "captured_at": "2026-10-16T12:00:00+00:00", "schema_text": "schema"}
    value["schema_sha256"] = p._sha(b"schema")
    value["snapshot_sha256"] = p._snapshot_hash(value)
    return value


def publish(root, body="first"):
    return p.publish_snapshot(snapshot(body), root=root, operation_id=body)


def test_pinned_reader_survives_switch(tmp_path):
    first = publish(tmp_path)
    reader = p.pin_generation(root=tmp_path)
    before = reader.read("index.md")
    second = publish(tmp_path, "second")
    assert first != second
    assert reader.read("index.md") == before
    assert b"first" in reader.read("notes/one.md")
    assert b"second" in p.pin_generation(root=tmp_path).read("notes/one.md")
    assert (tmp_path / "current").resolve() == tmp_path / "generations" / second["generation_id"]


def test_rollback_keeps_history_and_originals(tmp_path):
    (tmp_path / "history.jsonl").write_bytes(b"history\n")
    (tmp_path / "original.md").write_bytes(b"original")
    first = publish(tmp_path)
    second = publish(tmp_path, "second")
    assert p.activate_generation(first["generation_id"], root=tmp_path,
                                 operation_id="rollback") == first
    assert b"first" in p.pin_generation(root=tmp_path).read("notes/one.md")
    assert (tmp_path / "generations" / second["generation_id"]).is_dir()
    assert (tmp_path / "history.jsonl").read_bytes() == b"history\n"
    assert (tmp_path / "original.md").read_bytes() == b"original"
    assert publish(tmp_path) == first


@pytest.mark.parametrize("damage", ["bytes", "extra", "symlink", "manifest", "snapshot", "schema"])
def test_corruption_fails_closed_without_overwrite(tmp_path, damage):
    first = publish(tmp_path)
    directory = tmp_path / "generations" / first["generation_id"]
    target = directory / "notes/one.md"
    if damage == "extra":
        (directory / "extra").write_bytes(b"bad")
    elif damage == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "original")
    else:
        target = directory / {"manifest": "manifest.json", "snapshot": "snapshot.json",
                              "schema": "schema.md"}.get(damage, "notes/one.md")
        target.write_bytes(b"bad")
    with pytest.raises((ValueError, OSError)):
        p.pin_generation(root=tmp_path)
    with pytest.raises((ValueError, OSError)):
        publish(tmp_path)
    assert target.is_symlink() or target.read_bytes() == b"bad" or damage == "extra"


@pytest.mark.parametrize("path", ["../index.md", "/index.md", "notes/../index.md", "./index.md", "notes\\one.md", "", "manifest.json"])
def test_reader_rejects_unsafe_or_unlisted_paths(tmp_path, path):
    publish(tmp_path)
    with pytest.raises(ValueError):
        p.pin_generation(root=tmp_path).read(path)


@pytest.mark.parametrize("component", [".staging", "generations", "publication.lock", "current.json", "current"])
def test_symlink_control_paths_rejected(tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / component).symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        publish(tmp_path)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("identity", ["../escape", "/escape", "g" * 64, ""])
def test_invalid_generation_id(tmp_path, identity):
    with pytest.raises(ValueError):
        p.activate_generation(identity, root=tmp_path, operation_id="bad")


def test_pointer_digest_is_verified(tmp_path):
    publish(tmp_path)
    pointer = json.loads((tmp_path / "current.json").read_text())
    pointer["manifest_sha256"] = "0" * 64
    (tmp_path / "current.json").write_text(json.dumps(pointer))
    with pytest.raises(ValueError):
        p.pin_generation(root=tmp_path)


@pytest.mark.parametrize("boundary", ["write", "file_fsync", "dir_fsync", "rename", "pointer", "after_pointer", "exdev"])
def test_interrupted_publication_keeps_complete_version(tmp_path, monkeypatch, boundary):
    first = publish(tmp_path)
    real_fsync, real_rename, real_replace = os.fsync, os.rename, os.replace
    real_write = p._write_generation_file
    def fail(*args, **kwargs):
        raise OSError(errno.EXDEV if boundary == "exdev" else errno.EIO, "injected")
    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory == (boundary == "dir_fsync"):
            fail()
        return real_fsync(fd)
    def replace(src, dst):
        if str(dst).endswith("current.json"):
            if boundary == "after_pointer":
                real_replace(src, dst)
            fail()
        return real_replace(src, dst)
    if boundary == "write":
        monkeypatch.setattr(p, "_write_generation_file", fail)
    elif boundary in ("file_fsync", "dir_fsync"):
        monkeypatch.setattr(os, "fsync", fsync)
    elif boundary in ("rename", "exdev"):
        monkeypatch.setattr(os, "rename", fail)
    else:
        monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError):
        publish(tmp_path, "second")
    monkeypatch.setattr(os, "fsync", real_fsync)
    monkeypatch.setattr(os, "rename", real_rename)
    monkeypatch.setattr(os, "replace", real_replace)
    monkeypatch.setattr(p, "_write_generation_file", real_write)
    reader = p.pin_generation(root=tmp_path)
    body = reader.read("notes/one.md")
    assert (b"first" in body) != (b"second" in body)
    assert (tmp_path / "generations" / first["generation_id"]).exists()
    publish(tmp_path, "second")


def test_real_directory_fsync_and_no_store_lock(tmp_path, monkeypatch):
    seen = []
    real = os.fsync
    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            seen.append(os.readlink(f"/proc/self/fd/{fd}"))
        real(fd)
    def forbidden():
        raise AssertionError("store lock under publication")
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(p.shared_memory, "_events_lock", forbidden)
    result = publish(tmp_path)
    assert str(tmp_path) in seen
    assert str(tmp_path / "generations") in seen
    assert any("notes" in path for path in seen)
    assert result.keys() == {"generation_id", "snapshot_sha256", "manifest_sha256"}


@pytest.mark.parametrize("boundary", ["write", "fsync", "rename", "replace"])
def test_every_boundary_before_and_after_failure(tmp_path, monkeypatch, boundary):
    # Exercise every occurrence, including nested directory flushes and alias switch.
    first = publish(tmp_path)
    real = p._write_generation_file if boundary == "write" else getattr(os, boundary)
    calls = []
    def observe(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)
    owner, name = (p, "_write_generation_file") if boundary == "write" else (os, boundary)
    monkeypatch.setattr(owner, name, observe)
    publish(tmp_path, "count")
    monkeypatch.setattr(owner, name, real)
    for index in range(len(calls)):
        for after in (False, True):
            root = tmp_path / f"{boundary}-{index}-{after}"
            publish(root)
            count = 0
            def interrupt(*args, **kwargs):
                nonlocal count
                hit = count == index
                count += 1
                if hit:
                    if after:
                        real(*args, **kwargs)
                    raise OSError(errno.EIO, "boundary crash")
                return real(*args, **kwargs)
            monkeypatch.setattr(owner, name, interrupt)
            with pytest.raises(OSError):
                publish(root, "count")
            monkeypatch.setattr(owner, name, real)
            reader = p.pin_generation(root=root)
            assert reader.read("snapshot.json") in (
                p._json_bytes(snapshot()), p._json_bytes(snapshot("count")))
            assert p.pin_generation(root=root, generation_id=first["generation_id"]).read("index.md")
            publish(root, "count")


@pytest.mark.parametrize("damage", ["extra_dir", "nested_symlink", "generation_symlink"])
def test_rejects_unexpected_directories_and_generation_symlinks(tmp_path, damage):
    result = publish(tmp_path)
    directory = tmp_path / "generations" / result["generation_id"]
    if damage == "extra_dir":
        (directory / "extra").mkdir()
    elif damage == "nested_symlink":
        (directory / "notes/one.md").unlink()
        (directory / "notes").rmdir()
        (directory / "notes").symlink_to(tmp_path)
    else:
        retained = tmp_path / "retained"
        directory.rename(retained)
        directory.symlink_to(retained)
    with pytest.raises((ValueError, OSError)):
        p.pin_generation(root=tmp_path)


def test_blocked_candidate_has_no_filesystem_effect(tmp_path):
    value = snapshot()
    value["records"][0]["links"] = ["missing"]
    value["snapshot_sha256"] = p._snapshot_hash(value)
    with pytest.raises(ValueError, match="invalid_publication_candidate"):
        p.publish_snapshot(value, root=tmp_path, operation_id="blocked")
    assert list(tmp_path.iterdir()) == []


def test_nested_root_creation_flushes_all_new_parents(tmp_path, monkeypatch):
    seen = []
    real = p._fsync_directory
    def observe(path):
        seen.append(path)
        real(path)
    monkeypatch.setattr(p, "_fsync_directory", observe)
    publish(tmp_path / "a/b/root")
    assert tmp_path in seen
    assert tmp_path / "a" in seen


def test_reused_generation_flushes_rename_parent_before_pointer(tmp_path, monkeypatch):
    result = publish(tmp_path)
    seen = []
    real_sync, real_pointer = p._fsync_directory, p.safe_io.atomic_write_json
    def sync(path):
        seen.append(path)
        real_sync(path)
    def pointer(*args, **kwargs):
        assert tmp_path / "generations" in seen
        return real_pointer(*args, **kwargs)
    monkeypatch.setattr(p, "_fsync_directory", sync)
    monkeypatch.setattr(p.safe_io, "atomic_write_json", pointer)
    p.activate_generation(result["generation_id"], root=tmp_path, operation_id="retry")
