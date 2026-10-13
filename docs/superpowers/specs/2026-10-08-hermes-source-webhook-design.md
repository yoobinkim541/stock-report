# Hermes Source Collection Webhook

## Intent

The user wants Hermes to own source collection and deliver source bodies through
a webhook, rather than repeatedly fetching login-protected pages from the stock
server. Keep the current raw/archive, evidence classification and LLM wiki
pipeline. Do not create another chatbot or copy browser credentials.

## Selected Flow

1. A dedicated Hermes collection job reads authorized source pages using its
   browser integration. Social login, MFA and CAPTCHA remain user interactions.
   Do not evade provider access restrictions or use stealth browser mechanisms.
2. Hermes produces a dedicated `stock-report.source-batch.v1` document with a
   delivery ID, collection timestamp, source URL, title, publication timestamp,
   complete extracted body, extraction scope and original content when available.
   It never sends cookies, tokens, login forms, conversation history or unrelated
   tool results. A summary is not original source text.
3. A small stock-report webhook receiver accepts only signed source batches.
   It uses a dedicated secret, timestamp replay protection, request-size and
   record-count limits, constant-time signature checking and durable idempotency.
   Default binding is loopback; cross-host access requires private networking or
   HTTPS. The receiver never fetches arbitrary submitted URLs.
4. Validate the whole batch before writing. Reject challenge/login pages, missing
   bodies, unsupported source URLs, malformed timestamps and truncated full-text
   claims. Save immutable source artifacts and extracted text, then use the
   existing source event append/classification APIs. Persist a receipt so retries
   do not create duplicate events or falsely claim wiki completion.
5. The existing wiki worker reads stored bodies, creates cited persistent drafts,
   and updates related pages/index/history. Webhook acceptance means source
   ingestion succeeded, not that LLM synthesis or review succeeded.
6. Authentication expiry is `reauth_required`, not a successful empty scrape.
   Transport blocks, parser failures and wiki failures have separate health
   states. Public-source collection remains available during migration; disable
   duplicate collectors only after a real Hermes delivery succeeds.

## Existing Hermes Reuse

Use existing job/route APIs and browser providers. Do not subscribe to all
`post_tool_call` events or transmit general tool payloads. A collection-only
sender signs the narrow data envelope; lifecycle callbacks alone are not a
source-body protocol. Confirm the configured browser is actually connected to
the user's logged-in laptop before claiming recovery.

## Acceptance Gates

- Tests fail first for unauthorized/replayed/oversized/invalid deliveries.
- A complete source body, including its last paragraph, survives raw storage
  and reaches the existing wiki synthesis prompt.
- Exact duplicate delivery retries and failed partial deliveries are recoverable.
- Original source bytes and prior wiki knowledge remain unchanged by maintenance.
- Signed HTTP delivery from the real Hermes runtime succeeds and its source is
  visible to the wiki worker. A login-protected SaveTicker article body must be
  read through an authorized browser before that source is marked recovered.

## Trade-Offs And Open Dependency

The server can maintain the receiver, but a laptop browser session cannot be
assumed available to a server-hosted Hermes runtime. Initial login/browser
connection needs user participation. Sessions expire and sources may explicitly
deny automation; those states stay visible instead of becoming fabricated news.
