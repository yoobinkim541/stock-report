# Agent Console Intent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add intent-first prompt contracts to AI console answers while leaving room for real retrieval providers later.

**Architecture:** `agent_console.agent` gets a small intent classifier that returns an intent contract with search requirements, default peers, forbidden templates, and a retrieval plan. `_build_general_chat_prompt` includes that contract before local context so LLMs treat missing context as a search trigger rather than a stop condition.

**Tech Stack:** Python 3.11, pytest, existing Codex/Hermes/Gemini LLM chain.

## Global Constraints

- Do not reintroduce rule-based answer generation before LLM when LLM is enabled.
- Keep local fallback behavior available only when LLMs fail or are disabled.
- Leave provider execution as a future extension point through a structured retrieval plan.

---

### Task 1: Intent Contract Tests

**Files:**
- Modify: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `agent_console.agent._try_llm_chat(question, pack, history, runner)`
- Produces: failing prompt-contract tests for peer comparison, meta, portfolio, and technical analysis.

- [ ] **Step 1: Add tests that capture `_try_llm_prompt` input and assert intent contracts.**
- [ ] **Step 2: Run the focused tests and verify they fail because prompt contracts are absent.**

### Task 2: Intent Classifier And Prompt Contract

**Files:**
- Modify: `agent_console/agent.py`

**Interfaces:**
- Produces: `_classify_question_intent(question, pack=None, history=None) -> dict` and `_intent_contract_lines(intent) -> list[str]`.

- [ ] **Step 1: Implement minimal classification for `meta`, `peer_compare`, `portfolio_review`, `market_brief`, `technical_analysis`, `ticker_research`, and `general`.**
- [ ] **Step 2: Add the contract section before local context in `_build_general_chat_prompt`.**
- [ ] **Step 3: Run focused tests and make them pass.**

### Task 3: Regression Verification

**Files:**
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: existing LLM preference and Codex search tests.

- [ ] **Step 1: Run the full agent console test file.**
- [ ] **Step 2: Run Python import smoke checks for touched modules.**
- [ ] **Step 3: Commit and push with Korean `fix)` message.**
