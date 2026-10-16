# Wiki Full-Tab Evidence

## Scope

The real wiki storage API was seeded with synthetic parent/child documents in
temporary directories. Streamlit AppTest rendered the complete wiki tab, verified
stored claim and limitation text, clicked its body reference button and verified
the selected child and its body. No model, production store or network was used.
This is widget-layer integration evidence, not a production-browser screenshot.

## Additional Defect

The documented `WikiPage` input path could not accept structured knowledge,
although dictionary inputs retained it. A regression test first failed with
`TypeError` for the missing constructor field. The dataclass now accepts the
field and exports through the same allowlisted deep-copy projection used for
dictionary pages. The regression also checks that exported edits do not mutate
the original claim and that private root metadata is excluded.

## Verification

Full-tab storage, render, navigation, structured-view and browser model tests:
36 passed in 4.07 seconds. `git diff --check` passed.

Source atomic integration, pipeline, structured context, chat provenance,
acceptance and agent regressions: 273 passed in 35.38 seconds. This is not a new
full-repository run. Read-only recovery-state inspection still reports
`phase=awaiting_user`; no human confirmation was manufactured or consumed.

No deployment or authenticated collection recovery is inferred from these tests.
The human browser check is still pending unless confirmed by actual recovery
and collection results. Full goal completion remains unproven.
