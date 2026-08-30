# 퀀트 투자 서비스 통합 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 국내 장중 단기매매와 국내·미국 중기 포트폴리오가 동일한 전략 명세·백테스트·모의투자 원장을 공유하면서, 규칙·팩터·ML·앙상블 전략을 비용과 리스크를 반영해 검증할 수 있는 서비스를 구축한다.

**Architecture:** 기존 `ml/strategy_studio`를 중심으로 데이터 계약, 신호 어댑터, 비용 인지형 배분, 바 기반 실행, 시계열 검증을 작은 모듈로 분리한다. `StrategySpec`은 기존 규칙 필드를 유지하면서 `data_profile`, `signal`, `portfolio`, `execution`, `promotion`을 추가하고, 국내 단기와 글로벌 중기는 프로필별 데이터·체결 정책으로 분리한다. Streamlit 연구 화면, 전략 캔버스, AI 콘솔은 하나의 전략 실행 DTO와 API를 공유한다.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn/LightGBM 선택 어댑터, 기존 Flask API, Streamlit, SQLite/기존 파일 저장소, pytest. Optuna·Redis·Transformer·RL은 선택적 후속 어댑터로 두며 기본 실행 경로의 필수 의존성으로 추가하지 않는다.

**Spec:** `docs/superpowers/specs/2026-08-28-quant-investing-service-integration-design.md`

## Global Constraints

- 전략의 의미는 `StrategySpec`으로 보존하고, 시장별 차이는 `data_profile`과 `execution_profile`로 분리한다.
- 사용자가 임의 Python을 입력해 실행하는 방식은 사용하지 않는다.
- foundation model은 독립적인 매매 결정기가 아니라 후보 신호 또는 앙상블 구성요소로 사용한다.
- RL은 종목 선정보다 시장가/지정가 선택과 분할체결 같은 실행 문제에 먼저 적용한다.
- 일반적인 전략 실험에는 사람의 수동 검토를 요구하지 않고 자동 검증 게이트를 사용한다.
- 실거래 연결은 sandbox와 별도의 명시적 활성화 상태로 둔다.
- Redis는 모든 기능의 전제조건으로 두지 않으며, hot cache·stream·queue가 실제 병목이 될 때만 도입한다.
- 백테스트·모의투자·실거래 어댑터는 같은 주문·체결 원장 형태를 사용한다.
- 모든 예측·체결·이벤트에는 거래소 시각과 수집/사용 가능 시각을 구분해 기록한다.
- 기존 RSI·EMA 전략과 현재 테스트의 하위 호환을 유지한다.

## File Map

### Create

- `ml/strategy_studio/contracts.py`: 데이터 시점, 신호, 주문 의도, 체결 이벤트, 포지션 상태의 typed DTO와 직렬화.
- `ml/strategy_studio/signals.py`: 규칙·팩터·모델·앙상블 신호 어댑터와 등록 레지스트리.
- `ml/strategy_studio/allocation.py`: 신호를 목표 비중으로 변환하는 위험예산·비용 인지형 배분기.
- `ml/strategy_studio/execution.py`: 다음 바 체결, 갭, 스프레드, 슬리피지, 지연, 부분체결을 처리하는 공통 실행기.
- `ml/strategy_studio/profiles.py`: 국내 단기·글로벌 중기·미국 extended-hours의 실행 기본값과 데이터 freshness 상태.
- `ml/strategy_studio/validation.py`: purged walk-forward, embargo, CPCV 경로, DSR/PBO 지표와 자동 승격 게이트.
- `ml/strategy_studio/registry.py`: feature/model/execution 플러그인과 버전 메타데이터의 등록·조회.
- `tests/test_strategy_contracts.py`: DTO와 StrategySpec 확장 검증.
- `tests/test_strategy_signals.py`: 신호 어댑터와 앙상블 검증.
- `tests/test_strategy_allocation.py`: 비중·위험·거래비용 제약 검증.
- `tests/test_strategy_execution.py`: 체결 이벤트와 포지션 라이프사이클 검증.
- `tests/test_strategy_validation.py`: 시간 분할·누수·검증 게이트 검증.
- `tests/test_strategy_registry.py`: 플러그인·모델 버전과 stale fallback 검증.

### Modify

- `ml/strategy_studio/spec.py`: 새 필드와 중첩 스키마 검증, 지원 프로필·신호 타입·검증 모드 추가.
- `ml/strategy_studio/engine.py`: 기존 규칙 시뮬레이션을 신호·배분·실행 파이프라인으로 연결하고 기존 입력을 호환 변환.
- `ml/strategy_studio/report.py`: 실행 원장, 검증 결과, 데이터 품질, 비용 drag, 제약조건 진단을 결과 DTO에 포함.
- `ml/strategy_studio/presets.py`: RSI·EMA·평균회귀·돌파 외 momentum/factor/ensemble 예제 추가.
- `ml/strategy_studio/__init__.py`: 새 공개 인터페이스 export.
- `ml/data_pipeline.py`: point-in-time 유니버스와 가격 데이터 메타데이터를 실행 입력으로 보존.
- `ml/models.py`: 기존 모델을 versioned model adapter로 감싸고 확률·예측 시점·feature schema를 반환.
- `agent_console/strategy_studio.py`: 실행·검증·LLM patch 흐름을 새 DTO에 연결하고 heuristic-only 경로를 구조화 patch 경로로 교체.
- `agent_console/server.py`: 전략 실행·검증 결과 API와 sandbox 승격 API를 추가.
- `dashboard/strategy_studio.py`: 실행 프로필, 검증 모드, 비용 시나리오, diff, 진단, 거래 마커를 표시.
- `dashboard/pages/research.py`: 연구 화면과 전략 캔버스가 같은 실행 결과를 사용하도록 연결.
- `tests/test_strategy_studio.py`: 새 signal/portfolio/execution/validation 경로와 기존 preset 회귀를 추가.
- `tests/test_agent_console.py`: 새 API route와 AI patch 검증을 추가.
- `tests/test_strategy_studio_pages.py`: 새 컨트롤·결과 패널·오류 표시를 추가.

## Task 1: StrategySpec과 공통 DTO

**Files:**
- Create: `ml/strategy_studio/contracts.py`
- Modify: `ml/strategy_studio/spec.py`, `ml/strategy_studio/__init__.py`
- Test: `tests/test_strategy_contracts.py`, `tests/test_strategy_studio.py`

**Interfaces:**
- Produces `DataStamp`, `SignalOutput`, `OrderIntent`, `FillEvent`, `PositionState` dataclasses.
- Produces `StrategySpec.data_profile`, `StrategySpec.execution_profile`, `StrategySpec.signal`, `StrategySpec.portfolio`, `StrategySpec.execution`, `StrategySpec.promotion`.
- Produces `serialize_event(value: object) -> dict[str, object]` and `deserialize_event(payload: dict[str, object], event_type: str) -> object`.

- [ ] **Step 1: Write failing tests for DTO validation and spec extensions**

```python
def test_strategy_spec_accepts_profile_signal_and_promotion_fields():
    spec = StrategySpec.from_dict({
        "name": "5m momentum",
        "market": "kr",
        "timeframe": "5m",
        "base_symbol": "005930.KS",
        "data_profile": "kr_intraday",
        "execution_profile": "kr_intraday",
        "universe": {"type": "list", "symbols": ["005930.KS"]},
        "signal": {"type": "ensemble", "members": [{"type": "rule", "ref": "rsi"}]},
        "portfolio": {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.15},
        "execution": {"latency_ms": 500, "partial_fill": True},
        "promotion": {"environment": "sandbox"},
    })
    assert spec.data_profile == "kr_intraday"
    assert spec.signal["type"] == "ensemble"
    assert spec.promotion["environment"] == "sandbox"


def test_strategy_spec_rejects_unknown_profile_and_python_plugin():
    with pytest.raises(ValueError, match="unsupported data profile"):
        StrategySpec.from_dict({"name": "bad", "data_profile": "unknown"})
    with pytest.raises(ValueError, match="python"):
        StrategySpec.from_dict({
            "name": "bad",
            "signal": {"type": "model", "plugin": "python"},
        })


def test_fill_event_round_trip_preserves_partial_fill_fields():
    event = FillEvent(
        run_id="run-1", symbol="AAPL", side="buy", requested_qty=100,
        filled_qty=60, decision_price=100.0, fill_price=100.2,
        status="partial", decision_at="2026-08-28T10:00:00Z",
        filled_at="2026-08-28T10:00:01Z",
    )
    restored = deserialize_event(serialize_event(event), "fill")
    assert restored.filled_qty == 60
    assert restored.status == "partial"
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing fields/interfaces**

Run: `pytest tests/test_strategy_contracts.py tests/test_strategy_studio.py -q`

Expected: FAIL because the new DTOs and StrategySpec fields are not implemented.

- [ ] **Step 3: Implement the minimal DTOs and nested StrategySpec validation**

Use frozen or slots dataclasses. Normalize timestamps to timezone-aware ISO strings, reject negative quantities and prices, require `symbol`, `source`, `timeframe`, and `quality` on `DataStamp`, and reject `python`, `shell`, `exec`, and unknown plugin types anywhere under `signal`, `features`, or `execution`.

Add these supported values without changing existing defaults:

```python
SUPPORTED_DATA_PROFILES = {"kr_intraday", "global_swing", "extended_us", "generic"}
SUPPORTED_SIGNAL_TYPES = {"rule", "factor", "model", "ensemble"}
SUPPORTED_EXECUTION_PROFILES = {"kr_intraday", "global_swing", "extended_us", "bar"}
SUPPORTED_PROMOTION_ENVIRONMENTS = {"sandbox", "paper", "live"}
```

- [ ] **Step 4: Run focused and existing strategy tests**

Run: `pytest tests/test_strategy_contracts.py tests/test_strategy_studio.py -q`

Expected: PASS, including the pre-existing RSI, EMA, multi-field price, and patch tests.

- [ ] **Step 5: Commit the contract layer**

```bash
git add ml/strategy_studio/contracts.py ml/strategy_studio/spec.py ml/strategy_studio/__init__.py tests/test_strategy_contracts.py tests/test_strategy_studio.py
git commit -m "add) 퀀트 전략 공통 계약과 실행 DTO 추가"
```

## Task 2: 신호 플러그인과 모델 레지스트리

**Files:**
- Create: `ml/strategy_studio/signals.py`, `ml/strategy_studio/registry.py`
- Modify: `ml/models.py`, `ml/strategy_studio/engine.py`, `ml/strategy_studio/__init__.py`
- Test: `tests/test_strategy_signals.py`, `tests/test_strategy_registry.py`

**Interfaces:**
- Produces `SignalPanel` with `score`, `confidence`, `reason`, `as_of`, `feature_version`, and `model_version` columns keyed by `(timestamp, symbol)`.
- Produces `SignalPanel.from_score(provider: str, scores: pd.DataFrame, confidence: float | pd.DataFrame) -> SignalPanel` and `combine_signal_panels(panels: list[SignalPanel], weights: list[float]) -> SignalPanel`.
- Produces `register_signal_provider(name: str, provider: SignalProvider) -> None` and `get_signal_provider(name: str) -> SignalProvider`.
- Produces `register_model(model_id: str, model: object, metadata: dict[str, object]) -> None` and `get_model(model_id: str) -> RegisteredModel | None`.
- Produces `build_signal_panel(strategy: StrategySpec, compiled: CompiledStrategy) -> SignalPanel`.

- [ ] **Step 1: Write failing tests for rule, momentum, factor, and ensemble signals**

```python
def test_momentum_provider_uses_only_prior_bars_for_score():
    prices = pd.DataFrame({"AAPL": [100, 101, 102, 99, 103]}, index=pd.date_range("2026-01-01", periods=5))
    spec = StrategySpec.from_dict({
        "name": "momentum", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 2},
    })
    compiled = compile_strategy(spec, prices)
    panel = build_signal_panel(spec, compiled)
    assert panel.score.loc[pd.Timestamp("2026-01-03"), "AAPL"] == pytest.approx(0.02)
    assert pd.isna(panel.score.iloc[0, 0])


def test_ensemble_confidence_is_weighted_and_invalid_member_is_reported():
    panel = combine_signal_panels([
        SignalPanel.from_score("rule", pd.DataFrame({"AAPL": [1.0]}), confidence=0.8),
        SignalPanel.from_score("model", pd.DataFrame({"AAPL": [-0.5]}), confidence=0.6),
    ], weights=[0.75, 0.25])
    assert panel.score.iloc[0, 0] == pytest.approx(0.625)
    assert panel.confidence.iloc[0, 0] == pytest.approx(0.75 * 0.8 + 0.25 * 0.6)
```

- [ ] **Step 2: Run signal tests and verify they fail**

Run: `pytest tests/test_strategy_signals.py -q`

Expected: FAIL because provider registration and `SignalPanel` do not exist.

- [ ] **Step 3: Implement provider registry and deterministic baseline providers**

Implement these providers:

- `rule`: existing `rules.entry/exit/trim` outputs converted to score `1`, `0`, or `-1`.
- `momentum`: `close / close.shift(lookback) - 1`, with no future rows and explicit warmup NaN.
- `volatility`: inverse rolling volatility score, with zero-volatility rows invalid rather than infinite.
- `cross_sectional_rank`: rank a score column across the active universe at each timestamp.
- `model`: consume a registered model adapter and require prediction metadata.
- `ensemble`: weighted score and confidence aggregation with a minimum confidence gate.

Expose existing RSI/EMA/Bollinger/VWAP rules through the `rule` provider so old specs do not change behavior. Reject unregistered providers with a warning and a failed run rather than silently using a rule fallback.

- [ ] **Step 4: Adapt existing model classes to versioned metadata**

Add a wrapper that returns:

```python
{
    "model_id": "lgbm_excess_return_v1",
    "feature_version": "features-2026-08-28",
    "predictions": prediction_series,
    "confidence": confidence_series,
    "as_of": last_training_timestamp,
}
```

Keep `MarketRiskModel` and `ExcessReturnModel` public APIs intact. If a model is unavailable or feature columns do not match, return an invalid signal with a diagnostic instead of a fabricated numeric prediction.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest tests/test_strategy_signals.py tests/test_strategy_registry.py tests/test_strategy_studio.py -q`

Expected: PASS.

```bash
git add ml/strategy_studio/signals.py ml/strategy_studio/registry.py ml/models.py ml/strategy_studio/engine.py ml/strategy_studio/__init__.py tests/test_strategy_signals.py tests/test_strategy_registry.py
git commit -m "add) 규칙 팩터 ML 신호 레지스트리 통합"
```

## Task 3: 위험·비용 인지형 목표 비중

**Files:**
- Create: `ml/strategy_studio/allocation.py`
- Modify: `ml/strategy_studio/spec.py`, `ml/strategy_studio/engine.py`, `ml/optimization.py`
- Test: `tests/test_strategy_allocation.py`

**Interfaces:**
- Produces `AllocationResult(weights: pd.DataFrame, diagnostics: list[dict[str, object]], warnings: list[str])`.
- Produces `allocate_targets(signal_panel: SignalPanel, returns: pd.DataFrame, config: dict[str, object], costs: dict[str, object]) -> AllocationResult`.
- Produces `estimate_shrunk_covariance(returns: pd.DataFrame) -> pd.DataFrame`.

- [ ] **Step 1: Write failing tests for constraints, turnover, and risk budget**

```python
def test_allocate_targets_obeys_position_gross_and_turnover_limits():
    idx = pd.date_range("2026-01-01", periods=3)
    scores = pd.DataFrame({"A": [1.0, 1.0, -1.0], "B": [0.0, 1.0, 1.0]}, index=idx)
    returns = pd.DataFrame({"A": [0.01, -0.02, 0.01], "B": [0.02, 0.01, -0.01]}, index=idx)
    result = allocate_targets(
        SignalPanel.from_score("factor", scores, confidence=1.0), returns,
        {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.6,
         "max_gross_exposure": 1.0, "max_turnover": 0.25, "target_volatility": 0.2},
        {"fees_bps": 5, "slippage_bps": 10, "spread_bps": 5},
    )
    assert (result.weights.abs().sum(axis=1) <= 1.0 + 1e-9).all()
    assert (result.weights.abs() <= 0.6 + 1e-9).all().all()
    assert "turnover_limit" in {d["type"] for d in result.diagnostics}


def test_shrunk_covariance_is_symmetric_positive_semidefinite():
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01], "B": [0.02, 0.01, -0.02]})
    cov = estimate_shrunk_covariance(returns)
    assert cov.equals(cov.T)
    assert np.linalg.eigvalsh(cov.to_numpy()).min() >= -1e-10
```

- [ ] **Step 2: Run allocation tests and verify they fail**

Run: `pytest tests/test_strategy_allocation.py -q`

Expected: FAIL because the allocation module and result type do not exist.

- [ ] **Step 3: Implement covariance, score-to-weight, and constraints**

Use Ledoit-Wolf when available and a diagonal-safe sample covariance fallback when it is not. Implement the deterministic default objective:

```text
score_weight
- risk_aversion * portfolio_variance
- turnover_penalty * abs(new_weight - previous_weight)
- cost_bps * abs(new_weight - previous_weight) / 10000
```

Apply constraints in this order: invalid/low-confidence scores to zero, per-position cap, gross exposure cap, target-volatility scaling, then turnover cap. Every truncation emits a diagnostic with `type`, `symbol`, `before`, `after`, and `constraint`.

Use `equal_weight` and `risk_budget` as deterministic fallbacks. Preserve current fixed percentage behavior when `portfolio` is absent and `sizing.type` is one of the existing values.

- [ ] **Step 4: Connect allocation to the strategy engine without changing legacy rule output**

In `run_strategy_backtest`, select the allocation path only when the input explicitly contains `signal`, `portfolio`, or `execution` blocks. A default/empty `data_profile` alone must never route a legacy spec to the new path. For legacy specs, keep `_target_weight_for_symbol()` and existing trade actions. For new specs, store the target weights before execution and expose allocation diagnostics in `StrategyRun`.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_strategy_allocation.py tests/test_strategy_studio.py -q`

Expected: PASS, including all legacy strategy tests.

```bash
git add ml/strategy_studio/allocation.py ml/strategy_studio/spec.py ml/strategy_studio/engine.py ml/optimization.py tests/test_strategy_allocation.py
git commit -m "add) 비용과 위험예산을 반영한 전략 배분기 추가"
```

## Task 4: 백테스트·모의투자 공통 실행기

**Files:**
- Create: `ml/strategy_studio/execution.py`
- Modify: `ml/strategy_studio/contracts.py`, `ml/strategy_studio/engine.py`, `ml/strategy_studio/report.py`
- Test: `tests/test_strategy_execution.py`, `tests/test_strategy_studio.py`

**Interfaces:**
- Produces `ExecutionConfig.from_dict(payload: dict[str, object]) -> ExecutionConfig` with fields `latency_bars: int = 1`, `fees_bps: float = 0.0`, `slippage_bps: float = 0.0`, `spread_bps: float = 0.0`, `max_participation_rate: float = 1.0`, and `partial_fill: bool = True`.
- Re-exports `execution_defaults(profile: str, session: str = "regular") -> ExecutionConfig` from `ml.strategy_studio.profiles`.
- Produces `execute_intents(intents: list[OrderIntent], bars: dict[str, pd.DataFrame], config: ExecutionConfig) -> list[FillEvent]`.
- Produces `apply_fills(position: PositionState, fills: list[FillEvent]) -> PositionState`.
- Produces `run_execution_backtest(target_weights: pd.DataFrame, bars: dict[str, pd.DataFrame], config: ExecutionConfig) -> ExecutionResult`.

- [ ] **Step 1: Write failing tests for next-bar, gap, costs, and partial fills**

```python
def test_market_order_uses_next_bar_and_applies_fee_slippage():
    bars = pd.DataFrame({
        "open": [100.0, 101.0], "high": [101.0, 103.0],
        "low": [99.0, 100.0], "close": [100.5, 102.0], "volume": [1000, 100],
    }, index=pd.date_range("2026-01-01", periods=2))
    intent = OrderIntent(symbol="AAPL", side="buy", quantity=80,
                         decision_at=bars.index[0], decision_price=100.5)
    fills = execute_intents([intent], {"AAPL": bars}, ExecutionConfig(
        latency_bars=1, fees_bps=5, slippage_bps=10, spread_bps=5,
        max_participation_rate=0.5, partial_fill=True,
    ))
    assert fills[0].filled_at == bars.index[1]
    assert fills[0].filled_qty == 50
    assert fills[0].fill_price > 101.0
    assert fills[0].status == "partial"


def test_sell_stop_gap_fills_at_open_not_stop_price():
    bars = {"AAPL": pd.DataFrame({
        "open": [90.0], "high": [92.0], "low": [88.0], "close": [91.0], "volume": [1000],
    }, index=pd.date_range("2026-01-02", periods=1))}
    intent = OrderIntent(symbol="AAPL", side="sell", quantity=10,
                         order_type="stop", stop_price=95.0, decision_at="2026-01-01")
    fills = execute_intents([intent], bars, ExecutionConfig(latency_bars=0))
    assert fills[0].fill_price == pytest.approx(90.0)
    assert fills[0].reason == "stop_gap"
```

- [ ] **Step 2: Run execution tests and verify they fail**

Run: `pytest tests/test_strategy_execution.py -q`

Expected: FAIL because the execution DTOs and simulator are not implemented.

- [ ] **Step 3: Implement execution profiles and event transitions**

Implement `kr_intraday`, `global_swing`, `extended_us`, and `bar` defaults. Use this event order:

```text
decision → latency → eligible bar → order eligibility → volume cap → fill price → fee/slippage → position update
```

For market orders use next-bar open plus signed half-spread and slippage. For stop orders, fill at the bar open when the open crosses the stop; otherwise fill at the stop when the intrabar range crosses it. Limit orders fill only when the bar range reaches the limit. Never fill more than `volume * max_participation_rate` when partial fills are enabled.

- [ ] **Step 4: Replace new-strategy weight changes with execution events**

Keep the legacy weight simulator for old specs. New specs must create `OrderIntent` rows, pass them through `run_execution_backtest`, and derive equity from `FillEvent` and mark-to-market prices. Add `decision_at`, `submitted_at`, `filled_at`, `requested_qty`, `filled_qty`, `decision_price`, `fill_price`, `fee`, `slippage`, and `status` to the report trade table.

- [ ] **Step 5: Run focused and regression tests, then commit**

Run: `pytest tests/test_strategy_execution.py tests/test_strategy_studio.py tests/test_trade_events.py -q`

Expected: PASS with existing rule-based trade actions unchanged.

```bash
git add ml/strategy_studio/execution.py ml/strategy_studio/contracts.py ml/strategy_studio/engine.py ml/strategy_studio/report.py tests/test_strategy_execution.py tests/test_strategy_studio.py
git commit -m "add) 백테스트와 모의투자 공통 체결 엔진 추가"
```

## Task 5: 시계열 검증과 자동 승격 게이트

**Files:**
- Create: `ml/strategy_studio/validation.py`
- Modify: `ml/walk_forward.py`, `ml/strategy_studio/engine.py`, `ml/strategy_studio/report.py`
- Test: `tests/test_strategy_validation.py`

**Interfaces:**
- Produces `ValidationSplit(train: pd.Index, test: pd.Index, path_id: str, embargo_bars: int)`.
- Produces `ValidationReport.from_dict(payload: dict[str, object]) -> ValidationReport` with `folds`, `aggregate`, `warnings`, and `promotion_eligible` fields.
- Produces `PromotionDecision(accepted: bool, environment: str, failed_checks: list[str], warnings: list[str])`.
- Produces `make_purged_walk_forward_splits(index: pd.Index, train_bars: int, test_bars: int, step_bars: int, embargo_bars: int) -> list[ValidationSplit]`.
- Produces `make_cpcv_splits(index: pd.Index, groups: int, test_groups: int, embargo_bars: int) -> list[ValidationSplit]`.
- Produces `evaluate_validation_folds(folds: list[StrategyRun], benchmarks: dict[str, StrategyRun]) -> ValidationReport`.
- Produces `promotion_gate(report: ValidationReport, config: dict[str, object]) -> PromotionDecision`.

- [ ] **Step 1: Write failing tests for purge, embargo, and gate rejection**

```python
def test_purged_walk_forward_has_no_train_test_overlap_or_embargo_leak():
    index = pd.date_range("2020-01-01", periods=30, freq="D")
    splits = make_purged_walk_forward_splits(index, train_bars=10, test_bars=5, step_bars=5, embargo_bars=2)
    for split in splits:
        assert split.train[-1] < split.test[0]
        assert (split.test[0] - split.train[-1]).days >= 3


def test_promotion_gate_rejects_positive_gross_but_negative_net_result():
    report = ValidationReport.from_dict({
        "aggregate": {"net_cagr": -0.02, "benchmark_excess_cagr": -0.03,
                       "max_drawdown": -0.25, "trade_count": 200, "turnover": 0.8,
                       "dsr": 0.1, "pbo": 0.75},
        "folds": [{"net_cagr": -0.01, "trade_count": 100}],
    })
    decision = promotion_gate(report, {"min_trades": 100, "max_pbo": 0.5, "min_dsr": 0.95,
                                       "require_cost_adjusted_positive_excess": True})
    assert decision.accepted is False
    assert "net_excess" in decision.failed_checks
```

- [ ] **Step 2: Run validation tests and verify they fail**

Run: `pytest tests/test_strategy_validation.py -q`

Expected: FAIL because split, report, and gate interfaces do not exist.

- [ ] **Step 3: Implement split generators with explicit timestamps**

`purged_walk_forward` must remove training rows whose label horizon overlaps the test window and then remove `embargo_bars` rows immediately after the test window from the next training set. `cpcv` must enumerate group combinations, preserve chronological order inside each fold, and return a path identifier for each test combination.

Reject non-monotonic or duplicate indexes before creating splits. Return an empty list with a diagnostic when the requested windows cannot fit instead of creating overlapping folds.

- [ ] **Step 4: Implement metrics and gate checks**

Aggregate gross/net CAGR, Sharpe, Sortino, Calmar, MDD, turnover, cost drag, trade count, benchmark excess, DSR, PBO, fold dispersion, and regime stability. DSR/PBO calculations must record the number of tested configurations and return `None` with a warning when the sample is insufficient; no fabricated confidence value is allowed.

Gate checks must include minimum trades, minimum test periods, positive cost-adjusted benchmark excess, maximum MDD, maximum turnover, maximum PBO, minimum DSR, and maximum regime concentration. `PromotionDecision` contains `accepted`, `failed_checks`, `warnings`, and `environment`.

- [ ] **Step 5: Connect validation to preview and commit**

Add `validation` and `promotion` blocks to `StrategyRun` and `build_strategy_report()`. `single_pass` remains a fast preview and always reports `promotion_eligible=False`. `purged_walk_forward` and `cpcv` can be eligible only when every configured gate passes.

Run: `pytest tests/test_strategy_validation.py tests/test_strategy_studio.py -q`

Expected: PASS.

```bash
git add ml/strategy_studio/validation.py ml/walk_forward.py ml/strategy_studio/engine.py ml/strategy_studio/report.py tests/test_strategy_validation.py
git commit -m "add) 시계열 검증과 자동 승격 게이트 추가"
```

## Task 6: 데이터 시점·유니버스·모델 provenance

**Files:**
- Modify: `ml/data_pipeline.py`, `ml/models.py`, `ml/strategy_studio/contracts.py`, `ml/strategy_studio/registry.py`
- Test: `tests/test_strategy_registry.py`, `tests/test_ml_data_sources.py`, `tests/test_ml_universe.py`

**Interfaces:**
- Produces `normalize_data_snapshot(frame: pd.DataFrame, *, symbol: str, source: str, timeframe: str, session: str, adjustment: str) -> DataSnapshot`.
- Produces `point_in_time_universe(symbols: pd.DataFrame, as_of: pd.Timestamp) -> list[str]`.
- Produces `DataSnapshot(data_stamps: list[DataStamp], raw_ref: str | None, quality: str)`.
- Produces `ModelProvenance(model_id: str, feature_version: str, train_start: str, train_end: str, code_commit: str, seed: int, metrics: dict[str, float | None])`.

- [ ] **Step 1: Write failing tests for timestamp, stale data, and point-in-time membership**

```python
def test_data_snapshot_separates_event_and_received_times():
    frame = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-08-28T10:00:00+09:00"]))
    snapshot = normalize_data_snapshot(frame, symbol="A", source="kis", timeframe="5m", session="regular", adjustment="raw")
    assert snapshot.data_stamps[0].timestamp.isoformat().startswith("2026-08-28T10:00:00")
    assert snapshot.data_stamps[0].received_at is not None


def test_point_in_time_universe_excludes_future_members():
    membership = pd.DataFrame([
        {"symbol": "A", "effective_from": "2026-01-01", "effective_to": "2026-06-30"},
        {"symbol": "B", "effective_from": "2026-07-01", "effective_to": None},
    ])
    assert point_in_time_universe(membership, pd.Timestamp("2026-03-01")) == ["A"]
```

- [ ] **Step 2: Run data tests and verify they fail**

Run: `pytest tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py -q`

Expected: FAIL for the new snapshot and point-in-time interfaces.

- [ ] **Step 3: Add provenance without breaking current collectors**

Wrap current yfinance/KIS/KRX/news outputs with `source`, `timeframe`, `session`, `adjustment`, `received_at`, `quality`, and `raw_ref`. Keep existing DataFrame return values and attach the snapshot through `.attrs` or the strategy input object so current consumers remain compatible.

- [ ] **Step 4: Add model provenance and stale fallback diagnostics**

Require every registered model to declare feature names, training range, code commit, seed, and model version. If input data is older than the profile freshness threshold, emit `data_stale` and set signal confidence to zero. Do not silently substitute the latest available value for a stale intraday bar.

- [ ] **Step 5: Run regression tests and commit**

Run: `pytest tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py -q`

Expected: PASS.

```bash
git add ml/data_pipeline.py ml/models.py ml/strategy_studio/contracts.py ml/strategy_studio/registry.py tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py
git commit -m "fix) 전략 데이터 시점과 모델 provenance 보강"
```

## Task 7: 전략 실행 API와 AI patch 협업

**Files:**
- Modify: `agent_console/strategy_studio.py`, `agent_console/server.py`, `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Adds `run_strategy_spec(spec: dict[str, object], *, period: str | None, validation_mode: str | None) -> dict[str, object]`.
- Adds `propose_strategy_patch_with_llm(question: str, current_spec: dict[str, object], context: dict[str, object]) -> dict[str, object]`.
- Adds `validate_strategy_patch(patch: dict[str, object], current_spec: dict[str, object]) -> list[str]`.
- Adds `POST /api/strategy-studio/specs/<spec_id>/validate` and `POST /api/strategy-studio/specs/<spec_id>/activate`.

- [ ] **Step 1: Write failing API tests for execution, validation, and patch rejection**

```python
def test_strategy_validate_route_returns_validation_and_promotion(monkeypatch):
    from agent_console.server import create_app
    from agent_console import strategy_studio

    app = create_app()
    client = app.test_client()
    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *a, **k: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "run_strategy_spec", lambda *a, **k: {
        "ok": True, "run_id": "run-1", "validation": {"promotion_eligible": False},
    })
    response = client.post("/api/strategy-studio/specs/spec-1/validate", json={"mode": "purged_walk_forward"})
    assert response.status_code == 200
    assert response.get_json()["validation"]["promotion_eligible"] is False


def test_llm_patch_cannot_insert_python_or_live_activation():
    errors = validate_strategy_patch(
        {"execution": {"plugin": "python"}, "promotion": {"environment": "live"}},
        {"name": "test", "promotion": {"environment": "sandbox"}},
    )
    assert "python" in " ".join(errors)
    assert "live" in " ".join(errors)
```

- [ ] **Step 2: Run API tests and verify they fail**

Run: `pytest tests/test_agent_console.py -k 'strategy_validate or llm_patch' -q`

Expected: FAIL because the new routes and structured patch validator do not exist.

- [ ] **Step 3: Add run and validation service wrappers**

Make `run_strategy_spec()` load the stored spec, resolve the requested profile and period, run the common engine, and return JSON-safe `report`, `validation`, `promotion`, `data_quality`, and `diagnostics`. Reuse `preview_strategy_spec()` for fast `single_pass` calls and reserve full validation for the explicit validate route.

- [ ] **Step 4: Replace heuristic-only patching with structured patch generation**

Build an LLM prompt containing the current spec, allowed schema, recent validation, data quality, and explicit restrictions. Parse only JSON Patch-like dictionaries, validate with `StrategySpec.from_dict()` plus forbidden plugin checks, show diff, and run preview before saving. If the LLM is unavailable, return a clear `llm_unavailable` diagnostic; do not silently present a rule-based answer as an LLM result.

Allow automatic sandbox version creation after validation. The activate route must reject `environment=live` unless the request explicitly contains `confirm_live=true` and the promotion gate has accepted the run.

- [ ] **Step 5: Run API regression tests and commit**

Run: `pytest tests/test_agent_console.py -k 'strategy or agent' -q`

Expected: PASS, including existing strategy API, context, storage, and LLM routing tests.

```bash
git add agent_console/strategy_studio.py agent_console/server.py agent_console/agent.py tests/test_agent_console.py
git commit -m "add) AI 전략 수정과 검증 API 연결"
```

## Task 8: 연구·전략 캔버스 UI와 프리셋

**Files:**
- Modify: `ml/strategy_studio/presets.py`, `dashboard/strategy_studio.py`, `dashboard/pages/research.py`
- Test: `tests/test_strategy_studio_pages.py`, `tests/test_dashboard_pages.py`

**Interfaces:**
- UI consumes the common result keys `report`, `validation`, `promotion`, `data_quality`, `diagnostics`, `trades`, `equity`, `weights`, and `signals`.
- Adds controls for `data_profile`, `execution_profile`, `validation.mode`, cost scenario, and benchmark.
- Adds panels for equity/drawdown/turnover/exposure, trade markers, gate failures, and AI patch diff.

- [ ] **Step 1: Write failing renderer tests for controls and diagnostics**

```python
import os
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_strategy_lab_renders_profile_validation_and_gate_diagnostics():
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio
strategy_studio.render_strategy_lab(
    "research", pack={{"strategy_studio": {{"ok": True}}}}, mode="research",
    catalog={{"specs": []}}, preview={{
        "ok": False,
        "validation": {{"promotion_eligible": False, "failed_checks": ["net_excess"]}},
        "data_quality": {{"stale": True}},
        "diagnostics": [{{"type": "turnover_limit"}}],
    }})
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in at.markdown)
    assert "실행 프로필" in body
    assert "승격" in body
    assert "데이터 품질" in body
```

- [ ] **Step 2: Run page tests and verify they fail**

Run: `pytest tests/test_strategy_studio_pages.py tests/test_dashboard_pages.py -k 'strategy' -q`

Expected: FAIL because the new controls and panels are not rendered.

- [ ] **Step 3: Add non-RSI preset examples**

Add presets with deterministic data-only rules:

- `momentum_rank`: cross-sectional 20/60-day momentum with top-N selection and turnover cap.
- `mean_reversion`: Bollinger/RSI rule with ATR risk budget.
- `breakout_with_trailing_stop`: rolling high, ATR trailing stop, time stop.
- `factor_ensemble`: momentum, volatility, and quality model slots with equal-weight fallback.
- `kr_intraday_vwap`: VWAP reclaim, volume shock, 5-minute execution profile.

Each preset must include a benchmark, cost assumptions, validation mode, and an explicit warning when it uses a non-point-in-time universe.

- [ ] **Step 4: Render result and interaction states**

Keep heavy calculations behind the existing run buttons/fragments. Render stale data, missing model, insufficient sample, gate rejection, partial fill, and validation success as distinct states. Show the exact changed paths from an AI patch and provide separate `sandbox 저장` and `실거래 활성화` actions.

- [ ] **Step 5: Run page tests and commit**

Run: `pytest tests/test_strategy_studio_pages.py tests/test_dashboard_pages.py -k 'strategy' -q`

Expected: PASS.

```bash
git add ml/strategy_studio/presets.py dashboard/strategy_studio.py dashboard/pages/research.py tests/test_strategy_studio_pages.py tests/test_dashboard_pages.py
git commit -m "add) 전략 캔버스 검증 시각화와 다중 전략 프리셋 추가"
```

## Task 9: 국내 단기·글로벌 중기 프로필 통합과 운영 검증

**Files:**
- Create: `ml/strategy_studio/profiles.py`
- Modify: `providers/intraday_bars.py`, `providers/kr_microstructure.py`, `providers/realtime_quotes.py`, `providers/market_data.py`, `ml/strategy_studio/execution.py`, `agent_console/context.py`
- Test: `tests/test_intraday_signal.py`, `tests/test_chart_replay_rules.py`, `tests/test_trade_events.py`, `tests/test_strategy_execution.py`, `tests/test_agent_console.py`

**Interfaces:**
- Produces `ProfileHealth(status: str, reason: str, age_seconds: float | None)`.
- Produces `profile_health(profile: str, *, last_bar_at: str, now: str, max_age_seconds: int) -> ProfileHealth`.
- Produces `execution_defaults(profile: str, session: str = "regular") -> ExecutionConfig`.
- `kr_intraday` consumes existing KIS/KRX bars, flow, orderbook, breadth, and freshness signals.
- `global_swing` consumes existing Yahoo/market data and applies FX, corporate-action, overnight-gap metadata.
- `extended_us` uses the same order event DTO with a distinct spread, participation, and session policy.

- [ ] **Step 1: Write failing integration tests for profile-specific freshness and session rules**

```python
def test_kr_intraday_pauses_when_1m_sink_is_stale():
    decision = profile_health("kr_intraday", last_bar_at="2026-08-28T10:00:00+09:00",
                             now="2026-08-28T10:05:30+09:00", max_age_seconds=60)
    assert decision.status == "pause"
    assert decision.reason == "stale_intraday_bar"


def test_extended_us_uses_wider_costs_than_regular_session():
    regular = execution_defaults("global_swing", session="regular")
    extended = execution_defaults("extended_us", session="extended")
    assert extended.spread_bps > regular.spread_bps
```

- [ ] **Step 2: Run integration tests and verify they fail**

Run: `pytest tests/test_intraday_signal.py tests/test_chart_replay_rules.py tests/test_trade_events.py -q`

Expected: FAIL only for the new profile health and execution policy assertions.

- [ ] **Step 3: Connect existing collectors without duplicating storage**

Map KIS/KRX timestamps and source health into `DataSnapshot`. Reuse existing stream files and stores; the strategy engine reads normalized snapshots and does not create a second intraday collector. Keep US overnight data separate from regular-session bars and mark missing sessions rather than forward-filling across a closed market.

- [ ] **Step 4: Add operational pause and paper replay behavior**

When intraday freshness, bar completeness, or quote source health fails, block new entries, allow configured exits, and record a `strategy_paused` diagnostic. The same condition must be replayable from a saved snapshot in tests and visible in the AI console context.

- [ ] **Step 5: Run the cross-module suite and commit**

Run: `pytest tests/test_intraday_signal.py tests/test_chart_replay_rules.py tests/test_trade_events.py tests/test_strategy_execution.py tests/test_agent_console.py -q`

Expected: PASS.

```bash
git add providers/intraday_bars.py providers/kr_microstructure.py providers/realtime_quotes.py providers/market_data.py ml/strategy_studio/execution.py agent_console/context.py tests/test_intraday_signal.py tests/test_chart_replay_rules.py tests/test_trade_events.py
git commit -m "fix) 국내 단기와 글로벌 중기 실행 프로필 연결"
```

## Task 10: 전체 회귀·문서·배포 준비

**Files:**
- Modify: `docs/superpowers/specs/2026-08-28-quant-investing-service-integration-design.md`, `README.md` if present, deployment/config documentation used by the project.
- Test: full existing `tests/` suite and a new smoke command documented below.

- [ ] **Step 1: Add end-to-end smoke coverage**

Create `tests/test_strategy_end_to_end.py` with a fully synthetic path:

```python
import numpy as np
import pandas as pd


def make_synthetic_ohlcv_panel(symbols: list[str], periods: int) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=periods, freq="D")
    return pd.DataFrame({
        symbol: 100.0 + np.arange(periods, dtype=float) + offset
        for offset, symbol in enumerate(symbols)
    }, index=index)


def test_synthetic_strategy_runs_from_spec_to_promotion_report():
    spec = builtin_strategy_presets()["momentum_rank"]
    prices = make_synthetic_ohlcv_panel(["A", "B"], periods=260)
    result = run_strategy_backtest(spec, prices, benchmark="A")
    assert result.spec["name"] == spec["name"]
    assert "validation" in build_strategy_report(result)
    assert all("strategy_version" in trade or "date" in trade for trade in result.trades)
```

- [ ] **Step 2: Run focused, dashboard, and full tests**

Run:

```bash
pytest tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_strategy_allocation.py tests/test_strategy_execution.py tests/test_strategy_validation.py tests/test_strategy_registry.py tests/test_strategy_studio.py tests/test_strategy_studio_pages.py tests/test_agent_console.py -q
pytest -q
```

Expected: both commands PASS. Any network-dependent test must use its existing mock/skip mechanism; no new test may require live market credentials.

- [ ] **Step 3: Run static and diff checks**

Run:

```bash
git diff --check
python -m compileall ml/strategy_studio agent_console dashboard
```

Expected: no whitespace errors and no compile errors.

- [ ] **Step 4: Update operational documentation**

Document the three profiles, the difference between `single_pass` and promotion-eligible validation, the source freshness pause behavior, the sandbox/live activation policy, and the Redis introduction threshold. Include one synthetic local run command that does not require credentials.

- [ ] **Step 5: Review the final change set and commit**

Inspect `git diff --stat`, verify no generated data/cache files are included, and commit the completed implementation with a Korean title using the repository convention:

```bash
git add ml agent_console dashboard providers tests docs
git commit -m "add) 공통 퀀트 전략 백테스트와 실행 프로필 완성"
```

## Rollout Order

1. Ship Tasks 1–3 with existing rule presets still using the legacy path.
2. Ship Task 4 in synthetic replay and paper-only mode.
3. Enable Task 5 validation reports before allowing any new strategy to enter paper mode.
4. Enable Task 6 provenance warnings and point-in-time universe when the source metadata is available.
5. Ship Tasks 7–8 to research and sandbox UI.
6. Enable Task 9 for domestic intraday first, then global swing and extended US.
7. Keep foundation-model and RL adapters in shadow mode until their own validation reports pass the same gate.

## Definition of Done

- Existing RSI and EMA presets pass unchanged.
- Momentum, mean reversion, breakout, factor ranking, and ensemble strategies run through the same public backtest API.
- Backtest and paper execution share `OrderIntent` and `FillEvent` shapes.
- Costs, spread, slippage, latency, participation, and partial fills appear in the result.
- Purged walk-forward and CPCV produce non-overlapping, embargoed folds.
- Promotion reports include net performance, risk, turnover, DSR/PBO availability, data quality, and failed checks.
- AI strategy edits produce validated structured patches, diffs, sandbox versions, and no fabricated LLM result when the model is unavailable.
- Research, strategy canvas, and AI console consume the same result DTO.
- No arbitrary Python strategy execution is introduced.
- No Redis dependency is required for the first working release.
