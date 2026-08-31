# 위키 중심 지식 자산화 — 설계 스펙 (Spec)

> 이 문서는 구현 계획(plan)이 아니라 **스펙**입니다. 유빈님 확인 후 `superpowers:writing-plans` 로 세부 작업 계획을 뽑습니다.

## 배경

유빈님 관찰: "지금 위키는 그냥 출처만 보아놓은 그런 느낌" — source_digest 가 원문 수집에서 곧바로, 무차별적으로 쌓이기만 하고, 리포트와도 연결이 없다.

참고 프로젝트 [`mywiki`](https://github.com/yoobinkim541/SK_Suni_5th_project-myWiki): 문서→신뢰도/중요도 점수→리포트 조립(상위 문서만 채택)→그 리포트가 인용한 문서만 위키로 승격, 이라는 "리포트가 먼저" 구조.

**유빈님 방향 확정(대화 중 정정)**: mywiki 와 반대로 **위키가 먼저, 리포트는 위키에서 나온다.** 다만 mywiki 의 "위키가 계속 업데이트·갱신되는" 지속 큐레이션 장치(스코어링·중복병합·발행게이트)는 그대로 가져온다.

## 확정된 방향

```
수집(원문) ─┬─▶ source_digest (원문 보존, 지금처럼 유지)
            │
            ▼
       증류(distillation) — 신뢰도·중요도 스코어링 + 중복병합 추가
            │
            ▼
     judgment 위키 (playbook/risk/concept) ── 지속 갱신되는 지식베이스
            │
            ▼  (신규 연결점 — 폭넓은 조회 아님, 아래 참고)
     리포트가 이미 정한 스코프(보유종목·그날 이벤트·포폴 변화) 각각에 대해서만
     매칭되는 위키(그 종목/이슈의 risk·playbook)를 찾아 인용
            │
            ▼
   investment_report / market_report (기존 그대로, 입력만 위키 경유 추가)
```

핵심: **wiki 는 upstream(지속 갱신), report 는 downstream(위키 조회 결과물)** — mywiki 와 데이터 흐름 방향은 반대지만, "지속적으로 자동 갱신되는 위키" 라는 mywiki 의 핵심 가치는 그대로 채택.

**리포트 스코프 정정(대화 중 정정)**: 리포트는 위키 전체에서 "관련성 높은 걸" 폭넓게
끌어오는 게 아니다. 리포트는 이미 자체 기준으로 다룰 대상을 정한다 — ① 그 전날
있었던 중요한 일 ② 보유 중인 종목의 뉴스 ③ 포트폴리오에 있었던 변화, 이 세 가지뿐.
위키 조회는 **그 세 가지 각각에 대해서만** "이 종목/이슈에 걸린 위키가 있는가?"를
찾는 것이지, 별도의 "관련 지식 톱N 뽑기" 단계가 아니다. 예: 오늘 리포트가 MSFT
뉴스를 다룬다면 → MSFT 관련 risk/playbook 위키가 있는지만 찾아 인용. 위키에
아무리 좋은 지식이 있어도 리포트의 3가지 스코프 밖이면 인용되지 않는다.

## 현재 코드 기준선 (2026-08-31)

- `reports/source_wiki_curator.py` — 수집 이벤트 → `source_digest` 위키 페이지 (원문 보존, kind=source_digest)
- `reports/wiki_distillation.py` — source_digest → judgment 카드(playbook/risk/concept). 크론 6시간마다, 배치 20건(`ab0eb87`, 2026-08-31 신규)
- `agent_console/wiki.py` — 저장소 CRUD, `list_pages`(검색랭킹), `lint_pages`, `archive_stale_pages`, `_merge_pages`(ad-hoc 병합만 존재, **정기 배치 없음**)
- `reports/wiki_pipeline_health.py` — 소스/위키/큐레이션 헬스 리포트(`curation_health.source_digest_unlinked_count` 등)
- **확인된 문제(2026-08-31 감사)**: judgment 카드 123개 중 55개(45%)가 근본적으로 같은 원칙의 재탕 — mywiki 의 "중복 병합 배치"가 정확히 이 문제를 겨냥한 장치였음.
- `reports/investment_report.py` 등 — 매일 자동 발송 리포트. **현재 위키를 전혀 조회하지 않음** — 매번 처음부터 데이터를 새로 모음.
- `agent_console/wiki.py::build_context_section()` — AI 콘솔 챗용 위키 컨텍스트 조회 함수. 리포트용 조회 함수의 출발점으로 재사용 가능.

## 채택할 mywiki 장치 (스코프)

| # | 장치 | 우선순위 | stock-report 적용 지점 |
|---|------|---------|----------------------|
| 1 | **중복 병합 배치**(정기 크론, LLM 유사도 임계치로 판단카드 병합) | **P0** — 이미 확인된 45% 중복 해결 | `agent_console/wiki.py::_merge_pages` 재사용 + 신규 크론 스크립트 |
| 2 | 신뢰도·중요도 연속 점수(0~100, 시간감쇠 포함) | P1 — 발행게이트·리포트 채택 기준의 토대 | `agent_console/wiki.py` 신규 필드/함수, `_record_to_page` 확장 |
| 3 | 발행 게이트(점수 임계치로 draft/reviewed/stable/archived) | P1 — 현재 규칙 기반 status 를 점수 기반으로 교체 | `normalize_trust_status` 대체/확장 |
| 4 | 리포트 스코프(보유종목·그날 이벤트·포폴 변화) 각각에 매칭되는 위키만 조회해 인용 | **P0** — 유빈님이 지적한 핵심 갭 | `reports/investment_report.py` 등에 위키 조회 단계 추가 |
| 5 | **자율 위키 관리(대주제 병합 + 길면 자식 위키 분할)** — 유빈님 신규 요청 | **P0** | `agent_console/wiki.py::_merge_pages`/`_split_page`(이미 존재, 지금은 챗 트리거만) 재사용 + 신규 정기 크론 |
| 6 | 키워드 태깅 배치(관련도 점수) | P2 — 검색 품질 개선용, 급하지 않음 | 신규, 후순위 |
| 7 | **본문 고정 스키마**(mywiki 의 key_facts/implications/watch_points 식 — 자유서술 아님) | **P0** — #4(리포트 조회)의 전제조건 | `agent_console/wiki.py` 본문 생성부, `reports/source_wiki_curator.py`, `reports/wiki_distillation.py` |

## 위키 본문 포맷 — mywiki 대비 현재 격차

**mywiki**: 모든 위키 페이지가 고정 3필드 스키마(`key_facts`/`implications`/`watch_points`) — 어느 페이지든 구조가 동일해 기계적으로 필드를 뽑아 쓸 수 있음.

**현재 stock-report**(source_digest 실측 예): "수집 기준 / 요약 / 핵심 관찰 / 핵심 근거 / 후속 질문 / 답변 사용법" — 섹션이 그때그때 조건부로 붙는 **자유 서술**(`_record_to_page` 가 decisions/openQuestions/messages 유무에 따라 다른 섹션을 이어붙이는 구조, [wiki.py:496-515](../../../agent_console/wiki.py)). 페이지마다 구조가 미묘하게 달라 리포트 쪽에서 "이 페이지의 핵심이 뭔지"를 기계적으로 뽑을 수 없음 — 매번 전체 본문을 LLM 에 다시 넣어 재요약해야 함.

**결정(2026-08-31 예시 작업으로 확정)**: mywiki 처럼 필드를 쪼개 나열하는 스펙시트 형태가 아니라, **흐르는 문장의 백과사전형 위키 문서**로 간다 — 배경·이유·적용·예외·관찰 사례·같이 보기가 하나의 글로 이어짐. 유일하게 고정으로 강제하는 건 문서 맨 끝의 **"리포트 인용 요약" 한 줄**(리포트가 전문을 재요약하지 않고 그대로 뽑아 쓰는 용도). 실제 예시는 [`2026-08-31-wiki-body-schema-example.md`](2026-08-31-wiki-body-schema-example.md) 참고.

미결정: kind별 소제목(왜/어떻게/예외 등)을 프롬프트로 고정할지, 매번 LLM이 자연스럽게 짓게 할지 — 예시 문서의 판단해주실 것 #3.

이건 #4(리포트→위키 조회) 가 실제로 잘 동작하기 위한 전제조건이라 P0 로 승격 — Phase 1 에 포함.

## Phase 1 — 작게 쪼갠 하위 단계 (유빈님 지시: "페이스는 작게 쪼개자")

각 하위 단계가 독립적으로 동작·검증 가능해야 함(TDD, 각자 커밋). 순서는 의존성 순.

### Phase 1a — 중복 병합 배치 크론
`reports/wiki_dedup_batch.py`(신규), 1일 2회(mywiki 관행). 같은 `surface`+`kind`(playbook/risk/concept) 페이지 쌍을 제목·본문 토큰 유사도(`_tokens`/`_candidate_score` 재사용)로 스코어링 → 임계치 이상이면 LLM에 "같은 원칙인가?"(정확 재탕만, #10 대주제 판정과는 다름) 이진 판정 → 예이면 `_merge_pages` 로 병합(target=최근 갱신+confidence 높은 쪽, source=나머지) → `deploy/crontab.stock-report` 등록.

### Phase 1b — 위키 문서 문체 전환
자유서술(조건부 섹션 이어붙이기)을 백과사전형 프롬프트로 교체 + 문서 끝 "리포트 인용 요약" 한 줄 고정. 기존 페이지는 그대로 읽히게 하위호환 유지 — **백필은 이 단계에서 안 함**(Phase 1e).

### Phase 1c — 자율 위키 관리(대주제 병합 + 길면 자식 위키 분할)
`reports/wiki_autonomous_curator.py`(신규) 크론, 1일 1회(대주제 판단은 무거우니 dedup 보다 낮은 빈도). 판정 시 #10 의 8가지 경계 원칙 프롬프트에 명시.
- **대주제 병합**: 같은 `surface`+연관 태그 페이지들을 LLM이 "합쳤을 때 잃는 정보가 있는가?" 프레이밍으로 판정 → 기존 `_merge_pages`(`agent_console/wiki.py:1432`) 재사용.
- **길면 분할**: 본문이 가독성 임계치(~2000~3000자, 저장 상한 `WIKI_BODY_LIMIT=12000` 과 다름) 넘으면 LLM이 의미 단위로 쪼갤지 판정 → 기존 `_split_page`(`agent_console/wiki.py:1598`) 재사용. 계층은 신규 `parent_page_id` 필드로 저장.
- 안전장치: `auto_curate_from_chat` 의 병합 인젝션 가드(source-backed 보호, surface 불일치 거부, `agent_console/wiki.py:1498` 부근)를 그대로 적용.
- 지금은 두 함수 모두 **AI 콘솔 챗 중 LLM 트리거로만** 호출됨(`auto_curate_from_chat`) — 이 단계의 신규 작업은 "정기 스캔 → 판정 → 기존 함수 호출"을 잇는 자율 트리거뿐, 병합/분할 로직 자체는 재사용.

### Phase 1d — 리포트→위키 조회 연결 (investment_report.py 만, 다른 리포트는 후속 단계)
`agent_console/wiki.py` 에 리포트 전용 조회 함수(예: `wiki.for_ticker(ticker, kind="risk|playbook", limit=N)`) 신설 — 리포트가 이미 정한 대상(보유 종목 티커·그날 이벤트 종목·포폴 변화 종목) 각각에 정확히 매칭되는 위키만 조회. `reports/investment_report.py` 가 이미 다루는 대상 목록을 순회하며 호출 → 매칭되면 "리포트 인용 요약"(Phase 1b 산출물) 한 줄을 **본문에 각주처럼 텍스트로 삽입**(링크만 X). `market_report.py` 등 다른 리포트 연결은 Phase 2 로 분리.

### Phase 1e — 기존 페이지 백필
Phase 1b 문체로 기존 748개+ 페이지를 재작성. Phase 1b·1c 안정화 후 진행(문체가 확정 안 된 채로 백필하면 재작업 위험) — 일괄이 아니라 배치 처리(다른 크론들과 마찬가지로 flock+배치 상한 적용, `ab0eb87` 관행 따름).

### (참고) Phase 2 로 미루는 것
신뢰도/중요도 정식 스코어링 엔진(Phase 1 은 기존 `confidence` 필드 + 최근성만 사용), 키워드 태깅 배치, market_report.py 등 investment_report.py 외 리포트 연결.

## 조율 필요 사항 (다른 세션과의 충돌 위험)

`agent_console/wiki.py`, `reports/wiki_distillation.py`, `reports/source_wiki_curator.py` 를 다른 세션이 하루에도 여러 번 커밋하며 활발히 수정 중(`ab0eb87`, `4f0f744`, `faf0a9e`, `9252cba`, `54d9413`...). Phase 1 작업 시작 전 반드시:
- `git fetch && git log --oneline -20 origin/master` 로 최신 상태 재확인
- 특히 "중복 병합"을 그 세션이 먼저 만들었을 가능성 확인(스펙 작성 시점엔 없었음)

## 확정된 결정 (2026-08-31)

1. **유사도 판정**: 임베딩 인프라 없음(확인함) — 토큰 오버랩(`_tokens`/`_candidate_score` 재사용)으로 후보 추림 → 최종 판정만 LLM 이진 질문.
2. **병합 방향**: target(살아남는 페이지) = 최근 갱신 + confidence 높은 쪽. source 들을 흡수.
3. **리포트 인용 방식**: 링크만 X — "리포트 인용 요약" 한 줄을 리포트 본문에 **각주처럼 텍스트로 직접 삽입**.
4. **Phase 1 스코프**: `investment_report.py` 만. `market_report.py` 등은 Phase 2.
6. **백필**: 기존 748개+ 페이지도 새 문체로 백필한다(Phase 1e, 문체 안정화 후).
7. **자율 관리 크론 주기**: 정확 재탕 병합(dedup, Phase 1a) 1일 2회 / 대주제 병합·분할(Phase 1c) 1일 1회 — 후자가 판단이 더 무거워 낮은 빈도.
8. **분할 임계치**: 저장 상한(`WIKI_BODY_LIMIT`=12000자)이 아니라 가독성 기준 ~2000~3000자.
9. **계층 필드**: 신규 `parent_page_id` — 기존 `links`(교차참조)와 용도 분리.
10. **대주제 병합 경계** (8가지):
    1. **kind 경계는 항상 유지** — risk/playbook/concept/source_digest 는 목적이 달라 같은 종목이라도 kind 가 다르면 병합 후보에서 제외.
    2. **판단의 결이 다르면 분리** — 같은 종목이라도 밸류에이션 리스크/규제·정책 리스크/공급망 리스크/거버넌스 리스크는 대응 방향이 다르므로 별개 유지.
    3. **시간성이 다르면 분리** — 1회성 이벤트(예: 이번 분기 실적 리스크) vs 지속 원칙(구조적 밸류에이션 리스크)은 같은 risk kind 라도 분리.
    4. **결론이 상충하면 병합 금지** — 매수 근거·매도 근거처럼 방향이 반대인 페이지는 유사해 보여도 합치지 않음. LLM 판정 프롬프트에 "결론 방향이 같은가" 체크리스트 명시.
    5. **근거 강도가 다르면 분리** — source-backed 와 unverified 를 합치면 페이지 전체 신뢰도가 오염됨.
    6. **LLM 판정 프레이밍**: "유사한가?"가 아니라 **"합쳤을 때 잃는 정보가 있는가?"**로 묻는다 — 전자는 병합 쪽으로 편향되기 쉽고 후자는 보수적으로 작동.
    7. **분할 기준은 글자수보다 의미 단위** — 리포트가 특정 부분만 따로 인용하고 싶을 만큼 독립적인 하위 판단이 섞여 있으면 짧아도 분할, 하나의 판단 흐름이면 길어도 유지.
    8. **출처 보존은 무조건** — 병합 시 원본 페이지 id 전부를 `merge_history` 에 유지(신규 구현 불필요, 기존 필드 재사용).

11. **소제목 자율 여부 — 확정(2026-08-31)**: 소제목 고정 X, 다룰 내용(배경·적용·예외·관찰 사례·같이 보기)만 프롬프트로 가이드 — 실제 소제목 문구·순서·구성은 LLM이 매번 자연스럽게 짓는다. 이유: Phase 1d 가 기계적으로 뽑는 건 "리포트 인용 요약" 한 줄뿐이라 본문 내부 헤더를 고정할 기술적 필요가 없고, 고정하면 스펙시트 느낌으로 돌아감([예시 문서](2026-08-31-wiki-body-schema-example.md) 참고).

## 남은 미해결 질문

(없음 — 전부 확정. 다음 단계: `superpowers:writing-plans` 로 세부 구현 계획 작성)
