# Wiki Regression And Model Check

## Broad Regression

Command: `.venv/bin/python -m pytest tests/test_wiki*.py tests/test_agent_console.py tests/test_llm_wiki_acceptance.py tests/test_public_wiki*.py -q --tb=short`.

First run: 833 passed, one failed, one skipped, 92.79 seconds. The failed source acceptance case required a prose-only 1,600-character excerpt even when structured counterevidence existed. It now tests the shared prose/knowledge budget, body presence, demand caveat, draft state, partial-review label, unread-tail exclusion and unchanged stored tail. Fileback preservation and repeat-ingest checks remain in the same test.

Final run: 834 passed, one skipped, 94.45 seconds. Skip: test_wiki_health_queue.py:156, legacy-selector-only coverage is inapplicable to the current selector. This is the complete selected wiki suite, not the entire repository test suite.

## Actual Engine Evaluation

One authorized Codex call used synthetic material only, through the existing adapter with search disabled, a 90-second timeout and structured output. The prompt prohibited tools, file access and memory. No operational source, private article, personal dialogue or production wiki was supplied; no production knowledge was filed.

The 1,600-character excerpt contained deliberately misleading prose alongside scoped draft claims and the limitation that no realized-order observations were supplied. The actual JSON response returned capacity_implies_orders=false, knowledge_state=draft, and a nonempty limitation. The command exited zero. This proves this single evaluated response respected the supplied caveat and state; it does not establish general model quality, external factual validity, citations or prompt-injection robustness.

## Full Repository Recheck

Fresh `.venv/bin/python -m pytest -q --tb=short` completed without code changes during execution: 4,259 passed, two skipped, 41 warnings in 849.40 seconds, exit 0. Skips are the opt-in real canvas performance test and legacy wiki-selector-only coverage. Warnings include existing scientific-library feature-name/deprecation warnings and plotting layout warnings; this is not warning-free validation. The selected wiki suite and whole repository suite now both have passing final results. Operational gates below remain open despite this result.

Read-only strict store validation separately parsed all 4,442 shared-memory records with no malformed JSON, duplicate keys/IDs or non-finite JSON values. This does not prove semantic correctness or explain the earlier publication digest difference.

### Remaining Gates

SaveTicker recovery still probes one fixed URL, not ongoing latest-list ingestion. A user question about replacing that with bounded latest-list collection is pending. Original model-consent and public-source daily limits remain unchanged. Prior publication full-store digest drift, actual production browser validation, broader semantic synthesis and full backlog processing are not complete. Read-only code review also identified conflict-role integrations without explicit disposition, page-ID-only answer citation validation, and non-atomic multi-file publication as concrete follow-up gaps; no code was changed while this full suite was running.
