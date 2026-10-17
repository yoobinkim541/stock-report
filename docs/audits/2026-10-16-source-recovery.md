# Source recovery — 2026-10-16

## Implemented on Hermes

- Added `reports/saveticker_browser.py`: loopback-only CDP, existing authenticated SaveTicker tabs, same-origin allowlisted JSON requests, explicit challenge/session failures. No cookies or credentials are exported. Requests and navigation share a cross-process browser lock.
- News and economic-calendar HTTP 403/451 now attempt genuine authenticated browser fetches. Scheduled news API previews remain explicitly `preview_only`; they are not mislabeled as full bodies and do not trigger further blocked article HTTP requests.
- Private full-body collection uses direct CDP instead of the Hermes tool lifecycle that emitted Python-shutdown atexit warnings. API ID discovery replaces clipboard/modal row discovery in this runtime; discovery prefers unseen IDs and scans up to five pages, bounded to 20 articles maximum.
- Recovery service repository definition increases its batch from one to ten articles and now enforces `MemoryMax=2G`, `MemorySwapMax=0`. The deployed user service was updated only after the focused tests and bounded live checks below passed. Existing collection/recovery timer schedules were not changed.
- Partial deliveries now report `degraded` and preserve a last-success timestamp; subsequent error-free delivery clears the error phase.
- Immutable HTML/text/manifests and checksums retain the existing private archive contract. This recovery does not change private-source knowledge authorization settings.

## TDD and live observations

1. `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_saveticker_browser_recovery.py`: **6 failed before implementation**, including missing browser recovery, sticky error and old runtime.
2. Deployment regression changed to require ten articles; **1 failed before implementation** (`--limit 1` versus `--limit 10`).
3. Focused suite after implementation: **152 passed, 3 failed** in 11.44s. All three failures were `FileNotFoundError: node` because this noninteractive SSH shell omitted `~/.local/bin` from PATH. The corrected-PATH rerun could not finish after the server became unreachable.
4. Structural-only authenticated browser probe at about 13:54 UTC: news API HTTP **200**, **30** rows; calendar API HTTP **200**, **51** events. Only object keys, counts, identifiers and content lengths were emitted, never article bodies.
5. Real `collect_private(limit=10)` at **13:59:20–13:59:30 UTC**: **10 delivered, 0 unchanged, errors=[]**, all ten signed webhook POSTs returned **200**. No emergency-cleanup/atexit warning occurred in this process.

## Interrupted verification — not acceptance evidence

At approximately 14:00 UTC Hermes became unreachable. The scheduled-provider recovery command and corrected-PATH test run lost their SSH transport and hit their 120s/180s command deadlines. Subsequent explicit SSH connection attempts timed out on port 22; a Tailscale ping to 100.93.71.84 also received no reply. Therefore fresh central `source_health`, C14 unhealthy=0, production unit activation and the requested bounded Luna review have **not** been verified. No health record has been edited or fabricated.

The initially staged transport regression exposed the remaining body-readiness issue: CDP `read()` recognized only whitespace-pre-wrap/data-article-body readiness despite the extractor supporting `.prose`, and returned the first captured DOM after a delay rather than recapturing a stable body. That defect is now resolved as recorded below.

## Resumed verification and deployment

- `CDPBrowser.read()` now validates each capture through the existing article extractor, retries only incomplete/unsupported-in-progress bodies within its existing deadline, and requires two consecutive matching DOM digests before returning the newest capture. This reuses `.prose`, `.article-content`, `data-article-body`, `data-testid="article-body"`, and existing direct/modal support instead of maintaining a second readiness-selector list. Access/challenge checks and original-digest checks still fail closed.
- TDD red: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_saveticker_browser_transport.py` returned **1 failed, 5 passed**, specifically the `.prose` loading→complete→stable case (`StopIteration` because no supported body was accepted).
- Green with transport/layout/digest regressions added: the nine-file focused recovery/collector suite returned **179 passed in 7.18s** with `PATH=$HOME/.hermes/node/bin:$PATH`.
- Final focused suite, including deployment memory assertions and economic-calendar tests: `PATH=$HOME/.hermes/node/bin:$PATH PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_saveticker_browser_transport.py tests/test_saveticker_browser_recovery.py tests/test_saveticker_recovery_deployment.py tests/test_saveticker_hermes_collector.py tests/test_saveticker_recovery.py tests/test_saveticker_runtime.py tests/test_saveticker_forwarding.py tests/test_saveticker_auth.py tests/test_source_collector.py tests/test_intrinsic_econ.py` → **188 passed in 7.09s**. No full suite was run.
- Real news check at **2026-10-16 14:59:34 UTC**: isolated `reports.source_pipeline --source saveticker --max-workers 1`, with `STOCK_COLLECTOR_SAVETICKER_MAX_PAGES=1`, under a transient user unit with **MemoryMax=2G, MemorySwapMax=0, RuntimeMaxSec=90**. Result: **44 fetched, 0 persisted, available**, provider duration **1235ms**, unit runtime **4.415s**, exit **0**.
- Real calendar check at **2026-10-16 14:59:44 UTC**: isolated `reports.source_pipeline --source economic_calendar --max-workers 1` under the same memory/deadline controls. Result: **51 fetched, 0 persisted, available**, provider duration **1224ms**, unit runtime **4.507s**, exit **0**.
- Central source health was updated by those real provider runs, not manually edited: news **44 deduped**, calendar **51 deduped**, both **0 collisions**, `fetch_success=true`, `persist_success=true`, `zero_persist_streak=0`, and no blocked availability marker. Zero newly persisted rows were existing-cache deduplication, not collection failure.
- Real authenticated full-body check at **15:00:06–15:00:18 UTC**: `collect_private(limit=10)` under **MemoryMax=2G, MemorySwapMax=0, RuntimeMaxSec=180** returned **10 delivered, 0 unchanged, no errors**; ten signed local webhook requests returned HTTP **200**. Unit runtime **14.746s**, exit **0**. Only status/counts/error-reasons and HTTP access lines were emitted; no bodies or secrets.
- After all of those checks, installed `deploy/stock-report-saveticker-recovery.service` into the existing user systemd path and ran `systemctl --user daemon-reload`. Effective `ExecStart` is `scripts/saveticker_recovery_tick.py --limit 10`, `MemoryMax=2147483648`, `MemorySwapMax=0`, timeout **4 minutes**. No collector timer restart or wiki pipeline activation was performed. Public wiki timer remained **inactive**.
- No 60-day event load, commits, new authorization, credential export, or CAPTCHA automation was performed. These results establish collection health and immutable private delivery, not knowledge-compounding acceptance or wiki activation.
