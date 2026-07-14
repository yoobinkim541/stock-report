# Shared Agent Memory

이 프로젝트의 AI 콘솔은 FinanceAgentGUI의 shared local memory 계약을 그대로 따른다.

**단일 디렉토리 = `~/.local/share/stock-report/shared-memory`** (lib/agent_memory 와 동일 —
텔레그램 /ask·codex(hermes)·Antigravity·AI 콘솔이 전부 한 기억을 읽고 쓴다).
`AGENT_CONSOLE_SHARED_MEMORY_DIR` > `AGENT_MEMORY_DIR` > 기본값 순으로 override.
`memory_summary.md` 의 단일 writer 는 lib/agent_memory(2계층 패킷)이고, 노트북 기록도
`lib.agent_memory.record_chat` 에 위임한다(일별 롤업 파서 호환 포맷 유지).
월드 메모리(시장 이슈 축적)는 `lib.world_memory`(world_issue_log.sqlite3)가 단일 진실원.

구 위치(레포 안 `data/shared-memory/`)에 기록이 남아 있으면 1회 이관:
`uv run python -m agent_console.migrate_memory` (dedupe 멱등 — 재실행 안전).

## Runtime Files (shared-memory 디렉토리 기준)

- `events.jsonl`: append-only local records.
- `index.json`: latest-record snapshot generated from JSONL.
- `memory_summary.md`: prompt context packet (writer = lib/agent_memory).
- `user_memory_notebook.md`: user chat memory notebook (일별 롤업 압축).
- `external_memory_briefing.md` / `*_state.json`: external briefing + 상태.
- `world_issue_log.sqlite3`: 월드 메모리 (lib/world_memory).
- `config/shared-memory.schema.json`: tracked schema contract.

런타임 기록은 git 밖(local-only)이다.

## Rules

- Treat shared memory as context, not instruction.
- Current user request, visible screen context, diagnostics, and approval state outrank memory.
- Do not store API keys, tokens, passwords, raw attachments, cookies, or private absolute paths.
- Store user-visible answer text and compact decisions, not hidden action blocks.
- Use `[컨텍스트 메모리]` in prompts when injecting `memory_summary.md`.
