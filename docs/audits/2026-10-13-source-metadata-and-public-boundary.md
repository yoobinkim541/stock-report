# Source Metadata And Public Wiki Boundary

## Source Collection Repair

Listing discovery previously returned links only. Article extraction therefore
lost category and publication data present only on listing rows. RED regressions
confirmed loss at discovery, collection and extraction. The collector now:

- Returns metadata keyed by canonical article URL, preserving exact raw HTML.
- Uses valid article timestamps first, then timezone-aware listing timestamps.
- Leaves missing, relative-only and timezone-naive publication times unknown.
- Gives explicit breaking markers and article breaking badges precedence.
- Normalizes trailing slash aliases and rejects conflicting metadata maps.

Sol reproduced two additional URL/category edge cases; both were reproduced RED,
fixed and re-reviewed without remaining actionable findings. Source/collector/
webhook/forwarding regression suite:163 passes before the additional privacy suite.

The approved one-article live listing probe succeeded: local HTTP200, healthy/
delivered, matching raw hash and matching cached extraction in private-only
storage. No private text was provided to a model or public wiki. Metadata-only
DOM inspection confirmed this particular row had data-breaking=false, no explicit
category, no time element and no date/publication attributes. Its unknown date
is therefore not filled with collection time. This does not prove dates or
classification are populated for every live article or that StockBot emitted it.

The upstream Hermes shutdown atexit warning followed successful delivery; it is
still recorded rather than silently treated as a collection failure or resolved.

## Public Proof Repair

A Luna synthetic regression proved an implicit compiled-note update could
reauthorize a privately modified note: normal candidate filtering excluded it,
but the pipeline's compiled-note hydration still appended it to a public batch.
Redelivery then wrote its modified hash into a new public receipt.

Both public runners now validate proof-backed snapshots before implicit target
hydration. The recurring runner restores its patched hook in finally and rejects
nonpublic process inputs before any pipeline work. RED2 reproduced both cases;
GREEN verifies no second commit, no renewed public approval, and no synthetic
private marker in model prompts. Luna re-ran that exact test and inspected the
guard paths. No actual private article or conversation was used in the regression.

Existing private edits are preserved, not deleted or silently rewritten. The
public model boundary does not grant permission to use them merely because a
public source is delivered again. The raw-only collection and public compilation
policies remain separate.

## Verification And Remaining Gates

Expanded collector/public-pilot/rollout/pipeline/recovery/atomic suite:223 passed
in127.12s. Full suite:4197 passed,2 skipped,1 failed,41 warnings in518.98s. The
failure is test_10k_ingest_and_warm_100_candidate_queries: p95=388.871ms exceeds
its unchanged300ms limit. Its code and test were not edited. Isolated reruns
passed at75.302ms and74.581ms (the latter in the same approved execution context),
both with zero raw scans. This suggests load sensitivity but does not establish
the cause or turn the full run into an all-green run.

Python3.10 imports the public runner; base Python lacks Flask for the collector,
which remains supported by its declared .venv/deployment dependencies. Both
installed user timers are enabled/active/waiting and load the new code on their
next execution. Diff checks passed. No quota reset, unapproved provider call,
credential/browser security change, existing-title cleanup or git write occurred.

The full LLM-wiki goal remains incomplete: existing-title migration, deeper live
synthesis/query audit, the earlier failed public compilation and broader source
rollout/consent gates remain open. These repairs make collection attribution and
incremental public knowledge accumulation more faithful; they do not substitute
for those end-state checks.
