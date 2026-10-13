# Hermes Source Webhook Operations

## Receiver

The server accepts only signed source batches at
`http://127.0.0.1:8777/webhooks/hermes-sources`. It is not publicly exposed.
Install the additional WSGI dependency into the existing project environment:

```bash
.venv/bin/python -m pip install -r deploy/requirements-hermes-webhook.txt
```

Keep `HERMES_SOURCE_WEBHOOK_SECRET` in `.env.hermes-source` with mode `0600`.
Use a dedicated random secret, not an API key or login token. The file is ignored
by Git. Both the receiver and sender load it without printing its contents.
Install `deploy/stock-report-hermes-webhook.service` in the user systemd directory,
then enable the service. The unit assumes `~/projects/stock-report`.

## Collector

Use `config/hermes-source-collection.md` as the Hermes collection instruction.
Do not forward generic tool events or browser storage. The allowed source scopes
are article bodies for SaveTicker/Arca, exact public messages for Telegram and
series notes for FRED. PDFs require the existing report download/extraction path;
this JSON endpoint does not accept binary PDFs or fabricated OCR text.

The logged-in browser must be attached to the Hermes process doing collection.
Server Hermes cannot automatically see a laptop browser. For this deployment,
Hermes runs on the server: use a dedicated supervised server browser and a
collection-only account where the site's supported login options permit one.
Alternatively, run Hermes on the laptop with its browser connected and send over
an authenticated SSH tunnel. Keep browser debug ports private. Login/MFA/challenges
are user actions; do not copy cookies or bypass them. Do not enable an unattended
browser job until an actual authorized article has passed the end-to-end test.

The installed Hermes supports masked credential entry with `hermes vault add`.
Run it interactively on the collection host, never paste a password into chat,
command arguments, source payloads or Git files. Its local vault encrypts saved
credentials and fills only the saved exact origin through its supervised browser.
Saving a login does not establish an authenticated session or complete a social
login approval. Complete initial login manually and stop for reauthentication
when the session expires; never archive an authentication page as an article.

### First Server Login

1. Prefer a collection-only account if the provider supports it. Do not reuse a
   personal Kakao account merely to avoid creating an account.
2. From an interactive terminal on the collection server, use
   `hermes vault add --kind login` for masked entry when password storage is
   needed. Bind the login to the actual authentication page origin, not a guessed
   SaveTicker origin. Never send the password to the model or this project's API.
3. Use a dedicated visible browser profile and attach it from the interactive
   Hermes CLI with `/browser connect`. The server needs a private graphical
   session for human interaction; a terminal or a laptop login alone is not enough.
   Keep remote desktop and CDP accessible only over authenticated private access.
   Do not publish browser debug ports through the dashboard's public tunnel.
4. Complete social consent, MFA and challenges personally. Then verify an actual
   protected article in the same browser before testing source delivery.
5. Confirm raw hash, complete extracted body, a source-backed wiki draft or an
   explicit skip, and retry deduplication. Only then enable unattended collection.

Chromium, Xvfb and the Hermes vault command are available on the server. This is
readiness information, not evidence of a login: no credential was registered and
no authenticated browser session was established by these checks. Encryption
does not remove the need to protect the server account and its vault files.

## Delivery And Verification

```bash
.venv/bin/python scripts/hermes_source_send.py /absolute/path/source-batch.json
```

Retry the exact file and delivery ID after a transport failure. A reused ID with
different content returns `409`. Redirects are rejected. Successful responses
include `wiki_status: pending`, meaning only raw/text/event ingestion succeeded.

Original raw artifacts and complete extracted text use the existing report
archive. A full-body revision can supersede a previously collected preview in
recent-event reads without deleting historical rows. Durable delivery receipts
are in `~/reports/hermes-source-receipts/` (or the configured reports root).
Inspect `source-cache/source_health.json` for the separate `hermes_webhook` entry;
it never clears an HTTP block on the canonical public collector.

Hermes defaults to `on_demand`: a running receiver is not evidence of an enabled
automatic browser collector. Its last delivery remains visible in pipeline health
but does not trigger periodic collection-gap alerts or count as healthy scheduled
collection. Existing receipts without a mode use this same passive default.
After an authorized article passes verification and a recurring sender is actually
enabled, set `HERMES_SOURCE_COLLECTION_MODE=scheduled` in `.env.hermes-source` and
restart the receiver. Its next delivery records the scheduled mode for health
checks; a delivery gap over 24 hours then alerts again. If scheduling is stopped,
set `on_demand` in the health-check environment as well until the next delivery
records that mode. Never refresh `last_success` simply to clear an alert.
Invalid modes fail closed to scheduled monitoring.

The existing source-wiki curator runs at minutes 12 and 42, and distillation runs
every six hours. Receiver health alone does not prove wiki synthesis or factual
review. Oversized articles remain explicit chunking-required failures in the
wiki stage, not silently truncated successes.
