# Task 2 구현 보고서 — Narrative investment editor and numbered sources

## 기준과 범위

- 기준 커밋: `567a35b`
- 작업 트리: `/tmp/stock-report-wiki-upgrade-20260909`
- Task 2 소유 파일만 구현·검증했다.
- Task 3의 source ingress/cron 파일은 수정하거나 stage하지 않았다.
- production 데이터, live LLM, 네트워크 수집, Telegram, QMD canary는 실행하지 않았다.

## 구현

### 1. article-cache snapshot 소비

- `wiki_distillation.run(..., article_cache_dir=...)`가 실행당 `article_queue.load_index()`를 한 번 호출한다.
- 후보 선택 전에 source page와 metadata를 evidence ID 또는 canonical URL로 연결한다.
- ready `content_hash`를 evidence fingerprint에 포함하여 evidence ID가 같아도 기사 본문 버전이 달라지면 같은 지식 문서를 다시 편집한다.
- fingerprint에 article version이 없는 legacy 페이지는 기존 evidence-only fingerprint 표현을 유지한다.
- 후보로 선택된 page에 대해서만 ready body를 읽고, 페이지당 최대 8건·본문당 최대 3,000자로 제한한다.
- pending/unavailable/missing/title-only body는 model 입력에 넣지 않는다. article-backed 그룹에 ready body가 없으면 model을 부르지 않고 기존 bounded failed retry 상태를 기록한다.
- `institution_watch`, `13f`, `notable_investor`, macro source는 기존 non-article 증류 경로를 유지한다.

### 2. get_article snapshot API

- `get_article(url, *, root=None, index=None)` keyword-only 인자를 추가했다.
- `index` 미지정 시 기존처럼 metadata를 직접 읽으므로 모든 기존 호출과 호환된다.
- Task 2는 실행 초기에 얻은 snapshot을 전달하여 body마다 index를 재로딩하지 않는다.
- trade-off: 한 번의 증류 실행 동안 metadata는 의도적으로 동일한 snapshot이다. 그 사이 body 파일이 사라지거나 바뀌어 검증에 실패하면 `get_article`은 안전하게 `None`을 반환하고 해당 source는 retryable failure로 남는다.

### 3. narrative prompt와 strict renderer

새 `reports/wiki_narrative.py`가 다음을 담당한다.

- 회사 연혁·헤드라인 목록·일반 뉴스 튜토리얼을 금지하고, 현재 근거를 매출/비용/현금흐름 메커니즘, 성립 조건, 반증, 날짜가 있는 변화로 연결하도록 요구한다.
- 관련 없는 ticker 언급으로 domain insight를 발명하지 않고 근거가 부족하면 skip하도록 요구한다.
- 현재 기사에는 `S1...`, 기존 유효 출처에는 `P1...`의 stable reference ID를 제공한다.
- model은 prose와 reference ID만 반환하며 URL/title/publisher/date를 작성하지 않는다.
- renderer가 supplied metadata에서만 bibliography를 만들고, first-use 순서로 contiguous `[1]`, `[2]` marker를 부여한다.
- references 배열의 중복, unknown ID, 본문 marker와 references 불일치, model이 만든 raw URL/출처 섹션/숫자 marker를 fail closed 처리한다.
- canonical URL 기준으로 기존/신규 source를 중복 제거하고, unknown publication date는 생략한다.
- 기존 valid numeric citations/bibliography는 `P*`로 정규화해 model이 보존한 주장과 함께 다시 렌더링할 수 있다.
- report citation은 정확히 한 번 유지하고 sources 아래에 synthetic chat messages를 만들지 않는다.

### 4. 실패·갱신 안전성

- invalid citation은 payload를 만들지 않으므로 기존 knowledge document를 갱신하지 않는다.
- source digest에만 기존 failed retry state/cooldown 정보를 기록한다.
- 한 run에서 model은 후보당 한 번만 호출되며 validation 실패 뒤 즉시 재호출하지 않는다.
- result ID는 기존 `last_result_id` 또는 source/kind 기반 stable ID를 그대로 사용한다.
- archived knowledge, draft/status, semantic duplicate, overflow, retry guard는 기존 흐름을 유지했다.

### 5. 테스트 격리

- `tests/conftest.py`가 import 시점에 `ARTICLE_CACHE_DIR`을 suite-owned temporary directory로 강제한다.
- inherited production article-cache 환경변수를 사용하지 않으므로 기존/신규 wiki 테스트 모두 production cache를 읽거나 쓰지 않는다.

## TDD 및 검증

RED 확인:

1. `get_article(..., index=snapshot)` 테스트는 구현 전 `TypeError: unexpected keyword argument 'index'`로 실패했다.
2. `tests/test_wiki_narrative.py`는 구현 전 module import failure로 실패했다.
3. renderer 구현 뒤 renderer focused 테스트 4개가 통과했고, distillation 연결 전 run-level 계약은 미구현 상태였다.
4. 기존 focused suite 첫 실행에서 obsolete raw-archive/old citation fixture 3건이 실패했고, 새 article-cache/reference 계약으로만 갱신했다.

최종 fresh 검증:

```text
.venv/bin/python -m py_compile reports/wiki_distillation.py reports/wiki_narrative.py reports/article_queue.py
.venv/bin/python -m pytest -q tests/test_wiki_narrative.py tests/test_wiki_distillation.py tests/test_wiki_knowledge_refresh.py tests/test_wiki_evidence_fingerprint.py tests/test_wiki_merge_overflow.py tests/test_wiki_refresh_retry.py tests/test_article_queue.py
.........................................                                [100%]
41 passed in 1.47s
```

테스트가 입증하는 주요 계약:

- 300자 이후 ready body가 prompt에 도달한다.
- pending title은 prompt에 들어가지 않고 pending-only 그룹은 model을 부르지 않는다.
- metadata는 run당 한 번 로드되고 supplied snapshot body read는 index를 재로딩하지 않는다.
- content hash 변경은 동일 evidence ID를 재개방하며, unchanged content는 skip한다.
- first-use contiguous numbering, date omission, URL dedupe, previous citation preservation이 동작한다.
- duplicate/unknown reference가 거부된다.
- invalid reference가 이전 document를 byte-for-byte 보존하고 model을 한 번만 호출한다.
- temporary wiki roundtrip 뒤 bibliography/report citation/messages/stable ID가 유지되며 두 번째 unchanged run은 idempotent하다.
- institution digest는 article-cache가 비어도 legacy path로 처리된다.

## 파일

- `reports/article_queue.py`
- `reports/wiki_distillation.py`
- `reports/wiki_narrative.py`
- `tests/conftest.py`
- `tests/test_article_queue.py`
- `tests/test_wiki_narrative.py`
- `tests/test_wiki_knowledge_refresh.py`
- `tests/test_wiki_refresh_retry.py`
- `.superpowers/sdd/2026-09-09-wiki-crawler/task-2-report.md`

## 잔여 우려

- source classification은 현재 승인 대상인 SaveTicker와 index에 실제 등장한 source root를 article-backed로 취급한다. institution/13F/FRED/worldgovernmentbonds는 명시적으로 legacy path에 남긴다. 향후 새로운 non-news source를 article queue에도 넣는 경우 legacy tag 목록 또는 source-type metadata 계약을 함께 확장해야 한다.
- metadata snapshot은 run consistency를 선택한다. 실행 도중 들어온 새 article은 다음 cron run에서 반영된다.
