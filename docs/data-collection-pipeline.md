# 데이터 수집 · World Memory · 위키 파이프라인

> 뉴스 공급자 전환: 2026-11-15 재반영(저장소·운영 코드 동시 수정). 나머지 파이프라인 점검 기준: 2026-08-21.

이 문서는 "뭐가 얼마나 자주 도는지 · 어디서 원본이 얼마나 보존되는지 · LLM이 어디를 언제 건드리는지"를 한 곳에 모은 운영 참조다. 각 컴포넌트의 세부 계약(디렉토리 구조, 신뢰 등급, 쓰기 규약)은 중복 설명하지 않고 [`agent-console.md`](agent-console.md), [`shared-agent-memory.md`](shared-agent-memory.md)를 참조한다 — 이 문서는 **빈도·경로·최근 변경**에 집중한다.

## 1. 원본 수집 (raw collection)

| 스크립트 | 빈도 | 대상 | 비고 |
|---|---|---|---|
| `reports/source_pipeline.py --group news` | **30분마다** (`5,35`) | Investing.com 한국어 RSS·Yahoo 종목별 RSS·기존 RSS·telegram | 제공자별 동시 실행·실패 격리. arca·SaveTicker 뉴스는 수집하지 않음 |
| `reports/source_pipeline.py --group market` | **30분마다** (`7,37`) | yahoo_finance | 전 종목 batch 다운로드 후 누락 티커만 fallback |
| `reports/source_pipeline.py --group prediction` | **30분마다** (`9,39`) | Polymarket·Kalshi | 확률을 공급자별로 분리 저장. 한 곳만 정상이면 degraded 성공 |
| `reports/source_pipeline.py --group calendar` | **매시간** (`11`) | saveticker 경제 캘린더 | 향후 14일 일정·중요도 정규화 |
| `reports/source_pipeline.py --group macro` | **6시간마다** (`15 0,6,12,18`) | FRED·worldgovernmentbonds | 저빈도 거시 스냅샷 |
| `crons/news_spike_detector.py` | **매 1분** (`* * * * *`) | 저장된 RSS·Yahoo 뉴스 | `속보` 태그 또는 `[속보]` 제목만 중요도 채점·텔레그램 발송 |

속보 감지기는 캐시만 읽으며 공급자를 추가 호출하지 않는다. 새 뉴스의 유입 주기는 30분이며, 1분 주기는 저장 이후의 감지 주기다.

### SaveTicker 뉴스 API 종료에 따른 전환

- 기존 뉴스 API는 `legacy-news-sunset-notice`만 반환하므로 뉴스 fetch/parse 코드를 제거했다. 경제 캘린더(`providers/econ_calendar.py`)와 리포트 PDF 아카이브(`crons/saveticker_report_archive.py`)는 그대로 둔다.
- Arca Live 주식 채널은 2026-11-14부터 모든 페이지가 Cloudflare challenge 403이고, `r.jina.ai` 미러는 채널 헤더만 반환한다. 접근 통제를 우회하지 않으므로 수집·프록시·리포트 섹션을 제거했다. 이미 저장된 `source=arca`/`saveticker` 이벤트는 라벨·렌더·헬스의 `availability=disabled` 스킵으로 계속 읽는다.
- `/save`와 옛 인라인 버튼은 종료 안내만 보낸다. 원격 브라우저 복구 모듈은 삭제했다. 운영 crontab에 뉴스 복구 워커 항목이 남아 있으면 제거해야 한다. 이 저장소의 `deploy/crontab.stock-report`에는 해당 항목이 없다.
- [Investing.com 공식 RSS 목록](https://kr.investing.com/webmaster-tools/rss)의 주식 `news_25.rss`, 경제 `news_14.rss`를 별도 제공자로 수집한다. 소스 키는 `rss:investing_kr_stock`, `rss:investing_kr_economy`다.
- Yahoo는 개인별 최근 조회 페이지 대신 공개 종목별 RSS(`feeds.finance.yahoo.com/rss/2.0/headline`)를 사용한다. 소스 키는 `yahoo_headline`이다.
- RSS는 제목·제공된 요약·원문 링크만 보장한다. `body_coverage=partial`, `text_complete=false`이며 기사 전문 복구로 표시하지 않는다.
- 이전 뉴스 복구 타이머는 중지·비활성화하고 복구 상태는 `retired`로 전환한다. `/save status|open|check` 및 이전 버튼은 대체 공급자 안내만 표시한다.
- 종료된 소스의 마지막 성공 시각은 보존하고 `availability=disabled`와 전환 사유를 기록한다. 새 공급자의 실패·수집 지연은 계속 경고한다.
- [Naver 뉴스 검색 API](https://developers.naver.com/docs/serviceapi/search/news/news.md)는 별도 Client ID/Secret이 필요해 연결하지 않았다. [TradingView 정책](https://www.tradingview.com/policies/)의 자동 수집·비표시 이용 제한에 따라 해당 페이지는 수집하지 않는다.
- 11월 13일 운영 전환 뒤 다른 코드 반영으로 뉴스 registry가 되돌아간 사례가 있었다. `collect_once()`도 `default_registry()`를 사용하도록 통일하며, 배포 시 저장소의 공급자 변경과 운영 전용 복구 모듈 변경을 함께 보존해야 한다.
- 11월 15일 운영 검증: Investing 20건·Yahoo 47건 수집, 신규 21건 저장. 소스 수집 헬스체크 경고 없음, 종료 안내 텔레그램 발송 성공, 복구 타이머 `inactive/disabled`. 반영 전 파일·상태와 적용 패치는 `hermes:/home/ubuntu/backups/stockbot-provider-cutover-20261115T084814Z/`에 보존한다.
- 11월 15일 후속 수정 (백업 `hermes:/home/ubuntu/backups/stockbot-followup-fix-20261115T104518Z/`):
  - 격리 워커(`reports/source_provider_worker.py`)가 자식 프로세스의 소스별 오류·가용성 기록(`_LAST_ERRORS`, `_SOURCE_AVAILABILITY`)을 응답에 실어 보내고, 부모(`source_pipeline._mirror_worker_source_state`)가 해당 제공자의 소스 키에만 반영한다. 이전에는 일부 피드만 실패해도 `available`·오류 없음으로 기록됐다. 반영 직후 `rss:sec`(시간 초과)가 실패로 기록됐다. 같은 날 arca는 Cloudflare 403이 지속돼 수집 대상에서 뺐다.
  - 종목 식별자는 거래소 접미사만 뗀다. 뉴스 라벨·World Memory는 `providers.news_labels._ticker_base`(`.KS/.KQ/.T/.HK/.L`), 시세 캐시 키는 `providers.intraday_bars.base_symbol`(`.KS/.KQ`)을 쓴다. `BRK.A`·`BRK.B`를 `BRK`로 접던 `split(".")[0]`은 쓰지 않는다.
  - Yahoo 종목별 RSS에서 같은 기사가 여러 종목에 나오면 `tickers`에 모두 담는다. 이미 저장된 단일 종목 행은 다시 쓰지 않는다.
  - `/save`와 이전 버튼 콜백은 종료 안내만 하도록 저장소에 반영했다.

### 텔레그램 공개 채널 (2026-11-15 확대)

운영 반영 2026-11-15 14:07 UTC, 백업 `hermes:/home/ubuntu/backups/stockbot-tg-frac-fix-20261115T140725Z/` (원본 파일·`change.patch`·`proof.json`). 같은 반영에 아카라이브·SaveTicker 뉴스 수집 제거와 키움 해외 거래내역 동기화가 들어 있다.

- 로그인 없이 공개 미리보기 `https://t.me/s/<채널>` 만 읽는다. 직접 HTML 을 먼저 받고, 비어 있을 때만 `r.jina.ai` 미러로 제목을 받는다. 9개 채널을 차례로 받으므로 `telegram` 제공자 제한 시간은 90초다(실측 약 12초).
- 기본 채널(`_DEFAULT_TELEGRAM_CHANNELS`, `STOCK_COLLECTOR_TG_CHANNELS` 로 교체 가능):

| 채널 | 성격 |
|---|---|
| `insidertracking` | 미국 주식 속보·레딧 분석 |
| `FastStockNewsUSA` | 미국 뉴스 속보 |
| `kwusa` | 키움증권 미국주식 |
| `mirae_dm` | 미래에셋 선진국 전략 |
| `Samsung_Global_AI_SW` | 삼성증권 글로벌 AI/SW |
| `daishinstrategy` | 대신증권 전략·시황 |
| `yuantais` | 유안타증권 투자전략 |
| `shStrategy` | 신한투자증권 주식전략·시황 |
| `thekobeissiletter` | 영문 매크로 코멘트 |

- `yuzukinaok1` 은 2026-06-30 이후 새 글이 없어 뺐다. 하루 수백 건씩 올리는 채널(`hankyung_fin`, `marketfeed`, `sedaily_industry_company`, `firstsquaw`)과 `kwusa` 와 내용이 겹치는 `KiwoomResearch` 는 넣지 않았다.
- 제공자는 자기 소스 키(`telegram:<채널>`)의 이벤트만 저장한다. 채널을 빼면 그 채널 이벤트는 저장되지 않고, 헬스 기록은 `availability=disabled` 로 바꿔 공백 경보에서 제외한다.

### 키움 해외 거래내역 → 소수점 보유·체결 원장 (`crons/kiwoom_sync_rest.py`, 평일 23:35)

- 잔고 조회(`ust21070`)는 온주만 돌려준다. 소수점 보유(`portfolio_snapshot.json` 의 `overseas_fractional`)는 거래내역(`ust21100`) 누적으로 만든다. 2026-11-15 이전에는 손으로 넣은 값이라 매도·온주 전환이 반영되지 않았다.
- 거래내역은 종류가 `deal_kind_nm`(`매매`/`소수점매매`/`입출고`/`입출금`), 방향이 `rmrk_nm`(`매수`/`매도`/`대체출고`/`대체입고`/`대체온주전환출고`/`대체온주전환입고`)에 온다. 조회 기간은 1년 이내, 한 번에 20행이며 응답 헤더 `cont-yn`/`next-key` 로 이어 받는다. `deal_no` 는 날짜별 일련번호라 `(deal_dt, deal_no)` 가 고유 키다.
- 전체 이력은 store 컬렉션 `kiwoom_us_transactions` 에 캐시한다. 첫 실행은 계좌 개설 시점까지 거슬러 받고, 이후에는 마지막 캐시 날짜 14일 전부터 다시 받아 그 구간을 교체한다.
- 거래내역으로 만든 온주 수량이 잔고와 한 주라도 다르면 이력이 불완전한 것으로 보고 소수점 보유를 덮어쓰지 않는다(전체 재조회 1회 후에도 다르면 텔레그램 알림).
- 체결(`매매`·`소수점매매`의 매수·매도)은 `event_id=kiwoomus:<날짜>:<거래번호>` 로 체결 원장에 남는다. 최근 14일 구간만 기록하고 과거 이력은 채우지 않는다. 소수점 매수는 `crons/accumulate_daily.py` 가 종가 기준으로 먼저 적으므로(`manual_holding`), 같은 종목에 하루 이내의 그 기록이 있으면 건너뛴다. 잔고 수량 변화로 추정하던 기록(`kiwoom_sync`)은 중복이라 해외 쪽에서 껐다.

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

종료된 SaveTicker 뉴스 수집이 같은 기사를 API 응답에 남아 있는 동안 폴링마다 원본을 재아카이브하던 문제. 발견 당시 **3일 만에 raw 40만 파일/2.8G**, 기사 하나가 198번 중복 저장된 사례도 있었음. `reports/raw_archive.py`의 크로스런 dedup 인덱스(`load_dedupe_index`/`save_dedupe_index`, 최근 14일 내 아카이브한 기사는 재저장 생략)는 남아 있는 소스에 그대로 적용된다. 텔레그램은 아직 이 dedup이 안 걸려 있다.

### 알려진 이슈

- **Polymarket Gamma는 현재 서버 리전에서 HTTP 451**이다. 고정 upstream·read-only relay는 `deploy/polymarket_relay`에 있으며, `POLYMARKET_RELAY_URL/TOKEN`을 설정해야 활성화된다. relay도 451이면 blocked 상태를 유지하고 Kalshi만 사용한다.
- **Arca와 SaveTicker 뉴스는 영구 제외**다. 저장된 이벤트 TTL·라벨은 유지하고, 새 수집은 하지 않는다.
- Kalshi 전체 시장 피드에서 관련 시장이 0건이면 `/series`의 카테고리·태그·거래량으로 관련 series를 찾고, 최대 12개 series를 동시성 4로 조회한다.

## 2. World Memory 적재 경로

World Memory(`lib/world_memory.py`, SQLite `world_issue_log.sqlite3`)로 들어가는 길은 4갈래, 빈도가 전부 다르다.

| 경로 | 소스명(`payload.source`) | 빈도 | 트리거 |
|---|---|---|---|
| LLM 구조화 라벨링 | `news_llm_label` | **시간당 1번** (평일 매시 05분, 2026-07-24부터) | `crons/news_llm_snapshot.py`. §4 참고 |
| 속보 알림(중요도 7+) | `news_spike` | **캐시 저장 후 ~1분** (수집은 30분 주기) | `crons/news_spike_detector.py` — 발송 성공한 속보만 `log_issue()`로 영구 기록 |
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

1. **매 1분** — `news_spike_detector.py`(공개 뉴스 캐시의 명시적 속보), (참고: 시세 폴러/워치독류는 이 문서 범위 밖)
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
