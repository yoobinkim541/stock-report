# Market Snapshot Store Design

## Goal

AI 콘솔이 한국 시장 마이크로스트럭처 데이터를 빠르게 읽을 수 있도록, 파일 캐시를 기본 저장소로 쓰고 Redis를 선택 확장으로 붙일 수 있는 저장소 경계를 만든다.

## Scope

This design covers only the storage and AI-console read path. It does not implement Kiwoom/KRX collectors yet. Future collectors can write the same payload shape to `~/.cache/kr_market_microstructure.json` or Redis key `market:kr:microstructure`.

## Data Flow

```text
future Kiwoom/KRX/Naver collector
  -> FileSnapshotStore or RedisSnapshotStore
  -> market_snapshot_store.load_market_microstructure()
  -> realtime_market.build_market_snapshot()
  -> AI console prompt and UI metadata
```

## Storage Behavior

- Default storage is `FileSnapshotStore`.
- File path defaults to `~/.cache/kr_market_microstructure.json`.
- `KR_MARKET_MICROSTRUCTURE_CACHE` overrides the file path.
- If `REDIS_URL` or `UPSTASH_REDIS_URL` exists, Redis is attempted first.
- If Redis returns empty, stale, or fails, the loader falls back to the file cache.
- Redis is optional. Missing `redis` package must not break the AI console.

## Payload Shape

```json
{
  "ts": 1785128400,
  "as_of": "2026-07-27T05:00:00+00:00",
  "max_age_s": 120,
  "source": "kiwoom_collector",
  "indices": {
    "kospi": {"price": 3210.5, "change_pct": 0.7},
    "kosdaq": {"price": 820.1, "change_pct": -0.2}
  },
  "investor_flow": {
    "kospi": {"foreign_net": 120000000000, "institution_net": -50000000000}
  },
  "k200_futures": {"price": 425.5, "change_pct": 0.4, "foreign_net": 3500},
  "breadth": {"advancers": 512, "decliners": 318, "unchanged": 75}
}
```

## Freshness

The loader rejects payloads when `now - ts > max_age_s`. If `ts` is missing, the payload is treated as usable for backward compatibility, but future collectors should always write `ts`.

## AI Console Behavior

`realtime_market.build_market_snapshot()` merges microstructure fields into the existing market snapshot. Populated fields are removed from the `unavailable` list. Compact prompt lines include KOSPI, KOSDAQ, investor flow, KOSPI200 futures, and market breadth when present.

## Trade-Off

This design avoids adding Redis as a mandatory dependency now. The trade-off is that cross-server real-time sharing still depends on enabling Redis later through environment variables and installing the Redis client package.
