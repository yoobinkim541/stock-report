# Citation Exposure And Fileback

## Reproduced Defects

Two RED tests showed wiki citations inherited every reference of a cited page, including references beyond the four exposed in the context and artifact-only references never displayed. Two further RED tests showed event citations inherited alternate/artifact URLs and sources beyond the twelve-event citation context. A separate RED test demonstrated that out-of-range event IDs were still allowed in the answer contract.

## Repair

Wiki context display and fileback now share the same normalized four-reference selector. Event fileback uses only the displayed primary URL (or the displayed source_url fallback), among the first twelve events with a title/summary. The answer allowed-ID set uses the same event range and content-presence condition. Original records/references are retained; exclusion applies to attribution of the current answer, not deletion of stored history.

Successful provenance fixtures now include actual displayable headlines. Their long-reference preservation, existing-evidence retention and real postprocess assertions remain unchanged. Titleless events are separately asserted to have no allowed citation ID. Combined provenance/source-acceptance/agent suite: 224 passed, 12.04 seconds; diff whitespace check passed.

Corrected evidence usage/trace/cards/provenance suite: 35 passed, 1.21 seconds. An earlier command named a nonexistent evidence_context test file and ran no tests; that command supplies no verification evidence.

## Limitations

This establishes exposure alignment, not claim entailment or source truth. A valid cited page ID is not proof that every assertion in an answer follows from its excerpt. The Sol reviewer failed before inspection because of account usage limits; no independent sign-off is claimed. The previous full repository pass predates this repair. No production data, consent policy or daily source budget was changed.
