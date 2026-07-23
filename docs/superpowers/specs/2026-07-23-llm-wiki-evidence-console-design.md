# LLM Wiki Evidence Console Design

**Goal:** Turn the existing AI Wiki UI into an evidence console that shows whether the LLM wiki is trustworthy, how each page is backed, and what will be injected into prompts.

## Scope

This design enhances the existing Streamlit AI Wiki tab and graph. It does not introduce Obsidian, a new database, or human approval gates.

## Design

1. Wiki Health Bar
- Show qmd provider status, qmd markdown file count, fallback state, source-backed count, unverified count, lint issue count, and open question count.
- Data comes from existing `agent_console.wiki.search_health()`, `wiki.lint_pages()`, and the currently loaded pages.

2. Trust-Aware Browser Model
- `dashboard/wiki_browser.py` should compute a compact health model from visible/all pages.
- Each normalized page should expose `verification_status` and `trust_warnings` when present.
- Query matching should support simple aliases so related pages like leverage/risk/loss are not hidden unexpectedly.

3. Evidence-First Selected Page
- The selected page panel should show sections in this order: judgment, evidence, verification, open questions, prompt injection preview.
- Source refs and warnings must be prominent before long body text.

4. Trust-Aware Graph
- Graph nodes should carry `verification_status`, `trust_warnings`, and `lint_issue_count`.
- Node color should prioritize lint issues, then verification status, then archived status.
- Edges that share source refs should be visually stronger than tag-only edges.

5. Editor Guardrail
- The editor should tell the user when `reviewed/stable` will be downgraded because source refs are conversation-only or missing.

## Non-Goals

- No Obsidian integration.
- No manual human review workflow.
- No canonical store migration.
- No new external dependency.

## Success Criteria

- Tests cover health model, selected page evidence model, editor guardrail, graph trust colors, and alias query behavior.
- Existing AI console wiki tab still renders.
- qmd unavailable remains a normal fallback state, not an error.
