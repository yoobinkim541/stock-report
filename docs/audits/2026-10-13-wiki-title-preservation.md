# Wiki Title Preservation

## Root Cause And Change

The compiler requested and accepted a model-provided title but discarded it from
CompiledSource. The pipeline then constructed a note title from the original
headline plus ` compiled`. Two synthetic regressions reproduced that loss and
the resulting long wiki heading; a third reproduced loss during chunk aggregation.

Validated model titles are now retained (at most 80 characters), propagated through
accepted chunk output, and used for newly created source-summary notes. Processing
state is not appended by the pipeline. Original titles remain in source receipts.
Legacy title-less compilations still load without recompilation; the fallback
uses the original headline without the processing suffix. Existing pages are not
renamed, and immutable accepted compilations are not rewritten.

For fragmented sources the first non-skipped, titled accepted fragment supplies
the display title. Other fragments, claims, scope and cross-span uncertainty remain
in the complete aggregate. This is not a separately model-validated whole-document
title; heterogeneous-source heading quality remains a refinement gate.

## Verification

Initial regressions failed: model-title field absent, original-headline note title,
and aggregated title absent. Compiler/chunks/pipeline/journal/recovery passed 86
tests after fixes. Sol found a Python 3.10 import incompatibility with NotRequired;
it was reproduced, fixed using an optional TypedDict base, and re-reviewed with
successful compiler/chunk/pipeline imports. Expanded focused verification:
97 passed in 29.42 seconds, including skipped and title-less chunk outputs.

## Actual Public Operation

The existing approved service attempted five public sources within the remaining
daily allowance. Four compiled and committed, one failed compilation. All four
new source-summary headings exactly match retained model titles; lengths 15-32,
no appended processing suffix, draft trust, source citations and Markdown mirrors.
Examples include `2026년 10월 10일 리야드 공항 관련 미국 안보 경보`.

The quota reached 20/20 for KST2026-10-14; no failed slot was refunded and no extra
retry was made. Service exit 2 correctly reports the partial failure. A generic
compile_failed receipt does not establish the failed source's underlying cause.
It remains pending for a later budget period. The unrestricted pipeline is off.

## Collection Check

A separately approved bounded listing probe collected one SaveTicker article to
the private raw-only store and local webhook (HTTP200, phase healthy/delivered).
It used an isolated recovery-state file, not the installed service state. No
private text was sent to a model or public wiki. The raw hash matches and extracted
text exactly matches its cached extraction. The manifest has no text_sha256 field,
so comparing against that absent field is not evidence of a corrupt text archive.

Luna independently found that listing discovery drops listing-only category and
publication time before article extraction. Current probe metadata also lacks
both. A synthetic propagation regression and repair remain required; this is not
a certification of breaking-news classification or StockBot delivery. The upstream
Hermes shutdown atexit warning followed successful delivery and remains recorded.

Fresh full-repository verification completed in the approved local-listener
environment: **4191 passed, 2 skipped, 41 warnings in 912.68 seconds**; exit 0.
The skips are not represented as passes. Python 3.10 imports and the actual
four-page title/citation/Markdown checks also passed. This subsystem
progress does not complete the broad LLM-wiki objective. No existing wiki cleanup,
private-model activation, Git commit or push was performed.

Skip follow-up: the legacy wiki selector requires the legacy baseline and is
inapplicable to the new selector; real Chromium chart performance requires the
separate RUN_CANVAS_BROWSER_PERF=1 opt-in. These are not missing compiler/title
assertions. The latter browser-performance gate was not enabled in this run.
