# KR Daily Mock Trading Momentum Overlay Design

Date: 2026-08-01

## Purpose

Add a momentum-following overlay to the existing daily KR mock trading engine without replacing the current quality/fundamental policy.

The goal is not to rebuild the portfolio engine from scratch. The goal is to make the current daily mock trader less conservative by letting strong trends tilt selection and retention, while preserving the current explainability, safety gates, and fallback behavior.

This change is limited to the daily KR mock trading path. The intraday / 1m-5m mock system already has its own separate design and should remain unchanged in this spec.

## Current State

The repo already has the main pieces:

- `crons/kiwoom_mock_track.py` collects daily KR signals, ranks candidates, and plans rebalance orders.
- `ml/kr_policy.py` builds the current `policy_score` from ranker, fundamentals, signal alignment, confidence, and price axes.
- `dashboard/pages/paper.py` shows account KPIs, positions, order history, and the decision ledger.
- `dashboard/views.py` joins decisions and outcomes for the paper page and exposes mock-trading summaries.
- `tests/test_kiwoom_mock.py` and `tests/test_dashboard_pages.py` already cover the current mock-trading flow and its presentation.

The current weakness is not a missing pipeline. It is that the ranking logic is still too cautious, so the mock portfolio tends to behave more like a quality filter than a trend follower.

## Design Decision

Use a **single-account blended score** with a momentum overlay.

We keep the existing base policy as the canonical score. A second momentum layer computes a `momentum_score`, then blends it into the selection score only when the data is fresh and the market regime supports trend following.

The selected portfolio is still one portfolio. We are not creating a separate real account, separate capital pool, or separate execution path. The overlay is a ranking and retention tilt, not a new trading system.

### Why this shape

- It preserves the explainability of the current policy score.
- It makes the trend-following effect visible without ripping out the current engine.
- It gives us a clean fallback: when momentum data is stale or missing, the engine behaves exactly like today.
- It keeps the door open for a later US expansion through the same interface.

## Architecture

### 1. Inputs

The overlay reads:

- adjusted daily close history for the candidate ticker,
- adjusted daily close history for the benchmark / market proxy,
- the latest market regime label,
- quote or history freshness,
- existing daily base signals and `policy_score`.

### 2. Momentum feature builder

Create a small momentum overlay module, for example `ml/kr_momentum_overlay.py`, with pure functions that build these features:

- `mom12`: 12-1M momentum
- `mom63`: 3M trend
- `accel20`: momentum acceleration / deceleration
- `close_vs_sma50`
- `close_vs_sma200`
- `relative_strength_60d`
- `volume_confirmation`

The exact feature list should stay small enough to explain in the paper ledger. The overlay should prefer features already present in the codebase or easy to derive from existing daily history.

### 3. Overlay scorer

The scorer should return:

- `momentum_score` in `[0, 1]`
- `momentum_confidence` in `[0, 1]`
- `momentum_active` as a boolean
- `momentum_reason_codes` for the paper ledger

The scorer should normalize weakly comparable inputs into a single score, then let the regime gate decide how much of that score can influence selection.

### 4. Regime gate

The momentum overlay only contributes when the regime is supportive:

- `risk_on`: full or near-full overlay weight
- `neutral`: moderate overlay weight
- `risk_off`: reduced or zero overlay weight

If the regime classifier is unavailable, stale, or low confidence, the overlay should not force trades. In that case the engine falls back to the current base policy.

### 5. Score blending

The canonical ranking score becomes:

```text
selection_score = (1 - w) * base_score + w * momentum_score
```

Where:

- `base_score` is the current `policy_score`,
- `momentum_score` is the overlay score,
- `w` is a regime-aware overlay weight, clipped by data freshness and confidence.

This keeps the score bounded and easy to explain.

We also store:

```text
momentum_tilt = selection_score - base_score
```

so the ledger and UI can show how much the overlay changed the decision.

## Data Flow

```text
daily history / regime / freshness
        -> momentum feature builder
        -> momentum scorer
        -> regime-aware overlay weight
        -> selection_score
        -> plan_rebalance()
        -> ledger + paper dashboard
```

### Decision flow

1. Build the existing daily KR signals.
2. Compute the base `policy_score` as today.
3. Compute the momentum overlay score for the same candidates.
4. Blend base and overlay into `selection_score`.
5. Rank candidates by `selection_score`.
6. Apply the existing rebalancing controls:
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
- `regime`
- `overlay_active`

The row explanation should remain short. The page should answer:

- why the name was selected,
- whether momentum contributed,
- whether the contribution came from trend or from the current base policy.

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

- overlay feature extraction from synthetic monotonic and mean-reverting price series,
- regime-aware overlay weighting,
- selection score blending,
- fallback to base policy when overlay data is missing,
- no movement in ranking when overlay is disabled.

### Integration tests

- `kiwoom_mock_track` still produces a daily plan when overlay is on,
- a strong trend candidate moves up in ranking relative to the base-only order,
- a stale-data candidate does not get forced into selection,
- the ledger stores both base and overlay contributions.

### UI tests

- the paper page still renders with no data,
- the paper page shows the new score breakdown when overlay data exists,
- the paper page keeps current KPI and order history behavior intact.

## Rollout

Ship behind a feature flag:

```text
KR_MOCK_MOMENTUM_OVERLAY_ENABLED
```

Recommended rollout:

1. default off while tests and shadow logs are added,
2. enable in shadow or dry-run mode,
3. compare overlay vs base behavior,
4. promote to active mode only after the logs show that the overlay improves participation in trends without blowing up turnover or drawdown.

## Scope Boundaries

### In scope

- daily KR mock trading,
- momentum overlay scoring,
- regime-aware blending,
- paper ledger and UI visibility,
- test coverage for scoring and fallback.

### Out of scope

- intraday / 1m / 5m mock trading,
- US mock trading mechanics,
- new databases,
- manual approval workflows,
- full replacement of the current base policy.

## Success Criteria

- The daily KR mock trader can explain `base_score`, `momentum_score`, and `selection_score` separately.
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

