# Wiki P1 개선 + Critical Review (v2)

## 배경
P0/P2 완료. 이제 P1 2개 구현 + Claude Code 교차검증/비판적 리뷰.

## P1a: 페이지 유용성 피드백 (30min)
**파일**: `agent_console/wiki.py` — `_build_auto_curation_prompt()`, `auto_curate_from_chat()`

**목표**: LLM이 큐레이션할 때 후보 페이지의 유용성을 평가하고, 피드백을 페이지에 저장.

**구현**:
1. `_build_auto_curation_prompt()`의 후보 목록에 각 페이지의 `useCount`와 `lastUsedAt` 포함
2. LLM JSON 응답에 `page_feedback` 필드 추가 — `{page_id: "helpful"|"not_helpful"|"neutral"}`
3. `auto_curate_from_chat()`에서 `page_feedback` 파싱
4. `_store_page_feedback(page_id, rating)` 함수 — `upsert_page()`로 페이지의 `feedback` 필드 업데이트
   - `feedback` 구조: `{"helpful": 3, "not_helpful": 1, "neutral": 5}`
5. `lint_pages()`에 새 규칙 추가: `high_negative_feedback` — not_helpful > helpful * 2

**LLM 프롬프트 추가**:
```
[페이지 피드백]
제공된 위키 페이지 중 이 대화에 도움이 된 것과 아닌 것을 평가:
- "page_id_1": "helpful"  (이 페이지의 정보를 실제로 사용함)
- "page_id_2": "neutral"  (참고만 했음)
- "page_id_3": "not_helpful"  (관련 없거나 부정확함)
page_feedback 필드에 JSON 객체로 담아주세요.
```

## P1b: LLM 수집 큐레이션 (1hr)
**파일**: `reports/source_wiki_curator.py`

**목표**: source_wiki_curator가 이벤트 그룹핑 후 LLM에게 제목/요약/태그 생성을 위임.

**구현**:
1. `_llm_enrich_event_group(group_title, events, llm_fn)` 함수 추가
2. 조건: 그룹 내 이벤트가 3개 이상일 때만 LLM 호출 (비용 제어)
3. LLM 프롬프트:
   ```
   너는 stock-report 위키 큐레이터다. 아래 수집 이벤트들을 분석해서:
   1. 더 나은 제목 (8-15자, 명사형)
   2. 2문장 요약
   3. 태그 3-5개
   4. 연관 키워드 (다른 위키 페이지 검색용)
   
   JSON: {"title": "...", "summary": "...", "tags": [...], "search_keywords": [...]}
   ```
4. `_llm_enrich_event_group()` 결과로 `title`, `summary`, `tags`를 덮어씀
5. `from agent_console.agent import _try_llm_prompt` 사용
6. LLM 실패 시 기존 heuristic 값 유지 (fallback)

**LLM 호출 조건**: 
- len(events) >= 3 (비용 제어)
- `os.getenv("SOURCE_WIKI_LLM_ENABLED", "1")` != "0"
- LLM 함수 사용 가능

## 검증 (공통)
1. `uv run pytest tests/ -x -q` — 전체 테스트
2. `uv run python -c "from agent_console import wiki; wiki.stats()"` — import 정상
3. `uv run python -c "from reports.source_wiki_curator import *; print('OK')"` — import 정상

## Critical Review
P1 구현 후, Claude Code로 wiki 시스템 전체 교차검증 진행:
1. **에지 케이스**: 빈 질문, 특수문자, 10000자 초과 입력, 동시성
2. **메모리/성능**: 사용량 추적 오버헤드, storage.limit() 도달 시
3. **데이터 무결성**: upsert/delete/merge 동시 호출, source_refs 중복
4. **보안**: LLM 프롬프트 인젝션, 파일 경로 조작
5. **회귀**: 기존 테스트 커버리지, 누락된 테스트
6. **권장사항**: 3순위 이내의 개선점

결과는 `docs/2026-07-24-wiki-critical-review.md`에 저장.