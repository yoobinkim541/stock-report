# Harness Reference

This file holds the longer, less frequently needed notes that used to live in the root harness. Keep the root `CLAUDE.md` short and read this file only when you need the broader map.

## Common runtime areas

- `dashboard/` for the Streamlit terminal and its pages
- `agent_console/` for the local AI console, shared memory, wiki, and API routes
- `bot/` for Telegram command handlers and prompt-driven advisor flows
- `reports/` for report generation and source collection
- `crons/` for scheduled collection, evaluation, and retraining jobs
- `providers/` for market data, earnings, quotes, and read-only broker adapters
- `ml/` for model code, policy layers, and validation helpers
- `lib/` for shared memory, world memory, accumulation, and trade-event stores
- `tests/` for smoke tests and targeted behavior checks

## Common state and cache paths

- `~/.local/share/stock-report/` for app runtime data and persistent local state
- `~/reports/` for generated reports, ML artifacts, and cached analysis data
- `~/.cache/` for short-lived tokens, quote caches, and watched state files
- `portfolio_snapshot.json`, `dca_weights.json`, and `target_weights.json` are user-owned state and must not be committed

## Common edit patterns

- Touch the smallest surface that owns the behavior.
- Reuse existing helpers before adding a new layer.
- Update focused tests alongside the code change.
- If a change affects user-visible behavior or runtime wiring, update the relevant docs in the same commit.

## When in doubt

- Search the repo with `rg` first.
- Prefer the current module's helper functions and naming.
- If a task needs broader historical context, read the relevant module docstring, the local `CLAUDE.md`, or the specific page/docs file rather than loading the whole repo map.
