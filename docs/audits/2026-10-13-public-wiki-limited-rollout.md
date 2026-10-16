# Limited Public Wiki Operation

## Scope

The user approved public Telegram originals only: at most five source attempts
per run, twenty attempts per KST day. Login-only articles and personal dialogue
remain excluded. Existing wiki context must match a committed public source
receipt, including the exact snapshot supplied to inference. Global unrestricted
source compilation remains disabled; external QMD indexing remains disabled.

## Implementation And Review

- Atomic, serialized daily reservations are written before model calls. Failed
  and resumed attempts consume quota; no release/reset of failed reservations.
- Source allowlists apply to discovery, backlog compilation and pending operations.
- A whole-run lock prevents overlapping batches. Each batch uses the existing
  Codex engine, with search disabled and no alternative provider fallback.
- Sol review identified snapshot TOCTOU, an unenforced global enable switch, and
  a clock captured before the quota lock. RED regressions reproduced all three;
  fixes validate one consistent record snapshot, reject unrestricted mode before
  work, and capture the implicit clock after lock acquisition. Re-review found
  no remaining blockers in those three fixes.
- Model-executable preflight avoids reservations when Codex is absent. Failed
  source stages now return a nonzero exit status for service monitoring.

## Actual Server Execution

Installed user units: stock-report-public-wiki.service and
stock-report-public-wiki.timer. The timer is enabled and active; it processes
batches thirty minutes after the previous run ends, with a boot/start trigger.

The first run at 2026-10-13 15:49 UTC attempted five sources but failed all model
calls because systemd's PATH excluded the user's Codex/Node installation. The
timer was stopped for diagnosis. The service now explicitly includes
%h/.local/bin; reservations were retained rather than refunded.

The next run ended at 15:54:53 UTC with five compiled sources, five committed
operations, no compile/routing/artifact failures and no stale targets.
Read-only post-run verification confirmed:

- Five public originals, fourteen compiled claims, five cited draft targets.
- Local Markdown mirrors exist for all five targets.
- All five sources are no longer pending, without additional inference calls.
- Target fingerprints match their committed public receipts.
- Unrestricted source pipeline remains false.
- Budget at that completion was 10/20 for KST 2026-10-14, including failures.

The timer was reactivated and its enabled/active status verified. Reactivation's
boot trigger began another bounded batch; the above totals describe the first
verified successful batch, not a claim that subsequent batches have completed.

Receipts: ~/reports/public-wiki-pilot-20261013/automatic-latest.json,
automatic-budget.json and automatic-attempts/. This reuses the pilot journal,
preventing the prior approved five-source pilot from being compiled again.

## Limits

Final focused regression run: **106 passed in 31.67 seconds**. Final service/timer
syntax verification and git diff --check passed. The full repository suite is
tracked separately; these focused results do not certify unrelated modules.

The full suite completed: 4180 passed, 2 skipped, 3 failed in 671.34 seconds.
Failures: test_wiki_lint_missing_cross_ref_suggests_merge expects the obsolete
merge action rather than review_link; test_target_projection_must_match_supplied_hash_and_content
used the earlier incomplete fixture loaded before its correction (the final
focused run passed); test_private_runtime_archives_source_without_production_queue
could not create its local HTTP listener under the filesystem/network sandbox.
No all-tests-pass claim is made and the latter two residual gate categories
(lint contract alignment and permitted listener verification) remain tracked.

The subsequent timer-triggered batch also completed successfully: five public
sources, 33 claims, six target updates, no source remaining pending. Public
fingerprint, draft/citation and Markdown checks passed again. Its one source
updated two targets; this is an observed integration, not a general semantic
quality certification. The quota is now 15/20 for KST2026-10-14, including the
initial failed five attempts. The service is inactive/success and the enabled
timer is waiting for its next scheduled run.

The records remain draft, not independently fact-certified. This is public
source compilation into durable cited notes, not proof of complete topic-level
cross-document synthesis. Full LLM-wiki completion, login-source consent,
bulk SaveTicker collection and broader relationship/semantic auditing remain
separate work. No Git commit, push or shared-branch merge was performed.
