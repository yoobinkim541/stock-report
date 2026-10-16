# Limited Public Source Rollout

User approved recurring public Telegram compilation: <=5 source attempts per run,
<=20 per KST day, personal dialogue/login-only sources excluded, only public-derived
wiki context used for integration. Use the existing Codex engine; do not enable
the unrestricted global source pipeline.

1. Implement/test atomic daily reservations in reports/wiki_public_budget.py.
   Reserve before any model call; failed/crashed/resumed attempts consume slots.
   Serialize updates using existing file locks. Keep UTC timestamps but KST day keys.
2. Add a scoped source-revision allowlist to pipeline.run, including backlog resume.
   Default remains unchanged. No nonselected source may reach inference or commit.
3. Implement scripts/run_public_wiki_rollout.py with a dedicated process-wide lock,
   verified public URL/provider/scope gates, reusable pilot journal, accepted public
   target hashes, bounded Codex-only calls and no private context/provider fallback.
   Load oldest pending public revisions first; counts represent attempts, not a
   claim of successful compilation. Failed sources remain retryable within quotas.
4. Add a user-level service/timer (30 minutes after inactive, finite runtime) that
   executes only the gated runner. Test first with synthetic fixtures, inspect a
   real first batch, verify quota state and runtime receipts, then inspect service.
   Keep draft trust and local Markdown export; external QMD indexing remains off.

No CAPTCHA bypass, credential changes, source-retention changes, bulk private
ingestion, global auto mode or unapproved paid compute is included. Preserve the
dirty worktree. No full-goal completion claim based on this subsystem alone.
