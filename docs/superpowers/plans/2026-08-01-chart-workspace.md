# Chart Workspace Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 10개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TradingView-grade chart workspace layer with saved layouts, templates, multi-chart fullview, AI patch previews, and deterministic analysis panels while preserving the current Plotly chart runtime.

**Architecture:** Add a pure `dashboard.chart_workspace` model that validates workspace state, templates, patches, and analysis summaries. Persist the model through `agent_console.storage` using the same versioned pattern as strategy studio. Render the workspace in `dashboard/pages/chart_full.py` by reusing the existing `dashboard.pages.ticker._price_chart` component per panel.

**Tech Stack:** Python 3.11, Streamlit, Plotly, pandas/numpy, SQLite through `agent_console.storage`, pytest, Streamlit AppTest.

## Global Constraints

- Reuse `dashboard.charts`, `dashboard.plotly_embed`, and `dashboard.pages.ticker._price_chart`; do not replace the existing chart runtime.
- Preserve comparison normalization, weekend/holiday rangebreaks, trade markers, price alerts, replay, and localStorage drawings.
- No arbitrary code execution in chart patches.
- Do not claim complete browser crosshair synchronization until a browser-runtime test proves it.
- Intraday data limitations must be shown honestly when 1m/5m/1h data is unavailable or provider-limited.
- Keep UI dense and operational; do not create a landing page or decorative chart marketing screen.

---

### Task 1: Pure Workspace Model And Patch Engine

**Files:**
- Create: `dashboard/chart_workspace.py`
- Test: `tests/test_chart_workspace.py`

**Interfaces:**
- Produces: `default_workspace(ticker: str = "MSFT") -> dict`
- Produces: `normalize_workspace(workspace: dict | None, *, ticker: str = "MSFT") -> dict`
- Produces: `validate_workspace(workspace: dict) -> tuple[list[str], list[str]]`
- Produces: `workspace_id(workspace: dict) -> str`
- Produces: `apply_workspace_patch(workspace: dict, patch: dict) -> dict`
- Produces: `diff_workspaces(before: dict, after: dict) -> list[dict]`
- Produces: `allowed_indicator_names() -> set[str]`

- [x] **Step 1: Write failing tests**

Add this to `tests/test_chart_workspace.py`:

```python
from __future__ import annotations

import pytest

from dashboard import chart_workspace as cw


def test_default_workspace_has_one_valid_panel():
    ws = cw.default_workspace("NVDA")
    errors, warnings = cw.validate_workspace(ws)
    assert errors == []
    assert ws["panels"][0]["ticker"] == "NVDA"
    assert ws["sync"]["interval"] is True
    assert ws["layout"] == "1"


def test_patch_updates_nested_panel_without_clobbering_other_fields():
    ws = cw.default_workspace("MSFT")
    after = cw.apply_workspace_patch(ws, {
        "panels[0].timeframe": "5m",
        "panels[0].top_indicators": ["이동평균선", "VWAP(세션)", "매물대"],
    })
    assert after["panels"][0]["timeframe"] == "5m"
    assert after["panels"][0]["ticker"] == "MSFT"
    assert after["panels"][0]["period"] == ws["panels"][0]["period"]
    diff = cw.diff_workspaces(ws, after)
    assert any(row["path"] == "panels[0].timeframe" for row in diff)


def test_patch_rejects_unknown_indicator_and_panel():
    ws = cw.default_workspace("MSFT")
    with pytest.raises(ValueError, match="unknown indicator"):
        cw.apply_workspace_patch(ws, {"panels[0].top_indicators": ["Pine Script"]})
    with pytest.raises(ValueError, match="panel index"):
        cw.apply_workspace_patch(ws, {"panels[5].ticker": "AAPL"})
```

- [x] **Step 2: Run tests to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: import failure because `dashboard.chart_workspace` does not exist.

- [x] **Step 3: Implement the pure model**

Create `dashboard/chart_workspace.py` with:

```python
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import ticker_names

LAYOUTS = {"1": 1, "2v": 2, "2h": 2, "2x2": 4, "3+1": 4, "2x3": 6}
TIMEFRAMES = {"5m", "1h", "2h", "4h", "1d", "1wk", "1mo"}
PERIODS = {"3mo", "6mo", "1y", "5y", "전체"}
CHART_KINDS = {"line", "candle", "heikin_ashi"}
DRAWING_SYNC = {"off", "layout_symbol", "global_symbol"}
TOP_INDICATORS = {
    "이동평균선", "자동 추세선·채널", "지수이평(EMA)", "볼린저 밴드", "일목균형표",
    "슈퍼트렌드", "엔벨로프", "파라볼릭 SAR", "프라이스 채널", "매물대", "프랙탈",
    "VWAP(세션)", "앵커드 VWAP", "켈트너 채널", "KAMA", "샹들리에 엑시트",
}
BOTTOM_INDICATORS = {
    "거래량", "RSI", "RSI 다이버전스", "MACD", "스토캐스틱",
    "Aroon", "%b", "PVT", "분기 EPS", "펀더멘털",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_ticker(value: Any, fallback: str = "MSFT") -> str:
    ticker = ticker_names.normalize_input(str(value or "").strip())
    return ticker or fallback


def _panel(panel_id: str, ticker: str) -> dict[str, Any]:
    return {
        "id": panel_id,
        "ticker": _norm_ticker(ticker),
        "timeframe": "1d",
        "period": "6mo",
        "chart_kind": "candle",
        "top_indicators": ["이동평균선"],
        "bottom_indicators": ["거래량", "RSI"],
        "compare": [],
        "log_scale": False,
        "style_template_id": None,
        "indicator_template_id": None,
        "series_template_id": None,
    }


def default_workspace(ticker: str = "MSFT") -> dict[str, Any]:
    tk = _norm_ticker(ticker)
    return {
        "id": "default",
        "name": "Default Workspace",
        "layout": "1",
        "active_panel": "p1",
        "sync": {
            "symbol": False,
            "interval": True,
            "range": True,
            "crosshair": True,
            "drawings": "layout_symbol",
        },
        "panels": [_panel("p1", tk)],
        "metadata": {"created_at": _now(), "updated_at": _now()},
    }
```

Continue the implementation with normalization, validation, patching, and diffing:

```python
def allowed_indicator_names() -> set[str]:
    return set(TOP_INDICATORS) | set(BOTTOM_INDICATORS)


def normalize_workspace(workspace: dict | None, *, ticker: str = "MSFT") -> dict[str, Any]:
    base = default_workspace(ticker)
    if not isinstance(workspace, dict):
        return base
    out = copy.deepcopy(base)
    out.update({k: copy.deepcopy(v) for k, v in workspace.items() if k not in {"sync", "panels", "metadata"}})
    out["layout"] = str(out.get("layout") or "1")
    count = LAYOUTS.get(out["layout"], 1)
    panels = [p for p in workspace.get("panels", []) if isinstance(p, dict)]
    merged = []
    for idx in range(count):
        src = copy.deepcopy(panels[idx]) if idx < len(panels) else {}
        p = _panel(f"p{idx + 1}", src.get("ticker") or ticker)
        p.update(src)
        p["id"] = str(p.get("id") or f"p{idx + 1}")
        p["ticker"] = _norm_ticker(p.get("ticker"), ticker)
        p["timeframe"] = str(p.get("timeframe") or "1d")
        p["period"] = str(p.get("period") or "6mo")
        p["chart_kind"] = str(p.get("chart_kind") or "candle")
        p["top_indicators"] = [x for x in p.get("top_indicators", []) if x in TOP_INDICATORS]
        p["bottom_indicators"] = [x for x in p.get("bottom_indicators", []) if x in BOTTOM_INDICATORS]
        p["compare"] = [_norm_ticker(x) for x in p.get("compare", [])][:3]
        merged.append(p)
    out["panels"] = merged
    sync = dict(base["sync"])
    sync.update(workspace.get("sync") if isinstance(workspace.get("sync"), dict) else {})
    sync["drawings"] = sync.get("drawings") if sync.get("drawings") in DRAWING_SYNC else "layout_symbol"
    out["sync"] = sync
    out["active_panel"] = out.get("active_panel") if any(p["id"] == out.get("active_panel") for p in merged) else merged[0]["id"]
    meta = dict(workspace.get("metadata") or {})
    meta["updated_at"] = _now()
    out["metadata"] = meta
    return out
```

Add validation, patch path parsing, and diffing:

```python
def validate_workspace(workspace: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    ws = normalize_workspace(workspace)
    if ws["layout"] not in LAYOUTS:
        errors.append(f"unsupported layout: {ws['layout']}")
    if len(ws["panels"]) != LAYOUTS.get(ws["layout"], 1):
        errors.append("panel count does not match layout")
    for idx, p in enumerate(ws["panels"]):
        if p["timeframe"] not in TIMEFRAMES:
            errors.append(f"panel[{idx}] unsupported timeframe: {p['timeframe']}")
        if p["period"] not in PERIODS:
            errors.append(f"panel[{idx}] unsupported period: {p['period']}")
        if p["chart_kind"] not in CHART_KINDS:
            errors.append(f"panel[{idx}] unsupported chart_kind: {p['chart_kind']}")
        for name in p.get("top_indicators", []):
            if name not in TOP_INDICATORS:
                errors.append(f"unknown indicator: {name}")
        for name in p.get("bottom_indicators", []):
            if name not in BOTTOM_INDICATORS:
                errors.append(f"unknown indicator: {name}")
        if len(p.get("compare") or []) > 3:
            warnings.append(f"panel[{idx}] compare symbols capped at 3")
    return errors, warnings


def workspace_id(workspace: dict) -> str:
    name = str((workspace or {}).get("name") or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9가-힣._-]+", "-", name).strip("-") or "workspace"
    digest = hashlib.sha1(json.dumps(workspace, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    return f"{raw[:48]}-{digest}"
```

Patch code:

```python
_PATH_RE = re.compile(r"^panels\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)$")


def apply_workspace_patch(workspace: dict, patch: dict) -> dict:
    out = normalize_workspace(workspace)
    for path, value in (patch or {}).items():
        if path in {"layout", "name", "active_panel"}:
            out[path] = value
            out = normalize_workspace(out)
            continue
        if path.startswith("sync."):
            out["sync"][path.split(".", 1)[1]] = value
            out = normalize_workspace(out)
            continue
        m = _PATH_RE.match(str(path))
        if not m:
            raise ValueError(f"unsupported patch path: {path}")
        idx, field = int(m.group(1)), m.group(2)
        if idx >= len(out["panels"]):
            raise ValueError(f"panel index out of range: {idx}")
        if field in {"top_indicators", "bottom_indicators"}:
            allowed = TOP_INDICATORS if field == "top_indicators" else BOTTOM_INDICATORS
            bad = [x for x in value if x not in allowed]
            if bad:
                raise ValueError(f"unknown indicator: {bad[0]}")
        out["panels"][idx][field] = value
        out = normalize_workspace(out)
    errors, _warnings = validate_workspace(out)
    if errors:
        raise ValueError("; ".join(errors))
    return out
```

Diff code:

```python
def diff_workspaces(before: dict, after: dict) -> list[dict]:
    rows: list[dict] = []
    b = normalize_workspace(before)
    a = normalize_workspace(after)

    def walk(path: str, left, right):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                walk(f"{path}.{key}" if path else key, left.get(key), right.get(key))
            return
        if isinstance(left, list) and isinstance(right, list):
            for idx in range(max(len(left), len(right))):
                lv = left[idx] if idx < len(left) else None
                rv = right[idx] if idx < len(right) else None
                walk(f"{path}[{idx}]", lv, rv)
            return
        if left != right:
            rows.append({"path": path, "before": left, "after": right})

    walk("", b, a)
    return rows
```

- [x] **Step 4: Run tests to verify pass**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add dashboard/chart_workspace.py tests/test_chart_workspace.py
git commit -m "add) 차트 워크스페이스 모델 추가"
```

---

### Task 2: Storage APIs For Workspaces And Templates

**Files:**
- Modify: `agent_console/storage.py`
- Test: `tests/test_chart_workspace.py`

**Interfaces:**
- Consumes: `dashboard.chart_workspace.normalize_workspace`, `workspace_id`
- Produces: `save_chart_workspace`, `save_chart_workspace_version`, `list_chart_workspaces`, `get_chart_workspace`, `list_chart_workspace_versions`, `save_chart_template`, `list_chart_templates`

- [x] **Step 1: Write failing storage tests**

Append:

```python
def test_chart_workspace_storage_round_trip(tmp_path, monkeypatch):
    from agent_console import storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "agent_console.sqlite3")
    ws = cw.default_workspace("NVDA")
    saved = storage.save_chart_workspace(ws)
    assert saved["workspace"]["panels"][0]["ticker"] == "NVDA"

    versioned = storage.save_chart_workspace_version(saved["id"], {
        **saved["workspace"],
        "name": "Trend Layout",
    }, note="rename")
    assert versioned["version"] == 2

    rows = storage.list_chart_workspaces()
    assert rows[0]["name"] == "Trend Layout"
    assert storage.get_chart_workspace(saved["id"])["version"] == 2
    assert storage.get_chart_workspace(saved["id"], version=1)["version"] == 1
    assert storage.list_chart_workspace_versions(saved["id"])[0]["version"] == 2


def test_chart_template_storage_filters_kind(tmp_path, monkeypatch):
    from agent_console import storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "agent_console.sqlite3")
    storage.save_chart_template({"id": "trend", "kind": "indicators", "name": "Trend", "payload": {"top_indicators": ["이동평균선"]}})
    storage.save_chart_template({"id": "style", "kind": "style", "name": "Noir", "payload": {"chart_kind": "candle"}})
    assert [r["id"] for r in storage.list_chart_templates(kind="indicators")] == ["trend"]
```

- [x] **Step 2: Run tests to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: `AttributeError` for missing storage functions.

- [x] **Step 3: Add tables in `_init_db`**

Add SQL near strategy studio tables:

```python
CREATE TABLE IF NOT EXISTS chart_workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    layout TEXT NOT NULL,
    workspace_json TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chart_workspace_versions (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    workspace_json TEXT NOT NULL,
    note TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, version)
);

CREATE TABLE IF NOT EXISTS chart_templates (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    template_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- [x] **Step 4: Add row helpers and CRUD functions**

Implementation pattern:

```python
def _chart_workspace_row(row) -> dict[str, Any]:
    payload = _loads(row["workspace_json"]) if row else {}
    return {
        "id": row["id"],
        "name": row["name"],
        "layout": row["layout"],
        "version": int(row["current_version"]),
        "workspace": payload,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
```

Use `dashboard.chart_workspace.normalize_workspace` before writing. `save_chart_workspace` creates version 1 when no workspace row exists; `save_chart_workspace_version` always increments from `MAX(version)`.

Template rows should store normalized `id`, `kind`, `name`, and full template payload as JSON.

- [x] **Step 5: Run storage tests**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add agent_console/storage.py tests/test_chart_workspace.py
git commit -m "add) 차트 워크스페이스 저장소 추가"
```

---

### Task 3: Dashboard Wrappers And Cached Access

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/cached.py`
- Test: `tests/test_chart_workspace.py`

**Interfaces:**
- Produces: `views.chart_workspace_catalog()`
- Produces: `views.chart_workspace_detail(workspace_id: str | None = None)`
- Produces: `views.chart_workspace_versions(workspace_id: str)`
- Produces: `views.chart_workspace_save(workspace: dict)`
- Produces: `views.chart_workspace_patch_preview(workspace: dict, prompt: str | None = None, patch: dict | None = None)`
- Produces matching cached wrappers for read-only functions.

- [x] **Step 1: Write failing wrapper tests**

Append:

```python
def test_dashboard_workspace_wrappers_forward_storage(monkeypatch):
    from dashboard import views, cached

    monkeypatch.setattr(views.storage, "list_chart_workspaces", lambda limit=50: [{"id": "w1", "name": "Main"}])
    monkeypatch.setattr(views.storage, "get_chart_workspace", lambda workspace_id, version=None: {"id": workspace_id or "w1", "workspace": cw.default_workspace("AAPL")})
    monkeypatch.setattr(views.storage, "list_chart_workspace_versions", lambda workspace_id, limit=30: [{"version": 1}])

    cached.chart_workspace_catalog.clear()
    assert views.chart_workspace_catalog()["count"] == 1
    assert views.chart_workspace_detail("w1")["id"] == "w1"
    assert cached.chart_workspace_catalog()["workspaces"][0]["id"] == "w1"
```

- [x] **Step 2: Run test to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: missing wrapper function failure.

- [x] **Step 3: Implement wrappers**

Add to `dashboard/views.py`:

```python
from dashboard import chart_workspace
from agent_console import storage


def chart_workspace_catalog(limit: int = 50) -> dict:
    rows = storage.list_chart_workspaces(limit=limit)
    return {"ok": True, "count": len(rows), "workspaces": rows, "latest": rows[0] if rows else None}
```

Add detail/version/save/patch wrappers. `chart_workspace_patch_preview` should call `chart_workspace.apply_workspace_patch` and return `{ok, before, after, diff, warnings}`.

Add `@st.cache_data` read wrappers in `dashboard/cached.py`:

```python
@st.cache_data(ttl=30)
def chart_workspace_catalog():
    return views.chart_workspace_catalog()
```

- [x] **Step 4: Run tests**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add dashboard/views.py dashboard/cached.py tests/test_chart_workspace.py
git commit -m "add) 차트 워크스페이스 대시보드 래퍼 추가"
```

---

### Task 4: Multi-Chart Workspace Renderer

**Files:**
- Create: `dashboard/chart_workspace_ui.py`
- Modify: `dashboard/pages/chart_full.py`
- Test: `tests/test_chart_workspace_pages.py`

**Interfaces:**
- Consumes: `dashboard.chart_workspace.normalize_workspace`
- Consumes: `dashboard.pages.ticker._price_chart_frag`
- Produces: `render_chart_workspace(workspace: dict | None = None, *, data_loader: callable | None = None) -> dict`

- [x] **Step 1: Write failing AppTest smoke**

Create `tests/test_chart_workspace_pages.py`:

```python
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_chart_workspace_renderer_surfaces_layout_and_sync_controls():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workspace_ui

workspace = {{
    "id": "w1",
    "name": "Main Workspace",
    "layout": "2v",
    "active_panel": "p1",
    "sync": {{"symbol": False, "interval": True, "range": True, "crosshair": True, "drawings": "layout_symbol"}},
    "panels": [
        {{"id": "p1", "ticker": "MSFT", "timeframe": "1d", "period": "6mo", "chart_kind": "candle", "top_indicators": ["이동평균선"], "bottom_indicators": ["거래량"], "compare": [], "log_scale": False}},
        {{"id": "p2", "ticker": "QQQ", "timeframe": "1d", "period": "6mo", "chart_kind": "line", "top_indicators": ["이동평균선"], "bottom_indicators": ["RSI"], "compare": [], "log_scale": False}},
    ],
}}
chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "Main Workspace" in body
    assert "동기화" in body
    assert "MSFT" in body
    assert "QQQ" in body
```

- [x] **Step 2: Run test to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace_pages.py -q
```

Expected: import failure for `dashboard.chart_workspace_ui`.

- [x] **Step 3: Implement renderer shell**

Create `dashboard/chart_workspace_ui.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard import cached, chart_workspace, data, theme
from dashboard.pages import ticker as ticker_pg


def _grid_columns(layout: str):
    if layout in {"2v", "2h"}:
        return st.columns(2)
    if layout == "2x2":
        return st.columns(2)
    if layout == "2x3":
        return st.columns(3)
    return [st.container()]


def render_chart_workspace(workspace=None, *, render_charts: bool = True) -> dict:
    ws = chart_workspace.normalize_workspace(workspace, ticker=st.session_state.get("ticker", "MSFT"))
    st.markdown(f"#### {ws['name']}")
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.4, 1.0], vertical_alignment="center")
    ws["layout"] = c1.segmented_control("레이아웃", ["1", "2v", "2h", "2x2", "3+1", "2x3"], default=ws["layout"], key="_cw_layout") or ws["layout"]
    ws["sync"]["interval"] = c2.toggle("봉 동기화", value=bool(ws["sync"].get("interval")), key="_cw_sync_interval")
    ws["sync"]["range"] = c3.toggle("기간 동기화", value=bool(ws["sync"].get("range")), key="_cw_sync_range")
    ws["sync"]["drawings"] = c4.selectbox("드로잉", ["off", "layout_symbol", "global_symbol"], index=["off", "layout_symbol", "global_symbol"].index(ws["sync"].get("drawings", "layout_symbol")), key="_cw_sync_drawings")
    st.caption("동기화 설정은 워크스페이스에 저장됩니다. 크로스헤어 동기화는 브라우저 런타임 검증 전까지 설정값만 보관합니다.")
    ws = chart_workspace.normalize_workspace(ws)
    cols = _grid_columns(ws["layout"])
    for idx, panel in enumerate(ws["panels"]):
        with cols[idx % len(cols)]:
            st.markdown(f"**{panel['ticker']}** · `{panel['timeframe']}` · `{panel['period']}`")
            if render_charts:
                hist = cached.ohlc(panel["ticker"], period="max")
                pos = data.holding_position(panel["ticker"])
                ticker_pg._price_chart_frag(panel["ticker"], hist, pos.get("avg_price_usd") if pos else None, data.trade_events(panel["ticker"]), fullscreen=False)
    return ws
```

Update `dashboard/pages/chart_full.py` to call this renderer above the legacy single-chart fallback. Keep a session toggle `워크스페이스 모드` default `True`.

- [x] **Step 4: Run page test**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace_pages.py -q
```

Expected: pass.

- [x] **Step 5: Commit**

```bash
git add dashboard/chart_workspace_ui.py dashboard/pages/chart_full.py tests/test_chart_workspace_pages.py
git commit -m "add) 멀티차트 워크스페이스 화면 추가"
```

---

### Task 5: AI Patch Preview And Apply UI

**Files:**
- Modify: `dashboard/chart_workspace.py`
- Modify: `dashboard/chart_workspace_ui.py`
- Test: `tests/test_chart_workspace.py`
- Test: `tests/test_chart_workspace_pages.py`

**Interfaces:**
- Produces: `propose_workspace_patch(prompt: str, workspace: dict) -> dict`

- [x] **Step 1: Add heuristic patch tests**

Append:

```python
def test_workspace_ai_patch_heuristic_handles_intraday_vwap():
    ws = cw.default_workspace("NVDA")
    proposal = cw.propose_workspace_patch("5분봉으로 바꾸고 VWAP랑 거래량을 봐줘", ws)
    assert proposal["ok"] is True
    assert proposal["patch"]["panels[0].timeframe"] == "5m"
    assert "VWAP(세션)" in proposal["patch"]["panels[0].top_indicators"]
```

- [x] **Step 2: Run test to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py -q
```

Expected: missing `propose_workspace_patch`.

- [x] **Step 3: Implement heuristic patcher**

Add:

```python
def propose_workspace_patch(prompt: str, workspace: dict) -> dict:
    text = str(prompt or "").lower()
    ws = normalize_workspace(workspace)
    patch: dict[str, Any] = {}
    top = list(ws["panels"][0].get("top_indicators") or [])
    bottom = list(ws["panels"][0].get("bottom_indicators") or [])
    if "5분" in text or "5m" in text or "intraday" in text or "장중" in text:
        patch["panels[0].timeframe"] = "5m"
        if "VWAP(세션)" not in top:
            top.append("VWAP(세션)")
        if "거래량" not in bottom:
            bottom.append("거래량")
    if "추세" in text or "trend" in text:
        for name in ("이동평균선", "자동 추세선·채널", "지수이평(EMA)"):
            if name not in top:
                top.append(name)
    if "변동성" in text or "volatility" in text or "밴드" in text:
        for name in ("볼린저 밴드", "켈트너 채널"):
            if name not in top:
                top.append(name)
    if top:
        patch["panels[0].top_indicators"] = top[:8]
    if bottom:
        patch["panels[0].bottom_indicators"] = bottom[:6]
    after = apply_workspace_patch(ws, patch) if patch else ws
    return {"ok": True, "summary": "차트 요청을 워크스페이스 패치로 변환했습니다.", "patch": patch, "after": after, "diff": diff_workspaces(ws, after), "warnings": []}
```

- [x] **Step 4: Add UI preview/apply**

In `dashboard/chart_workspace_ui.py`, add a popover:

```python
with st.popover("AI"):
    prompt = st.text_area("요청", key="_cw_ai_prompt", placeholder="예: 5분봉으로 바꾸고 VWAP/매물대를 추가해줘")
    if st.button("패치 미리보기", key="_cw_ai_preview"):
        st.session_state["_cw_patch_preview"] = chart_workspace.propose_workspace_patch(prompt, ws)
    preview = st.session_state.get("_cw_patch_preview")
    if preview:
        st.json(preview.get("patch") or {})
        if st.button("적용", key="_cw_ai_apply"):
            st.session_state["_cw_workspace"] = preview["after"]
            st.toast("차트 워크스페이스 패치를 적용했습니다.")
            st.rerun()
```

- [x] **Step 5: Run tests**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py tests/test_chart_workspace_pages.py -q
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add dashboard/chart_workspace.py dashboard/chart_workspace_ui.py tests/test_chart_workspace.py tests/test_chart_workspace_pages.py
git commit -m "add) AI 차트 워크스페이스 패치 UI 추가"
```

---

### Task 6: Deterministic Analysis Rail

**Files:**
- Create: `dashboard/chart_analysis.py`
- Modify: `dashboard/chart_workspace_ui.py`
- Test: `tests/test_chart_analysis.py`

**Interfaces:**
- Produces: `multi_timeframe_summary(load_ohlc: callable, ticker: str) -> dict`
- Produces: `pattern_candidates(hist) -> list[dict]`
- Produces: `seasonality_summary(hist) -> dict`
- Produces: `relative_strength_summary(hist, benchmark) -> dict`

- [x] **Step 1: Write failing analysis tests**

Create `tests/test_chart_analysis.py`:

```python
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_analysis as ca  # noqa: E402


def _hist(n=260, start=100.0, step=0.2):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "Open": close,
        "High": [x + 1 for x in close],
        "Low": [x - 1 for x in close],
        "Close": close,
        "Volume": [1000 + i for i in range(n)],
    }, index=idx)


def test_seasonality_summary_returns_month_rows():
    out = ca.seasonality_summary(_hist(520))
    assert out["ok"] is True
    assert len(out["months"]) == 12
    assert {"month", "avg_return", "win_rate", "sample"} <= set(out["months"][0])


def test_relative_strength_summary_classifies_quadrant():
    hist = _hist(step=0.4)
    bench = _hist(step=0.1)
    out = ca.relative_strength_summary(hist, bench)
    assert out["ok"] is True
    assert out["quadrant"] in {"leading", "weakening", "lagging", "improving"}


def test_pattern_candidates_handles_short_data():
    assert ca.pattern_candidates(_hist(20)) == []
```

- [x] **Step 2: Run test to verify failure**

Run:

```bash
./.venv/bin/pytest tests/test_chart_analysis.py -q
```

Expected: import failure.

- [x] **Step 3: Implement analysis helpers**

Create `dashboard/chart_analysis.py` with deterministic pandas/numpy functions:
- `seasonality_summary`: monthly close-to-close returns grouped by calendar month
- `relative_strength_summary`: 60-day relative strength and 20-day relative momentum vs benchmark
- `pattern_candidates`: simple recent window detectors for Bollinger squeeze expansion, channel breakout, double top/bottom
- `multi_timeframe_summary`: calls `load_ohlc(ticker, tf)` for `5m`, `1h`, `1d`, `1wk`; returns trend based on close vs MA20/MA60 and RSI zone

Use no network calls inside these helpers.

- [x] **Step 4: Render analysis rail**

In `dashboard/chart_workspace_ui.py`, add a right rail under the AI patch popover:

```python
st.markdown("##### 분석")
active = ws["panels"][0]
hist = cached.ohlc(active["ticker"], period="max")
bench = cached.ohlc("QQQ", period="max")
if hist is not None and bench is not None:
    st.json(chart_analysis.relative_strength_summary(hist, bench))
    st.json(chart_analysis.seasonality_summary(hist))
```

For production polish, convert `st.json` to compact markdown metrics after tests pass.

- [x] **Step 5: Run tests**

Run:

```bash
./.venv/bin/pytest tests/test_chart_analysis.py tests/test_chart_workspace_pages.py -q
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add dashboard/chart_analysis.py dashboard/chart_workspace_ui.py tests/test_chart_analysis.py
git commit -m "add) 차트 워크스페이스 분석 레일 추가"
```

---

### Task 7: Final Regression, Commit, Push

**Files:**
- Verify all touched files.

- [x] **Step 1: Run focused regression suite**

Run:

```bash
./.venv/bin/pytest tests/test_chart_workspace.py tests/test_chart_workspace_pages.py tests/test_chart_analysis.py tests/test_dashboard_charts.py tests/test_plotly_embed.py -q
```

Expected: all tests pass.

- [x] **Step 2: Run runtime embed tests if Node dependencies are available**

Run:

```bash
./.venv/bin/pytest tests/test_plotly_embed_runtime.py -q
```

Expected: pass or explicitly document missing local runtime dependencies.

- [x] **Step 3: Compile touched Python files**

Run:

```bash
./.venv/bin/python -m py_compile dashboard/chart_workspace.py dashboard/chart_workspace_ui.py dashboard/chart_analysis.py dashboard/pages/chart_full.py dashboard/views.py dashboard/cached.py agent_console/storage.py
```

Expected: exit code 0.

- [x] **Step 4: Commit remaining files**

If any implementation files remain uncommitted:

```bash
git status --short
git add dashboard/chart_workspace.py dashboard/chart_workspace_ui.py dashboard/chart_analysis.py dashboard/pages/chart_full.py dashboard/views.py dashboard/cached.py agent_console/storage.py tests/test_chart_workspace.py tests/test_chart_workspace_pages.py tests/test_chart_analysis.py
git commit -m "add) 차트 워크스페이스 고도화 마무리"
```

- [x] **Step 5: Push branch**

Run:

```bash
git push origin codex/ticker-analysis-always-visible-ui
```

Expected: remote branch updates successfully.

## Self-Review

Spec coverage:
- Saved layouts: Tasks 1-4
- Multi-chart workspace: Task 4
- Templates: Task 2
- AI chart patching: Task 5
- TrendSpider/Koyfin/StockCharts-inspired improvements: Task 6
- Regression and push: Task 7

Placeholder scan:
- No `TBD`, `TODO`, or "implement later" placeholders.
- Crosshair sync is explicitly listed as unverified browser-runtime work, not a completed claim.

Type consistency:
- `workspace`, `template`, `patch`, and `diff` payloads are plain dict/list values throughout.
- Storage and dashboard wrappers return dict records shaped like strategy studio wrappers.
