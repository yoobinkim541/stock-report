#!/usr/bin/env python3
"""Filesystem helpers for raw source artifacts and derived text.

Raw sources are stored under ``~/reports/raw`` (or ``STOCK_REPORT_REPORTS_DIR/raw``)
and derived text under ``~/reports/text``.  Raw originals expire by TTL, but
derived text remains available for wiki and memory layers. Cold artifacts can be
packed into date-level ``tar.gz`` bundles without changing their logical paths.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
REPORTS_DIR_ENV = "STOCK_REPORT_REPORTS_DIR"
DEFAULT_REPORTS_DIR = Path.home() / "reports"
DEFAULT_RAW_TTL_DAYS = 30
BUNDLE_FORMAT = "stock-report.raw-bundle.v1"
BUNDLE_DIRNAME = ".bundles"
RAW_TTL_DAYS_BY_SOURCE = {
    "saveticker_report_pdf": 180,
    "saveticker_article": 60,
    "saveticker": 60,
    "telegram": 14,
    "arca": 7,
    "yahoo_finance": 30,
    "fred": 30,
    "worldgovernmentbonds": 30,
}


def reports_root() -> Path:
    return Path(os.getenv(REPORTS_DIR_ENV, str(DEFAULT_REPORTS_DIR))).expanduser()


def raw_root() -> Path:
    root = reports_root() / "raw"
    root.mkdir(parents=True, exist_ok=True)
    return root


def text_root() -> Path:
    root = reports_root() / "text"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


def _date_parts(dt: datetime) -> tuple[str, str, str]:
    kst = _ensure_tz(dt).astimezone(KST)
    return f"{kst:%Y}", f"{kst:%m}", f"{kst:%d}"


def _stamp(dt: datetime) -> str:
    return _ensure_tz(dt).astimezone(KST).strftime("%Y%m%d-%H%M%S")


def _slugify(text: str, limit: int = 80) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", (text or "").strip()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:limit].strip("-") or "artifact"


def _artifact_dir(source: str, fetched_at: datetime, *, base: Path | None = None) -> Path:
    y, m, d = _date_parts(fetched_at)
    root = (base or raw_root()) / source / y / m / d
    root.mkdir(parents=True, exist_ok=True)
    return root


def _text_dir(source: str, fetched_at: datetime, *, base: Path | None = None) -> Path:
    y, m, d = _date_parts(fetched_at)
    root = (base or text_root()) / source / y / m / d
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported value for json serialization: {type(value)!r}")


def _normalize_source(source: str) -> str:
    return str(source or "").strip().lower().split(":", 1)[0]


def resolve_raw_ttl_days(source: str, *, kind: str | None = None, ttl_days: int | None = None) -> int:
    if ttl_days is not None:
        return max(1, int(ttl_days))
    root = _normalize_source(source)
    if root.startswith("telegram"):
        return RAW_TTL_DAYS_BY_SOURCE.get("telegram", DEFAULT_RAW_TTL_DAYS)
    if root in RAW_TTL_DAYS_BY_SOURCE:
        return RAW_TTL_DAYS_BY_SOURCE[root]
    if kind and str(kind).lower() == "pdf" and root.startswith("saveticker"):
        return RAW_TTL_DAYS_BY_SOURCE["saveticker_report_pdf"]
    return DEFAULT_RAW_TTL_DAYS


def save_raw_artifact(
    source: str,
    kind: str,
    fetched_at: datetime,
    title: str,
    url: str,
    payload: bytes | str,
    suffix: str,
    ttl_days: int | None = None,
) -> dict:
    fetched_at = _ensure_tz(fetched_at)
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    digest = _sha256_bytes(data)
    stamp = _stamp(fetched_at)
    slug = _slugify(title)
    base_name = f"{stamp}-{slug}-{digest[:12]}"
    resolved_ttl_days = resolve_raw_ttl_days(source, kind=kind, ttl_days=ttl_days)

    raw_dir = _artifact_dir(source, fetched_at)
    text_dir_ = _text_dir(source, fetched_at)
    raw_path = raw_dir / f"{base_name}{suffix}"
    manifest_path = raw_dir / f"{base_name}.manifest.json"
    text_path = text_dir_ / f"{base_name}.txt"
    expires_at = (fetched_at + timedelta(days=resolved_ttl_days)).astimezone(KST)

    raw_path.write_bytes(data)
    record = {
        "source": source,
        "kind": kind,
        "title": title,
        "url": url,
        "source_url": url,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "ttl_days": resolved_ttl_days,
        "raw_path": str(raw_path),
        "text_path": str(text_path),
        "manifest_path": str(manifest_path),
        "content_type": kind,
        "suffix": suffix,
        "sha256": digest,
    }
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return record


def save_extracted_text(raw_record: dict, text: str) -> dict:
    text_path = Path(raw_record["text_path"])
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text or "", encoding="utf-8")
    record = dict(raw_record)
    record["text_path"] = str(text_path)
    manifest_path = Path(raw_record["manifest_path"])
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = dict(raw_record)
        manifest["text_path"] = str(text_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return record


def _parse_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _safe_component(value: object) -> str:
    component = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", str(value or "").strip())
    return component.strip("._") or "unknown"


def _bundle_path(source: str, fetched_at: datetime, ttl_days: int) -> Path:
    y, m, d = _date_parts(fetched_at)
    return (
        raw_root() / BUNDLE_DIRNAME / _safe_component(source) / y / m
        / f"{d}-ttl{int(ttl_days)}.tar.gz"
    )


def _archive_members_for_path(path: Path) -> list[tuple[Path, str]]:
    """Map an old individual artifact path to its deterministic bundle member."""
    raw = raw_root()
    text = text_root()
    category = ""
    try:
        relative = path.resolve().relative_to(raw.resolve())
        category = "manifest" if relative.name.endswith(".manifest.json") else "raw"
    except ValueError:
        try:
            relative = path.resolve().relative_to(text.resolve())
            category = "text"
        except ValueError:
            return []
    if len(relative.parts) != 5:
        return []
    source, year, month, day, filename = relative.parts
    if not (re.fullmatch(r"\d{4}", year) and re.fullmatch(r"\d{2}", month) and re.fullmatch(r"\d{2}", day)):
        return []
    bundle_root = raw / BUNDLE_DIRNAME / _safe_component(source) / year / month
    try:
        bundles = sorted(bundle_root.glob(f"{day}-ttl*.tar.gz"))
    except OSError:
        bundles = []
    return [(bundle, f"{category}/{filename}") for bundle in bundles]


def _read_bundle_member(bundle: Path, member: str) -> bytes | None:
    if not bundle.exists() or ".." in Path(member).parts or member.startswith("/"):
        return None
    try:
        with tarfile.open(bundle, mode="r:gz") as archive:
            # getmember() first materializes every tar header. Bundles can contain
            # thousands of entries, so stream until the requested member instead.
            for info in archive:
                if info.name != member or not info.isfile():
                    continue
                extracted = archive.extractfile(info)
                return extracted.read() if extracted else None
            return None
    except (OSError, KeyError, tarfile.TarError):
        return None


def read_raw_artifact(path: str | Path, *, encoding: str | None = None) -> bytes | str | None:
    """Read a raw, text, or manifest artifact before or after cold compaction.

    Existing callers may keep the original path in evidence records. After a
    bundle compaction that path is resolved to its member transparently.
    """
    raw_path = Path(path).expanduser()
    if raw_path.exists() and raw_path.is_file():
        data = raw_path.read_bytes()
    else:
        candidates = _archive_members_for_path(raw_path)
        if not candidates:
            return None
        data = None
        for bundle, member in candidates:
            data = _read_bundle_member(bundle, member)
            if data is not None:
                break
        if data is None:
            return None
    if encoding is None:
        return data
    return data.decode(encoding, errors="replace")


def read_raw_text(path: str | Path, encoding: str = "utf-8") -> str | None:
    value = read_raw_artifact(path, encoding=encoding)
    return value if isinstance(value, str) else None


def _bundle_index(bundle: Path) -> dict | None:
    data = _read_bundle_member(bundle, "_index.json")
    if not data:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("format") == BUNDLE_FORMAT else None


def _add_file_to_tar(archive: tarfile.TarFile, path: Path, member: str) -> None:
    info = archive.gettarinfo(str(path), arcname=member)
    with path.open("rb") as source:
        archive.addfile(info, source)


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    stop = Path(os.path.abspath(stop))
    while current.exists() and Path(os.path.abspath(current)) != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _path_date(path: Path, root: Path) -> tuple[datetime, str] | None:
    try:
        relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    except ValueError:
        return None
    if len(relative.parts) < 5:
        return None
    source, year, month, day = relative.parts[:4]
    try:
        fetched_at = datetime(int(year), int(month), int(day), tzinfo=KST)
    except (TypeError, ValueError):
        return None
    return fetched_at, source


def _bundle_entry(
    *,
    source: str,
    fetched_at: datetime,
    ttl_days: int,
    raw_path: Path | None = None,
    text_path: Path | None = None,
    manifest_path: Path | None = None,
    expires_at: str | None = None,
) -> dict:
    return {
        "source": source,
        "fetched_at": _ensure_tz(fetched_at).isoformat(timespec="seconds"),
        "expires_at": expires_at or (_ensure_tz(fetched_at) + timedelta(days=ttl_days)).isoformat(timespec="seconds"),
        "ttl_days": int(ttl_days),
        "raw_path": str(raw_path) if raw_path else "",
        "text_path": str(text_path) if text_path else "",
        "manifest_path": str(manifest_path) if manifest_path else "",
        "raw_member": f"raw/{raw_path.name}" if raw_path else "",
        "text_member": f"text/{text_path.name}" if text_path else "",
        "manifest_member": f"manifest/{manifest_path.name}" if manifest_path else "",
    }


def compact_raw_artifacts(
    *,
    now: datetime | None = None,
    min_age_days: int = 2,
    dry_run: bool = False,
    max_files: int | None = None,
    max_bundles: int | None = None,
) -> dict:
    """Pack cold raw/text/manifest files into date-level gzip tar bundles.

    A bundle contains the original bytes and a small index. The old paths are
    intentionally recorded in that index so evidence references remain stable;
    ``read_raw_artifact`` resolves them after the individual files are removed.
    """
    now = _ensure_tz(now or datetime.now(KST))
    cutoff = now - timedelta(days=max(0, int(min_age_days)))
    raw = raw_root().resolve()
    text = text_root().resolve()
    groups: dict[tuple[str, str, str, str, int], dict] = {}
    claimed_raw: set[Path] = set()
    claimed_text: set[Path] = set()
    raw_files: list[Path] = []
    manifest_files: list[Path] = []
    text_files: list[Path] = []

    def add_group(entry: dict, fetched_at: datetime, source: str, ttl_days: int) -> None:
        y, m, d = _date_parts(fetched_at)
        key = (source, y, m, d, int(ttl_days))
        bucket = groups.setdefault(key, {"fetched_at": fetched_at, "source": source, "ttl_days": ttl_days, "entries": []})
        bucket["entries"].append(entry)

    # os.walk를 한 번만 사용해 수십만 개의 Path.resolve/rglob 호출을 피한다.
    for directory, directories, filenames in os.walk(raw):
        directories[:] = [name for name in directories if name != BUNDLE_DIRNAME]
        for filename in filenames:
            path = Path(directory) / filename
            if filename.endswith(".manifest.json"):
                manifest_files.append(path)
            else:
                raw_files.append(path)
    for manifest_path in manifest_files:
        manifest = _load_manifest(manifest_path)
        fetched_at = _parse_datetime(manifest.get("fetched_at")) if manifest else None
        raw_path = Path(manifest.get("raw_path") or "") if manifest else Path()
        if not manifest or not fetched_at or fetched_at > cutoff or not raw_path.is_file():
            continue
        source = str(manifest.get("source") or manifest_path.parts[len(raw.parts)] or "unknown")
        ttl_days = resolve_raw_ttl_days(source, kind=manifest.get("kind"), ttl_days=manifest.get("ttl_days"))
        text_path = Path(manifest.get("text_path") or "")
        entry = _bundle_entry(
            source=source,
            fetched_at=fetched_at,
            ttl_days=ttl_days,
            raw_path=raw_path,
            text_path=text_path if text_path.is_file() else None,
            manifest_path=manifest_path,
            expires_at=str(manifest.get("expires_at") or ""),
        )
        add_group(entry, fetched_at, source, ttl_days)
        claimed_raw.add(Path(os.path.abspath(raw_path)))
        if text_path.is_file():
            claimed_text.add(Path(os.path.abspath(text_path)))

    def add_orphan_raw(raw_path: Path) -> None:
        raw_key = Path(os.path.abspath(raw_path))
        if raw_key in claimed_raw or raw_path.name.endswith(".manifest.json"):
            return
        dated = _path_date(raw_path, raw)
        if not dated:
            return
        fetched_at, source = dated
        if fetched_at > cutoff:
            return
        ttl_days = resolve_raw_ttl_days(source)
        relative = raw_key.relative_to(raw)
        text_path = text / Path(*relative.parts)
        text_path = text_path.with_suffix(".txt")
        entry = _bundle_entry(
            source=source,
            fetched_at=fetched_at,
            ttl_days=ttl_days,
            raw_path=raw_path,
            text_path=text_path if text_path.is_file() else None,
        )
        add_group(entry, fetched_at, source, ttl_days)
        claimed_raw.add(raw_key)
        if text_path.is_file():
            claimed_text.add(Path(os.path.abspath(text_path)))

    for raw_path in raw_files:
        add_orphan_raw(raw_path)

    for directory, _, filenames in os.walk(text):
        text_files.extend(Path(directory) / filename for filename in filenames if filename.endswith(".txt"))
    for text_path in text_files:
        if Path(os.path.abspath(text_path)) in claimed_text:
            continue
        dated = _path_date(text_path, text)
        if not dated:
            continue
        fetched_at, source = dated
        if fetched_at > cutoff:
            continue
        ttl_days = resolve_raw_ttl_days(source)
        entry = _bundle_entry(source=source, fetched_at=fetched_at, ttl_days=ttl_days, text_path=text_path)
        add_group(entry, fetched_at, source, ttl_days)
        claimed_text.add(text_path.resolve())

    result = {
        "dry_run": bool(dry_run),
        "groups": len(groups),
        "bundles": 0,
        "files_packed": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "errors": [],
    }
    file_limit = max(1, int(max_files)) if max_files is not None else None
    bundle_limit = max(1, int(max_bundles)) if max_bundles is not None else None
    for bucket in groups.values():
        if bundle_limit is not None and result["bundles"] >= bundle_limit:
            break
        entries = bucket["entries"]
        bundle = _bundle_path(bucket["source"], bucket["fetched_at"], bucket["ttl_days"])
        existing_index = _bundle_index(bundle) if bundle.exists() else None
        existing_entries = (existing_index or {}).get("entries") or []
        existing_paths = {
            tuple(str(item.get(field) or "") for field in ("raw_path", "text_path", "manifest_path"))
            for item in existing_entries
            if isinstance(item, dict)
        }
        entries = [
            entry for entry in entries
            if tuple(str(entry.get(field) or "") for field in ("raw_path", "text_path", "manifest_path")) not in existing_paths
        ]
        if not entries:
            continue
        if file_limit is not None:
            selected: list[dict] = []
            selected_files = 0
            for entry in entries:
                entry_files = sum(
                    1 for field in ("raw_path", "text_path", "manifest_path")
                    if Path(entry.get(field) or "").is_file()
                )
                if selected and selected_files + entry_files > file_limit:
                    break
                if entry_files:
                    selected.append(entry)
                    selected_files += entry_files
                if selected_files >= file_limit:
                    break
            entries = selected
        if not entries:
            continue
        for entry in entries:
            for field in ("raw_path", "text_path", "manifest_path"):
                path = Path(entry.get(field) or "")
                if path.is_file():
                    result["bytes_before"] += path.stat().st_size
                    result["files_packed"] += 1
        if dry_run:
            result["bundles"] += 1
            continue
        bundle.parent.mkdir(parents=True, exist_ok=True)
        temp_name = ""
        try:
            fd, temp_name = tempfile.mkstemp(prefix=".raw-bundle-", suffix=".tar.gz", dir=bundle.parent)
            os.close(fd)
            with tarfile.open(temp_name, mode="w:gz", compresslevel=6) as archive:
                if existing_index and bundle.exists():
                    with tarfile.open(bundle, mode="r:gz") as previous:
                        for member in previous.getmembers():
                            if member.name == "_index.json":
                                continue
                            extracted = previous.extractfile(member) if member.isfile() else None
                            archive.addfile(member, extracted)
                for entry in entries:
                    for field, member_field in (("raw_path", "raw_member"), ("text_path", "text_member"), ("manifest_path", "manifest_member")):
                        path = Path(entry.get(field) or "")
                        member = str(entry.get(member_field) or "")
                        if path.is_file() and member:
                            _add_file_to_tar(archive, path, member)
                index = {
                    "format": BUNDLE_FORMAT,
                    "created_at": now.isoformat(timespec="seconds"),
                    "source": bucket["source"],
                    "date": "/".join(_date_parts(bucket["fetched_at"])),
                    "ttl_days": bucket["ttl_days"],
                    "entries": [*existing_entries, *entries],
                }
                encoded = json.dumps(index, ensure_ascii=False, sort_keys=True).encode("utf-8")
                info = tarfile.TarInfo("_index.json")
                info.size = len(encoded)
                info.mtime = int(now.timestamp())
                archive.addfile(info, io.BytesIO(encoded))
            os.replace(temp_name, bundle)
            temp_name = ""
            result["bundles"] += 1
            for entry in entries:
                for field in ("raw_path", "text_path", "manifest_path"):
                    path = Path(entry.get(field) or "")
                    if path.is_file():
                        path.unlink()
                        _remove_empty_parents(path, raw if field != "text_path" else text)
            result["bytes_after"] += bundle.stat().st_size
        except (OSError, tarfile.TarError, ValueError) as exc:
            result["errors"].append(f"{bundle}: {exc}")
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink()
                except OSError:
                    pass
    if dry_run:
        result["bytes_after"] = 0
    return result


def _load_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _dedupe_index_path() -> Path:
    return reports_root() / "raw_archive_dedupe_index.json"


def load_dedupe_index() -> dict[str, str]:
    """archived_at 시각으로 키를 저장하는 간이 크로스런 dedupe 인덱스.

    같은 기사가 API 응답에 계속 남아있는 동안(예: saveticker top-stories) 매 폴링마다
    save_raw_artifact 가 재호출돼 파일이 무한 중복 저장되는 걸 막는다. 파일 수만 개를
    스캔하지 않고 O(1) 조회하기 위한 별도 인덱스.
    """
    path = _dedupe_index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_dedupe_index(index: dict[str, str], *, now: datetime | None = None, keep_days: int = 14) -> None:
    now = _ensure_tz(now or datetime.now(KST))
    cutoff = now - timedelta(days=keep_days)
    pruned = {}
    for key, ts in index.items():
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
        except Exception:
            continue
        if dt >= cutoff:
            pruned[key] = ts
    _dedupe_index_path().write_text(json.dumps(pruned, ensure_ascii=False), encoding="utf-8")


def cleanup_expired_raw_artifacts(now: datetime | None = None) -> dict:
    """만료된 원본 아카이브 정리 — 각 아티팩트가 기록 시점에 저장한 자기 자신의
    expires_at(소스별 정책, resolve_raw_ttl_days) 기준으로만 판단한다. 소스마다
    보존기간이 달라 전역 ttl_days 매개변수로 일괄 override 하는 개념이 없다
    (과거엔 매개변수가 있었지만 실제로는 아무 데도 쓰이지 않던 죽은 파라미터 —
    감사 #34)."""
    now = _ensure_tz(now or datetime.now(KST))
    deleted_raw = 0
    deleted_manifests = 0
    deleted_bundles = 0
    deleted_bundle_entries = 0
    scanned = 0
    root = raw_root()
    for manifest_path in root.rglob("*.json"):
        scanned += 1
        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue
        expires_at = manifest.get("expires_at")
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=KST)
        except Exception:
            continue
        if expires_dt > now:
            continue
        raw_path = Path(manifest.get("raw_path") or "")
        if raw_path.exists():
            raw_path.unlink()
            deleted_raw += 1
        if manifest_path.exists():
            manifest_path.unlink()
            deleted_manifests += 1
    bundle_root = root / BUNDLE_DIRNAME
    try:
        bundles = list(bundle_root.rglob("*.tar.gz")) if bundle_root.exists() else []
    except OSError:
        bundles = []
    for bundle in bundles:
        index = _bundle_index(bundle)
        entries = (index or {}).get("entries") or []
        if not entries:
            continue
        expiry_times = [_parse_datetime(entry.get("expires_at")) for entry in entries if isinstance(entry, dict)]
        if len(expiry_times) != len(entries) or any(value is None or value > now for value in expiry_times):
            continue
        try:
            bundle.unlink()
            deleted_bundles += 1
            deleted_bundle_entries += len(entries)
            _remove_empty_parents(bundle, bundle_root)
        except OSError:
            continue
    return {
        "scanned": scanned,
        "deleted_raw": deleted_raw,
        "deleted_manifests": deleted_manifests,
        "deleted_bundles": deleted_bundles,
        "deleted_bundle_entries": deleted_bundle_entries,
    }
