# Institution Hub Cross-Screening & LLM Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add, above the existing institution-hub cards/comparison table (already behind the `_watch_show_hub` lazy toggle), a donut chart per institution, a cross-institution 신규편입/증가/감소 screener across all 10 real 13F institutions, a 90-day House-member top-bought/sold screener, and a button-gated LLM "왜?" explanation of both.

**Architecture:** Pure aggregation functions in `reports/institution_watch.py` (position-change screener) and `providers/congress_trading.py` (top-traded), wrapped by thin `dashboard/views.py` delegates and `dashboard/cached.py` TTL caches, rendered in `dashboard/pages/watchlist.py` inside the existing `_institution_hub_section()`. Reuses `charts.allocation_donut()` — no new chart function.

**Tech Stack:** Python 3.11, pytest, Streamlit `AppTest`, existing `thirteenf`/`congress_trading` providers, plotly (via `dashboard/charts.py`).

## Global Constraints

- Put-option data is NOT available in the House dataset (asset_type is always "Stock") — do not attempt to extract options positions. Confirmed by direct inspection of 23,891 real rows (2026-08-15).
- Cross-institution screening covers exactly these 10 keys (the real 13F-backed ones): `berkshire, bridgewater, scion, citadel, duquesne, pershing_square, point72, third_point, tudor, nps`. `founders_fund` (seed-only) is excluded.
- New/increased/decreased classification threshold: `delta_pct >= 0.005` (0.5 percentage points) for "increased", `<= -0.005` for "decreased" (fraction units, matching the `return_proxy` convention — NOT percent).
- Politician screener window: last 90 days from `datetime.now()`, House only.
- Everything in this plan renders inside the EXISTING `_watch_show_hub` toggle in `dashboard/pages/watchlist.py` — do not add a second toggle or auto-load anything outside it.
- LLM calls (`explain_screen`) are gated behind a NEW dedicated button — never auto-invoked, matching the existing `_watch_llm_btn` precedent for the 13F common-moves summary.
- All monetary/percentage fields already computed elsewhere (e.g. `weight_pct` from `thirteenf.latest_holdings`) are in the units that function already returns — do not re-scale.

---

### Task 1: `screen_position_changes` in `reports/institution_watch.py`

**Files:**
- Modify: `reports/institution_watch.py` (add near `_compute_return_proxy`, reuse its CUSIP-matching idea)
- Test: `tests/test_institution_watch.py`

**Interfaces:**
- Consumes: `thirteenf.latest_holdings(key)` / `thirteenf.latest_holdings(key, skip=1)` — each returns `{"holdings": [{"cusip", "ticker", "issuer", "value_usd", "shares", "weight_pct"}, ...], ...}` or `None`.
- Produces: `screen_position_changes(institution_keys: list[str]) -> dict` returning `{"new_buys": [...], "increased": [...], "decreased": [...]}`. Each list entry: `{"ticker": str|None, "name": str, "institutions": list[str], "count": int, "avg_delta_pct": float}`, sorted by `count` desc then `abs(avg_delta_pct)` desc, capped to 10 entries per bucket. Later tasks (views/cached/UI) call this exact function name and shape.

- [ ] **Step 1: Write the failing tests**

```python
def test_screen_position_changes_classifies_new_increased_decreased(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        # 두 기관 모두 NVDA 를 늘리고, AAPL 은 A만 줄이고, TSLA 는 B만 신규편입
        data = {
            "alpha": {
                0: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 150.0, "shares": 10.0, "weight_pct": 15.0},
                    {"cusip": "AAPL1", "ticker": "AAPL", "issuer": "APPLE",
                     "value_usd": 50.0, "shares": 5.0, "weight_pct": 5.0}],
                1: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0},
                    {"cusip": "AAPL1", "ticker": "AAPL", "issuer": "APPLE",
                     "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0}],
            },
            "beta": {
                0: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 200.0, "shares": 20.0, "weight_pct": 20.0},
                    {"cusip": "TSLA1", "ticker": "TSLA", "issuer": "TESLA",
                     "value_usd": 50.0, "shares": 5.0, "weight_pct": 5.0}],
                1: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 140.0, "shares": 20.0, "weight_pct": 14.0}],
            },
        }
        return {"holdings": data[key][skip]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha", "beta"])

    new_tickers = {r["ticker"] for r in out["new_buys"]}
    inc_tickers = {r["ticker"] for r in out["increased"]}
    dec_tickers = {r["ticker"] for r in out["decreased"]}
    assert new_tickers == {"TSLA"}
    assert inc_tickers == {"NVDA"}
    assert dec_tickers == {"AAPL"}

    nvda = next(r for r in out["increased"] if r["ticker"] == "NVDA")
    assert nvda["count"] == 2
    assert set(nvda["institutions"]) == {"alpha", "beta"}
    assert nvda["avg_delta_pct"] > 0


def test_screen_position_changes_ignores_small_moves_below_threshold(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        rows = {
            0: [{"cusip": "X1", "ticker": "X", "issuer": "X CORP",
                 "value_usd": 100.3, "shares": 10.0, "weight_pct": 10.03}],
            1: [{"cusip": "X1", "ticker": "X", "issuer": "X CORP",
                 "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0}],
        }
        return {"holdings": rows[skip]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha"])

    assert out["increased"] == []
    assert out["decreased"] == []
    assert out["new_buys"] == []


def test_screen_position_changes_skips_institution_with_no_data(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        if key == "broken":
            return None
        return {"holdings": [{"cusip": "A1", "ticker": "A", "issuer": "A CORP",
                              "value_usd": 100.0, "shares": 10.0, "weight_pct": 100.0}]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["broken", "alpha"])

    assert out == {"new_buys": [], "increased": [], "decreased": []}


def test_screen_position_changes_caps_each_bucket_at_ten(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        if skip == 1:
            return {"holdings": []}
        rows = [{"cusip": f"C{i}", "ticker": f"T{i}", "issuer": f"CO{i}",
                 "value_usd": 100.0, "shares": 10.0, "weight_pct": 100.0 / 15}
                for i in range(15)]
        return {"holdings": rows}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha"])

    assert len(out["new_buys"]) == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py -k screen_position_changes -v`
Expected: FAIL with `AttributeError: module 'reports.institution_watch' has no attribute 'screen_position_changes'`

- [ ] **Step 3: Implement `screen_position_changes`**

Add to `reports/institution_watch.py`, near `_compute_return_proxy`:

```python
_SCREEN_DELTA_THRESHOLD = 0.005   # 0.5%p (분수 단위, weight_pct/100 스케일)


def screen_position_changes(institution_keys: list[str]) -> dict:
    """13F 기관들의 직전분기 대비 종목별 비중 변화를 교차 집계 — 신규편입/증가/감소.

    각 기관 current/prior 보유를 CUSIP 매칭해 delta_pct(분수)를 구하고, 같은 티커를
    여러 기관이 어떻게 움직였는지 모아 기관수 내림차순→|평균변화폭| 내림차순 정렬,
    각 버킷 상위 10개. return_proxy 와 같은 raw value_usd/shares 기반(외부가격 불요)."""
    buckets: dict[str, dict[str, dict]] = {"new_buys": {}, "increased": {}, "decreased": {}}

    for key in institution_keys:
        try:
            current = thirteenf.latest_holdings(key)
            prior = thirteenf.latest_holdings(key, skip=1)
        except Exception:
            continue
        if not current or not prior:
            continue
        cur_rows = current.get("holdings") or []
        prior_by_cusip = {p["cusip"]: p for p in (prior.get("holdings") or []) if p.get("cusip")}

        for row in cur_rows:
            cusip = row.get("cusip")
            if not cusip:
                continue
            ticker = row.get("ticker")
            name = row.get("issuer") or ticker or cusip
            now_w = float(row.get("weight_pct") or 0.0) / 100.0
            prior_row = prior_by_cusip.get(cusip)
            prior_w = float(prior_row.get("weight_pct") or 0.0) / 100.0 if prior_row else 0.0
            delta = now_w - prior_w

            if prior_row is None:
                bucket_name = "new_buys"
            elif delta >= _SCREEN_DELTA_THRESHOLD:
                bucket_name = "increased"
            elif delta <= -_SCREEN_DELTA_THRESHOLD:
                bucket_name = "decreased"
            else:
                continue

            entry = buckets[bucket_name].setdefault(
                cusip, {"ticker": ticker, "name": name, "institutions": [], "deltas": []})
            entry["institutions"].append(key)
            entry["deltas"].append(delta)

    out: dict = {}
    for bucket_name, entries in buckets.items():
        rows = []
        for entry in entries.values():
            deltas = entry.pop("deltas")
            rows.append({
                **entry,
                "count": len(entry["institutions"]),
                "avg_delta_pct": round(sum(deltas) / len(deltas), 4),
            })
        rows.sort(key=lambda r: (r["count"], abs(r["avg_delta_pct"])), reverse=True)
        out[bucket_name] = rows[:10]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py -k screen_position_changes -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run broader regression**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py tests/test_thirteenf.py -v`
Expected: all pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add reports/institution_watch.py tests/test_institution_watch.py
git commit -m "add) 13F 교차기관 종목 스크리닝 — 신규편입/비중증가/비중감소"
```

---

### Task 2: `top_traded` in `providers/congress_trading.py`

**Files:**
- Modify: `providers/congress_trading.py`
- Test: `tests/test_congress_trading.py`

**Interfaces:**
- Consumes: `_load_all() -> list[dict]` (already exists — rows with `representative`, `transaction_date` (`MM/DD/YYYY`), `ticker`, `type` (`Purchase`/`Sale`/`Exchange`), `amount_mid`).
- Produces: `top_traded(days: int = 90, limit: int = 10) -> dict` returning `{"bought": [...], "sold": [...]}`. Each entry: `{"ticker": str, "member_count": int, "members": list[str], "total_amount_mid": float}`, sorted by `member_count` desc then `total_amount_mid` desc, capped at `limit`. Rows with `ticker` falsy (e.g. `"--"`) are excluded. `Exchange` type rows are excluded from both buckets.

- [ ] **Step 1: Write the failing tests**

```python
def test_top_traded_groups_by_ticker_and_ranks_by_member_count(monkeypatch):
    from datetime import datetime, timedelta
    today = datetime.now()
    recent = (today - timedelta(days=10)).strftime("%m/%d/%Y")
    old = (today - timedelta(days=200)).strftime("%m/%d/%Y")

    rows = [
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "NVDA",
         "type": "Purchase", "amount_mid": 100000},
        {"representative": "Mike Kelly", "transaction_date": recent, "ticker": "NVDA",
         "type": "Purchase", "amount_mid": 20000},
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "GOOGL",
         "type": "Purchase", "amount_mid": 50000},
        {"representative": "Nancy Pelosi", "transaction_date": old, "ticker": "AMZN",
         "type": "Purchase", "amount_mid": 999999},   # 기간 밖 — 제외
        {"representative": "Mike Kelly", "transaction_date": recent, "ticker": "ABT",
         "type": "Sale", "amount_mid": 8000},
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "--",
         "type": "Purchase", "amount_mid": 1000},   # 티커 없음 — 제외
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "VSNT",
         "type": "Exchange", "amount_mid": 15},   # 교환 — 제외
    ]
    monkeypatch.setattr(ct, "_load_all", lambda: rows)

    out = ct.top_traded(days=90)

    bought_tickers = [r["ticker"] for r in out["bought"]]
    assert bought_tickers[0] == "NVDA"   # member_count=2 가 1등
    nvda = out["bought"][0]
    assert nvda["member_count"] == 2
    assert set(nvda["members"]) == {"Nancy Pelosi", "Mike Kelly"}
    assert "AMZN" not in bought_tickers   # 90일 밖
    assert "--" not in bought_tickers

    sold_tickers = [r["ticker"] for r in out["sold"]]
    assert sold_tickers == ["ABT"]


def test_top_traded_respects_limit(monkeypatch):
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(days=5)).strftime("%m/%d/%Y")
    rows = [{"representative": f"Member {i}", "transaction_date": recent, "ticker": f"T{i}",
            "type": "Purchase", "amount_mid": 1000} for i in range(15)]
    monkeypatch.setattr(ct, "_load_all", lambda: rows)

    out = ct.top_traded(days=90, limit=3)

    assert len(out["bought"]) == 3


def test_top_traded_empty_when_source_unavailable(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: [])
    out = ct.top_traded(days=90)
    assert out == {"bought": [], "sold": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_congress_trading.py -k top_traded -v`
Expected: FAIL with `AttributeError: module 'providers.congress_trading' has no attribute 'top_traded'`

- [ ] **Step 3: Implement `top_traded`**

Add to `providers/congress_trading.py`, after `member_transactions`:

```python
def top_traded(days: int = 90, limit: int = 10) -> dict:
    """최근 days 일 하원 공시 거래를 티커별로 묶어 매수/매도 상위 — 거래한 의원 수
    내림차순(같으면 추정금액 합 내림차순). Exchange·티커 미상('--' 등)은 제외."""
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=days)
    rows = _load_all()

    def _parse(d):
        try:
            return datetime.strptime(d or "", "%m/%d/%Y")
        except ValueError:
            return None

    buckets = {"Purchase": {}, "Sale": {}}
    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        if not ticker or ticker == "--":
            continue
        tx_type = r.get("type")
        if tx_type not in buckets:
            continue
        dt = _parse(r.get("transaction_date"))
        if dt is None or dt < cutoff:
            continue
        entry = buckets[tx_type].setdefault(
            ticker, {"ticker": ticker, "members": set(), "total_amount_mid": 0.0})
        entry["members"].add(r.get("representative") or "?")
        entry["total_amount_mid"] += float(r.get("amount_mid") or 0)

    def _finalize(bucket: dict) -> list[dict]:
        rows_out = [{
            "ticker": e["ticker"],
            "member_count": len(e["members"]),
            "members": sorted(e["members"]),
            "total_amount_mid": e["total_amount_mid"],
        } for e in bucket.values()]
        rows_out.sort(key=lambda r: (r["member_count"], r["total_amount_mid"]), reverse=True)
        return rows_out[:limit]

    return {"bought": _finalize(buckets["Purchase"]), "sold": _finalize(buckets["Sale"])}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_congress_trading.py -k top_traded -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run broader regression**

Run: `.venv/bin/pytest -q tests/test_congress_trading.py -v`
Expected: all pass (11 total: 8 existing + 3 new)

- [ ] **Step 6: Commit**

```bash
git add providers/congress_trading.py tests/test_congress_trading.py
git commit -m "add) 하원의원 90일 매수·매도 상위 종목 집계 (top_traded)"
```

---

### Task 3: `explain_screen` LLM analysis in `reports/institution_watch.py`

**Files:**
- Modify: `reports/institution_watch.py`
- Test: `tests/test_institution_watch.py`

**Interfaces:**
- Consumes: `screen: dict` (Task 1 shape), `congress: dict` (Task 2 shape), `agent_console.agent._try_llm_prompt(prompt, max_timeout=20)` (existing helper, same as `build_common_moves_analysis`).
- Produces: `explain_screen(screen: dict, congress: dict) -> dict` returning `{"summary": str, "confidence": float, "mode": "llm"|"heuristic"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_explain_screen_uses_llm_when_available(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen",
                        lambda prompt: '{"summary": "AI 랠리 지속 베팅", "confidence": 0.7}')

    screen = {"new_buys": [{"ticker": "SMCI", "count": 3}], "increased": [], "decreased": []}
    congress = {"bought": [{"ticker": "NVDA", "member_count": 5}], "sold": []}

    out = iw.explain_screen(screen, congress)

    assert out["mode"] == "llm"
    assert out["summary"] == "AI 랠리 지속 베팅"
    assert out["confidence"] == 0.7


def test_explain_screen_falls_back_to_facts_when_llm_unavailable(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen", lambda prompt: None)

    screen = {"new_buys": [{"ticker": "SMCI", "count": 3, "name": "Super Micro"}],
             "increased": [], "decreased": []}
    congress = {"bought": [], "sold": []}

    out = iw.explain_screen(screen, congress)

    assert out["mode"] == "heuristic"
    assert "SMCI" in out["summary"] or "Super Micro" in out["summary"]


def test_explain_screen_empty_inputs_stay_heuristic(monkeypatch):
    from reports import institution_watch as iw

    def _boom(prompt):
        raise AssertionError("빈 입력인데 LLM 호출됨")
    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen", _boom)

    out = iw.explain_screen({"new_buys": [], "increased": [], "decreased": []},
                            {"bought": [], "sold": []})

    assert out["mode"] == "heuristic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py -k explain_screen -v`
Expected: FAIL with `AttributeError: module 'reports.institution_watch' has no attribute 'explain_screen'`

- [ ] **Step 3: Implement `explain_screen`**

Add to `reports/institution_watch.py`, after `build_common_moves_analysis` (or near it):

```python
def _try_llm_prompt_for_screen(prompt: str) -> str | None:
    """explain_screen 전용 LLM 호출 seam (테스트 monkeypatch 지점)."""
    try:
        from agent_console.agent import _try_llm_prompt
        return _try_llm_prompt(prompt, max_timeout=20)
    except Exception:
        return None


def _screen_fallback_summary(screen: dict, congress: dict) -> str:
    parts = []
    for r in (screen.get("new_buys") or [])[:3]:
        parts.append(f"{r.get('institutions', [None])[0] and r['count']}개 기관이 "
                     f"{r.get('name') or r.get('ticker')} 신규편입")
    for r in (screen.get("increased") or [])[:3]:
        parts.append(f"{r['count']}개 기관이 {r.get('name') or r.get('ticker')} 비중 확대")
    for r in (congress.get("bought") or [])[:3]:
        parts.append(f"하원의원 {r['member_count']}명이 {r['ticker']} 매수 공시")
    if not parts:
        return "표시할 만한 공통 움직임이 아직 없습니다."
    return " · ".join(parts)


def explain_screen(screen: dict, congress: dict) -> dict:
    """교차기관 스크리닝 + 정치인 매매를 LLM 이 해설(왜 이런 흐름일 수 있는지).

    LLM 실패/미가용 시 추측 없이 사실 나열로 대체(기존 build_common_moves_analysis
    와 같은 원칙) — 버튼 클릭 시에만 호출되도록 UI 레이어에서 게이팅한다."""
    fallback = {"summary": _screen_fallback_summary(screen, congress),
               "confidence": 0.3, "mode": "heuristic"}
    has_data = any(screen.get(k) for k in ("new_buys", "increased", "decreased")) or \
        any(congress.get(k) for k in ("bought", "sold"))
    if not has_data:
        return fallback

    prompt = "\n".join([
        "너는 기관투자자·정치인 매매 스크리닝 결과 해설자다.",
        "아래 JSON(여러 13F 기관의 신규편입/비중증가/비중감소 종목, 하원의원 90일 매수·매도",
        "상위 종목)만 보고, 왜 이런 흐름이 나타날 수 있는지 간결하게(2~4문장) 추정해라.",
        "확정적으로 단언하지 말고 '~일 가능성' 식으로 표현해라. 데이터에 없는 사실은 지어내지 마라.",
        "출력은 JSON object만 허용. 키는 summary(string), confidence(number 0.0~1.0).",
        "",
        json.dumps({"screen": screen, "congress": congress}, ensure_ascii=False, default=str),
    ])
    text = _try_llm_prompt_for_screen(prompt)
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0)) if match else None
    if not isinstance(parsed, dict) or not parsed.get("summary"):
        return fallback
    try:
        confidence = min(max(float(parsed.get("confidence")), 0.0), 1.0)
    except Exception:
        confidence = 0.5
    return {"summary": str(parsed["summary"]).strip(), "confidence": round(confidence, 2),
           "mode": "llm"}
```

Confirm `json` and `re` are already imported at the top of `reports/institution_watch.py` (they are, used by `build_common_moves_analysis`) — no new imports needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py -k explain_screen -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run broader regression**

Run: `.venv/bin/pytest -q tests/test_institution_watch.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add reports/institution_watch.py tests/test_institution_watch.py
git commit -m "add) 스크리닝 결과 LLM 해설 explain_screen (버튼 게이팅용, fallback 사실나열)"
```

---

### Task 4: `views.py` + `cached.py` wrappers

**Files:**
- Modify: `dashboard/views.py`, `dashboard/cached.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `reports.institution_watch.screen_position_changes`, `reports.institution_watch.explain_screen`, `providers.congress_trading.top_traded` (Tasks 1-3).
- Produces:
  - `views.institution_screener(keys: tuple[str, ...]) -> dict` (graceful `{"new_buys": [], "increased": [], "decreased": []}` on exception)
  - `views.congress_top_traded(days: int = 90) -> dict` (graceful `{"bought": [], "sold": []}`)
  - `views.institution_screen_explain(screen: dict, congress: dict) -> dict` (graceful heuristic-shaped fallback `{"summary": "", "confidence": 0.0, "mode": "heuristic"}`)
  - `cached.institution_screener(keys)` — `st.cache_data(ttl=3600)`
  - `cached.congress_top_traded(days=90)` — `st.cache_data(ttl=3600)`
  - `cached.institution_screen_explain(screen, congress)` — `st.cache_data(ttl=3600)` (cache key includes the dict args, which Streamlit hashes; both are small JSON-safe structures so this is fine)

**Constant for callers:** define in `dashboard/pages/watchlist.py` (Task 5), not here:
```python
_SCREEN_13F_KEYS = ("berkshire", "bridgewater", "scion", "citadel", "duquesne",
                    "pershing_square", "point72", "third_point", "tudor", "nps")
```

- [ ] **Step 1: Write the failing tests**

```python
def test_views_institution_screener_delegates(monkeypatch):
    from dashboard import views
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "screen_position_changes",
                        lambda keys: {"new_buys": [{"ticker": "X"}], "increased": [], "decreased": []})

    out = views.institution_screener(("berkshire",))

    assert out["new_buys"][0]["ticker"] == "X"


def test_views_institution_screener_graceful_on_exception(monkeypatch):
    from dashboard import views
    from reports import institution_watch as iw

    def _boom(keys):
        raise RuntimeError("boom")
    monkeypatch.setattr(iw, "screen_position_changes", _boom)

    assert views.institution_screener(("berkshire",)) == {"new_buys": [], "increased": [], "decreased": []}


def test_views_congress_top_traded_delegates(monkeypatch):
    from dashboard import views
    from providers import congress_trading as ct

    monkeypatch.setattr(ct, "top_traded", lambda days=90: {"bought": [{"ticker": "NVDA"}], "sold": []})

    out = views.congress_top_traded(90)

    assert out["bought"][0]["ticker"] == "NVDA"


def test_views_congress_top_traded_graceful_on_exception(monkeypatch):
    from dashboard import views
    from providers import congress_trading as ct

    def _boom(days=90):
        raise RuntimeError("boom")
    monkeypatch.setattr(ct, "top_traded", _boom)

    assert views.congress_top_traded(90) == {"bought": [], "sold": []}


def test_views_institution_screen_explain_delegates(monkeypatch):
    from dashboard import views
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "explain_screen",
                        lambda screen, congress: {"summary": "test", "confidence": 0.5, "mode": "llm"})

    out = views.institution_screen_explain({}, {})

    assert out["summary"] == "test"


def test_views_institution_screen_explain_graceful_on_exception(monkeypatch):
    from dashboard import views
    from reports import institution_watch as iw

    def _boom(screen, congress):
        raise RuntimeError("boom")
    monkeypatch.setattr(iw, "explain_screen", _boom)

    out = views.institution_screen_explain({}, {})
    assert out["mode"] == "heuristic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_dashboard.py -k "institution_screener or congress_top_traded or institution_screen_explain" -v`
Expected: FAIL — `views` has no such attributes

- [ ] **Step 3: Implement the views.py wrappers**

Add to `dashboard/views.py`, near `congress_trading`:

```python
def institution_screener(keys) -> dict:
    """13F 교차기관 종목 스크리닝 위임. graceful 빈 버킷."""
    try:
        from reports import institution_watch as iw
        return iw.screen_position_changes(list(keys))
    except Exception:
        return {"new_buys": [], "increased": [], "decreased": []}


def congress_top_traded(days: int = 90) -> dict:
    """하원의원 매수·매도 상위 위임. graceful 빈 버킷."""
    try:
        from providers import congress_trading as ct
        return ct.top_traded(days=days)
    except Exception:
        return {"bought": [], "sold": []}


def institution_screen_explain(screen: dict, congress: dict) -> dict:
    """스크리닝 결과 LLM 해설 위임. graceful heuristic 형태."""
    try:
        from reports import institution_watch as iw
        return iw.explain_screen(screen, congress)
    except Exception:
        return {"summary": "", "confidence": 0.0, "mode": "heuristic"}
```

Add to `dashboard/cached.py`, near `congress_trading`:

```python
@st.cache_data(ttl=3600, show_spinner=False)
def institution_screener(keys):
    return views.institution_screener(keys)


@st.cache_data(ttl=3600, show_spinner=False)
def congress_top_traded(days=90):
    return views.congress_top_traded(days)


@st.cache_data(ttl=3600, show_spinner=False)
def institution_screen_explain(screen, congress):
    return views.institution_screen_explain(screen, congress)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_dashboard.py -k "institution_screener or congress_top_traded or institution_screen_explain" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run broader regression**

Run: `.venv/bin/pytest -q tests/test_dashboard.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py dashboard/cached.py tests/test_dashboard.py
git commit -m "add) 교차스크리닝·정치인상위·LLM해설 views/cached 래퍼"
```

---

### Task 5: UI wiring in `dashboard/pages/watchlist.py` + donut per card

**Files:**
- Modify: `dashboard/pages/watchlist.py`
- Modify: `dashboard/views.py` (add `total_value_usd` to the `institutions` list entries so cards can build the "기타" donut slice)
- Test: `tests/test_watchlist_page_ui.py`

**Interfaces:**
- Consumes: `cached.institution_screener(keys)`, `cached.congress_top_traded(days)`, `cached.institution_screen_explain(screen, congress)` (Task 4), `charts.allocation_donut(holdings: list[dict])` (existing, `dashboard/charts.py:353`).
- Produces: `_institution_hub_section()` renders the new content; no new public interface consumed by later tasks (this is the last task).

- [ ] **Step 1: Add `total_value_usd` to the institutions list in `views.institution_watch_summary`**

In `dashboard/views.py`, find the `institutions.append({...})` block inside `institution_watch_summary` (around the existing `"key": snapshot["institution_key"], ...` dict) and add one line:

```python
            institutions.append({
                "key": snapshot["institution_key"],
                "display_name": snapshot["display_name"],
                "source_kind": snapshot.get("source_kind", ""),
                "category": snapshot.get("category", ""),
                "freshness": snapshot.get("freshness", ""),
                "holdings_count": snapshot.get("holdings_count", 0),
                "availability_flags": dict(snapshot.get("availability_flags") or {}),
                "top_holdings": list(snapshot.get("top_holdings") or []),
                "total_value_usd": snapshot.get("total_value_usd"),
                "notes": list(snapshot.get("notes") or []),
                "primary_sources": list(snapshot.get("primary_sources") or []),
                "metric_capabilities": list(snapshot.get("metric_capabilities") or []),
                "refresh_policy": snapshot.get("refresh_policy", ""),
                "confidence": snapshot.get("confidence"),
            })
```

(Only the `"total_value_usd": snapshot.get("total_value_usd"),` line is new — insert it among the existing keys, keep everything else identical.)

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_dashboard.py`, near the other `institution_watch_summary` tests:

```python
def test_institution_watch_summary_carries_total_value_usd(monkeypatch):
    from dashboard import views
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "list_institutions",
                        lambda: [{"key": "berkshire", "source_kind": "13f",
                                  "category": "holding_company", "display_name": "버크셔"}])
    monkeypatch.setattr(iw, "latest_snapshot",
                        lambda key: {"institution_key": key, "display_name": "버크셔",
                                     "source_kind": "13f", "freshness": "fresh",
                                     "holdings_count": 1, "availability_flags": {},
                                     "top_holdings": [{"ticker": "AAPL", "issuer": "APPLE",
                                                       "value_usd": 100.0}],
                                     "notes": [], "primary_sources": [], "metric_capabilities": [],
                                     "refresh_policy": "quarterly", "confidence": 0.9,
                                     "total_value_usd": 1000.0})
    monkeypatch.setattr(iw, "compare_institutions", lambda keys, snapshots=None: {"rows": []})

    out = views.institution_watch_summary()

    assert out["institutions"][0]["total_value_usd"] == 1000.0
```

Add to `tests/test_watchlist_page_ui.py`:

```python
def test_screening_sections_hidden_until_hub_toggle_on(monkeypatch):
    """스크리닝/정치인/LLM 섹션도 기관허브 토글 뒤 — 첫 렌더에 호출되면 안 된다."""
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})

    def _boom(*a, **k):
        raise AssertionError("토글 꺼진 상태에서 스크리닝이 호출됨")
    monkeypatch.setattr(_cached, "institution_screener", _boom)
    monkeypatch.setattr(_cached, "congress_top_traded", _boom)

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_screening_sections_render_when_hub_toggle_on(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [
                            {"key": "berkshire", "display_name": "버크셔", "category": "holding_company",
                             "source_kind": "13f", "freshness": "fresh", "holdings_count": 1,
                             "availability_flags": {}, "primary_sources": [],
                             "top_holdings": [{"ticker": "AAPL", "issuer": "APPLE", "value_usd": 900.0}],
                             "total_value_usd": 1000.0}],
                        "comparison": {"rows": []},
                        "analysis": {"summary": "", "shared_moves": [], "divergences": [],
                                    "confidence": 0.0, "mode": "heuristic"}})
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [{"ticker": "SMCI", "name": "Super Micro",
                                                    "institutions": ["berkshire"], "count": 1,
                                                    "avg_delta_pct": 0.01}],
                                     "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded",
                        lambda days=90: {"bought": [{"ticker": "NVDA", "member_count": 3,
                                                     "members": ["A", "B", "C"],
                                                     "total_amount_mid": 300000}],
                                        "sold": []})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    assert "공통 움직임" in body
    assert "정치인" in body
    dataframes = [df.value for df in at.dataframe]
    assert any("SMCI" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)
    assert any("NVDA" in df.get("티커", pd.Series(dtype=object)).tolist() for df in dataframes)


def test_screen_explain_button_gates_llm_call(monkeypatch):
    from dashboard import cached as _cached

    monkeypatch.setattr(data, "load_watchlist", lambda: _ROWS)
    monkeypatch.setattr(cached, "watchlist_quotes", lambda tickers: {})
    monkeypatch.setattr(cached, "institution_watch",
                        lambda *a, **k: {"institutions": [], "comparison": {}, "analysis": {}})
    monkeypatch.setattr(_cached, "institution_screener",
                        lambda keys: {"new_buys": [], "increased": [], "decreased": []})
    monkeypatch.setattr(_cached, "congress_top_traded", lambda days=90: {"bought": [], "sold": []})

    calls = []
    monkeypatch.setattr(_cached, "institution_screen_explain",
                        lambda screen, congress: calls.append(1) or
                        {"summary": "설명", "confidence": 0.5, "mode": "llm"})

    at = AppTest.from_string(_RUN_SCRIPT, default_timeout=30)
    at.run()
    at.toggle(key="_watch_show_hub").set_value(True).run()

    assert calls == [], "버튼 안 눌렀는데 LLM 해설이 호출됨"

    at.button(key="_watch_screen_explain_btn").click().run()

    assert not at.exception, str(at.exception)
    assert calls == [1]
    body = " ".join(str(m.value) for m in at.markdown)
    assert "설명" in body
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_dashboard.py -k total_value_usd tests/test_watchlist_page_ui.py -k "screening or screen_explain" -v`
Expected: FAIL (attribute/UI content not found yet)

- [ ] **Step 4: Implement the UI**

In `dashboard/pages/watchlist.py`, add near the top (module level, alongside other constants):

```python
_SCREEN_13F_KEYS = ("berkshire", "bridgewater", "scion", "citadel", "duquesne",
                    "pershing_square", "point72", "third_point", "tudor", "nps")
```

Add a new function, placed right before `_institution_hub_section`:

```python
def _screening_section() -> None:
    screen = cached.institution_screener(_SCREEN_13F_KEYS)
    congress = cached.congress_top_traded(90)

    st.markdown("### 🔄 여러 기관 공통 움직임 (직전 분기 대비)")
    cols = st.columns(3)
    _bucket_table(cols[0], "🆕 신규편입", screen.get("new_buys") or [])
    _bucket_table(cols[1], "📈 비중 증가", screen.get("increased") or [])
    _bucket_table(cols[2], "📉 비중 감소", screen.get("decreased") or [])
    st.caption("직전 분기 13F 대비, 여러 기관이 같은 방향으로 움직인 종목만 표시 · 정보용")

    st.markdown("### 🏛️ 정치인 매수·매도 상위 (최근 90일, 하원)")
    ccols = st.columns(2)
    with ccols[0]:
        st.caption("많이 산 종목")
        _congress_table(congress.get("bought") or [])
    with ccols[1]:
        st.caption("많이 판 종목")
        _congress_table(congress.get("sold") or [])
    st.caption("금액은 신고 구간 중간값 추정 합계 · 매매 신호 아님")

    st.markdown("### 🧠 왜 이런 움직임일까? (LLM 분석)")
    if st.button("🧠 스크리닝 해설 생성", key="_watch_screen_explain_btn"):
        st.session_state["_watch_screen_explain_requested"] = True
    if st.session_state.get("_watch_screen_explain_requested"):
        with st.spinner("분석 중…"):
            explain = cached.institution_screen_explain(screen, congress)
        st.markdown(explain.get("summary") or "표시할 해설이 없습니다.")
        st.caption(f"신뢰도 {explain.get('confidence', 0.0):.2f} · "
                  f"{'LLM 추정' if explain.get('mode') == 'llm' else '사실 나열(LLM 미가용)'}"
                  " — 투자 조언 아님")
    else:
        st.caption("버튼을 누르면 위 스크리닝 결과를 LLM이 해설합니다 (자동 실행 안 함).")


def _bucket_table(col, title: str, rows: list[dict]) -> None:
    with col:
        st.caption(title)
        if not rows:
            st.caption("해당 없음")
            return
        df = pd.DataFrame([{
            "티커": r.get("ticker") or "—", "종목": r.get("name"),
            "기관수": r.get("count"), "평균변화": f"{r.get('avg_delta_pct', 0) * 100:+.1f}%p",
        } for r in rows])
        st.dataframe(df, hide_index=True, width="stretch")


def _congress_table(rows: list[dict]) -> None:
    if not rows:
        st.caption("해당 없음")
        return
    df = pd.DataFrame([{
        "티커": r.get("ticker"), "거래 의원수": r.get("member_count"),
        "추정금액(합)": f"${r.get('total_amount_mid', 0):,.0f}",
    } for r in rows])
    st.dataframe(df, hide_index=True, width="stretch")
```

Modify `_render_institution_cards` to add a donut per card. Find the existing function:

```python
def _render_institution_cards(rows: list[dict]):
    if not rows:
        st.info("기관 허브에 표시할 스냅샷이 아직 없습니다.")
        return
    cols = st.columns(min(3, len(rows)))
    for idx, row in enumerate(rows):
        flags = row.get("availability_flags") or {}
        col = cols[idx % len(cols)]
        with col:
            st.markdown(
                "\n".join([...]))
```

Add, right after the `st.markdown("\n".join([...]))` call inside the `with col:` block:

```python
            top = list(row.get("top_holdings") or [])
            total = row.get("total_value_usd")
            if top:
                donut_rows = [{"ticker": h.get("ticker") or h.get("issuer") or "?",
                              "value": h.get("value_usd") or 0,
                              "name": h.get("issuer") or h.get("ticker")} for h in top]
                if total:
                    top_sum = sum(d["value"] for d in donut_rows)
                    other = total - top_sum
                    if other > 0:
                        donut_rows.append({"ticker": "기타", "value": other, "name": "기타(상위10 외)"})
                from dashboard import charts
                st.plotly_chart(charts.allocation_donut(donut_rows), width="stretch",
                                config={"displayModeBar": False})
```

Finally, in `_institution_hub_section()`, call `_screening_section()` right after the `st.caption(f"선택 {len(selected_keys)} / 전체 {len(all_keys)}")` line and BEFORE `_render_institution_cards(...)`:

```python
        st.caption(f"선택 {len(selected_keys)} / 전체 {len(all_keys)}")
    _screening_section()
    st.divider()
    all_selected = len(selected_keys) == len(all_keys)
```

(i.e. insert `_screening_section()` and a `st.divider()` between the existing multiselect-caption block and the `all_selected = ...` line — the rest of the function is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_dashboard.py -k total_value_usd tests/test_watchlist_page_ui.py -v`
Expected: PASS (all)

- [ ] **Step 6: Run full watchlist/dashboard/institution regression**

Run: `.venv/bin/pytest -q tests/test_watchlist_page_ui.py tests/test_watchlist_quotes.py tests/test_dashboard.py tests/test_institution_watch.py tests/test_thirteenf.py tests/test_congress_trading.py -v`
Expected: all pass, no regressions

- [ ] **Step 7: Commit**

```bash
git add dashboard/pages/watchlist.py dashboard/views.py tests/test_dashboard.py tests/test_watchlist_page_ui.py
git commit -m "add) 기관허브 상단에 도넛+교차스크리닝+정치인상위+LLM해설 시각화"
```

---

### Task 6: Live smoke test, RED verification, merge to master, deploy verification

**Files:** none (verification only)

- [ ] **Step 1: Live smoke test the full pipeline against real data**

```bash
.venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from reports import institution_watch as iw
from providers import congress_trading as ct

keys = ('berkshire','bridgewater','scion','citadel','duquesne','pershing_square','point72','third_point','tudor','nps')
screen = iw.screen_position_changes(keys)
print('new_buys:', [(r[\"ticker\"], r[\"count\"]) for r in screen['new_buys'][:5]])
print('increased:', [(r[\"ticker\"], r[\"count\"], r[\"avg_delta_pct\"]) for r in screen['increased'][:5]])
print('decreased:', [(r[\"ticker\"], r[\"count\"], r[\"avg_delta_pct\"]) for r in screen['decreased'][:5]])

congress = ct.top_traded(days=90)
print('bought:', [(r['ticker'], r['member_count']) for r in congress['bought'][:5]])
print('sold:', [(r['ticker'], r['member_count']) for r in congress['sold'][:5]])

explain = iw.explain_screen(screen, congress)
print('explain mode:', explain['mode'], '-', explain['summary'][:200])
"
```

Expected: no exceptions, plausible-looking tickers and counts (sanity-check against known holdings, e.g. NVDA/major AI names likely appearing given the 2026 market backdrop established earlier in this session).

- [ ] **Step 2: Confirm RED against unpatched source for every file touched this plan**

```bash
git stash push -- reports/institution_watch.py providers/congress_trading.py dashboard/views.py dashboard/cached.py dashboard/pages/watchlist.py
.venv/bin/pytest -q tests/test_institution_watch.py tests/test_congress_trading.py tests/test_dashboard.py tests/test_watchlist_page_ui.py
git stash pop
```

Expected: multiple failures while stashed (proves the tests actually exercise the new code), all green after `git stash pop`.

(Note: this plan already had each task commit individually with its own RED→GREEN cycle: this step is a final belt-and-suspenders check across the WHOLE feature at once, catching any cross-task interaction the per-task checks might have missed.)

- [ ] **Step 3: Full local suite regression**

```bash
.venv/bin/pytest -q tests/
```

Expected: same pass count as before this feature plus the ~25 new tests, only the pre-existing unrelated `test_no_dead_ticker_mentions` failure (if still present) — no other regressions.

- [ ] **Step 4: Push feature branch, merge to main-repo master**

```bash
git push origin fix/audit-08-stock-advisor-sensitive-scan
git -C /home/ubuntu/projects/stock-report fetch origin
git -C /home/ubuntu/projects/stock-report merge origin/fix/audit-08-stock-advisor-sensitive-scan --no-edit
cd /home/ubuntu/projects/stock-report && .venv/bin/pytest -q tests/test_institution_watch.py tests/test_congress_trading.py tests/test_dashboard.py tests/test_watchlist_page_ui.py
git push origin master
```

Expected: fast-forward merge, tests pass on master, push succeeds.

- [ ] **Step 5: Verify in the actually-deployed dashboard**

The dashboard auto-restarts via `dashboard_watchdog.sh` freshness detection within ~1 minute of the master push (established pattern this session — no manual restart needed for `dashboard/` changes). After that window:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501   # or whatever port the live dashboard listens on — check with `ps aux | grep streamlit` / existing docs if unsure
```

Then use the `run` skill or a direct browser check (if the harness has browser tools available) to open the 관심종목 page, toggle "🏦 유명 투자자 비교 보기" on, and visually confirm: donut charts appear per institution card, the three 공통 움직임 tables render with real tickers, the 정치인 매수·매도 표 has real data, and clicking "🧠 스크리닝 해설 생성" produces a non-empty explanation without raising an error. If no browser tool is available in this environment, at minimum re-run Step 1's live smoke test against the master branch checkout to confirm the deployed code path produces identical, non-erroring output, and report to the user exactly what was and wasn't visually verified.

- [ ] **Step 6: Report completion to the user**

Summarize what shipped, what the live smoke test showed (sample tickers/counts), any caveats (put options excluded, House-only politician data, cold-cache load time), and confirm the `/goal` condition is met.
