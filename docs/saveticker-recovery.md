# SaveTicker Authentication Recovery

## Owner Flow

1. A monitored source read detects a login button, login page, or security check.
2. StockBot sends a private Tailscale remote-screen link and an incident-bound
   confirmation button. Normal retries do not attempt passwords or solve checks.
3. The owner opens the screen and completes security confirmation/MFA personally.
4. The owner presses **보안 확인 완료 · 복구 확인**, or sends `/save check`.
5. The worker consumes that confirmation durably, attempts ordinary saved-email
   login once if needed, and verifies a readable source through signed private
   raw/text ingestion. Only a successful ingest/no-change proof marks recovery.
6. StockBot reports recovery or failure. Failed result deliveries are retried
   independently of browser work, including after worker interruption.

`/save status` shows state. `/save open` requests a fresh remote-screen window;
opening a screen is not consent to submit a password. Old incident buttons and
guest/non-owner callbacks cannot authorize recovery. For owner group chats,
configure `STOCK_BOT_OWNER_USER_ID` explicitly to bind callbacks to the owner user.

## Cloudflare Access Denial

- `Attention Required! | Cloudflare` and `#cf-error-details` indicate a blocked
  page even when there is no CAPTCHA or `cf-chl-*` challenge element. CDP API
  reads, DOM capture, listing discovery and saved-email login detect this state
  before fetching, parsing or entering credentials.
- If the page says “Sorry, you have been blocked” without a verification
  control, this is not an expired login. Send the page URL, occurrence time and
  Cloudflare Ray ID to the site operator for review. Do not repeatedly reload,
  rotate IPs or clear the authenticated profile to evade the denial.
- The recovery state stays `awaiting_user`; timer ticks do not retry login or
  collection. Use `/save check` only after access is restored, not merely after
  opening the remote screen. Recovery still requires a real ingestion receipt.
- Regression verification: 110 related collector/auth/browser/recovery tests
  passed. A live smoke on the blocked server browser exercised all four probes,
  returned `blocked / delivered=0`, and kept recovery waiting. It used only
  `Runtime.evaluate`, with no navigation or SaveTicker network requests.
- This guard fix does not remove Cloudflare's block. Operator clearance remains
  a prerequisite for verifying successful live article collection.


## Bounds And Privacy

- Remote services bind only loopback (VNC/CDP) or Tailscale IPv4 (noVNC). No public
  tunnel, wildcard IPv6 listener, cloned browser profile or challenge solver.
- Each incident has an absolute two-hour remote-access deadline, persisted before
  provisioning. Delayed provisioning receives only the remaining TTL. Reminder
  messages never renew expired access. Renewal requires an explicit owner action.
- Reminders have a one-hour cooldown. Failed sends retry; Telegram delivery is
  at-least-once across a crash, not a transactionally exactly-once guarantee.
- Login credentials remain in the local Hermes vault and browser memory. They
  are never included in state, notifications, source records or console output.
- Source artifacts go to `~/reports/private-sources/{raw,text,cache,receipts}`.
  No external LLM or production wiki queue is used by this recovery worker.
- The deployed timer checks requests every minute and performs a normal probe
  every 15 minutes. It reads only the already authorized `news_235492` article.
  This establishes login/source-ingest readiness, not a fresh bulk-news feed or
  completed wiki compilation. Broad collection/model activation remains separate.
- The helper reuses the existing `hermes-source-display` and
  `hermes-source-browser` services. If those are absent it reports unavailable;
  it does not terminate user browsers or silently create a replacement profile.

## Deployment

Install the collector requirements into the project environment, then install
the unit files in `deploy/stock-report-saveticker-recovery.{service,timer}` into
the user's systemd directory and enable the timer. The worker loads `.env` and
ignored `.env.hermes-source` locally; never place their contents in unit files.
The existing StockBot poller owns Telegram updates; the worker only sends messages,
so no second `getUpdates` process is created.

```bash
.venv/bin/python scripts/saveticker_recovery_tick.py --limit 1 --url https://saveticker.com/news/news_235492
systemctl --user status stock-report-saveticker-recovery.timer
```

## Verification

TDD covers expired authentication, notification deduplication/retry, stale and
unauthorized callbacks, failed/empty/partial ingestion, interrupted confirmation,
absolute remote expiry, remaining TTL, screen-open versus login consent, and
durable success/failure notifications. Runtime source storage is isolated and
the signed loopback receiver is exercised by tests.

The live test simulates expiration without logging the owner out, then uses the
actual authenticated Chromium session and signed local receiver for the approved
article. It does not simulate or solve Cloudflare confirmation. Short-lived
Hermes browser-tool processes currently emit an upstream late-atexit cleanup
warning; it is not itself a failed ingest receipt.

### Server Receipt (2026-10-13)

- Final related regression: 278 passed in 9.75 seconds; no full-project claim.
- Live interrupted-session simulation plus real authenticated Chromium and signed
  private HTTP ingestion passed. Receipt:
  `~/reports/private-source-probes/20261013T042805-recovery/verification.json`.
- Installed/enabled the user recovery timer; first real approved-article probe
  returned `healthy / delivered`, service result success and exit status zero.
- Current StockBot process started after the callback-handler edits. A clearly
  labeled test message with a private remote-screen button was sent successfully.
- No actual logout or CAPTCHA solving was performed. New production-wiki/model
  activation and unrestricted bulk collection were not performed.
