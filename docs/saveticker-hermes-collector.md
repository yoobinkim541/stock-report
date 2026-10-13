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
