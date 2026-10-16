# Real Wiki Candidate Preflight

Early read-only preflight during approved implementation, not production
publication or final acceptance. Captured canonical wiki records under the
existing store lock; rendered and validated in memory only. No generated notes,
index, pointer, model calls, source changes or cleanup writes.

Snapshot: 4,026 records; 4,031 files; 49,191,328 rendered bytes. Candidate is
blocked: 78 `missing_markdown_link` diagnostics across 20 affected pages. A
separate captured classification found four diagnostics naming unique existing
titles and 74 unresolved targets. Counts are diagnostics, not necessarily 78
distinct broken links. No title or private content is included in this report.

The accepted synthetic snapshot module correctly refuses to silently omit these
records. Future activation requires resolving links with identity/provenance
preserved or an explicitly reviewed migration decision. This preflight does not
authorize blind title replacement, deletion, fabricated topic creation or a
relaxed validator. It also does not prove final deployment readiness.

Later read-only classification after the shared split-ID repair again captured
4,026 records and 78 diagnostics, now counted as 39 distinct targets. Four
diagnostics name unique existing titles; 74 are unresolved wiki names. No
absolute/relative external raw path was misclassified in this snapshot. Thus
the blocker cannot be dismissed as an external-source-path validator error.
No source text/titles or target names are emitted here; no cleanup was applied.

An additional local-only check used the already installed MarkdownIt parser to
separate fenced/block/inline code from prose in each offending source/rendered
note. All 78 diagnostics were prose-or-mixed, zero code-only. This rules out
literal code examples as the complete explanation for this snapshot; it does
not infer missing-page content or authorize merging. No package was installed,
source content emitted, model invoked or production artifact written.

## Triage And Existing UI Contract

Later read-only profiling: 20 affected pages comprise 19 drafts and one archived
page. None belongs to the currently receipt-verified public-source target set;
this does not prove they are all private, but does not authorize feeding their
contents to the restricted public compiler either. Two distinct targets match
unique existing titles, with zero cross-surface diagnostics; 37 remain unresolved.

All stored unique-title wikilinks were counted locally: two unlabelled title
references, no labelled title references. `dashboard/wiki_browser.py`'s existing
`transform_wiki_body` supports precisely this unique-title fallback, with ID
precedence; `tests/test_wiki_body_navigation.py` pins that contract. Therefore
the two references are supported UI navigation, not evidence of missing pages.
Generation export's ID-only filenames/validation lack that compatibility.

A bounded design was presented: preserve canonical body/snapshot and originals;
normalize only unambiguous legacy-title references in generated Markdown to the
stable target ID, preserving displayed labels. Duplicate/unknown references and
code literals must not be silently rewritten. No blank pages, deletion, source
model calls or operational activation. The user approved this bounded design;
implementation and focused RED/GREEN verification are now in progress. The
earlier full-suite result predates this correction and is not new-code proof.

Fresh pre-change check after approval reproduced the baseline: 4,026 records,
4,031 files, 78 missing-link diagnostics, 39 distinct targets and 20 affected
pages. `snapshot.json` exactly preserved the captured snapshot. The check used
the existing store lock and in-memory render/validation only; it did not publish
files, change source documents or invoke a model.

Post-change in-memory live check: 4,026 records / 4,031 files, 74 missing-link
diagnostics over 37 distinct targets and 19 pages; snapshot preserved exactly.
Only the four diagnostics for the two valid title references disappeared. The
candidate remains blocked, not published. Independent Luna review then found
unclosed/unequal-length Markdown-fence edge cases; follow-up TDD is in progress
before treating this bounded compatibility correction as verified.

The follow-up now protects unclosed fences through EOF and requires matching
markers with a sufficient closing length. Separate publication-file resolution
preserves existing `target.md`, `notes/target.md` and `notes/target` forms after
ID/title resolution; it does not create title aliases for labelled references.
Fence regressions first failed eight tests; extensionless path regressions first
failed two tests. All canonical JSON/snapshot checks and unknown/duplicate target
guards remain. Final independent review and refreshed integration are recorded
in the acceptance audit; no production activation is implied.

## Remaining-Target Safety Recheck

A later snapshot again has 74 diagnostics / 37 distinct targets / 19 affected
pages. All affected pages are drafts: eight concepts, four playbooks, six risks,
one source digest. Every affected page has source references; none has structured
`source_knowledge`. The existing conservative cleanup planner returns zero
actions globally and zero affected candidates. No canonical body exceeds 12,000
characters. It would be incorrect to delete these pages as empty junk or create
blank target pages merely to pass generation validation. Their source references
alone do not establish public-processing eligibility or support a guessed merge.
Further content/provenance review is required; this check emitted counts only.
