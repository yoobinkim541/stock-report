# LAYA Activation And Knowledge Readiness

## Deployment State

The public multilingual worker is a separate, offline JSONL process. It does
not replace the answering LLM, validate market facts, or place orders. A public
checkpoint always remains shadow-only, even if the generic validated-profile
environment flag is set. The original task-trained worker still requires its
own private trained head.

Official public model candidate:

- Repository: `convaiinnovations/laya-multilingual`
- Revision: `1720e3e3357cfe1e281542e223f8273b0890ca34`
- Weights: `model.safetensors`, 643,835,514 bytes
- Installed SDK: `laya==0.3.29`
- Official model metadata declares Apache-2.0 and ungated access.
- The encoder configuration names `jhu-clsp/mmBERT-base`; the harness's pinned
  English base and private relationship head are not Korean-finance validation.

Sources: https://huggingface.co/convaiinnovations/laya-multilingual and
https://github.com/FMsongX2/laya-graph-harness/blob/main/docs/deployment.md

The operating server has 4 ARM CPU cores, 23 GiB RAM (18 GiB available during
inspection), about 96 GiB free disk, and no detected NVIDIA tooling. The default
application environment does not contain Torch, Transformers or LAYA assets.

## Desktop Installation Receipt

The dedicated SSH key supplied by the user successfully authenticated as
`yoobinkim` at `100.94.176.38`. Host key verification is pinned to the user-confirmed
fingerprint `SHA256:B59Cx+8q01O/UXZjr+OpJ6tlt57+VqUO1pfOmUREmL4`. No GitHub
authentication key was reused for desktop SSH.

Installed into a new independent environment at
`/home/yoobinkim/projects/stock-report-laya-runtime`, leaving the production
Python environment unchanged. Hardware inspection found an RTX 5080 with
16,303 MiB VRAM, 30 GiB RAM and 307 GiB free disk. Runtime versions:

- Python 3.12.3
- Torch 2.7.1+cu128, CUDA 12.8, GPU architecture sm_120 supported
- LAYA 0.3.29
- Transformers 5.17.0
- Safetensors 0.8.0
- Hugging Face Hub 1.33.0
- NumPy 2.5.3

The pinned model weights have SHA-256
`9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`.
Inference loads local assets offline. No public inference HTTP port, private
model-hub upload, paid GPU or paid external API was used.

## Runtime Verification

Genuine CUDA execution returned the public multilingual ready handshake and
bounded JSONL decisions. Two synthetic Korean correction/constraint cases both
selected the wrong document: a domestic ETF correction selected cryptocurrency,
and an Oracle holding/risk question selected database context. An English bridge
smoke selected ETF with one wording and crypto with another candidate wording.
These tiny synthetic tests demonstrate functioning transport, not measured
service accuracy; the results explicitly do not justify active reranking.

The first server bridge smoke measured 3.083 seconds to load and 0.188 seconds
for one warm request. This is a single observation, not a latency distribution.
The bridge now accepts the SDK's separate entropy and answer-confidence fields;
legacy probability-as-confidence responses retain their strict checks.

Public profiles always remain shadow-only. Each transaction captures readiness
metadata under the worker lock, preventing concurrent close/restart from losing
the public-profile guard. SSH remote arguments are not checked against local
asset paths. Failure, timeout or an unvalidated profile preserves baseline
context ordering and source provenance.

Dashboard startup now optionally loads the ignored `.env.laya` configuration
after `.env`. The dedicated known-host file is also ignored. The running
dashboard restarted at 2026-10-07 14:01:01 UTC (23:01:01 KST), PID 3620285.
Its process environment confirmed enabled=1, validated=0 and the dedicated SSH
command. Local and public `/_stcore/health` both returned `ok`. Public URL:
https://plumbing-thought-course-portable.trycloudflare.com/
This is startup/configuration plus direct bridge verification, not an interactive
browser conversation or a financial-model validation.

## Code Verification

The six focused test modules passed: 179 tests covering public worker bounds,
bridge protocol, context integration, shadow invariants, optional dashboard
configuration and recommendation-calibration artifacts. Read-only review found
the close/restart guard race and remote-path preflight issue; both were repaired
and reviewed again. Desktop and local worker SHA-256 matched:
`e4ad613c2221d51ffcfafe0f9041d6cb291bc0dea81e7282f1a06cf209a500a8`.

An initial broader run exposed five calibration fixture failures after 2,519
passes. The fixtures used a collection-time cutoff one minute ahead of the real
clock; later tests trained after that cutoff. Training time is now fixed inside
those tests only, preserving production point-in-time rejection rules. All 61
calibration tests pass. The second full-suite attempt reached roughly 74% without
reported failures but hit its 300-second limit (exit 124); full-suite success is
not claimed. `git diff --check` passed. Browser chat behavior was not tested.

## Information Accumulation

Read-only audit snapshot: 2026-10-07 22:38 KST.

| Measure | Result |
| --- | ---: |
| Wiki total / active / archived | 3,937 / 3,079 / 858 |
| Active source digests | 1,630 |
| Active knowledge pages | 1,449, all draft |
| Reviewed / stable active knowledge | 0 / 0 |
| Active knowledge risk / playbook / concept | 922 / 461 / 66 |
| Digests linked to knowledge | 1,503 / 1,630 (92.2%) |
| Distillation pending / refresh subset | 76 / 75 |
| Evidence-summary template knowledge | 924 / 1,449 (63.8%) |
| Knowledge pages with evidence IDs | 1,448 / 1,449 |
| QMD documents / vector chunks | 3,937 / 0 |

The 328 reviewed pages in this snapshot are source digests, not validated
investment knowledge. Reference syntax and graph connectivity do not prove truth.

Separate article-cache index: 2,517 entries, including 2,369 ready, 101 blocked,
40 retry, 5 failed and 2 unavailable. All ready files existed and were nonempty.
A source-stratified 72-record sample contained 31 records shorter than 300
characters and 36 without publication time; summaries/structured observations
are mixed with articles. Therefore `ready` is not full-text coverage.

The last completed cache record was September 27, 09:27 KST. Current distillation
attaches recent 72-hour events (up to 8 events/topic, 3,000 characters/event),
rather than this separate cache. A digest body can currently satisfy the
`fulltext` path even without original article body, so its label is too broad.

No SaveTicker PDF archive was found in inspected default raw/text/bundle
directories. This does not prove no PDF exists in an unconfigured path. OCR
coverage is unknown; pypdf and Korean/English Tesseract are present, but the
current PDF-image fallback depends on missing PyMuPDF.

Current failed digest states: 184, comprising 100 kind-preservation failures
and 84 invalid-JSON failures. These are current page states, not counts of all
historical failed calls. In the prior 24 hours, SaveTicker news had 48 blocked
runs and its calendar 24 blocked runs, all HTTP 403 and no new persistence.
No access controls were bypassed.

Chat evidence telemetry in the prior seven days recorded one deduplicated
retrieval/context/answer-use query, last on October 3, 17:40 KST. This is not a
count of all chat requests. Reports on October 5-7 each contain 32 wiki citation
lines referring to 18 titles, with identical quoted text across the three days.
Draft eligibility and reuse do not establish independent validation.

## Fine-Tuning Decision

There is no verified query-to-correct-wiki-page dataset yet. Existing page votes,
retrieval exposure and model answers are not relevance ground truth. Do not train
the 1,449 draft documents into a presumed truth model or use the user's previous
assistant answers as correct labels.

Recommended sequence:

1. Public multilingual GPU inference and server transport have been verified.
   Keep selections in shadow and preserve original citations and baseline ordering;
   collect representative accuracy and latency measurements next.
2. Evaluate 20 representative questions to identify retrieval/selection failures.
   Keep exact wiki references separate: deterministic lookup already handles them.
3. Collect 300-500 reviewed question/candidate judgments, including hard negatives,
   ambiguous menus and no-match cases. Minimize private text and retain page
   snapshots, candidate order and source timestamps as of the question.
4. Split by time and conversation/page/merge/template family, not random rows.
   Keep an untouched later-time test slice. Add an explicit abstain contract before
   attempting to train or serve abstain labels; the current bridge has no such ID.
5. Compare baseline retrieval, public LAYA and domain-adapted LAYA on top-1/top-k
   relevance, abstention, calibration and latency, with Korean/English/freshness
   slices. Fine-tune only if the baseline gap and usable supervision warrant it.
6. Set acceptance/error-budget criteria before seeing test outcomes. Fit confidence
   calibration on validation only and activate only after blind evaluation shows
   improvement without critical regressions. Keep a rollback profile.

No fine-tuning or accuracy improvement is claimed here. Genuine public GPU
inference has been verified; task-specific supervised adaptation remains pending.

## Wiki Improvement Order

1. Repair JSON and kind-preservation output contracts before increasing retries.
2. Distinguish original full text, excerpt, translation/summary and structured
   observation; record PDF hash, page count, extraction/OCR method and failures.
3. Review 20 source-backed knowledge pages for claim/date/numbers/applicability/
   counterevidence, then promote only verified pages. Separate template summaries
   from substantive knowledge metrics and label draft report citations explicitly.
4. Verify retrieval on representative queries and record page IDs, source time,
   review state and user feedback as durable query-associated evaluation data.
5. Compare vector retrieval over a reviewed subset after source and lexical
   baselines are trustworthy; do not equate embedding all pages with quality.
