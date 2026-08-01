# Strategy Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리서치와 AI 콘솔에서 공통으로 쓰는 DSL 기반 전략 스튜디오를 만들고, AI 대화로 전략을 수정·버전업·재백테스트할 수 있게 한다.

**Architecture:** StrategySpec를 단일 진실원으로 두고, 엔진은 바(bar) 단위로 규칙을 평가해 주문/포지션/비용을 시뮬레이션한다. 리포트 계층은 벤치마크 비교, 거래표, 손익 곡선, 경고를 UI용 구조로 바꾸고, AI 패치 루프는 대화 내용을 구조화된 diff로 바꾼 뒤 미리보기 백테스트가 통과할 때만 새 버전으로 저장한다.

**Tech Stack:** Python, pandas, numpy, sqlite3, Flask, Streamlit, Plotly, 기존 `ml/backtest.py`, `ml/event_backtest.py`, `ml/optimization.py`, `ml/walk_forward.py`, `ml/reporting.py`.

## Global Constraints

- The first release should be DSL-first, not freeform-Python-first.
- Python escape hatches are reserved for a later extension point, not for the first version.
- The same compiled strategy should be runnable from the research page, the AI console, server APIs, and future cron or batch runners.
- The UI must not hide the core decision aids behind expanders.
- Existing strategy behavior remains available as a preset, not as the only path.
- Arbitrary Python execution in the first release is out of scope.
- Live trading execution is out of scope.
- Broker order routing is out of scope.
- New market data collection is out of scope.
- The AI patch loop should never silently mutate the live spec when a preview run fails.
- The first rollout should keep the current RSI path working as a preset so users do not lose a familiar starting point.
- If any of the following occur, the engine should return a structured error or warning state instead of silently fabricating a result: missing indicator data, unsupported rule expression, insufficient bars, stale quotes, too few trades, invalid parameter grid, lookahead or alignment risk, benchmark unavailable.

---

## File Map

- Create `ml/strategy_studio/__init__.py` for public exports.
- Create `ml/strategy_studio/spec.py` for `StrategySpec`, normalization, hashing, and validation.
- Create `ml/strategy_studio/engine.py` for compilation, execution, sizing, costs, walk-forward, and parameter sweep orchestration.
- Create `ml/strategy_studio/report.py` for strategy summary, benchmark comparison, trade table shaping, and warning aggregation.
- Create `ml/strategy_studio/presets.py` for built-in starter specs, including the legacy RSI cash preset.
- Create `ml/strategy_studio/patch.py` for structured diff/apply helpers and unsafe patch rejection.
- Create `agent_console/strategy_studio.py` for chat-driven patch proposals, preview orchestration, and saved-spec workflow helpers.
- Modify `agent_console/storage.py` for strategy spec tables and version history.
- Modify `agent_console/server.py` for strategy studio REST routes.
- Modify `agent_console/context.py` so the context pack exposes strategy studio status and version counts.
- Modify `dashboard/views.py` for strategy studio data access helpers.
- Modify `dashboard/cached.py` for cached strategy catalog / preview wrappers.
- Create `dashboard/strategy_studio.py` for shared Streamlit renderers.
- Modify `dashboard/pages/research.py` for the new strategy lab entry point.
- Modify `dashboard/pages/ai_console.py` for the conversation-driven strategy canvas and patch preview flow.
- Add `tests/test_strategy_studio.py` for core engine and patch regression tests.
- Add `tests/test_strategy_studio_pages.py` for Streamlit page smoke coverage.
- Extend `tests/test_agent_console.py` for storage, API, and strategy patch route coverage.

---

### Task 1: Canonical Strategy DSL, Engine, and Presets

**Files:**
- Create: `ml/strategy_studio/__init__.py`
- Create: `ml/strategy_studio/spec.py`
- Create: `ml/strategy_studio/engine.py`
- Create: `ml/strategy_studio/report.py`
- Create: `ml/strategy_studio/presets.py`
- Create: `ml/strategy_studio/patch.py`
- Test: `tests/test_strategy_studio.py`

**Interfaces:**
- Produces:
  - `StrategySpec.from_dict(payload: dict) -> StrategySpec`
  - `StrategySpec.to_dict() -> dict`
  - `strategy_spec_hash(spec: StrategySpec | dict) -> str`
  - `validate_strategy_spec(spec: StrategySpec | dict) -> list[str]`
  - `builtin_strategy_presets() -> dict[str, dict]`
  - `compile_strategy(spec: StrategySpec | dict, prices: pd.DataFrame) -> CompiledStrategy`
  - `run_strategy_backtest(spec: StrategySpec | dict, prices: pd.DataFrame, *, benchmark: str | None = None) -> StrategyRun`
  - `build_strategy_report(run: StrategyRun, *, spec: StrategySpec | dict | None = None) -> dict`
  - `diff_strategy_specs(before: dict, after: dict) -> list[dict]`
  - `apply_strategy_patch(spec: dict, patch: dict) -> dict`
- Consumes:
  - `ml.backtest.buy_and_hold`
  - `ml.backtest.compare`
  - `ml.event_backtest` fill/order primitives where they fit cleanly
  - `ml.optimization.grid_search_parameters`
  - `ml.walk_forward.walk_forward_splits`

- [ ] **Step 1: Write the failing tests**

```python
def test_builtin_rsi_cash_preset_runs_and_reports_trades():
    spec = builtin_strategy_presets()["rsi_cash"]
    run = run_strategy_backtest(spec, prices, benchmark="QQQ")
    assert run.ok is True
    assert run.metrics["trade_count"] > 0
    assert run.benchmark["symbol"] == "QQQ"
    assert any(t["action"] == "trim_half" for t in run.trades)


def test_unsupported_indicator_is_rejected():
    spec = {
        "name": "bad",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "x", "kind": "python", "code": "import os"}],
        "rules": {"entry": [], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 0, "slippage_bps": 0, "spread_bps": 0},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass"},
    }
    with pytest.raises(ValueError):
        StrategySpec.from_dict(spec).validate()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_strategy_studio.py -q
```

Expected: fail on missing `ml.strategy_studio` symbols and unimplemented DSL validation / engine behavior.

- [ ] **Step 3: Implement the minimum usable DSL and engine**

Implement:

```python
@dataclass(frozen=True)
class StrategySpec:
    ...


@dataclass(frozen=True)
class StrategyRun:
    ok: bool
    spec: dict
    metrics: dict
    trades: list[dict]
    equity: pd.Series | None
    benchmark: dict
    warnings: list[str]
    errors: list[str]
```

Use a small, bounded indicator registry (`rsi`, `ema`, `sma`, `atr`, `macd`, `bollinger`, `vwap`, `volume_zscore`, `rolling`, `drawdown`) and a rule evaluator that supports `all` / `any` / `cross_above` / `cross_below` / threshold comparisons / `trim_half` / `exit_all` / `time_stop`. Wrap the existing RSI cash flow as a preset so the current behavior remains available.

- [ ] **Step 4: Re-run the core tests**

Run:

```bash
pytest tests/test_strategy_studio.py -q
pytest tests/test_ml_backtest.py tests/test_event_backtest.py -q
```

Expected: the new strategy tests pass and the existing backtest primitives remain green.

- [ ] **Step 5: Commit**

```bash
git add ml/strategy_studio tests/test_strategy_studio.py
git commit -m "add) 범용 전략 DSL과 백테스트 엔진 추가"
```

---

### Task 2: Strategy Spec Persistence, Versions, and Patch APIs

**Files:**
- Create: `agent_console/strategy_studio.py`
- Modify: `agent_console/storage.py`
- Modify: `agent_console/server.py`
- Modify: `agent_console/context.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces:
  - `save_strategy_spec(payload: dict) -> dict`
  - `list_strategy_specs(limit: int = 50) -> list[dict]`
  - `get_strategy_spec(spec_id: str, *, version: int | None = None) -> dict | None`
  - `save_strategy_version(spec_id: str, spec: dict, *, patch: dict | None = None, source: str = "ui") -> dict`
  - `list_strategy_versions(spec_id: str, limit: int = 20) -> list[dict]`
  - `revert_strategy_version(spec_id: str, version: int) -> dict`
  - `strategy_lab_state() -> dict`
  - `propose_strategy_patch(question: str, current_spec: dict, history: list[dict] | None, pack: dict | None) -> dict`
  - Flask routes for catalog, CRUD, versions, preview, and patch preview
- Consumes:
  - `ml.strategy_studio.apply_strategy_patch`
  - `ml.strategy_studio.diff_strategy_specs`
  - `ml.strategy_studio.run_strategy_backtest`
  - `agent_console.agent._try_llm_chat` or a small public wrapper for patch generation

- [ ] **Step 1: Write the failing storage/API tests**

```python
def test_strategy_spec_versioning_round_trips(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import storage

    spec = {
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass"},
    }
    saved = storage.save_strategy_spec(spec)
    assert storage.get_strategy_spec(saved["id"])["name"] == "EMA trend"
    assert len(storage.list_strategy_versions(saved["id"])) == 1
```

```python
def test_strategy_patch_rejects_unsafe_code():
    base = {"rules": {"entry": []}, "indicators": []}
    patch = {"indicators": [{"kind": "python", "code": "import os"}]}
    with pytest.raises(ValueError):
        apply_strategy_patch(base, patch)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_agent_console.py -k "strategy_spec or strategy_patch or strategy_studio" -q
```

Expected: schema, route, and patch helpers are still missing.

- [ ] **Step 3: Add SQLite tables and version append logic**

Create two tables in `agent_console/storage.py`:

```sql
CREATE TABLE IF NOT EXISTS strategy_specs (...);
CREATE TABLE IF NOT EXISTS strategy_spec_versions (...);
```

Store the current head row plus append-only version rows. Keep the older `portfolio_scenarios` table intact for compatibility; do not migrate it away in this task.

- [ ] **Step 4: Add Flask routes and context exposure**

Expose routes such as:

```python
@app.get("/api/strategy-studio/specs")
@app.post("/api/strategy-studio/specs")
@app.get("/api/strategy-studio/specs/<spec_id>")
@app.get("/api/strategy-studio/specs/<spec_id>/versions")
@app.post("/api/strategy-studio/specs/<spec_id>/preview")
@app.post("/api/strategy-studio/specs/<spec_id>/patch-preview")
```

Also extend `context.strategy_experiment_state()` or add a sibling helper so the AI console can show saved strategy counts, latest version, and latest preview status in the context rail.

- [ ] **Step 5: Re-run the storage/API tests**

Run:

```bash
pytest tests/test_agent_console.py -k "strategy_spec or strategy_patch or strategy_studio" -q
```

Expected: storage round-trips, version history, and unsafe patch rejection all pass.

- [ ] **Step 6: Commit**

```bash
git add agent_console/storage.py agent_console/server.py agent_console/context.py agent_console/strategy_studio.py tests/test_agent_console.py
git commit -m "add) 전략 스펙 버전 저장과 패치 API 추가"
```

---

### Task 3: Shared Dashboard Wrappers and Strategy Studio UI Components

**Files:**
- Create: `dashboard/strategy_studio.py`
- Modify: `dashboard/views.py`
- Modify: `dashboard/cached.py`
- Test: `tests/test_strategy_studio_pages.py`

**Interfaces:**
- Produces:
  - `strategy_studio_catalog() -> dict`
  - `strategy_studio_spec(spec_id: str, version: int | None = None) -> dict`
  - `strategy_studio_preview(spec: dict, *, benchmark: str | None = None) -> dict`
  - `render_strategy_lab(surface: str, pack: dict, *, mode: str) -> None`
  - `render_strategy_editor(spec: dict, preview: dict | None, versions: list[dict] | None) -> None`
  - `render_strategy_report(report: dict) -> None`
  - `render_strategy_patch_preview(patch: dict, diff: list[dict], preview: dict | None) -> None`
  - `render_version_history(versions: list[dict]) -> None`
- Consumes:
  - `dashboard.cached` wrappers for catalog/spec/preview data
  - `ml.strategy_studio.build_strategy_report`
  - `plotly` equity and trade-marker charts

- [ ] **Step 1: Write the failing UI smoke tests**

```python
def test_strategy_lab_renders_summary_and_preview_first(monkeypatch, tmp_path):
    # AppTest smoke: the lab should render current spec summary, preview metrics,
    # and version history without forcing the user into expanders.
    ...
    assert "현재 전략" in at.markdown[0].value
    assert "백테스트 미리보기" in at.markdown[1].value
    assert "버전" in at.markdown[2].value
```

```python
def test_strategy_patch_preview_shows_diff_and_trade_markers(monkeypatch, tmp_path):
    ...
    assert not at.exception
    body = "\n".join(node.value for node in at.markdown)
    assert "거래" in body
    assert "버전" in body
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_strategy_studio_pages.py -q
```

Expected: the shared dashboard helper module and cached wrappers do not exist yet.

- [ ] **Step 3: Implement the shared dashboard helpers**

Add a compact render module that:

1. shows the current spec summary as visible cards,
2. renders the preview metrics and benchmark comparison side-by-side,
3. draws the equity curve and buy/sell markers with Plotly,
4. shows version history and patch diff in dedicated panels,
5. keeps raw JSON behind an optional secondary view only.

Use `dashboard/views.py` for data access and `dashboard/cached.py` for `st.cache_data` wrappers so the heavy preview run is shared across pages.

- [ ] **Step 4: Re-run the UI smoke tests**

Run:

```bash
pytest tests/test_strategy_studio_pages.py -q
```

Expected: the render helpers load, summary cards appear, and the preview is visible before any raw JSON.

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py dashboard/cached.py dashboard/strategy_studio.py tests/test_strategy_studio_pages.py
git commit -m "add) 전략 스튜디오 공용 UI와 캐시 래퍼 추가"
```

---

### Task 4: Research Page Integration

**Files:**
- Modify: `dashboard/pages/research.py`
- Test: `tests/test_strategy_studio_pages.py`

**Interfaces:**
- Consumes:
  - `dashboard.strategy_studio.render_strategy_lab`
  - `dashboard.cached.strategy_studio_catalog`
  - `dashboard.cached.strategy_studio_preview`

- [ ] **Step 1: Write the failing research-page smoke test**

```python
def test_research_strategy_lab_keeps_ml_preset_and_accepts_custom_spec(monkeypatch):
    # The strategy backtest section should default to a preset,
    # but the same page must also accept a saved custom spec.
    ...
    body = "\n".join(node.value for node in at.markdown)
    assert "전략 백테스트" in body
    assert "ML 프리셋" in body
    assert "전략 캔버스" in body
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
pytest tests/test_strategy_studio_pages.py -q
```

Expected: the research page still points at the old fixed ML-only path.

- [ ] **Step 3: Replace the fixed backtest block with the strategy studio**

Keep the existing ML workflow as one preset in the catalog, but let the user:

1. choose a preset,
2. edit indicators / rules / sizing / costs,
3. run a preview,
4. compare against benchmark,
5. sweep parameters,
6. inspect trade-by-trade outcomes.

The page should still render if no custom strategy is saved.

- [ ] **Step 4: Re-run the research smoke test**

Run:

```bash
pytest tests/test_strategy_studio_pages.py -q
```

Expected: the page shows the generic strategy lab, not only the old ML backtest summary.

- [ ] **Step 5: Commit**

```bash
git add dashboard/pages/research.py tests/test_strategy_studio_pages.py
git commit -m "add) 리서치 페이지에 범용 전략 백테스트 연결"
```

---

### Task 5: AI Console Strategy Canvas and Conversational Patch Loop

**Files:**
- Modify: `dashboard/pages/ai_console.py`
- Modify: `agent_console/agent.py`
- Modify: `agent_console/context.py` if the patch/status payload needs one more field
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces:
  - a new intent such as `strategy_studio` for strategy editing requests
  - `_compose_strategy_studio_answer(question, pack, history)` or an equivalent dedicated composer
  - `answer(... )` metadata that carries `strategy_patch`, `strategy_diff`, and `strategy_preview` when a patch is proposed
- Consumes:
  - `agent_console.strategy_studio.propose_strategy_patch`
  - `agent_console.strategy_studio.apply_strategy_patch`
  - `agent_console.strategy_studio.save_strategy_version`
  - the shared dashboard render helpers from Task 3

- [ ] **Step 1: Write the failing agent-console tests**

```python
def test_strategy_studio_intent_prefers_patch_preview_over_market_template(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import agent

    intent = agent._classify_question_intent("EMA 트렌드로 바꿔줘", {"surface": "lab"}, [])
    assert intent["name"] == "strategy_studio"


def test_strategy_patch_preview_is_saved_as_new_version_only_after_success(monkeypatch, tmp_path):
    ...
    assert result["context"]["strategy_patch"]["ok"] is True
    assert result["context"]["strategy_patch"]["version_action"] == "saved"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_agent_console.py -k "strategy_studio or strategy_patch" -q
```

Expected: the new strategy edit intent and patch metadata do not exist yet.

- [ ] **Step 3: Implement the new strategy conversation path**

Add a dedicated strategy editing route in `agent_console/agent.py` that:

1. detects strategy-editing questions before generic market fallback,
2. asks for a structured patch proposal,
3. runs a preview backtest,
4. returns the patch, the diff summary, and the preview result together,
5. refuses to save when preview validation fails.

The AI console page should show the current spec, the proposed patch, the preview result, and the version history in one visible layout.

- [ ] **Step 4: Verify accept / reject / save behavior**

The acceptance flow must:

1. save a new version when the preview is valid,
2. leave the live spec untouched when the preview fails,
3. preserve the old RSI preset as a selectable template,
4. keep the response useful even if the LLM is unavailable by falling back to the structured local explanation.

- [ ] **Step 5: Re-run the agent tests**

Run:

```bash
pytest tests/test_agent_console.py -k "strategy_studio or strategy_patch" -q
```

Expected: the agent now routes strategy-editing questions to the new patch loop, and version history survives updates.

- [ ] **Step 6: Commit**

```bash
git add dashboard/pages/ai_console.py agent_console/agent.py agent_console/context.py tests/test_agent_console.py
git commit -m "add) AI 콘솔 전략 대화형 패치 루프 추가"
```

---

### Task 6: End-to-End Verification and Final Rollout

**Files:**
- No new files expected unless a final polish fix is needed.

**Interfaces:**
- Produces:
  - one verified strategy catalog path shared by research and AI console
  - one verified patch loop that cannot silently save a failed preview

- [ ] **Step 1: Run the narrow feature suite**

Run:

```bash
pytest tests/test_strategy_studio.py tests/test_strategy_studio_pages.py tests/test_agent_console.py -q
```

- [ ] **Step 2: Run the existing dashboard smoke suite**

Run:

```bash
pytest tests/test_dashboard_pages.py -q
```

Expected: the new strategy studio does not break the rest of the Streamlit app.

- [ ] **Step 3: Smoke the local app**

Start the dashboard or Flask side locally and verify the following by hand:

1. the research page opens to the generic strategy lab,
2. the AI console shows the current spec and patch preview,
3. a saved strategy can be reopened from version history,
4. a failed preview does not mutate the saved version.

- [ ] **Step 4: Apply any final polish fix**

If any edge-case regression appears, fix only the smallest failing slice, rerun the narrow test set, and keep the change scoped to the feature files above.

- [ ] **Step 5: Final commit and push**

```bash
git add .
git commit -m "fix) 전략 스튜디오 통합 검증 및 마무리"
git push
```

---

## Self-Review

### 1. Spec coverage

- DSL-first canonical strategy contract: Task 1.
- Cost-aware backtest execution and trade logging: Task 1.
- Strategy report with benchmark comparison and warnings: Task 1 and Task 3.
- Versioned storage and revert flow: Task 2.
- AI patch loop that saves only after a valid preview: Task 2 and Task 5.
- Research page integration: Task 4.
- AI console strategy canvas integration: Task 5.
- Current RSI path preserved as a preset: Task 1, Task 4, and Task 5.
- Visible UI decision aids, not hidden in expanders: Task 3 and Task 4.

### 2. Placeholder scan

- No "TBD", "TODO", or "implement later" markers remain.
- Every test step names a real file and a concrete command.
- Every API / helper name used later is defined in an earlier task.

### 3. Type consistency

- `StrategySpec`, `StrategyRun`, and `CompiledStrategy` are the canonical core types.
- `strategy_studio` is the naming prefix for the new workflow across storage, API, and UI.
- `run_strategy_backtest` is the shared execution entry point everywhere.
- `strategy_patch` metadata is the shared name for AI proposal output in the agent response payload.
