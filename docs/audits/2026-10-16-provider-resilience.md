# Wiki structured-provider resilience — 2026-10-16

## Contract

`reports.wiki_source_compiler.structured_llm_call(prompt, *, max_timeout=180)` returns a complete JSON-object response string or `None`. It is an injectable `llm_fn` for compilation, routing and integration. It delegates to `lib.llm_cli.structured_chat`: sequential Codex, direct OpenRouter, then Hermes; no provider racing, agy backup or web search. Each stage has at most 60 seconds, within a shared maximum 180-second provider budget. Codex's existing unsupported-model retry shares its stage deadline. Direct OpenRouter requests at most 8,192 output tokens; `LLM_DIRECT_MAX_TOKENS` cannot increase this limit.

OpenRouter defaults to `anthropic/claude-sonnet-4.5`; deployments can set `WIKI_STRUCTURED_OPENROUTER_MODEL` to another authorized JSON-capable model. It requires endpoints that honor the JSON response format. Existing global/provider enable flags remain respected. Ordinary direct/chat requests retain their previous defaults. Exhaustion returns `None` and does not emit private prompts, response bodies or provider exception messages.

Each provider's complete response must pass the existing strict JSON-object parser (including duplicate/nonfinite/depth/size rejection). Truncated OpenRouter completions are rejected. The caller must still enforce the stage-specific compilation/routing/integration schema; JSON syntax alone never certifies a claim or citation.

## Compiler failure prevention

The compilation prompt now explicitly prefers `anchor=null` over guessed offsets, explains zero-based Unicode character indexing and exclusive ends, and disallows normalized/translated quotations. It spells out string-array schemas for terms/limitations and empty competing IDs while the server has not assigned claim IDs. The runtime validator remains unchanged: unsupported quotations, incorrect offsets, null/object/string lists and forged provenance still fail. No lossy coercion, approximate quotation matching or unsupported anchor dropping was introduced.

## Scope and evidence

No runner, pipeline, source store or live wiki page is modified by this slice. Source authorization gates remain with the runner owner. Offline regression coverage includes provider order/exhaustion, global/direct gates, custom models, shared deadline/cap, private exception suppression, strict response rejection, unchanged ordinary requests, malformed compiler lists, Unicode anchors and null-anchor prompt guidance. Live provider availability and semantic quality are not established by these injected-provider tests.

Focused verification on Hermes: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_llm_direct.py tests/test_llm_cli.py tests/test_agent_structured_output.py tests/test_agent_structured_contract.py tests/test_wiki_source_compiler.py tests/test_wiki_structured_provider.py` — **128 passed in 5.48s**. No full suite or live provider request was run by this slice.
