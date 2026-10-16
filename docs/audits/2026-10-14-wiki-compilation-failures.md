# Compilation Failure Diagnostics

## Evidence And Scope

The read-only operational audit found six terminal automatic attempts with compile failures, but no diagnostic reason beyond `compile_failed`. Existing records cannot establish whether the causes were empty model output, invalid JSON, citation validation, or claim validation. This repair does not retroactively assign causes.

Synthetic TDD reproduced missing diagnostic counters for absent output and invalid JSON. The pipeline now stores only explicitly allowlisted compiler validation codes in source receipts and aggregate reason counts. Arbitrary provider exception text is never serialized. A dictionary-valued exception regression reproduced a classifier crash, then verified the defensive string-type check.

## Invariants

- Compilation failure still increments `compile_failed` and leaves the source retryable.
- No failed knowledge is committed and no schema validation is relaxed.
- No original, prompt, model response, or arbitrary exception message is added to diagnostics.
- Daily public-source reservations are unchanged; no quota reset or extra model call occurred.
- Existing six failures retain their historical generic code until an approved future attempt provides new evidence.

## Verification

Initial failure-diagnostic tests: three RED, then three GREEN. Non-string exception regression: RED TypeError, then guarded implementation. Related compiler/chunk/journal/public-rollout suite: 90 passed. A final pipeline run including failed-then-successful retry: 25 passed; this proves error clearing, successful commit, and fresh counters on recovery. Sol scoped read-only review found no implementation defect and noted additional test gaps. Full repository and live model recovery are not claimed by these scoped checks.
