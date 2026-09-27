# 위키 파이프라인 복구 보고서

초안 작성일: 2026-09-24 UTC
최종 검증일: 2026-09-27 UTC

## 요약

로컬 세션에서 진행하던 위키 파이프라인 작업을 현재 작업 브랜치로 이어받아 검증했다. 핵심 흐름은 다음과 같다.

- 수집 이벤트의 원문(`body_raw`)을 먼저 기사 캐시에 승격한다.
- 원문이 없는 링크만 허용 호스트·robots·재시도 정책에 따라 HTTP 수집한다.
- 뉴스 라벨링은 일반 에이전트 모델 설정을 상속하지 않고 전용 설정을 사용한다.
- ChatGPT 계정에서 거부되는 실험용 `gpt-5.6-*` 명시 모델은 Codex CLI 기본 모델로 폴백한다.
- 위키 마크다운 산출물과 QMD 검색 인덱스를 전체 문서 기준으로 재생성한다.

## 런타임 점검 결과

점검 대상은 공유 World Memory와 `/home/ubuntu/reports/source-cache`다.

| 항목 | 결과 |
| --- | ---: |
| 위키 페이지 | 3,900 |
| source_digest 페이지(활성) | 1,629 |
| source_digest 페이지(전체, archived 포함) | 1,670 |
| 판단 카드로 연결된 source_digest | 1,554 |
| 의도적으로 승격하지 않은 source_digest | 113 |
| 재처리 가능한 미연결 후보 | 0 |
| 위키 인용 고유 URL | 2,428 |
| 기사 링크 큐(전체) | 2,517 |
| 기사 본문 준비 완료(큐 전체) | 2,369 |
| raw source-cache에서 네트워크 없이 승격 | 2,269 |
| 인용 URL 기준 접근 차단 | 100 |
| 인용 URL 기준 본문 추출 불가 | 2 |
| 인용 URL 기준 재시도/실패 | 12 |
| 인용 URL 기준 누락 | 0 |
| 원문 컨텍스트가 붙은 위키 페이지 | 3,600+ |
| QMD 문서 수 | 3,867 |
| QMD 인덱스 | 최신 / 검색 가능 |

raw import는 47개 이벤트 파일, 349,465행을 훑었고, 2,259건을 네트워크 요청 없이 기사 캐시에 등록했다. 이후 원문 캐시에 없던 공개 링크는 수정된 HTML 파서와 bounded crawler로 재시도했다. 인용 URL 기준으로 누락은 0건이며, 98건은 robots/Cloudflare 차단, 2건은 읽을 수 있는 본문 부재, 5건은 Kalshi의 HTTP 429로 재시도 한도에 도달했고 7건은 다음 재시도를 기다리는 상태로 명시적 상태에 보존했다. 큐 전체에는 현재 시점에 인용 관계가 끊긴 과거 URL과 ready 재검증 대기 항목이 포함될 수 있으므로, 운영 지표는 큐 전체가 아니라 위키 인용 URL 기준 커버리지를 우선한다. 접근 제한을 무시하거나 CAPTCHA를 우회하지 않았다.

`reports.wiki_article_backfill.reference_coverage()`가 모든 위키 페이지의 HTTP 출처를 다시 수집해 `missing`, `ready`, `blocked`, `unavailable`, `retry`, `failed`를 출처별로 집계한다. `blocked`와 `unavailable`은 재시도 대상이 아닌 감사 가능한 terminal 결과이며, `missing`·`pending`·`retry`·`failed`·본문이 없는 `ready`는 미해결로 표시한다. 기본 allowlist는 현재 위키에 실제 등장하는 SaveTicker, Arca, Telegram, Kalshi, Polymarket, Yahoo Finance, SEC, FRED, World Government Bonds 도메인으로 한정하고 새 호스트는 환경변수로 명시적으로 추가해야 한다.

이후 로컬 원문 기반 증류를 배치로 수행해 기존 문서와 새 수집 문서를 판단 카드에 연결했다. 외부 모델 호출은 사용하지 않았으며, source_digest의 다이제스트·보관 원문 발췌·출처 ID만 사용한 `draft` 카드로 저장했다. 현재 판단 카드는 risk 1,254건, playbook 796건, concept 178건이며, 단일 이벤트·일정 공지처럼 재사용 가능한 판단으로 일반화할 근거가 부족한 93건은 `skipped`로 보존했다. 이는 원문을 삭제하거나 사실을 추론해 채우지 않기 위한 의도적 보류다.

## 모델 라우팅 문제

기존 뉴스 라벨러가 `AGENT_CONSOLE_CODEX_MODEL`을 상속하면서 `gpt-5.6-luna-900k`를 직접 전달했고, ChatGPT 계정에서 `model is not supported`로 종료됐다. 변경 후에는 다음 규칙을 적용한다.

- `NEWS_LLM_LABELS_CODEX_MODEL`만 뉴스 라벨러의 명시 모델로 사용한다.
- 명시 값이 `gpt-5.6` 접두사면 `--model`을 생략해 CLI 기본 모델을 사용한다.
- 다른 모델 명시는 그대로 유지한다.

이렇게 하면 일반 대화 에이전트의 실험용 모델 설정이 뉴스 라벨링 배치까지 오염시키지 않는다.

## 위키 품질 및 검색

최종 lint는 2,782건이며, 열린 질문·미사용 후보·고립 페이지·부정 피드백은 품질 큐로 남겨두었다. `missing_cross_ref`는 908건으로 제한했고, source digest가 포함된 provenance 공유는 병합 후보에서 제외했다. 명시 링크 자체는 자동 병합하지 않았다.

관계 감사에서 source digest 간 명시 엣지와 병합 이력 없는 활성→아카이브 엣지를 제거했다. 같은 URL·이벤트의 원문은 각 페이지의 `source_refs`와 기사 캐시에 그대로 남아 있다. 병합 메타데이터가 있는 아카이브와 병합 이력은 보존했고, 활성 답변 컨텍스트의 백링크에서는 archived 페이지를 확장하지 않도록 했다.

추가로 과거 증류 실행에서 서로 다른 종목 문서가 같은 `distill-*` ID를 공유한 22개 충돌 그룹(54개 행, 추가 32개 문서)을 발견했다. 첫 행의 기존 ID는 호환성 앵커로 보존하고 나머지 32개 문서는 `distill-repair-*`로 원자적으로 분리했으며, source digest의 링크와 `last_result_id`도 종목별 카드로 복원했다. 복구 후 위키는 3,900행·3,900 고유 ID, QMD는 3,900파일로 일치한다. 중복·자기 링크·dangling link·source digest 간 잔여 정리 후보는 모두 0개다.

그래프는 명시 링크를 우선하고, 태그·출처 기반 추론 연결은 그룹 크기 제한과 노드당 최대 8개 이웃 제한을 적용한다. 자기 링크·없는 대상·중복 쌍은 그래프에 넣지 않으며, 검색용 QMD에는 위키 문서만 미러링한다.

최종 QMD 동기화 후 `file_count=3900`, `wiki_record_count=3900`, `duplicate_wiki_id_count=0`, `index_fresh=true`, `coverage_ok=true`, `mirror_complete=true`, `missing_file_count=0`, `stale_file_count=0`, `query_ok=true`를 권한 허용 런타임에서 확인했다. orphan·zero-usage·negative feedback은 별도 큐레이션 대상으로 남겼으며, 원문과 위키 페이지를 삭제하거나 무리하게 병합하지 않았다.

## LLM 증류 경계

최종 큐레이션 상태는 다음과 같다.

- source_digest 1,624건
- 판단 카드 연결 1,549건
- 의도적 스킵 93건
- 재처리 가능한 미연결 0건
- 외부 LLM 증류 실패 상태가 남은 문서 208건은 원문 보존과 별도로 상태를 기록하며, 현재 source_digest 승격 큐의 재처리 대상에는 포함되지 않는다.

추가 증류는 기사 원문을 외부 모델 프로세스에 전달할 수 있으므로, 기본 경로에서는 실행하지 않는다. 이번 세션은 데이터 반출 없이 로컬 source-only fallback을 사용했다. 카드 본문은 검색 가능한 위키 초안이며, 원문에 없는 인과관계·매매 처방·미래 예측을 만들지 않는다. HTML 파서의 속성 없는 태그 처리 오류도 수정해 Yahoo·SEC·Telegram 등 접근 가능한 공개 원문을 큐에 반영했다.

## 검증

현재 브랜치에서 다음 테스트를 통과했다.

- 뉴스 라벨 전용 모델 라우팅 테스트: 25 passed
- 기사 캐시 raw import 테스트: 1 passed
- 위키 raw cache backfill 테스트: 1 passed
- 계획의 기존 수집·운영·증류·헬스 focused suite: 111 passed

로컬 fallback·배치 저장·소진 실패 복구·HTML 파서·선택 URL crawler·린트 회귀 테스트와 공개 출처 allowlist·URL 커버리지 헬스 회귀 테스트를 포함한 관련 focused suite를 재실행해 258 passed를 확인했다. 권한 허용 환경의 전체 suite는 `2,419 passed, 1 skipped`까지 진행됐고, 기존의 delivery interpreter/matplotlib 환경 의존 실패와 dead ticker fixture 2건이 확인된 뒤 장시간 LightGBM 테스트에서 중단했다. 이 변경 범위의 compileall과 focused suite는 통과했다.
