# Task 9 Report: Profile Health Replay Hardening

## Status

Task 9 baseline implementation is preserved in commit `088704a`, on top of
Task 8 commit `940fb02`. This takeover adds a focused hardening commit; no
dashboard UI or live activation code was changed.

## Implemented

- Preserved `profile`, session/quality metadata, profile health, source health,
  and `data_snapshot` through the existing market microstructure snapshot
  store so saved data can reproduce the same pause decision.
- Let the AI console consume a direct saved Korean microstructure snapshot,
  without falling back to a live intraday-bar lookup.
- Rechecked profile/quote health at the actual eligible fill bar, including
  delayed target-weight orders, and emitted replayable `strategy_paused`
  cancellation events and diagnostics for blocked entries.
- Kept exits configurable during pause and made explicit status-only pause
  payloads conservative when no reason is supplied.
- Parsed persisted pause-policy booleans and rejected non-finite freshness or
  quote-age values so invalid input cannot make a source appear fresh.
- Continued using the existing intraday bar, KIS/KRX microstructure, Yahoo,
  realtime quote, and snapshot-store paths; no duplicate collector or writer
  was introduced.

## Verification

- `timeout 90s ./.venv/bin/pytest tests/test_intraday_signal.py tests/test_strategy_execution.py tests/test_realtime_quotes.py tests/test_market_snapshot_store.py tests/test_kr_microstructure.py tests/test_chart_replay_rules.py tests/test_trade_events.py -q`
  - 85 passed in 2.36s
- `timeout 60s ./.venv/bin/pytest tests/test_agent_console.py -k 'context_pack_empty or replayable_strategy_profile_health or strategy_profile_health_replays_direct_microstructure_snapshot' -q`
  - 3 passed in 30.25s
- Focused module `compileall` passed.
- `git diff --check` passed.

## Blockers and Trade-offs

- The broad `tests/test_agent_console.py` module is environment-slow and hit
  the requested timeout after passing 54 tests; the Task 9-specific console
  tests passed independently. No assertion failure was observed.
- Freshness and source failures fail closed for new entries. This can pause
  entries during transient data delays, while configured exits remain usable.
- The parked Task 5 chronology-proof alias blocker remains unchanged. Strict
  live activation and chronology safety were not weakened or bypassed.
- Existing unrelated working-tree changes, including dashboard files and the
  untracked plan/spec documents, were not staged or reverted.

## Follow-up Files

- `agent_console/context.py`
- `agent_console/market_snapshot_store.py`
- `ml/strategy_studio/execution.py`
- `ml/strategy_studio/profiles.py`
- `providers/realtime_quotes.py`
- `tests/test_agent_console.py`
- `tests/test_intraday_signal.py`
- `tests/test_realtime_quotes.py`
- `tests/test_strategy_execution.py`
