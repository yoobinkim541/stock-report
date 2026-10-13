# Hermes Source Webhook Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task.

**Goal:** Receive complete authorized source bodies from Hermes and feed the
existing immutable raw/archive and persistent cited wiki workflow.

**Architecture:** A dedicated signed source-batch sender and loopback Flask
receiver, with durable receipts, reuse current collection/storage APIs. Hermes
browser access and human authentication stay separate from webhook acceptance.

**Tech Stack:** Existing Python, Flask, requests, safe_io, pytest; no new paid APIs.

**Spec:** docs/superpowers/specs/2026-10-08-hermes-source-webhook-design.md

## Global Constraints

- No cookies, tokens, login forms or unrelated tool logs in source payloads.
- No automatic social login/MFA/CAPTCHA bypass.
- Validate whole batch before writes; preserve original source and prior wiki.
- Bind loopback by default; use private networking or HTTPS cross-host.
- Webhook acceptance is ingestion, never proof of LLM/wiki completion.

## Review Focus

- Retry after receiver crash must deduplicate durable partial ingestion.
- Reused delivery IDs with different content must fail, not discard new content.
- Published source dates and transport collection time must remain distinct.
- Login/challenge content must not masquerade as a complete article.
- Hermes browser and the user's laptop must really share an authorized session.

## Tasks

### 1. Signed Ingest Contract

Create `reports/hermes_source_webhook.py` and `tests/test_hermes_source_webhook.py`.
Expose `create_app()` and `ingest_batch(payload, cache_dir, state_dir)`.

- [x] Write failing authentication/replay/size/schema/body/URL tests.
- [x] Verify RED, then implement dedicated HMAC and strict field validation.
- [x] Add raw/extracted-text/event storage with locked durable delivery receipts.
- [x] Verify duplicate/conflicting-ID/crash-resume and original-byte tests.

### 2. Hermes Collection Sender

Create `scripts/hermes_source_send.py` and a collection prompt under `config/`.
Read an explicit source-batch file, sign the raw JSON and POST without redirects.
Reuse Hermes job/browser APIs; do not subscribe to general lifecycle payloads.

- [x] Add failing sender signature, redirect and failure-status tests.
- [x] Implement bounded sender and operator configuration documentation.
- [x] Verify a real local signed HTTP delivery and subsequent body retrieval.

### 3. Real Runtime And Wiki Verification

- [x] Configure private receiver and dedicated secret without printing it.
- [x] Implement deterministic SaveTicker browser capture, fair outbox retries,
  version idempotency and a no-side-effect connection readiness CLI.
- [ ] Wire Hermes job only after its browser access is confirmed.
- [ ] Read an actual login-protected article, deliver its body and immutable raw.
- [ ] Verify source event, classification, wiki synthesis input and cited draft.
- [ ] Preserve blocked/reauth-required states until those live gates pass.
- [x] Run wiki/source/webhook regression tests and deployed dashboard health.

Commit only owned verified changes; unrelated dirty workspace changes remain.

## Runtime Evidence (2026-10-08)

- Gunicorn user service is enabled and listening only on `127.0.0.1:8777`.
- Dedicated secret is ignored by Git and stored with `0600` permissions.
- A real public Telegram message was fetched using the Hermes Python environment
  and delivered over signed HTTP. The retry returned `duplicate: true`.
- Raw fragment bytes and all 2,363 extracted body characters matched storage;
  parsed wiki synthesis input contained the complete body. Classification remains
  `community_signal`, not an official or fact-verified report.
- The user's Hermes runs on the server, not the logged-in laptop. Browser job and
  authenticated SaveTicker verification remain pending. The user is considering
  a dedicated account/server browser; never request its password in chat.
- Public-source success is not evidence that social login, SaveTicker collection,
  or subsequent LLM wiki synthesis has recovered.
- Final regression: 568 passed, 1 skipped. Receiver HTTP health and deployed
  dashboard loopback health passed. Live browser/wiki interaction is not claimed.

Subsequent verification read the complete public messages through the actual
Codex provider: message 65448 produced a cited draft and message 65558 was
intentionally skipped. Both outcomes and source links were persisted; original
hashes were unchanged. Provider-level JSON clipping and downstream community
provenance loss were fixed. The current scoped suite passed 627 tests with one
skip. These public probes still do not fulfill the protected SaveTicker gate or
prove an autonomous Hermes collection job is configured.

FRED M2 and high-yield OAS notes were subsequently delivered through the actual
restarted receiver: 1,208 and 6,866 complete notes-body characters and immutable
notes DOM fragments. Both retries deduplicated. A webhook classification bug was
fixed with TDD so notes are distinct from numeric snapshots; the latest targeted
run passed 166 tests. The dashboard and receiver loopback health passed.

The planned M2-plus-existing-draft external model probe was rejected before
execution and is awaiting explicit payload/destination approval. No model result
or completed wiki refresh is claimed for the FRED notes. No login credential was
registered, no authenticated browser was established, and Task 3 remains partial.

## SaveTicker Collector Continuation (2026-10-13)

The collector and CLI are in `reports/saveticker_hermes_collector.py` and
`scripts/saveticker_collect.py`; operations and privacy gates are documented in
`docs/saveticker-hermes-collector.md`. TDD-based fixes cover incomplete preview
rejection, exact DOM integrity, independent article failure, A-B-A identity reuse,
fair retries and recovering after a missing browser connection. Focused tests
passed 99 cases. The user's real-one-article approval does not include external
LLM use; that probe requires isolated storage and a dedicated receiver.
Current readiness is `browser_connection_required`. Stored credentials and an
authenticated browser were not found or inferred from empty configuration.
Task 3's real article and recurring job gates remain open.

Final software verification: full project 4,043 passed / two skipped / 41 warnings
in 808.00 seconds, exit zero; focused 99 and staged-only 97 passed. This does not
close the real login/article or unattended collector gates. No credentials were
printed, no private article was exported, and no source-gap timestamp was faked.
