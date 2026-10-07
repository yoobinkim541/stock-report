# Recommendation Calibration, LAYA, And Wiki Audit

## Scope

- Keep legacy recommendation order and trading policy unchanged in shadow mode.
- Produce separate, versioned calibration artifacts for each surface, market,
  horizon, model version and parameter version from immutable ledgers.
- Add an optional local LAYA JSONL worker at the chatbot's wiki context selection
  boundary, without adding Torch, Neo4j, model downloads or paid API calls to the
  stock-report process.
- Report wiki inventory and knowledge readiness separately.

## Calibration Contract

Training uses whole-day chronological holdouts and purges training labels that
were not available before the first holdout decision. Future labels, conflicting
identities/versions, invalid metrics and duplicates cannot contribute. A known
lookup-addressable score version is required; legacy unknown versions are not
invented. Raw scores remain intact.

Acceptance requires sufficient samples, valid held-out evidence, Brier no worse
than normalized raw scores and ECE deterioration below 0.02. Serving requires an
exact scope/scale match, valid artifact structure, no future timestamps and age
no greater than 90 days. The cutoff cannot exceed the candidate decision time.
Failure retains the baseline. Calibration acceptance does not activate the
separate ranker gate or enable orders.

Entry scores have an explicit 0..1 scale. Other surfaces must supply their scale
explicitly. Per-batch scope/cutoff caching avoids repeated artifact reads.

Operational refresh (read-only ledgers, diagnostic artifact writes only):

```bash
.venv/bin/python crons/recommendation_calibration_refresh.py --max-bytes 134217728
```

The initial audit used a temporary artifact directory. It read 10,607 decisions
and 50,655 outcomes, approximately 73.5 MiB combined. Six scopes passed: KR/US
1d, 3d and 5d with entry-v1/default. 20d/60d, intraday and unversioned scopes did
not pass. The default 20d snapshot therefore retains its baseline; accepted 1d
artifacts must not be substituted for a 20d decision. This is calibration
evidence, not a demonstrated profit improvement.

## LAYA Runtime

Upstream: https://github.com/FMsongX2/laya-graph-harness

The repository provides a graph harness and local worker protocol, not a stock
return prediction model. Trained weights and approved corpus are not present.
Actual learned selection requires locally provisioned assets. No learned
inference was claimed or enabled during the integration.

Configuration example, with all paths replaced by actual local assets:

```dotenv
LAYA_WIKI_ENABLED=1
LAYA_MODEL_WORKER_COMMAND='["/path/to/laya/venv/bin/python","/path/to/laya/model-worker/worker.py","--model","/path/to/base-model","--head","/path/to/trained-head","--device","cpu"]'
LAYA_WIKI_PROFILE_VALIDATED=0
LAYA_WIKI_MIN_CONFIDENCE=0.95
```

Transport currently targets the POSIX server. A Windows notebook requires a
POSIX-hosted worker or a separately tested Windows transport; do not set up a
public unauthenticated model endpoint.

The bridge reviews at most eight existing candidates, passes recent conversation
and bounded excerpts, and returns four original pages with evidence references
intact. Exact supplied wiki references take precedence. Ambiguous references
remain a set. Invalid selections, unnormalized probabilities, worker failures
and timeouts preserve the baseline. Worker error details and credentials are not
exposed. Child execution is shell-free and local/offline.

Unvalidated financial/Korean profiles are shadow-only, regardless of confidence.
Only an explicitly validated profile above its threshold can reorder context.
Confidence measures selection, not truth. No wiki status promotion, source
rewriting, autonomous tool invocation or financial order is permitted here.
The original worker requires a trained head in this integration; base-model-only
execution is not reported as the tuned decision model. Preload outside chat
requests when provisioning assets, since cold loading can add up to 30 seconds.

Chat responses expose the decision in `context.laya_review`. Passive readiness:

```bash
.venv/bin/python -c 'from agent_console.laya_bridge import readiness; print(readiness())'
```

## Wiki Audit

Read-only snapshot: 2026-10-07 14:52 KST, with graph checks through 14:57 KST.

| Measure | Value |
| --- | ---: |
| Total / active / archived pages | 3,934 / 3,077 / 857 |
| Source digests | 1,671 |
| Risk / playbook / concept / note | 1,263 / 810 / 189 / 1 |
| Source-backed pages by reference syntax | 3,832 |
| Pages with evidence IDs | 3,818 |
| Active digests linked to knowledge | 1,504 / 1,630 |
| Active knowledge pages, all draft | 1,447 |
| Evidence-summary template pages | 924 |
| Graph nodes / edges | 3,934 / 8,508 |
| Dangling page links or wiki references | 0 |

Reference syntax, links and graph density do not establish source truth. The
1,447 draft knowledge pages are not 1,447 reviewed investment rules. Lexical
QMD coverage includes all 3,934 pages, but vector chunks are zero.

Follow-up snapshot on 2026-10-07 after deployment:

| Measure | Value |
| --- | ---: |
| Total / active / archived pages | 3,937 / 3,079 / 858 |
| Active knowledge pages, all draft | 1,449 |
| Actual distillation queue / refresh subset | 76 / 75 |

Background curation continues, so inventory changes between snapshots.

Completed correction and remaining priorities:

1. Fixed: health now uses the actual runtime selector instead of reporting zero
   pending/refresh work. The current queue contains 76 candidates, including 75
   refresh candidates. Scheduled cached reports update on their next normal run.
2. Recent distillation failures include 120 invalid JSON and 100 kind-preservation
   failures. Repair refresh output validation before increasing throughput.
3. Separate the 924 evidence summaries from substantive reviewed knowledge in
   conversion metrics and report eligibility. Do not automatically promote drafts.
4. SaveTicker news and economic calendar remain blocked by HTTP 403. Track access
   gaps explicitly; do not bypass controls or disguise heuristic labels as LLMs.

The audit scanned bounded wiki/usage records, small health records, read-only
SQL metadata and bounded log tails. It did not scan the approximately 13GB raw
source archive, execute models, mutate pages or send Telegram messages.

## Verification And Deployment

Regression tests cover rejected calibration, scope/time/identity validation,
baseline preservation, bounded local worker behavior and chatbot integration.
Existing operating worktree changes were preserved. Local commits:

- `952118f`: recommendation calibration and LAYA context-selection bridge.
- `987c13d`: wiki health aligned with the actual refresh queue.

Verification results:

- Isolated core regression suite: 466 passed.
- Operating core regression suite: 432 passed.
- Operating health/distillation/caller checks: 52 passed, 1 skipped.
- Final focused recheck: 176 passed, 1 skipped.
- Full repository suite was not verified; broader checks encountered unrelated
  missing chart dependencies.
- Live refresh read 10,607 decisions and 50,655 outcomes; 6 of 26 scopes passed.
- A live US 3d consumer attached accepted calibration in shadow mode, while a
  20d consumer retained its baseline. No trading policy or ranking activation.
- Installed weekday refresh at 09:21 and 16:21 KST using a nonblocking lock.
- Streamlit was restarted (PID 3600518). Bot PID 3595414 is running.
- Local and public Streamlit health returned `ok`; public root returned HTTP 200.
- Public access: https://plumbing-thought-course-portable.trycloudflare.com/

LAYA passive readiness remains `enabled=false`, `configured=false`, `ready=false`.
The bridge is deployed, but trained base-model/head assets and profile validation
are still required before real learned selection can be enabled. No production
environment model settings were changed. Subagent implementation/review used
gpt-6.1-sol and gpt-6-luna as requested.

GitHub push was not performed in this turn. No Vercel landing changes were made.
No financial orders, Telegram sends, model downloads or wiki promotion were
initiated by verification.
