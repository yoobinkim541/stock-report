# SaveTicker Hermes Collector

This implements the approved browser-to-signed-webhook flow. It collects news
article DOM, not LLM summaries. The existing report PDF cron remains separate.
An implementation/test success is not an authenticated-source recovery receipt.

## Runtime

Use the project's Python environment with the existing Hermes source checkout:

```bash
uv pip install --python .venv/bin/python -r deploy/requirements-saveticker-collector.txt
.venv/bin/python scripts/saveticker_collect.py --check-connection
.venv/bin/python scripts/saveticker_collect.py --limit 10
.venv/bin/python scripts/saveticker_collect.py --url https://saveticker.com/news/ARTICLE_ID --limit 1
```

The optional `HERMES_SOURCE_AGENT_ROOT` selects the Hermes checkout. The default
is `~/.hermes/hermes-agent`. `BROWSER_CDP_URL` or Hermes `browser.cdp_url` must
explicitly attach a private browser that the user has authorized. Public CDP,
URL credentials and Camofox are rejected. The collector does not create a cloud
browser, clone a real profile, export storage/cookies or perform social login.
Complete normal login/MFA/challenges personally in the attached browser.

`--check-connection` does not navigate, write state, collect articles or deliver
anything. Its `browser_configured` result would establish configuration only,
not successful authentication. Missing configuration exits with status 2.
Saving an account password in an environment file is not a logged-in browser.

The dedicated webhook secret is loaded from ignored `.env.hermes-source`.
The existing sender enforces loopback HTTP or remote HTTPS and signed delivery.
Never add passwords or browser storage to article batches or Git files.

## Data And Recovery

- Article URLs are same-origin `/news/...` paths; redirects to another article
  or an authentication page fail explicitly. The default list is the homepage.
- Live click-based listings use a bounded article-card/share-button flow; the
  homepage's same-origin `/news` redirect is accepted only for discovery. Article
  navigation still requires the exact requested URL. `--url` skips discovery.
- The attached page is selected by exact URL and brought to the foreground.
  Clipboard permission is scoped to SaveTicker in that browser context. Discovery
  writes a fresh sentinel before sharing and waits for a new URL; a failed share
  never reuses an old clipboard link. The operation replaces the debug browser's
  clipboard. Ordinary login/security confirmation remains user-managed.
- A dedicated article-body element and an H1 are required. Unknown layouts,
  previews, subscription/login gates and challenges are not complete sources.
  Actual SaveTicker DOM must be checked through an authorized browser before
  adapting selectors; fixture selectors alone do not establish that live layout.
- Browser-side SHA-256 and the captured fragment must match. This detects
  alteration/redaction/clipping in transit; it is not independent fact attestation.
  No source text is shortened to fit limits. Publication dates stay unknown
  unless an explicit timezone-bearing article timestamp is present.
- Each article version has a deterministic delivery ID and an immutable pending
  JSON file. Transport retries preserve its collection time and exact payload.
  Accepted identities prevent A-B-A revisions from reusing an ID with new bytes.
- A round-robin retry cursor prevents permanent failures from starving later
  files. Invalid outbox input is quarantined without being transmitted. Individual
  blocked articles do not discard independently valid articles.
- State is in `~/reports/hermes-source-outbox/saveticker/collector-state.json`.
  Pending files and quarantined failures are subdirectories of that outbox.
  `last_success` changes only after confirmed ingestion. CLI output contains
  counts, safe reasons and article URLs, never bodies or exception secrets.
- Successful delivery has `wiki_status=pending`. It does not assert synthesis,
  source truth or a successful automatic browser schedule.

## Activation Gate

The user allowed one real article verification without sending it to an external
LLM. For that probe, use a dedicated receiver with isolated raw/cache/state roots
and a fresh outbox. Do not put its private article into the live auto-distillation
queue, and do not mistake the existing production receiver's acceptance for this
privacy guarantee. Do not enable a recurring Hermes job until an actual readable
article, exact original hash, full body and signed delivery have been checked.

## Verification Status (2026-10-13)

TDD reproduced missing APIs, preview misclassification, A-B-A payload conflict,
retry starvation, state poisoning after missing CDP and other contracts.
The focused collector/webhook/health/revision run passed 99 tests. A synthetic
article passed the real archive and revision-discovery code with the final
paragraph and exact original HTML bytes retained. No private body was read.

Checked current project/Hermes environments, the active long profile and browser
configuration without printing values. No matching account/CDP setting or common
local browser debug listener was found. Runtime initially exposed an interpreter
dependency mismatch; the project environment now imports the browser API and
returns `browser_connection_required` in the side-effect-free readiness check.
No authenticated article or unattended schedule has been verified/enabled.

The initial live CLI request was rejected by the safety reviewer because it could
export an authenticated body. The user subsequently approved one article to local
storage/webhook only. No workaround or private export was executed before approval.

The staged-only checkout passed 97 tests. Two prior tests for a separate,
uncommitted legacy wiki API were kept in the working tree but not mixed into
this collector commit; the new archive-to-source-revision test verifies the
current integration boundary instead. A staged check also exposed the required
preview-to-full append-only upgrade; that narrow prerequisite is included.
The whole working-tree suite still runs those legacy tests.

An independent Sol review identified preview misclassification, A-B-A delivery
conflicts, retry starvation and failed-browser state poisoning. Reproduced
regressions were fixed before the final bounded re-review, which found no
remaining actionable P1/P2 within that scope. This is not live-source certification.
The actual JavaScript capture executed against a synthetic DOM in Node, matching
its HTML digest and detecting a gate outside the article without exporting page text.

Full-project verification is in progress. This gate remains separate from the
unresolved browser authentication dependency.

### Final Software Receipt

The fresh full run after the final state-recovery repair completed with
**4,043 passed, two skipped and 41 warnings in 808.00 seconds**, exit zero.
Log: `/tmp/saveticker-final-pytest-20261013-v2.log`. Focused 99 and staged-only
97 passed. Both diff checks passed. The earlier run was interrupted for the
state-recovery fix and is not counted as a completed verification.

Implementation and software verification are finished. Actual protected-source
recovery, confirmed live DOM selectors and a recurring authenticated Hermes job
are not finished. Supply the exact saved environment-file location or attach a
logged-in private browser before the authorized one-article live gate can run.

## Authenticated Live Continuation (2026-10-13)

The preceding missing-browser notes are historical. Chromium 149 now runs with a
dedicated owner-only profile; CDP listens on loopback port 9225. After the user
completed Cloudflare confirmation, the stored SaveTicker email login succeeded.
No challenge was solved automatically and no credential value was logged.

The actual site supports a direct `article.gap-6.p-6` page with an H2 and a body
of `p.whitespace-pre-wrap` siblings, plus the click-list modal variant. Extraction
requires one unambiguous body parent, excludes the separate AI-summary/footer
from body text, and retains the exact source DOM fragment as raw. No generic H2
or whole-modal fallback is accepted. Comments are outside the archived fragment.
Publication time stays unknown if the scoped DOM lacks a timezone-bearing time.

The authorized article `/news/news_235492` yielded 401 body characters through
the real collector CLI and Hermes browser adapter. A dedicated loopback receiver
confirmed signed delivery, duplicate delivery and a subsequent collector run
returning `no_change`. Raw HTML and text matched exactly. Artifacts are under
`~/reports/private-source-probes/`, not the production wiki queue. No external
model calls were made. Live Hermes discovery subsequently resolved one article
URL; that is not a receipt for bulk body ingestion or recurring execution.

New TDD covers live-layout extraction, summary/footer separation, ambiguous and
partial body rejection, explicit URL validation, bounded click discovery,
clipboard write delays/failure, exact-page selection and Chromium's default
context permission handling. The scoped collector/webhook/source/revision/health
suite passed 185 tests. The full-project 4,043-test receipt above predates these
changes and is not a fresh full-suite claim for this continuation.

No recurring private-source job or production LLM/wiki delivery is enabled by
these one-article probes. Expanding ingestion and sending login-protected bodies
to an external model are separate activation decisions. Hermes currently emits
an upstream shutdown cleanup warning after short-lived browser tool processes;
the collector/receipt assertions passed independently of that warning.
