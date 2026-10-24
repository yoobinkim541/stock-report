# Publication Link Validation

## Reproduced Defects

The publication wikilink regex stopped at a single closing bracket. A unique
existing title such as `Evidence [0001]` therefore failed exact-title export.
Three synthetic round-trip cases failed before the fix; unknown and duplicate
targets remained blocked. The new matcher prioritizes balanced bracket
components, with the legacy fallback retained so malformed open brackets do not
silently evade missing-link validation. A separate malformed case first exposed
that fallback requirement.

The standard Markdown-link regex also missed reference-style, collapsed,
shortcut, and reference-image destinations. Four synthetic missing-target
cases passed validation incorrectly before the fix. Destination extraction now
uses the already pinned `markdown-it-py` dependency and inspects parsed link
`href` and image `src` attributes. Existing relative-path and source-note query
checks remain in place; code examples remain excluded.

## Evidence

- Bracket fix and adjacent regression scope: 112 passed in 12.43 seconds,
  `/tmp/wiki-bracket-links-final-20261022.xml`.
- Independent bracket/ancestry review approved: 64 focused cases plus 18
  supplemental checks, including malformed-input performance. No actionable
  findings.
- Structured Markdown extraction scope: 149 passed in 11.72 seconds,
  `/tmp/wiki-markdown-parser-green-20261022.xml`. Independent review pending.
- A read-only, same-input comparison produced the following results from
  snapshot `2957b4bf0252c13026363adec694a47255b2660df888ae24870cc6ec3b9eb914`:
  old extraction: eight relationship errors and 15 Markdown-link errors;
  new extraction: eight relationship errors only. No stored records, raw
  sources, or operational export files were changed by this comparison.

## Separate Remaining Gates

The eight child-parent mismatches are existing stored relationship errors,
not parser false positives. They remain blocking; they were not hidden by
relaxing validation or rewriting live records.

Task 7 independently revealed a separate frozen query-link issue: a syntactically
valid unrelated generation could pass export validation and fail when clicked;
a generationless link could follow the current alias. The implementer is
repairing that binding with dedicated RED/GREEN click tests. Task 7 is not
approved until its scoped re-review passes. Pilot migration and the complete
end-to-end/full-repository gates remain outstanding.
