"""Shared maintenance schema and read-only access to approved source text."""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "wiki-maintainer.md"


def maintenance_schema() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    if not text.strip() or len(text) > 8000:
        raise ValueError("invalid wiki maintenance schema")
    return text


def read_raw_document(path, *, roots, max_bytes: int = 4_000_000) -> dict:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 16_000_000:
        raise ValueError("invalid source size limit")
    if str(path).startswith(("http://", "https://")):
        raise ValueError("remote URL is not an archived source")
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
        allowed = [Path(root).expanduser().resolve(strict=True) for root in roots]
    except OSError as exc:
        raise ValueError("source unavailable") from exc
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError("source outside permitted raw roots")
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("source must be a regular file")
    with resolved.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise ValueError("source empty or exceeds size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("source text extraction required") from exc
    if not text.strip():
        raise ValueError("source text empty")
    return {"text": text, "sha256": hashlib.sha256(data).hexdigest(),
            "complete": True, "bytes": len(data), "path": str(resolved)}
