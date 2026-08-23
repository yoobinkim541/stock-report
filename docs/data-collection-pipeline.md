# 데이터 수집 · World Memory · 위키 파이프라인

> 최종 확인: 2026-08-21 (코드·운영 캐시·QMD 실제 질의 점검 기준)

이 문서는 "뭐가 얼마나 자주 도는지 · 어디서 원본이 얼마나 보존되는지 · LLM이 어디를 언제 건드리는지"를 한 곳에 모은 운영 참조다. 각 컴포넌트의 세부 계약(디렉토리 구조, 신뢰 등급, 쓰기 규약)은 중복 설명하지 않고 [`agent-console.md`](agent-console.md), [`shared-agent-memory.md`](shared-agent-memory.md)를 참조한다 — 이 문서는 **빈도·경로·최근 변경**에 집중한다.

## 1. 원본 수집 (raw collection)

| 스크립트 | 빈도 | 대상 | 비고 |
|---|---|---|---|
| `reports/source_pipeline.py --group news` | **30분마다** (`5,35`) | saveticker·telegram·arca | 제공자별 동시 실행·실패 격리 |
| `reports/source_pipeline.py --group market` | **30분마다** (`7,37`) | yahoo_finance | 전 종목 batch 다운로드 후 누락 티커만 fallback |
| `reports/source_pipeline.py --group prediction` | **30분마다** (`9,39`) | Polymarket·Kalshi | 확률을 공급자별로 분리 저장. 한 곳만 정상이면 degraded 성공 |
| `reports/source_pipeline.py --group calendar` | **매시간** (`11`) | saveticker 경제 캘린더 | 향후 14일 일정·중요도 정규화 |
| `reports/source_pipeline.py --group macro` | **6시간마다** (`15 0,6,12,18`) | FRED·worldgovernmentbonds | 저빈도 거시 스냅샷 |
| `crons/news_spike_detector.py` | **매 1분** (`* * * * *`) | saveticker만 (`fetch_saveticker_events()` 직접 호출 — source_collector와 같은 함수 공유) | 속보(`속보` 태그) 감지·중요도 채점·텔레그램 발송 전용. §3 참고 |

두 크론이 **같은 `fetch_saveticker_events()` 함수를 공유**하기 때문에 saveticker API는 사실상 최대 1분 간격으로 폴링된다.

### raw 아카이브 (`~/reports/raw`, `~/reports/text`) — 소스별 TTL

`reports/raw_archive.py: RAW_TTL_DAYS_BY_SOURCE`

| 소스 | TTL |
|---|---|
| saveticker_report_pdf | 180일 |
| saveticker_article / saveticker | 60일 |
| yahoo_finance / fred / worldgovernmentbonds | 30일 (기본값) |
| telegram | 14일 |
| arca | 7일 |

청소는 `crons/raw_archive_cleanup.py`가 담당 — **2026-07-24에 크론탭 등록**(그 전엔 코드만 있고 실행이 안 돼서 무기한 누적되고 있었음). 매일 20:45 UTC(05:45 KST) 실행, `cleanup_expired_raw_artifacts()`가 각 아카이브 파일에 저장된 `expires_at`(저장 시점 + 소스별 TTL) 기준으로 삭제한다.

### 중복저장 버그 수정 (2026-07-24)

`fetch_saveticker_events()`가 같은 기사가 API 응답(top-stories/list)에 남아있는 동안 폴링마다(최대 1분 간격) 원본을 재아카이브하던 문제. 발견 당시 **3일 만에 raw 40만 파일/2.8G**, 기사 하나가 198번 중복 저장된 사례도 있었음. `reports/raw_archive.py`에 크로스런 dedup 인덱스(`load_dedupe_index`/`save_dedupe_index`, 최근 14일 내 아카이브한 기사는 재저장 생략)를 추가해 수정. 텔레그램·아르카는 아직 이 dedup이 안 걸려 있음(현재 볼륨이 작아 시급하지 않았을 뿐, 이론상 같은 패턴 재발 가능).

### 알려진 이슈

- **Polymarket Gamma는 현재 서버 리전에서 HTTP 451**이다. 고정 upstream·read-only relay는 `deploy/polymarket_relay`에 있으며, `POLYMARKET_RELAY_URL/TOKEN`을 설정해야 활성화된다. relay도 451이면 blocked 상태를 유지하고 Kalshi만 사용한다.
- **Arca 직접 fallback은 간헐적으로 403**이다. 다른 뉴스 수집을 막지는 않으며 source health에 별도 표시한다.
- Kalshi 전체 시장 피드에서 관련 시장이 0건이면 `/series`의 카테고리·태그·거래량으로 관련 series를 찾고, 최대 12개 series를 동시성 4로 조회한다.

## 2. World Memory 적재 경로

World Memory(`lib/world_memory.py`, SQLite `world_issue_log.sqlite3`)로 들어가는 길은 4갈래, 빈도가 전부 다르다.

| 경로 | 소스명(`payload.source`) | 빈도 | 트리거 |
|---|---|---|---|
| LLM 구조화 라벨링 | `news_llm_label` | **시간당 1번** (평일 매시 05분, 2026-07-24부터) | `crons/news_llm_snapshot.py`. §4 참고 |
| 속보 알림(중요도 7+) | `news_spike` | **~1분 이내** | `crons/news_spike_detector.py` — 발송 성공한 속보만 `log_issue()`로 영구 기록 |
| 대시보드 수동 입력 | `dashboard:manual:*` | 즉시 (제출 시) | AI 콘솔 "시장 기억" 탭 폼 |
| 최근 이벤트 일괄 적재 | `console:*` | **수동 트리거만** (자동 스케줄 없음) | "메모리 적재" 버튼 또는 `POST /api/memory/ingest` 직접 호출 (`agent_console/context.py: ingest_recent_memory`) |

### body 보존 길이 (2026-07-24 확대)

원본 → World Memory 표시까지 여러 단으로 잘림. 전부 4000자로 통일(이전엔 단계별로 제각각이라 저장은 늘려도 화면엔 짧게 보이는 문제가 있었음):

| 지점 | 파일 | 이전 | 현재 |
|---|---|---|---|
| 1차 컷(전 소스 공통) | `reports/source_collector.py: _classify_event()` | 2200 | 4000 |
| 저장 컷 | `lib/world_memory.py: log_issue()` | 1200 | 4000 |
| **읽기/표시 컷** (실제 UI 병목이었음) | `lib/world_memory.py: _rows_to_issues()` | 200 | 4000 |
| 사전 컷(중복) | `agent_console/context.py`, `agent_console/migrate_memory.py` | 1200 | 4000 |

4000은 임의값이 아니라 기존에 있던 텔레그램 안전 발송 한계(`notify.py: TG_MAX_CHARS=4000`)에 맞춘 것. `news_llm_labels.jsonl`(라벨 영구 아카이브, append-only·삭제 금지)에 들어가는 body는 이 컷과 별개로 **원본 그대로 보존**(`providers/news_labels.py`가 LLM 산출물이 아니라 신뢰된 원본 이벤트에서 직접 가져옴 — 환각 검증 목적).

World Memory UI(대시보드 "시장 기억" 탭)는 검색(제목·본문·티커, FTS5)과 행 클릭 시 본문 상세보기를 지원한다(`dashboard/pages/ai_console.py: _memory_tab`/`_memory_detail`, 2026-07-24 추가).

## 3. 뉴스 LLM 라벨링 (`crons/news_llm_snapshot.py`)

- **시간당 1번**, 평일만: 매시 05분 (2026-07-24부터 — 이전엔 하루 2번(00:05·14:05 UTC, KR/US 모의 결정 직전)이었으나, 뉴스 많은 날 회당 상한(30건)에 걸려 다음 배치까지 밀리는 경우가 있어 변경)
- 회당 최대 30건(`NEWS_LLM_LABELS_MAX`)만 LLM 배치 호출 — 비용 통제. 배치가 잘게 나뉘어도 하루 총 처리량은 대체로 비슷 — 상한 여유(하루 최대 720건)만 커짐
- 출력 라벨(`{티커, 이벤트유형, 방향, 강도}`)은 `{us,kr}_mock_track`의 `news_axis` 피처가 되며, 기본 가중치 0(관찰 전용) — 주간 학습의 신규 축 게이트(최소 20쌍+안정성)를 통과해야 실제 가중치가 붙는다
- `issue_date`는 **기사의 실제 `published_at` 기준**(point-in-time, look-ahead 방지) — 라벨링이 늦게 일어나도 World Memory엔 원래 발행일로 귀속됨. 그래서 "이번 라벨링 배치 결과"가 꼭 "방금 발행된 기사"는 아님.
- 속보(`news_spike`, §2)와는 완전히 다른 경로 — 이쪽은 즉시성보다 구조화 품질/비용 통제가 목적
- 2026-08-21 이후 라벨은 `label_method=llm|heuristic`, provider/model, 실패 이유, 생성 시각을 기록한다. 이전 1,192건은 provenance가 없어 `legacy-untracked`이며 소급 추정하지 않는다.

## 4. 위키 LLM 로직

뉴스 라벨링과 달리 위키는 **정보가 쌓이는 대로 계속 정리**하는 설계라 훨씬 자주 돈다.

| 무엇을 | 스크립트/함수 | 빈도 | LLM 사용 |
|---|---|---|---|
| 페이지 신규 생성/갱신 + QMD 전체 동기화 | `reports/source_wiki_curator.py` | **30분마다** (`12,42 * * * *`, `--limit 0`=무제한) | O — 저장 후 전체 위키 Markdown export와 `qmd update` 1회 실행 |
| 스테일 페이지 아카이브 | `agent_console.wiki.archive_stale_pages()` | 30분마다 (`14,44 * * * *`) | X — 규칙(30일 이상 미사용 시 자동 아카이브) |
| 헬스체크 기반 archive/delete/reactivate | `reports/wiki_health_check.py` | **2시간마다** (`15 */2 * * *`) | O — `reports/wiki_pipeline_health.py` 구조화 리포트를 바탕으로 `run_llm_health_review()`가 판단 |
| 대화 중 merge/split/delete/create | `agent_console/wiki.py: auto_curate_from_chat()` | **크론 아님 — AI 콘솔 채팅마다 즉시** | O |

위키 쓰기 규약(신뢰 등급, `verification_status`, source-backed 승격 조건 등)은 [`shared-agent-memory.md`의 "LLM Wiki 운영 규약"](shared-agent-memory.md) 참조.

> **2026-07-25 / 2026-07-31**: `source_wiki_curator`가 매 실행마다 이벤트 개수 상위 N개 그룹만 저장하던
> `--limit 8` 캡을 제거함(`--limit 0`=무제한). 소수 거시 주제(중동/전쟁·기술/AI 등)가
> 이벤트 수에서 항상 상위권을 독점해, 종목별/유형별 신규 페이지가 하루 넘게 하나도
> 저장되지 못하는 문제가 있었음 — LLM enrich 비용은 그룹 계산 단계에서 이미 지불되므로
> 저장 캡 제거의 추가 비용은 거의 없음. 2026-07-31에는 source digest 본문 길이와 후속 질문
> 섹션을 늘려 지식 자산화 밀도를 높이고, `wiki_distillation` 주기를 6시간마다로 올려
> 판단 카드 승격을 하루 4회 이상 시도하도록 바꿨다.

### QMD 및 활용 헬스

- QMD 헬스는 설치 여부가 아니라 실제 검색 명령 성공(`query_ok`)과 최신 위키 대비 Markdown 시각(`index_fresh`)을 검사한다.
- 2026-08-21 점검 당시 공유 위키 최신 갱신은 8월 21일, QMD export는 8월 5일로 멈춰 있었다. 새 동기화 경로는 이를 장애로 표시하고 다음 성공한 curator 실행에서 전체 재생성한다.
- 위키 검색과 프롬프트 포함은 질문 원문 없이 해시 query id와 page id만 `evidence_usage.jsonl`에 기록한다. `retrieval_to_context_ratio`와 fallback 비율로 실제 사용 여부를 확인한다.

## 5. 전체 크론 빈도 한눈에

빈도가 짧은 순:

1. **매 1분** — `news_spike_detector.py`(saveticker 속보), (참고: 시세 폴러/워치독류는 이 문서 범위 밖)
2. **30분마다** — news/market/prediction 독립 수집, `source_wiki_curator.py`, `archive_stale_pages`
3. **6시간마다** — `wiki_distillation.py`(source_digest → playbook/risk/concept 판단카드)
4. **시간당(평일)** — `news_llm_snapshot.py`(뉴스 구조화 라벨링, 2026-07-24부터 하루 2번에서 변경)
5. **2시간마다** — `wiki_health_check.py`(LLM 헬스 리뷰 · 구조화된 파이프라인 헬스 리포트 사용)
6. **매일** — `raw_archive_cleanup.py`(20:45 UTC, TTL 청소)
7. **트리거 기반(스케줄 없음)** — `auto_curate_from_chat()`(채팅마다), `ingest_recent_memory`(버튼/API 수동 호출)

전체 크론 단일 진실원은 `deploy/crontab.stock-report`(적용은 `crontab deploy/crontab.stock-report`) — 이 문서의 빈도는 그 파일에서 파생된 것이니, 크론탭이 바뀌면 이 표도 같이 바뀌어야 한다.

운영 반영 후 `.venv/bin/python scripts/check_crontab_drift.py`로 설치 크론과 저장소 파일의 차이를 확인하고, `.venv/bin/python scripts/probe_prediction_sources.py`로 공급자별 availability/count/transport를 확인한다.

## 유지보수

이 문서는 스냅샷이다. 아래 중 하나라도 바뀌면 **같은 커밋에서 이 문서도 갱신**한다(CLAUDE.md "Documentation sync" 규칙과 동일):

- `deploy/crontab.stock-report`의 수집/라벨링/위키 관련 스케줄
- `RAW_TTL_DAYS_BY_SOURCE`(`reports/raw_archive.py`)
- World Memory 적재 경로 추가/제거, 또는 body 길이 컷
- 위키 LLM 자동화 로직(§4 표의 스크립트/함수)
