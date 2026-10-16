# Actual Public Wiki Pilot Result

## Authorization And Execution

After the user changed execution permissions, an approved escalated readiness
check reached local Chromium on 127.0.0.1:9225. The earlier socket-denial gate was
no longer the active blocker. Auto-reviewed escalated commands then executed the
explicitly approved public-source pilot; no alternate process bypass was used.

The five frozen public Telegram revisions in public-wiki-pilot.md were revalidated
against immutable raw/text/manifest provenance before processing. Only these five
originals and output derived from them were sent to the existing Codex engine.
Search was disabled, no other provider fallback was used, and model instructions
excluded tools, memory loading and file inspection. Personal conversations and
login-only source material were not provided as input.

The one-off runner is scripts/run_public_wiki_pilot.py (read-only unless --apply).
It uses a dedicated journal, frozen source IDs, and only previously committed
public pilot targets whose semantic fingerprint still matches their receipt.
Legacy private wiki content is not sent as target context. A pre-pilot backup was
kept on the server. The persistent source-pipeline enable switch remains false.

## Verified Result

- Five sources compiled and integrated in five committed operations.
- Five actual new draft pages, containing 21 claims and retained limitations.
- All five pages cite the approved public Telegram sources and have Markdown
  mirrors. Their records are retrievable by the normal wiki search path.
- Source hash/expiry revalidation passed again after integration.
- Repeat run: zero model calls, zero newly committed operations, zero failed or
  pending stages, zero pending artifact/search/log effects and zero stale bases.
- Runtime receipt: ~/reports/public-wiki-pilot-20261013/verification.json, ok=true.
- Index/Markdown/log effects were recorded. External QMD indexing was explicitly
  disabled; local Markdown export still succeeded. Do not claim external indexing.

No original records were deleted. The five pilot target IDs have zero overlap
with the preexisting record set. A whole-store comparison observed 78 preexisting
source_digest records changing during the window; the pilot receipts target none
of those records. Concurrent collection is active, so no exclusive cause or claim
that the entire live store was unchanged is asserted.

The model attribution audit compared the same five public originals with compiled
claims: verdict=pass, issues=0. It explicitly disclaimed independent factual
verification. All pages remain draft; no truth/trust promotion was performed.

## Failures Found And Fixed

The first actual run compiled three sources, failed two, committed two, and left
one routing pending. Cached successes were preserved. A second run completed both
failed compilations and committed two more, retaining one routing failure.
Captured public model output proved the router chose market_outlook, an unsupported
kind. The routing prompt had not exposed the allowed kind enum. RED regression
then fixed the prompt by supplying allowed_new_page_kinds without weakening
validation. The third pass committed the last source with one routing call and
passed the no-model repeat test.

A bounded Luna code review also found vacuous success with zero verified pages and
missing repeat-side-effect checks. RED regression fixed both. Model call records
and public responses have run-specific filenames; no raw bodies or credentials
are included in this repository report. The reviewer was closed; final fixes were
locally validated rather than independently re-reviewed.

Fresh runner/routing/pipeline/recovery regressions: **54 passed in 28.99 seconds**;
diff validation passed. This is not a new full repository test result.

## Collection Status And Remaining Goal

The user-level stock-report-saveticker-recovery.timer and
stock-report-hermes-webhook.service are active. A recovery-state check had a recent
successful probe (~507 seconds old) and no last error. This verifies the installed
fixed-article recovery probe, not bulk SaveTicker ingestion or live breaking-news
StockBot delivery. Root-level units were inactive; user-level units are authoritative.

The approved pilot is complete. The broad LLM wiki objective is not complete:
general source rollout, login-source external-model consent, ongoing compilation,
cross-page synthesis/relationship repair and the full operational/semantic audit
remain open. No permanent automation switch, deployment, commit or push was made.
