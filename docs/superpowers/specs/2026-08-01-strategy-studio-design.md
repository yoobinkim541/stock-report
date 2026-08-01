# Strategy Studio Design

Date: 2026-08-01

## Purpose

Build a single strategy backtesting service that can express more than RSI-only rules, run those strategies with realistic cost-aware backtests, and let the AI console revise or evolve strategies through conversation.

The current product has useful but fragmented strategy tools:

- `dashboard/pages/ai_console.py` exposes a simple strategy canvas, but it is effectively RSI-only through `rsi_cash_program(...)`.
- `dashboard/pages/research.py` runs a fixed ML backtest flow, not a general strategy runner.
- `agent_console/portfolio_matrix_dsl.py` already contains a compact declarative strategy language, but it is too narrow to be the product’s main strategy surface.
- `ml/backtest.py` and `ml/event_backtest.py` provide strong reusable simulation primitives, but they are not yet wrapped into a user-facing studio.

The objective of this change is not to add another one-off strategy helper. It is to create a durable strategy studio where any supported strategy can be authored, tested, compared, optimized, and then revised through AI conversation.

## Current State

The repo already contains several relevant building blocks:

- `agent_console/portfolio_matrix_dsl.py` can express indicator + rule workflows and can emit backtest equity curves, trade logs, and metrics.
- `ml/backtest.py` provides baseline portfolio metrics and common backtest result containers.
- `ml/event_backtest.py` provides order/fill/portfolio simulation primitives.
- `ml/sweet_spot.py`, `ml/ranker.py`, and other `ml/*` modules already demonstrate walk-forward validation, parameter sweep, and model evaluation patterns.
- `dashboard/pages/research.py` has dedicated sections for screener, backtest, policy learning, and gate checks, but its backtest section is still a fixed ML workflow.
- `dashboard/pages/ai_console.py` already stores strategy scenarios and can show a compact strategy canvas, but the canvas only saves a fixed RSI cash rule today.
- `agent_console/agent.py` already routes some questions to portfolio/scenario reasoning, so conversational strategy editing can reuse the existing AI console flow instead of inventing a new chat surface.

The main gap is that strategy definition, backtest execution, optimization, and AI revision are not normalized around one shared contract.

## Design Decision

Use a **declarative StrategySpec DSL** as the canonical strategy contract, with the AI console acting as a patching and review layer on top of that spec.

The first release should be DSL-first, not freeform-Python-first.

Why:

- It preserves reproducibility and testability.
- It makes parameter sweeps and walk-forward validation easy to reason about.
- It allows the AI to propose diffs instead of generating opaque executable code.
- It keeps the product aligned with the existing Python data/analysis stack.

Python escape hatches are reserved for a later extension point, not for the first version.

## Architecture

The studio has four layers:

```text
StrategySpec -> Strategy Engine -> Strategy Report -> AI Patch Loop
```

### 1. StrategySpec

The spec is a versioned JSON-like object that fully describes a strategy. It should support:

- universe selection,
- timeframe,
- indicators,
- derived features,
- entry rules,
- exit rules,
- sizing rules,
- cost/slippage assumptions,
- optimization parameters,
- walk-forward validation settings,
- notes / rationale / tags.

### 2. Strategy Engine

The engine compiles a StrategySpec into an executable simulation. It should be able to:

- build indicator series,
- evaluate boolean or scored rules,
- emit entries, exits, trims, and full closes,
- simulate with transaction cost and slippage,
- record trade events,
- return a standard result object with metrics and an equity curve.

This engine should reuse the repo’s existing simulation helpers where possible rather than rewriting them.

### 3. Strategy Report

The report layer turns a strategy run into a readable product artifact:

- strategy metadata,
- parameter table,
- trade list,
- equity curve,
- benchmark comparison,
- drawdown / Sharpe / CAGR / turnover summary,
- warning badges for lookahead risk, missing data, or too few trades.

### 4. AI Patch Loop

The AI console should not overwrite strategy definitions wholesale. Instead, it should:

- read the current StrategySpec,
- interpret the user’s request as a patch,
- propose a structured diff,
- run a preview backtest,
- let the user accept or reject the patch,
- save the revised spec as a new version.

The AI layer is advisory and iterative. The spec remains the source of truth.

## StrategySpec Model

The initial schema should be compact but expressive.

```text
id
name
description
market              # kr, us, mixed
universe            # list, screen, watchlist, benchmark-relative
timeframe           # 1d, 5m, 1m, custom
base_symbol         # benchmark or signal symbol
indicators          # array of indicator definitions
rules               # entry / exit / trim rules
sizing              # fixed, volatility-scaled, risk-budgeted
costs               # fees, slippage, spread assumptions
optimization        # parameter grid / search target / constraints
validation          # walk-forward, purged split, embargo, trade minimum
metadata            # notes, tags, owner, created_by_ai, version
```

### Indicators

Indicators should be declarative and reusable:

- RSI
- EMA / SMA
- ATR
- MACD
- Bollinger Bands
- VWAP
- volume z-score
- drawdown or trend features
- custom rolling statistics

The first version should support only a bounded, well-tested set of indicators. If a user wants a new indicator family, it should be added to the registry rather than embedded as arbitrary Python.

### Rules

Rules should support:

- comparison expressions,
- threshold crossings,
- crossovers,
- score aggregation,
- all/any boolean grouping,
- partial sizing actions,
- exit on signal break,
- time stop,
- volatility stop,
- max daily loss stop,
- end-of-session flat.

### Example

```json
{
  "name": "QQQ trend + risk filter",
  "market": "us",
  "universe": {"type": "list", "symbols": ["QQQ", "TQQQ", "CASH"]},
  "timeframe": "1d",
  "base_symbol": "QQQ",
  "indicators": [
    {"name": "rsi", "period": 14, "field": "close", "output": "rsi"},
    {"name": "ema", "period": 50, "field": "close", "output": "ema50"},
    {"name": "atr", "period": 14, "field": "high_low_close", "output": "atr14"}
  ],
  "rules": {
    "entry": [{"all": [{"field": "rsi", "op": "<=", "value": 35}, {"field": "close", "op": ">", "ref": "ema50"}]}],
    "exit": [{"any": [{"field": "rsi", "op": ">=", "value": 70}, {"field": "close", "op": "<", "ref": "ema50"}]}],
    "trim": [{"field": "drawdown", "op": ">=", "value": 0.08, "action": "reduce_half"}]
  },
  "sizing": {"type": "risk_budget", "risk_per_trade": 0.005, "max_position_pct": 0.33},
  "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 3},
  "optimization": {"params": {"rsi_entry": [25, 30, 35], "rsi_exit": [65, 70, 75]}, "target": "sharpe"},
  "validation": {"mode": "walk_forward", "split": "purged", "min_trades": 30}
}
```

## UI Design

### Research page

Turn the research backtest section into a general strategy lab.

The section should let the user:

- choose a saved strategy,
- choose a template strategy,
- edit indicators / rules / sizing / costs,
- run a backtest,
- compare against benchmark series,
- sweep parameters,
- inspect trade-by-trade outcomes.

The current fixed ML backtest should remain available as one strategy preset, not as the only path.

### Strategy Canvas

The canvas in the AI console should become a visible strategy authoring surface:

- strategy name,
- market and timeframe,
- indicator stack,
- rule blocks,
- sizing rules,
- cost assumptions,
- optimization settings,
- save / duplicate / run / patch / revert actions.

The canvas should always show the current spec, the current preview result, and the current AI suggestion side by side or in clearly separated panels.

### AI Conversation Loop

The AI console needs a dedicated strategy conversation mode that answers questions like:

- “RSI 말고 추세 추종으로 바꿔줘”
- “손절을 ATR 기반으로 바꿔줘”
- “진입 조건을 더 엄격하게 해서 거래 횟수를 줄여줘”
- “이 전략에서 과최적화 위험이 어디 있어?”

The UI should show:

- current spec summary,
- AI-generated patch preview,
- diff-style explanation,
- rerun button,
- accept / reject controls,
- version history.

### What must stay visible

The UI must not hide the core decision aids behind expanders:

- strategy spec summary,
- active parameters,
- benchmark comparison,
- latest run metrics,
- recent trade list,
- AI patch preview.

Expanders may still exist for raw JSON or verbose details, but they must not be the only path to understanding the strategy.

## Data Flow

```text
StrategySpec edit
  -> validate
  -> compile
  -> backtest
  -> compare / optimize
  -> AI patch suggestion
  -> patch preview
  -> save new version
```

The same compiled strategy should be runnable from:

- the research page,
- the AI console,
- server APIs,
- and future cron or batch runners.

## Error Handling

The studio should fail visibly and conservatively.

If any of the following occur:

- missing indicator data,
- unsupported rule expression,
- insufficient bars,
- stale quotes,
- too few trades,
- invalid parameter grid,
- lookahead or alignment risk,
- benchmark unavailable,

the engine should return a structured error or warning state instead of silently fabricating a result.

If a strategy is invalid, the UI should show:

- what failed,
- where it failed,
- whether the failure is fixable,
- and whether the user can still save the draft spec.

The AI patch loop should never silently mutate the live spec when a preview run fails.

## Testing Strategy

### Core engine tests

- compile a valid StrategySpec into a runnable strategy,
- reject unsupported indicator or rule types,
- execute a simple moving-average crossover strategy,
- execute a simple RSI strategy,
- record entries and exits correctly,
- apply transaction costs and slippage,
- compute benchmark comparison metrics,
- preserve no-lookahead / shifted execution behavior.

### DSL tests

- parse nested all/any rules,
- support threshold and crossover expressions,
- support partial trims and full exits,
- support parameter expansion for optimization,
- reject invalid refs or ambiguous expressions.

### AI patch tests

- patch an RSI strategy into an EMA trend strategy,
- patch sizing without changing rules,
- patch entry thresholds while preserving universe and benchmark,
- reject patch suggestions that try to inject unsupported executable code,
- preserve version history across save / duplicate / revert.

### UI tests

- research page still renders if no custom strategy is saved,
- strategy canvas shows saved spec and preview result,
- AI console exposes patch preview and version metadata,
- visible summary cards render before raw JSON details,
- fallback states render when backtest fails.

## Rollout

Ship in stages.

1. Add the StrategySpec model and engine behind the existing UI.
2. Move the research backtest section to the new engine while keeping the current ML preset.
3. Upgrade the AI console canvas from RSI-only to generic DSL editing.
4. Add AI patch preview and version history.
5. Add more indicator and rule primitives only after the core flow is stable.

The first rollout should keep the current RSI path working as a preset so users do not lose a familiar starting point.

## Scope Boundaries

### In scope

- generic strategy DSL,
- backtest execution,
- parameter optimization,
- benchmark comparison,
- AI-assisted strategy patching,
- research page integration,
- AI console strategy canvas integration,
- versioned strategy storage.

### Out of scope

- arbitrary Python execution in the first release,
- live trading execution,
- broker order routing,
- new market data collection,
- new ML model training pipelines,
- replacing the existing portfolio / paper-trading systems.

## Success Criteria

- The user can author a strategy that is not RSI-only.
- The same strategy can be run from research and from the AI console.
- The backtest reports costs, benchmark comparison, and trades in a readable way.
- The AI console can revise strategy definitions through conversation and show the diff before saving.
- The strategy canvas no longer feels like a single hard-coded rule form.
- Existing strategy behavior remains available as a preset, not as the only path.
