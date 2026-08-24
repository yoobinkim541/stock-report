# 프로젝트 구조 — 기능별 찾아보기

이 문서는 "어떤 기능을 고치려면 어디를 봐야 하나"에 답하기 위한 지도다. 전체 아키텍처(6계층
데이터 흐름·엔지니어링 하이라이트)는 [`README.md`](../README.md)를 먼저 보는 게 낫다 — 여기는
그 다음 단계, 실제 파일을 찾는 용도.

> ⚠️ **파일을 폴더 간에 물리적으로 옮기지 않는다.** 이 저장소는 라이브 크론(`deploy/crontab.stock-report`,
> 사람 1명당 crontab 1개, 하루 종일 실행 중)이 파일 경로를 문자열로 하드코딩하고, 루트급 공용
> 모듈(`notify`·`store`·`safe_io` 등)은 각각 9~38개 파일에서 `import notify` 식으로 바로 참조한다.
> 파일 이동은 import 수백 곳 + crontab 수십 줄을 **한 번에 정확히** 맞춰야 하고 어긋나면 그 순간부터
> 운영 중인 크론이 깨진다. 정리는 이 문서(+ 아래 폴더 구조)로 하고, 실제 이동은 하지 않는다.
> 라이브 crontab과 저장소 상태가 어긋났는지는 `python scripts/check_crontab_drift.py`로 확인한다.

---

## 폴더 지도

이미 기능별로 20개 이상 폴더가 나뉘어 있다. 루트에는 크론·봇이 직접 참조하는 공용 모듈만 남는다.

| 폴더 | 무엇이 있나 | 대표 하위 그룹 |
|---|---|---|
| `crons/` | 예약 실행 스크립트 — `deploy/crontab.stock-report`에서 개별 호출 | 모의투자 추적/학습, 스냅샷 수집, 리포트 아카이브, 리마인더 |
| `dashboard/` | Streamlit 퀀트 터미널(8페이지) | `dashboard/pages/*.py`(화면 단위) + `chart_*.py`(차트 엔진 다수) |
| `providers/` | 외부 데이터 소스 어댑터(가격·펀더멘털·뉴스·브로커 API) | KIS/키움 연동, 국내/해외 데이터, 실시간 시세 |
| `ml/` | 랭킹·백테스트·리스크 모델 | `ml/adaptive/`(적응학습 OOS 게이트 — ledger·reward·learner·champion_challenger) |
| `lib/` | 여러 곳에서 재사용하는 공용 헬퍼 | 관심종목, 캐시, 트랑셰, 모의 LLM 실행 |
| `bot/` | 텔레그램 명령어별 핸들러 | `*_commands.py`(보유·세금·매집·관심종목 등) |
| `agent_console/` | AI 위키·공유 메모리·QMD 지식 시스템 | `wiki.py`(코어), `qmd_search.py`, `shared_memory.py` |
| `backtest/` | 전략/정책별 백테스트 스크립트 | 정책·팩터·레버리지·사이드웨이 등 전략 1개당 파일 1개 |
| `reports/` | 리포트 생성 + 소스 수집/큐레이션 파이프라인 | 일일 리포트류, `source_*.py`(수집), `wiki_*.py`(위키 갱신), `institution_watch.py`(기관) |
| `scripts/` | 배포용 셸 스크립트 + 운영 보조 파이썬 | `*_watchdog.sh`(감시), `deliver_*.sh`(발송), `check_crontab_drift.py` |
| `deploy/` | 배포 산출물 | `crontab.stock-report`(크론 단일 진실원), `polymarket_relay/`(Vercel) |
| `data/` | 저장 데이터(시드·백업·공유 메모리) | `shared-memory/`, `backups/` |
| `config/` | 설정 스키마 | `shared-memory.schema.json` |
| `tests/` | pytest (전체 스위트 2700+, 무네트워크 위주) | 소스 파일명과 1:1 대응 (`test_<module>.py`) |
| `docs/` | 설계 문서·디버그 노트 | `docs/superpowers/`(계획·감사 문서), `docs/plans/` |
| `kiwoom_sync/` | 키움 Windows→Ubuntu 동기화 하위 모듈 | `kiwoom_sync.py` |

---

## 루트 파일 — 왜 여기 있나

크론·봇이 실행 시점에 바로 참조하거나, 여러 계층에서 공용으로 쓰는 "단일 진실원" 모듈이라
서브패키지로 옮기지 않고 루트에 유지한다.

**상시 프로세스 진입점**
| 파일 | 역할 |
|---|---|
| `telegram_bot.py` | 양방향 텔레그램 봇 — 명령 라우터 |
| `barbell_strategy.py` | Phase 전술배분·DCA·레버리지 가드 엔진 |
| `portfolio_sync_server.py` | 키움 Windows 잔고 동기화 수신 서버(:8765) |
| `kis_stream.py` | KIS 실시간 시세 read-only WebSocket 상시 프로세스 |
| `quotes_poller.py` | REST 시세 폴러(WS 커버 밖 롱테일용) |
| `app.py` | Flask 앱(경량 라우트) |

**공용 인프라(여러 계층에서 import)**
| 파일 | 역할 |
|---|---|
| `notify.py` | 텔레그램 발송 단일 진실원 |
| `safe_io.py` | 원자적 쓰기 + 교차 프로세스 파일 락 |
| `store.py` | user_id 스코프 SQLite 저장소 |
| `fmt.py` | 텔레그램 출력 공통 포맷(모바일 안전) |
| `ohlc_utils.py` | OHLC 시계열 정규화 |
| `portfolio_universe.py` | 보유 종목 단일 소스 |
| `ticker_names.py` | 티커 ↔ 회사명 단일 진실원(표시·검색) |

**브로커/모의투자 어댑터**
| 파일 | 역할 |
|---|---|
| `kiwoom_mock.py` | 키움 국내 모의투자 어댑터 (모의 도메인 하드락) |
| `kis_mock.py` | KIS 해외 모의투자 어댑터 (모의 도메인 하드락) |

**포트폴리오/세금 상태 관리**
| 파일 | 역할 |
|---|---|
| `portfolio_tracker.py` | 일일 포트폴리오 히스토리 + 배당 기록 |
| `holding_manager.py` | 보유종목 CRUD + DCA 비중(`/holding` 백엔드) |
| `tax_tracker.py` | 실현손익 기록·세금 추산 |

**표시용 정적 시드**
`etf_meta.py`·`kr200_meta.py`·`kr_etf_seed.py`·`sp500_meta.py`·`sp500_seed.py` — 검색·시장맵·비교에
쓰는 종목명/섹터/시총 큐레이트 데이터. 로직이 아니라 데이터 시드라 별도 폴더 없이 루트에 둔다.

---

## 기능별로 찾기

특정 기능을 고치고 싶을 때 어디를 봐야 하는지 — 폴더 경계를 가로질러 실제로 관련된 파일을 묶었다.

### 🇰🇷 국내 모의투자
`crons/kiwoom_mock_track.py`(일일 추적) · `crons/kr_mock_learn.py`(주간 학습) ·
`crons/kiwoom_mock_report.py` · `crons/kiwoom_mock_close_generation.py` ·
`kiwoom_mock.py`(루트, 어댑터) · `ml/kr_policy.py` · `ml/kr_ranker.py` · `lib/rank_shadow.py`(섀도 원장)

### 🇺🇸 미국 모의투자
`crons/us_mock_track.py` · `crons/us_mock_learn.py` · `crons/us_mock_report.py` ·
`kis_mock.py`(루트, 어댑터) · `ml/us_policy.py` · `ml/ranker.py`(LightGBM 랭커)

### ⚡ 단기(분봉) 모의투자
`crons/intraday_mock_track.py` · `crons/intraday_mock_learn.py` ·
`ml/intraday_*.py`(axes·experiment·lifecycle·policy·signal) ·
`providers/intraday_bars.py` · `providers/intraday_universe.py`

### 🧠 적응학습 / OOS 게이트 (모의투자 공용 엔진)
`ml/adaptive/` 전체 — `ledger.py`(불변 원장) · `reward.py`(★목적함수) · `learner.py` ·
`champion_challenger.py` · `evolution.py`(콜드스타트·무엣지 판정) · `costs.py` · `policy.py`

### 📈 차트 / 대시보드
`dashboard/pages/*.py`(화면) · `dashboard/chart_*.py`(렌더러·워크스페이스·리플레이 다수) ·
`dashboard/plotly_embed.py`(iframe 임베드 JS) · `dashboard/views.py`(뷰 조립) · `dashboard/cached.py`

### 💬 텔레그램 봇 명령어
`telegram_bot.py`(루트, 라우터) · `bot/*_commands.py`(보유·세금·매집·관심종목·실적·진입) ·
`bot/evolve_command.py`(모의 진화 리포트) · `bot/stock_advisor.py`

### 📰 일일 리포트 생성·발송
`reports/investment_report.py` · `reports/market_report.py` · `reports/combined_daily_report.py` ·
`reports/report_charts.py`(PNG 대시보드) · `scripts/deliver_*.sh`(발송 크론)

### 🔎 소스 수집 파이프라인
`reports/source_pipeline.py`(그룹별 독립 수집 진입점) · `reports/source_collector.py`(개별 공급자) ·
`reports/source_identity.py` · `reports/source_runs.py` · `reports/operational_events.py` ·
`reports/prediction_markets.py`(Polymarket/Kalshi) · `deploy/polymarket_relay/`

### 📚 위키 / 지식 시스템 (AI Console)
`agent_console/wiki.py`(코어) · `agent_console/qmd_search.py` · `agent_console/shared_memory.py` ·
`agent_console/evidence_usage.py` · `reports/source_wiki_curator.py`(수집→위키) ·
`reports/wiki_distillation.py`(판단카드 증류) · `reports/wiki_health_check.py` ·
`dashboard/wiki_browser.py` · `dashboard/wiki_mesh.py`

### 🏛 기관투자자 / 의회 거래 추적
`reports/institution_watch.py`(다기관 위키+신규편입 알림) · `reports/notable_investors_wiki.py`(레거시 shim) ·
`providers/thirteenf.py`(SEC 13F 파싱) · `providers/congress_trading.py`

### 🧪 백테스트
`backtest/` 전체 — 전략 1개당 파일 1개(`*_backtest.py`), 공용 지표는 `ml/backtest.py`·`ml/walk_forward.py`

### 🌐 데이터 제공자
`providers/market_data.py`(가격·QQQ·VIX) · `providers/kis_quote.py` / `kis_fundamentals.py`(KIS) ·
`providers/kr_fundamentals.py` / `kr_market_data.py`(국내) · `providers/earnings_data.py` ·
`providers/news_labels.py` · `providers/realtime_quotes.py`

### 🛠 운영 / 배포
`deploy/crontab.stock-report`(크론 단일 진실원) · `scripts/*_watchdog.sh`(생존+코드신선도 감시) ·
`scripts/check_crontab_drift.py`(라이브 crontab ↔ 저장소 대조) · `tests/bot_healthcheck.py`

---

## 새 파일을 어디에 둘까

- 예약 실행 스크립트 → `crons/` (+ `deploy/crontab.stock-report`에 등록 + `check_crontab_drift.py`의
  `RELEVANT_MARKERS`에 필요시 추가)
- 외부 API/데이터 소스 어댑터 → `providers/`
- 랭킹·백테스트·리스크 로직 → `ml/` (모의투자 학습 루프에 얽히면 `ml/adaptive/`)
- 텔레그램 명령어 → `bot/`
- 여러 계층이 재사용하는 순수 헬퍼 → `lib/`
- 대시보드 화면/차트 → `dashboard/`
- 위키·지식 관련 → `agent_console/` 또는 `reports/wiki_*.py`
- 그 외 애매하면: 기존 폴더 중 가장 가까운 곳에 넣고, 정말 여러 곳에서 즉시 참조되는 공용
  모듈일 때만 루트에 남긴다(그리고 이 표를 갱신한다).
