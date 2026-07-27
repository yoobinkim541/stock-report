# Intraday Market Data Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the AI console chatbot reliable intraday market context for Korean indices, investor flows, KOSPI200 futures, market breadth, USD/KRW, source freshness, and unavailable-field explanations.

**Architecture:** Keep the chatbot read path simple: collectors write a normalized microstructure snapshot to `agent_console.market_snapshot_store`, and `agent_console.realtime_market.build_market_snapshot()` reads only that stable contract plus quote caches. Each source adapter is isolated, so KRX, Naver, Kiwoom/KIS, Toss, and fallback sources can fail independently without breaking chat answers.

**Tech Stack:** Python 3.11, existing JSONL/file cache patterns, optional Redis via `agent_console.market_snapshot_store.RedisSnapshotStore`, pytest, existing `providers.*`, `crons.*`, and `agent_console.*` modules.

## Global Constraints

- Never claim a field is real-time unless the payload has `as_of`, `source`, and freshness metadata.
- The chatbot must distinguish `missing`, `stale`, `fallback`, and `unavailable` fields.
- No live order placement. This work is data collection/context only.
- Use file snapshot store by default; Redis is optional and activated only by `REDIS_URL` or `UPSTASH_REDIS_URL`.
- All collectors must degrade gracefully when credentials/API access are absent.
- Korean market timestamps must be represented in KST and converted safely to UTC where needed.
- Keep raw provider payloads out of the chatbot prompt; expose bounded, normalized summaries only.

---

## File Structure

- Modify: `agent_console/market_snapshot_store.py`
  - Owns the normalized microstructure snapshot contract, file/Redis read/write, freshness checks.
- Modify: `agent_console/realtime_market.py`
  - Builds the chatbot-facing market snapshot from quote cache, FX, and microstructure store.
- Create: `providers/kr_microstructure.py`
  - Normalizes KRX/Naver/broker/fallback records into one schema.
- Create: `crons/kr_microstructure_snapshot.py`
  - Scheduled collector that fetches Korean intraday context and writes the snapshot.
- Modify: `tests/test_market_snapshot_store.py`
  - Contract tests for snapshot read/write/freshness/fallback.
- Modify: `tests/test_agent_realtime_market_context.py`
  - Tests that the chatbot context includes indices, flow, futures, breadth, USD/KRW, and missing-field explanations.
- Create: `tests/test_kr_microstructure.py`
  - Provider normalization and source fallback tests.
- Create: `tests/test_kr_microstructure_snapshot.py`
  - Cron writer tests using fake provider data.
- Modify: `tests/bot_healthcheck.py`
  - Add source freshness checks for microstructure snapshot age and stale subfields.

---

### Task 1: Snapshot Contract And Store Helpers

**Files:**
- Modify: `agent_console/market_snapshot_store.py`
- Modify: `tests/test_market_snapshot_store.py`

**Interfaces:**
- Produces: `normalize_market_microstructure(payload: dict, now: float | None = None) -> dict`
- Produces: `write_market_microstructure(payload: dict, store=None) -> bool`
- Consumes later: `load_market_microstructure(store=None) -> dict`

- [ ] **Step 1: Write failing tests for normalized schema**

Add tests requiring this shape:

```python
def test_normalize_market_microstructure_preserves_core_fields():
    from agent_console import market_snapshot_store as s

    payload = {
        "as_of": "2026-07-27T10:15:00+09:00",
        "source": "krx_public",
        "indices": {"kospi": {"price": 3310.2, "change_pct": 0.42}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -40000000000}},
        "k200_futures": {"price": 452.2, "change_pct": 0.31, "foreign_net": 1800},
        "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74},
        "fx": {"usdkrw": {"rate": 1387.2, "change": -2.1}},
    }

    got = s.normalize_market_microstructure(payload, now=1_000.0)

    assert got["schema"] == "kr-market-microstructure.v1"
    assert got["ts"] == 1_000.0
    assert got["max_age_s"] == 120
    assert got["indices"]["kospi"]["price"] == 3310.2
    assert got["investor_flow"]["kospi"]["foreign_net"] == 120000000000
    assert got["breadth"]["advancers"] == 510
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/pytest -q tests/test_market_snapshot_store.py::test_normalize_market_microstructure_preserves_core_fields`

Expected: FAIL with `AttributeError: module ... has no attribute 'normalize_market_microstructure'`.

- [ ] **Step 3: Implement store helpers**

Add:

```python
def normalize_market_microstructure(payload: dict, now: float | None = None) -> dict:
    payload = dict(payload or {})
    out = {
        "schema": "kr-market-microstructure.v1",
        "ts": time.time() if now is None else float(now),
        "max_age_s": int(payload.get("max_age_s") or DEFAULT_MAX_AGE_S),
        "as_of": payload.get("as_of"),
        "source": payload.get("source") or "unknown",
        "indices": payload.get("indices") or {},
        "investor_flow": payload.get("investor_flow") or {},
        "k200_futures": payload.get("k200_futures") or None,
        "breadth": payload.get("breadth") or payload.get("advancers_decliners") or None,
        "fx": payload.get("fx") or {},
        "field_status": payload.get("field_status") or {},
        "errors": payload.get("errors") or [],
    }
    return out


def write_market_microstructure(payload: dict, store=None) -> bool:
    target = store or default_store()
    normalized = normalize_market_microstructure(payload)
    ok = target.write(normalized)
    if ok and isinstance(target, RedisSnapshotStore):
        FileSnapshotStore().write(normalized)
    return ok
```

- [ ] **Step 4: Run store tests**

Run: `.venv/bin/pytest -q tests/test_market_snapshot_store.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_console/market_snapshot_store.py tests/test_market_snapshot_store.py
git commit -m "add) 장중 시장 스냅샷 계약 정규화"
```

---

### Task 2: Korean Microstructure Provider Normalizer

**Files:**
- Create: `providers/kr_microstructure.py`
- Create: `tests/test_kr_microstructure.py`

**Interfaces:**
- Produces: `build_snapshot(now: datetime | None = None, sources: dict | None = None) -> dict`
- Produces: `normalize_indices(raw: dict) -> dict`
- Produces: `normalize_investor_flow(raw: dict) -> dict`
- Produces: `normalize_futures(raw: dict) -> dict | None`
- Produces: `normalize_breadth(raw: dict) -> dict | None`
- Produces: `field_status(name: str, source: str, ok: bool, as_of: str | None, error: str | None = None) -> dict`

- [ ] **Step 1: Write failing normalizer tests**

```python
def test_build_snapshot_merges_indices_flow_futures_breadth_and_status():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from providers import kr_microstructure as km

    now = datetime(2026, 7, 27, 10, 15, tzinfo=ZoneInfo("Asia/Seoul"))
    sources = {
        "indices": {"kospi": {"price": 3310.2, "change_pct": 0.42}, "kosdaq": {"price": 912.1, "change_pct": -0.2}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -40000000000}},
        "k200_futures": {"price": 452.2, "change_pct": 0.31, "foreign_net": 1800},
        "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74},
        "fx": {"usdkrw": {"rate": 1387.2, "change": -2.1}},
    }

    got = km.build_snapshot(now=now, sources=sources)

    assert got["as_of"] == "2026-07-27T10:15:00+09:00"
    assert got["indices"]["kospi"]["price"] == 3310.2
    assert got["investor_flow"]["kospi"]["foreign_net"] == 120000000000
    assert got["field_status"]["breadth"]["ok"] is True
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure.py`

Expected: FAIL because `providers.kr_microstructure` does not exist.

- [ ] **Step 3: Implement pure normalization first**

Implement a source-agnostic module. Do not add network calls in this task. The module should accept injected `sources` so later provider-specific fetchers can plug in safely.

```python
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
FIELDS = ("indices", "investor_flow", "k200_futures", "breadth", "fx")


def field_status(name: str, source: str, ok: bool, as_of: str | None, error: str | None = None) -> dict:
    out = {"field": name, "source": source, "ok": bool(ok), "as_of": as_of}
    if error:
        out["error"] = str(error)
    return out


def _asof(now: datetime | None = None) -> str:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST).isoformat(timespec="seconds")


def _dict(value):
    return value if isinstance(value, dict) else {}


def build_snapshot(now: datetime | None = None, sources: dict | None = None) -> dict:
    sources = _dict(sources)
    as_of = _asof(now)
    out = {
        "as_of": as_of,
        "source": "kr_microstructure",
        "indices": _dict(sources.get("indices")),
        "investor_flow": _dict(sources.get("investor_flow")),
        "k200_futures": sources.get("k200_futures") if isinstance(sources.get("k200_futures"), dict) else None,
        "breadth": sources.get("breadth") if isinstance(sources.get("breadth"), dict) else None,
        "fx": _dict(sources.get("fx")),
        "field_status": {},
        "errors": [],
    }
    for name in FIELDS:
        value = out.get(name)
        ok = bool(value)
        out["field_status"][name] = field_status(name, "injected", ok, as_of, None if ok else "missing")
    return out
```

- [ ] **Step 4: Run provider tests**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add providers/kr_microstructure.py tests/test_kr_microstructure.py
git commit -m "add) 국내 장중 미시구조 정규화기 추가"
```

---

### Task 3: Collector Cron Writes Snapshot

**Files:**
- Create: `crons/kr_microstructure_snapshot.py`
- Create: `tests/test_kr_microstructure_snapshot.py`

**Interfaces:**
- Consumes: `providers.kr_microstructure.build_snapshot(now, sources) -> dict`
- Consumes: `agent_console.market_snapshot_store.write_market_microstructure(payload, store=None) -> bool`
- Produces: `collect_once(now: datetime | None = None, store=None, fetchers: dict | None = None) -> dict`

- [ ] **Step 1: Write failing cron test**

```python
def test_collect_once_writes_normalized_snapshot(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from agent_console.market_snapshot_store import FileSnapshotStore
    from crons import kr_microstructure_snapshot as cron

    store = FileSnapshotStore(tmp_path / "kr_market_microstructure.json")
    fetchers = {
        "indices": lambda: {"kospi": {"price": 3310.2, "change_pct": 0.42}},
        "investor_flow": lambda: {"kospi": {"foreign_net": 1, "institution_net": 2}},
        "k200_futures": lambda: {"price": 452.2, "foreign_net": 100},
        "breadth": lambda: {"advancers": 510, "decliners": 310},
        "fx": lambda: {"usdkrw": {"rate": 1387.2}},
    }

    result = cron.collect_once(
        now=datetime(2026, 7, 27, 10, 15, tzinfo=ZoneInfo("Asia/Seoul")),
        store=store,
        fetchers=fetchers,
    )

    assert result["ok"] is True
    saved = store.read()
    assert saved["indices"]["kospi"]["price"] == 3310.2
    assert saved["field_status"]["investor_flow"]["ok"] is True
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure_snapshot.py`

Expected: FAIL because cron module does not exist.

- [ ] **Step 3: Implement collector with injected fetchers**

```python
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from agent_console import market_snapshot_store
from providers import kr_microstructure

logger = logging.getLogger(__name__)


def _safe_fetch(name: str, fetchers: dict) -> tuple[object, str | None]:
    fn = (fetchers or {}).get(name)
    if fn is None:
        return None, "fetcher not configured"
    try:
        return fn(), None
    except Exception as exc:
        return None, str(exc)


def collect_once(now: datetime | None = None, store=None, fetchers: dict | None = None) -> dict:
    sources = {}
    errors = []
    for name in ("indices", "investor_flow", "k200_futures", "breadth", "fx"):
        value, error = _safe_fetch(name, fetchers or {})
        if error:
            errors.append({"field": name, "error": error})
        elif value:
            sources[name] = value
    payload = kr_microstructure.build_snapshot(now=now, sources=sources)
    payload["errors"] = errors
    ok = market_snapshot_store.write_market_microstructure(payload, store=store)
    return {"ok": ok, "snapshot": payload, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = collect_once()
    if args.dry_run:
        print(result)
    return 0 if result.get("ok") else 1
```

- [ ] **Step 4: Run cron tests**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure_snapshot.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crons/kr_microstructure_snapshot.py tests/test_kr_microstructure_snapshot.py
git commit -m "add) 국내 장중 미시구조 스냅샷 크론 추가"
```

---

### Task 4: Real Source Fetchers With Fallbacks

**Files:**
- Modify: `providers/kr_microstructure.py`
- Modify: `tests/test_kr_microstructure.py`

**Interfaces:**
- Produces: `fetch_indices() -> dict`
- Produces: `fetch_investor_flow() -> dict`
- Produces: `fetch_k200_futures() -> dict | None`
- Produces: `fetch_breadth() -> dict | None`
- Produces: `fetch_fx() -> dict`
- Consumes: existing `providers.toss_api`, `providers.kr_market_data`, `crons/naver_flow_snapshot.py` patterns where available.

- [ ] **Step 1: Write tests using monkeypatched provider calls**

```python
def test_fetch_fx_uses_injected_toss_provider(monkeypatch):
    from providers import kr_microstructure as km

    class FakeToss:
        @staticmethod
        def exchange_rate(base, quote):
            assert (base, quote) == ("USD", "KRW")
            return 1387.2

    monkeypatch.setitem(km.PROVIDER_REGISTRY, "toss_api", FakeToss)

    assert km.fetch_fx()["usdkrw"]["rate"] == 1387.2
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure.py::test_fetch_fx_uses_injected_toss_provider`

Expected: FAIL because `PROVIDER_REGISTRY` or `fetch_fx` does not exist.

- [ ] **Step 3: Implement fetchers conservatively**

Implementation rules:
- `fetch_fx()` uses `providers.toss_api.exchange_rate("USD", "KRW")` when available.
- `fetch_indices()` first tries broker/KIS/Kiwoom-compatible provider if present, then `providers.kr_market_data` if it exposes index functions, then returns `{}`.
- `fetch_investor_flow()` reads latest normalized line from `/home/ubuntu/reports/ml-data/kr_flow_snapshots.jsonl` as end-of-day fallback and labels it stale/previous-session in `field_status`; later broker API can replace it.
- `fetch_k200_futures()` returns `{}` until KRX/broker futures endpoint is configured; it must not fabricate values.
- `fetch_breadth()` returns `{}` until KRX/Naver all-symbol snapshot is configured; it must not infer breadth from watchlist.

- [ ] **Step 4: Update cron default fetchers**

In `crons/kr_microstructure_snapshot.py`, add:

```python
def default_fetchers() -> dict:
    return {
        "indices": kr_microstructure.fetch_indices,
        "investor_flow": kr_microstructure.fetch_investor_flow,
        "k200_futures": kr_microstructure.fetch_k200_futures,
        "breadth": kr_microstructure.fetch_breadth,
        "fx": kr_microstructure.fetch_fx,
    }
```

and call `collect_once(fetchers=default_fetchers())` in `main()`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest -q tests/test_kr_microstructure.py tests/test_kr_microstructure_snapshot.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add providers/kr_microstructure.py crons/kr_microstructure_snapshot.py tests/test_kr_microstructure.py tests/test_kr_microstructure_snapshot.py
git commit -m "add) 국내 장중 수급 데이터 fetcher 골격 추가"
```

---

### Task 5: Chatbot Market Snapshot Rendering

**Files:**
- Modify: `agent_console/realtime_market.py`
- Modify: `tests/test_agent_realtime_market_context.py`
- Modify: `tests/test_agent_console.py` if prompt behavior needs a regression test.

**Interfaces:**
- Consumes: `market_snapshot_store.load_market_microstructure() -> dict`
- Produces: enriched `build_market_snapshot()` with `indices`, `investor_flow`, `k200_futures`, `breadth`, `fx`, `field_status`, `unavailable`.
- Produces: `compact_snapshot_lines(snapshot: dict) -> list[str]` lines visible in LLM prompt.

- [ ] **Step 1: Write failing context test**

```python
def test_compact_snapshot_lines_include_field_status_and_asof(monkeypatch):
    from agent_console import realtime_market

    monkeypatch.setattr(realtime_market.market_snapshot_store, "load_market_microstructure", lambda: {
        "schema": "kr-market-microstructure.v1",
        "as_of": "2026-07-27T10:15:00+09:00",
        "source": "kr_microstructure",
        "indices": {"kospi": {"price": 3310.2, "change_pct": 0.42}},
        "investor_flow": {"kospi": {"foreign_net": 120000000000, "institution_net": -40000000000}},
        "k200_futures": {"price": 452.2, "foreign_net": 1800},
        "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74},
        "field_status": {"breadth": {"ok": True, "source": "krx_public"}},
    })

    snapshot = realtime_market.build_market_snapshot(symbols=[])
    text = "
".join(realtime_market.compact_snapshot_lines(snapshot))

    assert "KOSPI" in text
    assert "KOSPI 수급" in text
    assert "K200 선물" in text
    assert "시장 폭" in text
    assert "2026-07-27T10:15:00+09:00" in text
```

- [ ] **Step 2: Verify test fails if missing status/as_of behavior is absent**

Run: `.venv/bin/pytest -q tests/test_agent_realtime_market_context.py::test_compact_snapshot_lines_include_field_status_and_asof`

Expected: FAIL until formatting includes the required fields.

- [ ] **Step 3: Extend `build_market_snapshot()` minimally**

- Preserve existing `indices`, `investor_flow`, `k200_futures`, `breadth` keys.
- Add `field_status = micro.get("field_status") or {}`.
- Include `source` and `as_of` for user-visible freshness.
- Keep `UNAVAILABLE_FIELDS` only for fields not populated.

- [ ] **Step 4: Extend `compact_snapshot_lines()`**

Add concise lines:

```python
if snapshot.get("field_status"):
    stale = [k for k, v in snapshot["field_status"].items() if not v.get("ok")]
    if stale:
        lines.append("- 부족 필드: " + ", ".join(stale[:6]))
```

- [ ] **Step 5: Run agent context tests**

Run: `.venv/bin/pytest -q tests/test_agent_realtime_market_context.py tests/test_agent_console.py::test_agent_prompt_includes_realtime_market_snapshot`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_console/realtime_market.py tests/test_agent_realtime_market_context.py tests/test_agent_console.py
git commit -m "add) AI 콘솔 장중 시장 스냅샷 노출 강화"
```

---

### Task 6: Healthcheck And Operational Freshness

**Files:**
- Modify: `tests/bot_healthcheck.py`
- Create or modify: `tests/test_bot_healthcheck.py`

**Interfaces:**
- Produces: `check_market_microstructure_snapshot() -> tuple[str, str] | None`
- Consumes: `agent_console.market_snapshot_store.load_market_microstructure()`

- [ ] **Step 1: Write failing healthcheck test**

```python
def test_market_microstructure_health_warns_when_stale(monkeypatch):
    from tests import bot_healthcheck as health

    monkeypatch.setenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "true")
    monkeypatch.setattr(health.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(health, "_load_market_microstructure_for_health", lambda: {
        "ts": 100.0,
        "max_age_s": 120,
        "as_of": "2026-07-27T09:00:00+09:00",
    })

    key, msg = health.check_market_microstructure_snapshot()

    assert key == "market_microstructure_stale"
    assert "장중 시장 스냅샷" in msg
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/pytest -q tests/test_bot_healthcheck.py::test_market_microstructure_health_warns_when_stale`

Expected: FAIL because function is missing.

- [ ] **Step 3: Implement healthcheck**

Add helper:

```python
def _load_market_microstructure_for_health() -> dict:
    from agent_console import market_snapshot_store
    return market_snapshot_store.load_market_microstructure()
```

Add check:

```python
def check_market_microstructure_snapshot() -> tuple[str, str] | None:
    if os.getenv("KR_MARKET_MICROSTRUCTURE_ENABLED", "false").lower() != "true":
        return None
    payload = _load_market_microstructure_for_health()
    if not payload:
        return ("market_microstructure_missing", "⚠️ 장중 시장 스냅샷 없음")
    age = time.time() - float(payload.get("ts") or 0)
    max_age = float(payload.get("max_age_s") or 120)
    if age > max_age:
        return ("market_microstructure_stale", f"⚠️ 장중 시장 스냅샷 {age/60:.0f}분 미갱신 — {payload.get('as_of')}")
    return None
```

Append it to the `checks` list near `check_intraday_bars`.

- [ ] **Step 4: Run healthcheck tests**

Run: `.venv/bin/pytest -q tests/test_bot_healthcheck.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/bot_healthcheck.py tests/test_bot_healthcheck.py
git commit -m "add) 장중 시장 스냅샷 헬스체크 추가"
```

---

### Task 7: Documentation And Cron Wiring

**Files:**
- Modify: `README.md` or create `docs/intraday-market-data.md`
- Modify deployment/cron docs if present.

**Interfaces:**
- Documents env vars:
  - `KR_MARKET_MICROSTRUCTURE_ENABLED=true`
  - `KR_MARKET_MICROSTRUCTURE_CACHE=/home/ubuntu/.cache/kr_market_microstructure.json`
  - `KR_MARKET_MICROSTRUCTURE_STALE_S=120`
  - `REDIS_URL` or `UPSTASH_REDIS_URL` optional
  - `AGENT_CONSOLE_TOSS_FX_ENABLED=true` optional

- [ ] **Step 1: Write operational doc**

Create `docs/intraday-market-data.md` with:

```markdown
# Intraday Market Data

The AI console reads market context from `agent_console.realtime_market.build_market_snapshot()`.
The write path is `crons/kr_microstructure_snapshot.py -> agent_console.market_snapshot_store`.

## Fields
- `indices.kospi`, `indices.kosdaq`: price, change_pct, source, as_of
- `investor_flow.kospi`, `investor_flow.kosdaq`: foreign_net, institution_net, retail_net, source, as_of
- `k200_futures`: price, change_pct, foreign_net, source, as_of
- `breadth`: advancers, decliners, unchanged, source, as_of
- `fx.usdkrw`: rate, change, source, as_of

## Cron
Run every 1 minute during KR market hours:
`* * * * * cd /home/ubuntu/projects/stock-report && .venv/bin/python crons/kr_microstructure_snapshot.py >> /tmp/kr_microstructure_snapshot.log 2>&1`
```

- [ ] **Step 2: Run doc-adjacent smoke**

Run: `.venv/bin/python crons/kr_microstructure_snapshot.py --dry-run`

Expected: exit 0 if at least file write succeeds or dry-run reports missing optional fetchers explicitly.

- [ ] **Step 3: Run final test subset**

Run:

```bash
.venv/bin/pytest -q   tests/test_market_snapshot_store.py   tests/test_kr_microstructure.py   tests/test_kr_microstructure_snapshot.py   tests/test_agent_realtime_market_context.py   tests/test_bot_healthcheck.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/intraday-market-data.md
git commit -m "docs) 장중 시장 데이터 운영 문서 추가"
```

---

## Self-Review

**Spec coverage:**
- KOSPI/KOSDAQ index support: Task 2, Task 4, Task 5.
- Foreign/institution flow: Task 2, Task 4, Task 5.
- KOSPI200 futures: Task 2, Task 4, Task 5.
- USD/KRW: Task 2, Task 4, Task 5.
- Advancers/decliners: Task 2, Task 4, Task 5.
- Chatbot visibility: Task 5.
- Freshness/health: Task 1, Task 6, Task 7.
- Redis/file persistence: Task 1.

**Placeholder scan:** No TBD/TODO/implement-later placeholders. Source fetchers that cannot be honestly implemented without API credentials are explicitly designed to return missing/unavailable status instead of fabricated values.

**Type consistency:** `build_snapshot()`, `write_market_microstructure()`, `load_market_microstructure()`, `build_market_snapshot()`, and `compact_snapshot_lines()` names match across tasks.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-intraday-market-data-enrichment.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.
