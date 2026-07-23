# qmd Wiki Search Design

## Goal

Use `tobi/qmd` as the optional local search engine for the LLM wiki while keeping the existing shared-memory wiki store as the source of truth.

## Architecture

The existing `agent_console/wiki.py` remains responsible for page creation, update, delete, and fallback scoring. A new `agent_console/qmd_search.py` module wraps the qmd CLI, exports wiki pages to markdown, parses qmd JSON results, and reports status. `wiki.list_pages()` asks qmd first when a query is present and qmd is enabled; if qmd is unavailable, disabled, or returns no useful results, the existing in-process scoring path is used.

## Data Flow

1. Wiki pages are still stored as shared-memory records.
2. Before qmd search, current pages are mirrored to markdown under `AGENT_CONSOLE_QMD_WIKI_DIR` or the shared-memory `qmd-wiki` directory.
3. qmd searches its configured local collections and returns JSON.
4. Results with matching wiki page ids are hydrated from the source-of-truth page object.
5. qmd-only document hits are converted to read-only page-shaped results.
6. Fallback pages fill the remaining slots.

## Configuration

- `AGENT_CONSOLE_QMD_ENABLED=1` enables qmd lookup. Set `0`, `false`, `no`, or `off` to disable.
- `AGENT_CONSOLE_QMD_BIN=qmd` selects the CLI binary.
- `AGENT_CONSOLE_QMD_TIMEOUT_SEC=3` caps qmd calls.
- `AGENT_CONSOLE_QMD_COLLECTIONS=wiki` optionally scopes qmd searches.
- `AGENT_CONSOLE_QMD_WIKI_DIR` controls the markdown mirror directory.

## Error Handling

qmd failures are non-fatal. Missing binary, timeout, invalid JSON, empty results, and export errors all fall back to existing wiki scoring.

## Testing

Tests cover JSON parsing, markdown export, command construction, wiki fallback, qmd-prioritized `list_pages()`, and qmd context-section inclusion.
