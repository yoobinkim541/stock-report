from __future__ import annotations

import json
import os
import time
from pathlib import Path


DEFAULT_FILE_PATH = Path(os.path.expanduser("~/.cache/kr_market_microstructure.json"))
DEFAULT_REDIS_KEY = "market:kr:microstructure"
DEFAULT_MAX_AGE_S = 120


class FileSnapshotStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("KR_MARKET_MICROSTRUCTURE_CACHE", str(DEFAULT_FILE_PATH))).expanduser()

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write(self, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            payload = dict(payload)
            payload.setdefault("ts", time.time())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                import safe_io

                safe_io.atomic_write_json(str(self.path), payload)
            except Exception:
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                os.replace(tmp, self.path)
            return True
        except Exception:
            return False


class RedisSnapshotStore:
    def __init__(self, url: str, key: str = DEFAULT_REDIS_KEY):
        self.url = str(url or "")
        self.key = str(key or DEFAULT_REDIS_KEY)

    def _client(self):
        import redis

        return redis.Redis.from_url(self.url, decode_responses=True, socket_timeout=1.0, socket_connect_timeout=1.0)

    def read(self) -> dict:
        if not self.url:
            return {}
        try:
            raw = self._client().get(self.key)
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write(self, payload: dict, ttl_s: int | None = None) -> bool:
        if not self.url or not isinstance(payload, dict):
            return False
        try:
            payload = dict(payload)
            payload.setdefault("ts", time.time())
            ttl = int(ttl_s or payload.get("max_age_s") or DEFAULT_MAX_AGE_S)
            self._client().setex(self.key, ttl, json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return True
        except Exception:
            return False


def default_store():
    redis_url = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL")
    if redis_url:
        return RedisSnapshotStore(redis_url, os.getenv("KR_MARKET_MICROSTRUCTURE_REDIS_KEY", DEFAULT_REDIS_KEY))
    return FileSnapshotStore()


def _fresh(payload: dict, now: float | None = None) -> bool:
    if not payload:
        return False
    try:
        ts = float(payload.get("ts") or 0)
    except (TypeError, ValueError):
        return True
    if ts <= 0:
        return True
    max_age = float(payload.get("max_age_s") or os.getenv("KR_MARKET_MICROSTRUCTURE_STALE_S", DEFAULT_MAX_AGE_S))
    return (time.time() if now is None else now) - ts <= max_age


def _read_fresh(store) -> dict:
    try:
        payload = store.read()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) and _fresh(payload) else {}


def load_market_microstructure(store=None) -> dict:
    if store is not None:
        return _read_fresh(store)
    primary = default_store()
    payload = _read_fresh(primary)
    if payload:
        return payload
    if isinstance(primary, RedisSnapshotStore):
        return _read_fresh(FileSnapshotStore())
    return {}
