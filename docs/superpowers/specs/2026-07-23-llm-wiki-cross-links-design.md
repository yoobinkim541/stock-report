# LLM Wiki Cross-Links Design

## Goal

Close the biggest gap between the stock-report LLM wiki and Karpathy's LLM wiki idea: pages currently have no authored cross-references. `dashboard/wiki_mesh.py` only draws a similarity graph computed at render time (tag/source overlap), which is not the same as the wiki itself maintaining "the cross-references are already there." This design adds authored `links` between pages, computed `backlinks`, and lint checks that catch isolated or under-linked pages. Mesh/graph rendering integration is explicitly out of scope for this pass.

## Current Findings

- `agent_console/wiki.py` pages have no relationship field; `_render_index_md` prints a flat `[[title]]` list with no resolved targets or backlinks.
- `dashboard/wiki_mesh.py` computes edges from tag/source-ref overlap at graph-render time (`_edge_similarity`), which is a visualization heuristic, not an authored, persisted relationship.
- Two ingest paths exist: `auto_curate_from_chat` (LLM plan with heuristic fallback, already ranks candidate pages via `_candidate_score` / `_best_candidate_page`) and `reports/source_wiki_curator.py` (deterministic, event-driven, no LLM call, can create several pages per batch).
- `lint_pages()` currently checks only source-missing-for-promoted, open-questions-present, and empty-page. It has no relational checks.

## Design

### Data Model

- Add `links: list[str]` to the wiki page record and payload — a plain, untyped array of target `page_id` values. Capped at 12, deduped, self-links dropped. Non-existent target ids are allowed (a batch may create pages in an order where a forward reference is written before its target exists).
- `upsert_page` persists `links` on the record (alongside existing fields like `tags`, `source_refs`).
- Backlinks are **not stored**. `get_page` and `list_pages` compute a `backlinks: list[str]` field on each returned page by scanning all wiki records once per call and collecting ids whose `links` contain the page's id. This keeps writes simple (a link is written in exactly one place) and avoids the two-record consistency problem of physically writing back into the target on every upsert/delete. Given expected wiki sizes (hundreds to low thousands of pages), a full scan per read is not a performance concern; if it ever becomes one, an index can be added without changing the public `links`/`backlinks` contract.

### Link Authoring: Conversation Path (`auto_curate_from_chat`)

- `_build_auto_curation_prompt` already lists candidate pages (`id`, `title`, `status`, `kind`, `summary`) to the LLM. Add a `links` field to the requested JSON schema and an explicit instruction: if any listed candidate is genuinely related, include its id in `links`.
- `_parse_curation_plan` needs no change (generic dict parse already passes through unknown keys).
- `_plan_to_page_payload` reads `plan.get("links")`, dedupes/caps at 12, drops self-references, and on `update` merges with the target page's existing `links`.
- Heuristic fallback (no LLM): reuse the existing `_candidate_score` ranking already used by `_best_candidate_page`. Auto-link to candidates scoring at or above `AUTO_CURATE_MIN_SCORE`, capped at 3, to avoid over-linking on a weak heuristic signal.

### Link Authoring: Source Curator Path (`reports/source_wiki_curator.py`)

- Deterministic, no LLM. Within a single `curate_recent_source_wiki()` batch, `build_wiki_pages_from_events` already groups events by `topic:`, `type:`, and `ticker:` keys — the same underlying event can land in multiple groups.
- After building the page list, compute mutual `links` between any two pages in the batch whose event sets intersect (matched by event `url`, falling back to `title` when `url` is absent). This requires no LLM call and is fully deterministic, so it stays test-stable.

### Lint: Relational Checks

Two new lint codes in `lint_pages()`:

- `orphan_page` (severity: `info`) — a page with zero `links` and zero computed `backlinks`. Message: "다른 페이지와 연결이 없습니다."
- `missing_cross_ref` (severity: `warning`) — two pages share a `ticker:` tag, or have at least one identical `source_refs` entry (exact string match after existing `_clean` normalization — no fuzzy matching), but neither links to the other. Emitted once per unordered pair (not once per page) to avoid duplicate noise; the message names both titles.

Implementation builds a tag/ticker/source-ref index over the full page set passed into `lint_pages()` (already receives all pages via `list_pages(status="all", surface="all", limit=400)`), keeping the check close to O(n) for realistic wiki sizes rather than O(n²) pairwise text comparison. This is a lint-time relational check only — it does not touch `dashboard/wiki_mesh.py`.

### Rendering and Prompt Exposure

- `_render_index_md`: append a link-count marker to each entry, e.g. `- [[title]] (kind · status · verification) — summary [🔗2]`, where 2 is `len(links) + len(backlinks)` deduped.
- `wiki.build_context_section()` (consumed by `agent_console/agent.py` for both general chat and the main answer prompt): for each included page, add a line `- 관련: [[제목1]], [[제목2]]` listing linked/backlinked page titles (resolved via id lookup), so the LLM sees already-compiled connections without a follow-up query — this is the direct realization of Karpathy's "the cross-references are already there."
- No new markdown artifact file is added; `index.md` and `lint.md` extensions are sufficient for this pass (YAGNI).

## Constraints

- No new dependency.
- Mesh/graph visualization (`dashboard/wiki_mesh.py`) is not modified in this pass; its similarity-based edges remain a separate, complementary rendering and are not reconciled with authored `links` here.
- Link-type/semantics (e.g. `supersedes`, `contradicts`) are out of scope; `links` stays a plain untyped array.
- Backlinks remain computed, never persisted, to avoid dual-write consistency bugs on update/delete.
- Existing trust/verification contract (`normalize_trust_status`, `trust_warnings_for`) is unaffected; `links` is orthogonal to promotion status.
