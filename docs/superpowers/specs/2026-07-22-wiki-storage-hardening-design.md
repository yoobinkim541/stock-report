# 위키 저장 계층 강화 — 읽기 창 제거 + 원자적·직렬화된 쓰기

작성일: 2026-07-22

## 배경

AI 위키(`agent_console/wiki.py`)는 페이지를 별도 저장소가 아니라 공유 메모리
`events.jsonl` 에 채팅 기록과 함께 저장한다. 이 구조에서 두 가지 결함이 확인됐다.

### 문제 1 — 지식층이 최근 100 레코드 창에 갇힌다

`agent_console/shared_memory.py:245`:

```python
def list_records(limit: int = 50, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit or 50), 100))   # 400 을 요청해도 100 으로 잘림
```

`wiki.list_pages`(193행), `wiki.get_page`(216행), `wiki.stats`(223행) 은 모두
`list_records(limit=400)` 을 호출하지만 실제로는 **최근 100건**만 받는다. 그 100건은
위키 전용이 아니라 `append_chat_exchange` 가 쓰는 채팅 레코드와 공유하는 창이다.

레코드가 100을 넘으면:

- 오래된 위키 페이지가 목록·검색·통계에서 조용히 사라진다.
- `upsert_page` 가 `get_page()` 로 기존 페이지를 찾지 못해 신규로 취급하고,
  같은 id 를 가진 레코드가 중복 누적된다.

현재 저장소(`~/.local/share/stock-report/shared-memory/events.jsonl`)에는 67건이 있어
아직 발현되지 않았다. 대화가 쌓이면 곧 도달한다.

`list_records` 는 `_read_jsonl` 로 **파일 전체를 이미 읽은 뒤** 잘라내기만 하므로,
이 클램프는 성능 이득이 전혀 없는 순수 손실이다.

### 문제 2 — 비원자적·무락 쓰기, 그리고 writer 가 둘

`agent_console/wiki.py:287-289`:

```python
if existing and existing.get("id"):
    shared_memory.delete_record(page_id)   # 전체 재작성
saved = shared_memory.append_record(record)
```

`delete_record`(254행)는 파일 전체를 읽고 `open("w")` 로 truncate 후 재작성한다.
락이 없고, 삭제와 추가 사이에 프로세스가 죽으면 페이지가 사라진다.

더 중요한 것은 **같은 파일에 독립적인 writer 가 둘**이라는 점이다:

| | `agent_console/shared_memory.py` | `lib/agent_memory.py` |
| --- | --- | --- |
| events.jsonl | append + 전체 재작성(delete 시) | append (420행) |
| index.json | `{ok, schemaVersion, records[200]}` 비원자적 `write_text`(285행) | `{latestAt, latestTitle, count}` 원자적(425행) |

두 모듈이 같은 `index.json` 에 **서로 다른 스키마로 번갈아 덮어쓴다**. 한쪽만 락을
걸어도 전체 재작성 중 다른 쪽 append 가 유실되므로 의미가 없다.

저장소에는 이미 `safe_io.py` 가 있다 — `atomic_write_json`(temp→fsync→rename)과
flock 기반 `file_write_lock`. 락 파일이 `<path>.lock` 사이드카라 두 모듈이 경로만
같으면 자동으로 같은 락을 공유한다. `lib/` 에서도 import 가능함을 확인했다.

## 목표

1. 위키가 레코드 수와 무관하게 전체 페이지를 조회·갱신할 수 있게 한다.
2. `events.jsonl` 과 `index.json` 쓰기를 원자적으로 만들고 두 writer 를 직렬화한다.
3. `index.json` 의 스키마 상호 클로버를 없앤다.

## 비목표

- 위키 `status` 정책과 provenance(`source.writer`)는 변경하지 않는다. 사용자가
  기계의 `reviewed` 부여를 유지하기로 결정했다.
- `dashboard/wiki_browser.py` 의 중복 헬퍼 정리는 별건이다.
- `_write_index()` 호출 빈도는 줄이지 않는다. 원자적으로 바꾸되, 매 쓰기마다
  약 119KB 를 재작성하는 비용은 현재 규모(67건)에서 무해하므로 문서로만 남긴다.
- 위키를 별도 `wiki.jsonl` 로 분리하지 않는다(마이그레이션 범위 회피).
- `list_records(limit, offset)` 의 기존 의미론은 바꾸지 않는다.

## 설계

### 1. 클램프 없는 읽기 경로

`shared_memory` 에 함수를 하나 추가한다:

```python
def all_records() -> list[dict]:
    """전체 레코드를 createdAt 내림차순으로 반환한다. 창(window) 없음.

    list_records() 는 페이지네이션 계약(limit/offset)이라 그대로 두고,
    지식층처럼 '전수'가 필요한 소비자만 이 함수를 쓴다.
    """
```

`wiki.list_pages` / `get_page` / `stats` 의 `list_records(limit=400)` 호출을
`all_records()` 로 교체한다.

**위키 판별(`_is_wiki_record`)은 `wiki.py` 에 그대로 둔다.** `shared_memory` 로
옮기면 도메인 지식이 두 곳에 생기고, 이는 `dashboard/wiki_browser.py` 가 같은
헬퍼를 재구현해 드리프트한 것과 같은 실패를 반복하는 것이다.

`list_records` 는 손대지 않는다 — `query_shared_memories`(290행, limit=100),
`refresh_context_memory_summary` 경로(416행, limit=8), 외부 API
페이지네이션(533행)이 기존 의미론에 의존한다.

### 2. 락과 원자적 쓰기

`safe_io.py` 에 텍스트용 원자적 쓰기를 추가한다(현재 JSON 전용만 있다):

```python
def atomic_write_text(path: str, text: str) -> None:
    """text 를 path 에 원자적으로 기록 (temp→fsync→rename). 실패 시 원본 보존."""
```

`shared_memory` 에 단일 진입점을 만든다:

```python
def _events_lock():
    """events.jsonl 사이드카 락. lib/agent_memory 와 같은 경로를 쓰므로 공유된다."""
    return safe_io.file_write_lock(str(_paths()["events"]), timeout=30.0)


def upsert_record(record: dict) -> dict:
    """id 기준 치환-또는-추가를 한 번의 원자적 재작성으로 수행한다."""
```

변경 대상:

| 대상 | 변경 |
| --- | --- |
| `wiki.upsert_page` | `delete_record` + `append_record` → `upsert_record` 1회. 페이지가 사라지는 창 제거 |
| `shared_memory.append_record` | `_events_lock()` 안에서 append |
| `shared_memory.delete_record` | `_events_lock()` + truncate-write → `atomic_write_text` |
| `shared_memory._write_index` | `atomic_write_json` 사용, 아래 3번의 merge 규칙 적용 |
| `lib/agent_memory` 의 events append(420행) | 같은 `safe_io.file_write_lock(EVENTS_PATH)` 획득 |
| `lib/agent_memory._write_text_atomic` | `safe_io.atomic_write_text` 로 위임(원자적 쓰기 구현 3개 → 1개) |

락 획득 실패(`safe_io.LockTimeout`)는 삼키지 않는다. 조용한 실패는 이 저장소에서
이미 한 번 사고를 키웠다(`auto_curate_from_chat` 이 `try/except: pass` 뒤에서
몇 주간 죽어 있었다). 호출자에게 전파한다.

### 3. index.json 스키마 공존

단일 writer 로 몰지 않는다. `shared_memory` 가 이미 `lib.agent_memory` 를
import 하므로(`refresh_context_memory_summary`), 반대 방향 의존을 추가하면 순환이 된다.

대신 양쪽 모두 **읽기 → 자기 키만 update → `atomic_write_json`** 을 events 락 안에서
수행한다.

- `shared_memory._write_index` 가 소유하는 키: `ok`, `schemaVersion`, `updatedAt`,
  `recordCount`, `latestRecordAt`, `records`
- `lib/agent_memory` 가 소유하는 키: `latestAt`, `latestTitle`, `count`
- 각자 상대의 키는 읽어서 그대로 보존한다.

`index.json` 의 `records` 를 읽는 코드는 저장소에 없으나
`docs/shared-agent-memory.md:18` 이 "latest-record snapshot" 으로 문서화하고 있어
외부 도구가 읽을 가능성이 있으므로 제거하지 않는다.

## 테스트

`tests/test_shared_memory_storage.py` 를 신설한다. 모든 테스트는 `monkeypatch` 로
임시 디렉토리를 저장소로 잡아 실제 사용자 데이터를 건드리지 않는다.

1. **창 제거 회귀** — 레코드 150건을 만들고 그중 101번째보다 오래된 위치에 위키
   페이지를 심는다. `wiki.get_page(id)` 가 찾고, `wiki.list_pages()` 에 포함되고,
   `wiki.stats()["total"]` 이 그 페이지를 센다. (현행 코드에서는 실패해야 한다.)
2. **중복 id 미발생** — 위 오래된 페이지를 `wiki.upsert_page` 로 갱신한 뒤,
   `events.jsonl` 에서 해당 id 를 가진 행이 정확히 1개인지 확인한다.
3. **동시 쓰기 유실 0** — `multiprocessing` 으로 한 프로세스는 `delete_record`
   (전체 재작성)를, 다른 프로세스는 `append_record` 를 반복 실행한 뒤, 기대 레코드
   수가 정확히 보존되는지 확인한다.
4. **index 키 공존** — `shared_memory.append_record` 와 `lib.agent_memory` 의 기록을
   번갈아 실행한 뒤 `index.json` 에 양쪽 키(`recordCount`, `count`)가 모두 남는지
   확인한다.
5. **원자성** — `atomic_write_text` 실행 중 예외가 나도 원본 파일이 온전한지
   (temp 파일만 남고 원본 미변경) 확인한다.

기존 스위트 회귀 확인: `tests/test_agent_console.py`, `tests/test_agent_memory.py`,
`tests/test_dashboard_pages.py`. 판단은 개수가 아니라 **실패 목록 대조**로 한다.

## 리스크

| 리스크 | 대응 |
| --- | --- |
| `lib/agent_memory` 변경이 `/ask`·codex 등 외부 소비자에 영향 | index 키를 보존하고 기존 테스트(`test_agent_memory.py`)를 그대로 통과시킨다 |
| flock 이 NFS 등에서 무력 | 저장소는 로컬 디스크(`~/.local/share`)다. 해당 없음 |
| 락 경합으로 대화 응답이 지연 | 쓰기는 짧고 timeout 30초. 경합 시 `LockTimeout` 을 전파해 조용한 실패를 만들지 않는다 |
| 전체 재작성 중 크래시 | `atomic_write_text` 의 temp→rename 으로 원본이 항상 온전하다 |

## 열린 질문

없음.
