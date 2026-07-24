# Stock Report — Harness Core

This repo is an investment terminal with a dashboard, bot, cron jobs, data providers, ML models, and local memory layers.

## Behavioural principles

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
- Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Start here

- Main surfaces: `dashboard/`, `agent_console/`, `bot/`, `reports/`, `crons/`, `providers/`, `ml/`, `lib/`, `tests/`
- Detailed inventory and runtime path notes live in `docs/harness-reference.md`
- When exploring, read only the files that matter for the task. Prefer `rg` / `rg --files`, and parallelize file reads when it helps.

## Working rules

- Preserve unrelated dirty files. Do not revert user changes you did not make.
- Use `apply_patch` for normal edits. Keep edits scoped to the relevant module.
- Default to ASCII unless the file already uses a different character set.
- Avoid destructive commands such as `git reset --hard` or `git checkout --` unless the user explicitly asks.
- Prefer existing patterns and local helpers over new abstractions.
- If behavior changes, add or update focused tests for the touched area.

## Safety invariants

- Never commit secrets or runtime state: `.env`, `portfolio_snapshot.json`, `leverage_state.json`, `price_alerts.json`, token files, cache files, or other live credentials.
- Live trading paths stay read-only or fail-closed. Automated execution remains paper-only unless the repo already has a clearly separate mock path.
- KIS, Kiwoom, and Toss live integrations must not gain order paths by accident.
- `portfolio_snapshot.json` is always updated through atomic write / lock-aware helpers.
- Logs, records, and memory writes should go through the established single writers (`store.py`, `agent_console/storage.py`, `lib/agent_memory.py`, `lib/world_memory.py`).
- Portfolio tickers come from `portfolio_universe.load_portfolio_tickers()`; do not hardcode holdings in reports or pipelines.
- Keep display naming aligned with `ticker_names.py` and the existing formatting helpers.

## High-signal files

- Dashboard: `dashboard/app.py`, `dashboard/pages/*`
- Agent console: `agent_console/agent.py`, `agent_console/context.py`, `agent_console/wiki.py`, `agent_console/server.py`
- Data providers: `providers/*`
- ML and trading: `ml/*`, `crons/*`
- Memory layers: `lib/agent_memory.py`, `lib/world_memory.py`
- Tests: `tests/test_agent_console.py`, `tests/test_dashboard_pages.py`, and the narrowest relevant tests for the files you touch

## Verify

- Run the narrowest useful tests first.
- Use `python3 -m py_compile` when the edit is syntax-heavy.
- When dashboard behavior changes, run `./.venv/bin/pytest -q tests/test_dashboard_pages.py tests/test_agent_console.py -q` or a smaller focused subset.
- Do not claim server deployment is live unless you actually checked the deployed/runtime surface.

## Documentation sync

- If user-facing behavior, commands, or UX change, update `README.md`.
- If file roles, env vars, or operating rules change, update this file.
- Keep docs current with the actual code, not with plans or wishful intent.

## Nested harnesses

- `kiwoom_sync/CLAUDE.md` is the local harness for the Windows sync helper. Only touch it when that subproject changes.