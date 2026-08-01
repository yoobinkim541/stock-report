# Daily Mock Trading Momentum Overlay Design

Date: 2026-08-01

## Purpose

Add a momentum-following overlay to the existing daily mock trading engines for both KR and US without replacing the current base policy stack.

The goal is to make the mock portfolios less conservative when the tape is trending, while preserving the current explainability, safety gates, and fallback behavior.

This spec covers the daily KR and US mock trading paths. It does not change the intraday / 1m-5m mock system, which already has its own separate design and operating rules.

## Current State

The repo already has the main pieces:

- `crons/kiwoom_mock_track.py` builds the daily KR signal set, ranks candidates, and plans rebalances.
- `crons/us_mock_track.py` builds the daily US signal set, ranks candidates, and plans rebalances.
- `ml/kr_policy.py` and `ml/us_policy.py` each produce a current `policy_score` from base factors plus momentum-related axes.
- `dashboard/pages/paper.py` shows account KPIs, positions, order history, and decision history.
- `dashboard/views.py` joins decisions and outcomes for the paper page and exposes mock-trading summaries.
- `ml/sweet_spot.py` already demonstrates the project’s preferred pattern for trend-aware sizing: keep a base trend rule, then modulate size rather than forcing a brittle all-or-nothing switch.
- `tests/test_kiwoom_mock.py`, `tests/test_us_mock_track.py`, and `tests/test_dashboard_pages.py` already cover the current mock-trading flow and its presentation.

The current weakness is not a missing pipeline. It is that the daily engines still behave more like conservative selectors than momentum followers. The existing momentum-related axes influence ranking, but they do not yet make the portfolio obviously ride trends or scale out when momentum weakens.

## Design Decision

Use a shared **base score + momentum overlay** model with market-specific thresholds.

We keep each market’s existing base policy as the canonical score. A shared momentum overlay computes a `momentum_score` and a `momentum_multiplier`, then blends those into the daily ranking and target sizing only when the data is fresh and the regime supports trend following.

The selected portfolio remains a single portfolio per market. We are not creating a separate capital pool or a separate execution path. The overlay is a ranking and sizing tilt, not a new trading system.

### Why this shape

- It preserves the explainability of the current policy scores.
- It makes the trend-following effect visible without ripping out the current engine.
- It gives us a clean fallback: when momentum data is stale or missing, the engine behaves exactly like today.
- It keeps KR and US aligned through one shared overlay interface while still allowing market-specific thresholds.

## Architecture

### 1. Inputs

The overlay reads:

- adjusted daily close history for the candidate ticker,
- adjusted daily close history for the market benchmark,
- the latest market regime label,
- data freshness for the daily history,
- existing daily base signals and `policy_score`.

### 2. Shared momentum feature builder

Create a small shared module, for example `ml/mock_momentum_overlay.py`, with pure functions that build momentum features from daily data.

The helper should keep the feature set compact and explainable:

- `mom12`: 12-1M momentum
- `mom63`: 3M trend
- `close_vs_sma50`
- `close_vs_sma200`
- `relative_strength_60d`
- `accel20`: momentum acceleration or deceleration
- `volume_confirmation`

The same helper should work for KR and US by accepting the market benchmark series and a small market config object.

### 3. Overlay scorer

The scorer should return:

- `base_score`
- `momentum_score` in `[0, 1]`
- `selection_score` in `[0, 1]`
- `momentum_multiplier` in `[0, 1.25]` or a market-configured equivalent cap
- `overlay_active` as a boolean
- `momentum_state` as one of `strong`, `weak`, `broken`, `inactive`
- `momentum_reason_codes` for the paper ledger

The scorer should normalize weakly comparable inputs into a single score, then let the market regime decide how much of that score can influence selection and sizing.

### 4. Regime gate

The momentum overlay only contributes when the regime is supportive.

Suggested regime bands:

- `risk_on` or equivalent trend-friendly regime: full overlay weight
- `neutral`: moderate overlay weight
- `risk_off`: reduced or zero overlay weight

If the regime classifier is unavailable, stale, or low confidence, the overlay should not force trades. In that case the engine falls back to the current base policy.

### 5. Score blending and sizing

The canonical ranking score becomes:

```text
selection_score = (1 - w) * base_score + w * momentum_score
```

Where:

- `base_score` is the current `policy_score`,
- `momentum_score` is the overlay score,
- `w` is a regime-aware overlay weight, clipped by data freshness and confidence.

We also store:

```text
momentum_tilt = selection_score - base_score
```

for ledger and UI visibility.

For sizing, the overlay should produce a target multiplier that turns trend strength into partial trims, partial adds, or full exits:

- `strong` trend: multiplier above `1.0` up to a market-specific cap
- `weak` trend: multiplier below `1.0`, often around `0.5` to keep only a half-size position
- `broken` trend: multiplier near `0.0` so the position is exited or prevented from re-entering

This lets the existing rebalance planner naturally create:

- partial sells when momentum weakens,
- full exits when momentum breaks,
- incremental adds when momentum strengthens.

## Data Flow

```text
daily history / regime / freshness
        -> shared momentum feature builder
        -> momentum scorer
        -> regime-aware overlay weight
        -> selection_score + momentum_multiplier
        -> plan_rebalance()
        -> ledger + paper dashboard
```

### Decision flow

1. Build the existing daily KR or US signals.
2. Compute the base `policy_score` as today.
3. Compute the momentum overlay for the same candidates.
4. Blend base and overlay into `selection_score`.
5. Convert `momentum_multiplier` into target sizing.
6. Rank candidates by `selection_score`.
7. Apply the existing rebalancing controls:
   - position cap,
   - exit buffer,
   - minimum hold,
   - cash cap,
   - slippage buffer,
   - stale-data fallback.

## UI / Ledger Changes

The paper page should expose the overlay explicitly so the user can tell whether the engine is acting on fundamentals, momentum, or both.

Add the following visible fields to the daily mock-trading table / ledger:

- `base_score`
- `momentum_score`
- `selection_score`
- `momentum_tilt`
- `momentum_multiplier`
- `momentum_state`
- `regime`
- `overlay_active`

The row explanation should remain short. The page should answer:

- why the name was selected,
- whether momentum contributed,
- whether the contribution came from trend or from the current base policy,
- whether the overlay caused a trim, a hold, or an add.

If the overlay is inactive, the UI should say that plainly instead of pretending a trend signal was available.

## Error Handling and Fallbacks

The overlay must fail closed.

If any of the following happen, the engine should fall back to the current base policy:

- not enough daily history,
- stale quote or stale history,
- regime unavailable,
- confidence below threshold,
- benchmark history missing,
- overlay feature computation fails.

The fallback should preserve current behavior, not invent a pseudo-momentum score.

The ledger should record the reason for the fallback so that we can distinguish:

- true trend rejection,
- data unavailability,
- and conservative gating.

## Testing Strategy

Add tests that cover both the happy path and the fallback path.

### Unit tests

- overlay feature extraction from synthetic monotonic and mean-reverting daily series,
- regime-aware overlay weighting,
- selection score blending,
- target multiplier mapping for strong / weak / broken momentum,
- fallback to base policy when overlay data is missing,
- no movement in ranking when overlay is disabled.

### Integration tests

- `kiwoom_mock_track` still produces a daily plan when overlay is on,
- `us_mock_track` still produces a daily plan when overlay is on,
- a strong trend candidate moves up in ranking relative to the base-only order,
- a weak momentum candidate is trimmed instead of held at full size,
- a broken momentum candidate exits instead of being re-added,
- a stale-data candidate does not get forced into selection,
- the ledger stores both base and overlay contributions.

### UI tests

- the paper page still renders with no data,
- the paper page shows the new score breakdown when overlay data exists,
- the paper page keeps current KPI and order history behavior intact,
- the decision table shows overlay fields for both KR and US rows.

## Rollout

Ship behind feature flags:

```text
KR_MOCK_MOMENTUM_OVERLAY_ENABLED
US_MOCK_MOMENTUM_OVERLAY_ENABLED
```

Recommended rollout:

1. default off while tests and shadow logs are added,
2. enable in shadow or dry-run mode,
3. compare overlay vs base behavior,
4. promote to active mode only after the logs show that the overlay improves participation in trends without blowing up turnover or drawdown.

## Scope Boundaries

### In scope

- daily KR mock trading,
- daily US mock trading,
- shared momentum overlay scoring,
- regime-aware blending,
- target sizing and partial-exit behavior,
- paper ledger and UI visibility,
- test coverage for scoring and fallback.

### Out of scope

- intraday / 1m / 5m mock trading,
- new databases,
- manual approval workflows,
- full replacement of the current base policy,
- portfolio-level capital segregation into a second account.

## Success Criteria

- The daily KR and US mock traders can explain `base_score`, `momentum_score`, and `selection_score` separately.
- The overlay changes ranking only when momentum and regime support it.
- Data-poor or stale names behave exactly like the current engine.
- The paper dashboard shows the overlay contribution without breaking existing KPIs.
- Tests cover overlay scoring, fallback behavior, and dashboard rendering.
- The intraday system remains untouched.

## Risks / Trade-offs

- A momentum overlay can increase turnover if the weight is too high.
- A regime gate can be too conservative and hide good trends if the threshold is too strict.
- A blended score is easier to explain than a hard switch, but it can understate the impact of strong momentum if the weight is too small.
- Keeping the base policy canonical preserves stability, but it also means the overlay may need a few iterations before the trend-following effect is strong enough to matter.

