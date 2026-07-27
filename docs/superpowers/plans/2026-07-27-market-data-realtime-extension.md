# Market Data Realtime Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 콘솔이 한국 장중 수급/선물과 미국 프리장/본장/애프터장 실시간 시세를 실제 연결된 API와 공개 보조 소스로 읽어 답변 컨텍스트에 반영한다.

**Architecture:** `providers.kr_microstructure`가 지수/시장폭/수급/선물/환율을 정규화한 스냅샷을 만들고 `agent_console.realtime_market`가 이를 읽는다. 미국 시세는 `quotes_poller`가 Toss/Kiwoom REST 캐시에 세션 메타를 저장하고 `providers.realtime_quotes`가 WS/REST 우선순위로 읽는다.

**Tech Stack:** Python 3.11, pytest, Kiwoom REST wrapper, Toss/KIS quote providers, Vercel Flask entrypoint, cron collectors.

## Global Constraints

- Read-only market data only; order endpoints must remain absent from quote/microstructure collectors.
- Do not fabricate unavailable fields; expose explicit `unavailable` reasons.
- Write failing tests before production changes.
- Keep existing cache contracts backwards compatible.
- Commit messages must start with `fix)` or `add)` and be Korean.

---

### Task 1: Kiwoom Investor Flow Parser And Fetcher

**Files:**
- Modify: `providers/kr_microstructure.py`
- Test: `tests/test_kr_microstructure.py`

**Interfaces:**
- Consumes: Kiwoom `Sector.industrywise_investor_net_buy_request_ka10051(mrkt_tp, amt_qty_tp, stex_tp, base_dt)` result.
- Produces: `parse_kiwoom_investor_flow_payload(payload: dict, market: str) -> dict` and `fetch_kiwoom_investor_flow() -> dict`.

- [ ] **Step 1: Write the failing test**

```python
def test_parse_kiwoom_investor_flow_sums_sector_rows_to_market_krw():
    from providers import kr_microstructure as km
    payload = {"inds_netprps": [
        {"inds_nm": "종합", "frgnr_netprps": "+1,200", "orgn_netprps": "-300", "ind_netprps": "-900"},
        {"inds_nm": "대형주", "frgnr_netprps": "+200", "orgn_netprps": "-50", "ind_netprps": "-150"},
    ]}
    got = km.parse_kiwoom_investor_flow_payload(payload, market="kospi")
    assert got["kospi"] == {
        "foreign_net": 140000000,
        "institution_net": -35000000,
        "individual_net": -105000000,
        "unit": "KRW",
        "source": "kiwoom_ka10051",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_kr_microstructure.py::test_parse_kiwoom_investor_flow_sums_sector_rows_to_market_krw -q`
Expected: FAIL because `parse_kiwoom_investor_flow_payload` is missing.

- [ ] **Step 3: Write minimal implementation**

```python
def parse_kiwoom_investor_flow_payload(payload: dict, market: str) -> dict:
    rows = payload.get("inds_netprps") if isinstance(payload, dict) else []
    totals = {"foreign_net": 0.0, "institution_net": 0.0, "individual_net": 0.0}
    for row in rows or []:
        totals["foreign_net"] += _float(row.get("frgnr_netprps")) or 0.0
        totals["institution_net"] += _float(row.get("orgn_netprps")) or 0.0
        totals["individual_net"] += _float(row.get("ind_netprps")) or 0.0
    if not any(totals.values()):
        return {}
    return {market: {**{k: v * 100000 for k, v in totals.items()}, "unit": "KRW", "source": "kiwoom_ka10051"}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_kr_microstructure.py::test_parse_kiwoom_investor_flow_sums_sector_rows_to_market_krw -q`
Expected: PASS.

### Task 2: KOSPI200 Futures Availability Contract

**Files:**
- Modify: `providers/kr_microstructure.py`
- Test: `tests/test_kr_microstructure.py`

**Interfaces:**
- Consumes: source-file futures payload first; broker futures fetcher if a read-only endpoint is available.
- Produces: `fetch_k200_futures()` returns normalized futures dict or `None`, never KOSPI200 index mislabeled as futures.

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_k200_futures_does_not_alias_kospi200_index(monkeypatch):
    from providers import kr_microstructure as km
    monkeypatch.setattr(km, "fetch_snapshot_file", lambda: {"indices": {"kospi200": {"price": 452.2}}})
    assert km.fetch_k200_futures() is None
```

- [ ] **Step 2: Run test to verify it fails or confirms current behavior**

Run: `uv run pytest tests/test_kr_microstructure.py::test_fetch_k200_futures_does_not_alias_kospi200_index -q`
Expected: PASS if already safe; if not, FAIL and fix before any broker addition.

- [ ] **Step 3: Add read-only broker fetcher only after live endpoint shape is proven**

Implement `fetch_broker_k200_futures()` with explicit environment gate and source metadata. If no current Toss/Kiwoom/KIS read-only futures endpoint is available, leave `fetch_k200_futures()` source-file only and document unavailable reason.

### Task 3: US Extended-Hours Quote Sessions

**Files:**
- Modify: `quotes_poller.py`
- Modify: `providers/realtime_quotes.py`
- Modify: `agent_console/realtime_market.py`
- Test: `tests/test_quotes_poller.py`

**Interfaces:**
- Produces: `us_trading_session(now: datetime | None = None) -> str` returning `premarket`, `regular`, `afterhours`, or `closed`.
- Produces: REST cache entries include `session` for US symbols.

- [ ] **Step 1: Write the failing test**

```python
def test_us_trading_session_covers_extended_hours():
    from datetime import datetime, timezone
    import quotes_poller as Q
    assert Q.us_trading_session(datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc)) == "premarket"
    assert Q.us_trading_session(datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)) == "regular"
    assert Q.us_trading_session(datetime(2026, 7, 13, 21, 30, tzinfo=timezone.utc)) == "afterhours"
    assert Q.us_trading_session(datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)) == "closed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_quotes_poller.py::test_us_trading_session_covers_extended_hours -q`
Expected: FAIL because `us_trading_session` is missing.

- [ ] **Step 3: Implement session classifier and cache metadata**

Use `zoneinfo.ZoneInfo("America/New_York")`; weekdays only; premarket 04:00-09:30 ET, regular 09:30-16:00 ET, afterhours 16:00-20:00 ET.

- [ ] **Step 4: Verify focused tests**

Run: `uv run pytest tests/test_quotes_poller.py tests/test_realtime_quotes.py tests/test_agent_realtime_market_context.py -q`.

### Task 4: Deployment And QA

**Files:**
- Modify: `deploy/crontab.stock-report` only if new collector flags or processes are required.

- [ ] **Step 1: Run QA**

Run: `uv run pytest tests/test_kr_microstructure.py tests/test_kr_microstructure_snapshot.py tests/test_agent_realtime_market_context.py tests/test_quotes_poller.py tests/test_realtime_quotes.py tests/test_kis_stream.py -q`

- [ ] **Step 2: Run live collector smoke**

Run: `KR_MARKET_MICROSTRUCTURE_ENABLED=true AGENT_CONSOLE_TOSS_FX_ENABLED=true uv run python crons/kr_microstructure_snapshot.py`
Expected: writes snapshot with all available fields and explicit unavailable fields.

- [ ] **Step 3: Commit, push, deploy, verify production**

Commit with Korean `add)` message, push `master`, confirm Vercel production Ready and `/ai-console` HTTP 200.
