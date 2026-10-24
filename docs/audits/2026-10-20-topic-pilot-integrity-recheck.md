# Public Topic Pilot Integrity Recheck

Observed at host UTC 2026-10-19 15:10 (KST 2026-10-20 00:10).
This supersedes the earlier quota-only explanation for why the pilot cannot run.

## Evidence

- Current HEAD at inspection: `a119ca1`. Worktree initially had only the unrelated
  untracked `_tmp_stale_list.py`; no prior dirty-work assumptions were reused.
- Default, non-apply `scripts.run_public_topic_pilot` terminates with
  `pilot_source_note_unavailable`. No reservation, model call or wiki write.
- Shared budget reports KST day 2026-10-20, count 0 / limit 20. Quota availability
  does not authorize ignoring failed dependency verification.
- All three previously approved public source revisions pass `_verified_source`:
  retained archive identity, manifest, expiry, public scope and compilation checks.
- Their four candidate source-note records all fail `_note` with
  `context_page_changed`. Local comparison with committed upserts shows changed
  `body` and `links` on all four; one also changes report-citation/schema/update
  metadata. No changed field values, source prose or private titles were emitted.
- Across 74 committed single-source operations, zero distinct current targets
  satisfy `_matches_receipt_page`; 105 distinct targets lack a matching receipt.
  This is a page-fingerprint inventory, not verification of all 105 originals or
  proof that their editorial changes are incorrect. It does not identify a writer.
- Fresh synthetic regression: `tests/test_public_topic_pilot.py`,
  `tests/test_wiki_topic_public_targets.py`, and
  `tests/test_wiki_publication_legacy_links.py`: 66 passed, exit 0, 2.18 seconds.
  JUnit: `/tmp/wiki-current-pilot-preflight.xml`.
- Luna read-only code review confirms that page knowledge-field changes must
  remain excluded; current navigation-link tolerance does not exempt body edits.

## Boundary And Next Decision

Do not restore over current edits, weaken fingerprints, hand-edit receipts,
create dummy pages or treat archive validity as current-page validity. The
original goal remains incomplete: actual multi-source model compounding has not
been demonstrated by this pilot.

A proposed recovery direction preserves current editorial wiki versions while
separately deriving immutable, receipt-backed source-note snapshots from verified
public originals and compilations. Topic edits would depend on those snapshots,
not on an ever-changing display page. The user has been asked whether to write
the detailed recovery design; no implementation or live reconciliation is
authorized by this proposal. The existing three-source, two-attempt pilot limit
and privacy restrictions remain unchanged.
