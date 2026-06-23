# Stock Report — Intelligence Barbell

## 폴더 구조

```
stock-report/
├── ml/          # ML 모델 (Ranker, LeverageModel, MetaAllocator, Optimizer 등)
├── bot/         # 텔레그램 서브커맨드 핸들러 (telegram_bot이 import)
├── reports/     # 리포트·데이터 생성 라이브러리
├── crons/       # 크론 진입점 스크립트 (daily_*, news_*, notion_*, kiwoom_*)
├── tests/       # 스모크 테스트·헬스체크
├── backtest/    # 백테스트 분석 스크립트 (개발용)
└── scripts/     # 쉘 스크립트 (watchdog, deliver)
    (root)       # 상시 실행 프로세스: telegram_bot, barbell_strategy, portfolio_sync_server 등
```

## 아키텍처 핵심

```
scripts/deliver_investment_report.sh (크론 23:00 UTC)
  ├── reports/investment_report.py  → ~/reports/investment-{report,data,summary}
  ├── barbell_strategy.py           → Phase 분류·알림 (STATE: ~/.cache/barbell_state.json)
  └── portfolio_tracker.py          → 히스토리 기록 (~/.local/share/stock-report/)

crons/kiwoom_sync_rest.py (크론 23:35 UTC = 08:35 KST, 월~금)
  └── 키움 REST API kt00018 → portfolio_snapshot.json domestic 섹션 업데이트

crons/daily_leverage_retrain.py (크론 22:15 UTC, 평일)
  ├── LeverageModel 일일 재학습 → 진입 신호 발송
  └── (월요일만) Optuna 파라미터 재최적화 → ~/reports/ml-cache/leverage_best_params.json

telegram_bot.py (상시, fcntl 단일 인스턴스 잠금)
  ├── fetch_market()         → barbell_strategy 전체 조회 (5분 캐시, threading.Lock)
  ├── Phase 5min 감시        → barbell_state.json 공유 (크론과 중복 방지)
  ├── 가격알림 5min 체크
  ├── bot/holding_commands.py → /holding 서브커맨드 위임
  └── bot/tax_commands.py    → /tax 서브커맨드 위임

portfolio_sync_server.py (상시, port 8765)
  └── 외부 잔고 데이터 수신 → portfolio_snapshot.json 업데이트

crons/news_spike_detector.py (크론 매 1분)
  ├── reports/source_collector → JSONL 캐시 저장
  ├── 속보 태그 이벤트 필터 + 규칙 기반 중요도 판단
  └── 쿨다운: ~/.cache/news_spike_state.json (테마/티커별 1시간)

크론 검증:
  tests/bot_smoke_test.py   — 매일 00:00 UTC (09:00 KST), 25항목 실데이터 테스트
  tests/bot_healthcheck.py  — 매 30분, 프로세스·서버·파일 상태 점검
```

## 파일 역할 (핵심만)

**루트 (상시 실행 프로세스)**
| 파일 | 역할 | 상태파일 |
|------|------|----------|
| `barbell_strategy.py` | Phase 분류, DCA·SGOV·레버리지 계산, 리포트 | `~/.cache/barbell_state.json` |
| `telegram_bot.py` | 봇 메인 루프, 명령어 라우터, fcntl 단일 인스턴스 | `~/.local/state/stock-report/barbell_bot.pid` |
| `holding_manager.py` | 포트폴리오 CRUD + DCA/목표비중 파일 (atomic write) | `portfolio_snapshot.json`, `dca_weights.json`, `target_weights.json` |
| `portfolio_universe.py` | 보유 티커 단일 소스 + 은퇴 티커 기록 + 죽은 텍스트 감사 | `~/.local/share/stock-report/retired_tickers.json` |
| `tax_tracker.py` | 실현손익 기록·조회·세금 계산 | `~/.local/share/stock-report/tax_records.json` |
| `portfolio_tracker.py` | 일일 히스토리 + 배당 기록 | `~/.local/share/stock-report/` |
| `portfolio_sync_server.py` | 외부 잔고 수신 Flask 서버 (port 8765, Bearer 인증) | — |
| `safe_io.py` | 멀티프로세스 안전 파일 I/O — atomic write + 교차 프로세스 쓰기 락(portfolio_snapshot writer 공용) | `<path>.lock` |
| `notify.py` | 텔레그램 발송 단일 진실원 — send_telegram(4096 분할·토큰 마스킹)·send_photo (봇 제외 전 모듈 공용) | — |
| `providers/market_data.py` | 시장 데이터 수집층 — fetch_qqq_data·rsi·vix·fear_greed·ma200·portfolio_value·환율·캐시·leverage_state (barbell 에서 분리, 재export 호환) | `~/.cache/barbell_anchor·last_prices.json` |

**bot/ (텔레그램 서브커맨드)**
| 파일 | 역할 |
|------|------|
| `bot/holding_commands.py` | /holding 서브커맨드 (buy·sell·target·dca·dividend·apply) |
| `bot/tax_commands.py` | /tax 서브커맨드 (sim·sell·history·delete·import) |
| `bot/attachment_parser.py` | PDF/이미지 OCR 파싱, pending 파일 관리 |
| `bot/price_alerts.py` | 알림 CRUD + check_alerts() |
| `bot/order_generator.py` | Phase 기반 소수점 매수 주문서 생성 |
| `bot/stock_advisor.py` | AI 상담 프롬프트 실행 |

**reports/ (리포트·데이터 생성)**
| 파일 | 역할 |
|------|------|
| `reports/investment_report.py` | 일일 투자 리포트 생성 |
| `reports/market_report.py` | 시장 현황 리포트 생성 |
| `reports/source_collector.py` | 뉴스 JSONL 캐시 수집·다이제스트 |
| `reports/fundamental_score.py` | 종목 펀더멘털 점수 계산 |
| `reports/institutional_flow.py` | 기관 매집 추적 — 거래량 방향성(OBV·CMF·A/D) 매집 강도 + 美 13F 지분 변동 교차검증 |
| `reports/daily_signals.py` | 일일 시장 신호 탐지 |
| `reports/save_csv.py` | 투자 요약 CSV 저장 |
| `reports/report_charts.py` | 일일 리포트 시각화 — 포트폴리오 대시보드 PNG (등락률·벤치마크 추이·RSI·매집강도 4분할, 텔레그램 sendPhoto) |

**crons/ (크론 진입점)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `crons/daily_leverage_retrain.py` | LeverageModel 재학습 + 월요일 Optuna 재최적화 | 평일 22:15 UTC |
| `crons/daily_ranking.py` | ML 종목 랭킹 발송 | 평일 22:00 UTC |
| `crons/notion_sync.py` | Notion 대시보드 동기화 (리포트 23:00 이후) | 평일 23:30 UTC |
| `crons/news_spike_detector.py` | 속보 수집 + 급증 감지 + 텔레그램 알림 | 매 1분 |
| `crons/kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 | 평일 23:35 UTC |
| `reports/source_collector.py` | 전체 소스 수집 (텔레그램 채널·FRED·국채·시장 스냅샷) → JSONL 캐시 | 매 30분 (:05/:35) |
| `crons/paper_track.py` | MetaAllocator vs Phase 규칙 A/B 페이퍼 트레이딩 (월요일 Sharpe 비교 발송) | 평일 22:50 UTC |
| `crons/fundamental_snapshot.py` | 펀더멘털 point-in-time 스냅샷 적재 (look-ahead 없는 학습 피처용) | 토 01:00 UTC |
| `crons/options_snapshot.py` | 옵션 지표 스냅샷 (ATM IV·풋콜비·스큐·기대변동폭) — 학습 피처 축적 | 평일 21:30 UTC |
| `crons/institutional_snapshot.py` | 기관 매집 강도·13F 지분 주간 스냅샷 적재 (델타 추적용) + 상위 5 다이제스트 발송 | 토 01:30 UTC |
| `backtest/entry_calibration.py` | 진입점수 가중치·임계값 walk-forward 재추정 (OOS 개선 시만 자동 채택) | 매월 1일 14:00 UTC |

**tests/ (테스트·헬스체크)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `tests/bot_smoke_test.py` | 기능 검증 연기 테스트 25항목 (실패 시만 알림) | 평일 00:00 UTC |
| `tests/ml_smoke_test.py` | ML 파이프라인 end-to-end 58항목 (네트워크 불필요) | 평일 크론 |
| `tests/institutional_flow_smoke_test.py` | 기관 매집 스코어링 무네트워크 단위 테스트 (합성 데이터) | 평일 크론 |
| `tests/bot_healthcheck.py` | 봇·서버 상태 점검 (프로세스·PID·파일 신선도·store DB 무결성) | 매 30분 |

**ml/ (ML 모델)**
| 파일 | 역할 | 상태파일 |
|------|------|----------|
| `ml/sweet_spot.py` | AR(1) 합성 데이터 + 임계값 전략 그리드서치 | — |
| `ml/leverage_optimizer.py` | Optuna TPE 레버리지 파라미터 탐색 + Walk-Forward OOS | `~/reports/ml-cache/leverage_best_params.json` |

## 텔레그램 봇 명령어

```
── 시장 현황 ─────────────────────────────────────
/status              Phase + 핵심 수치 + 1M모멘텀 + 수익률 (5분 캐시)
/summary             한 줄 빠른 현황 — Phase·QQQ·총액·F&G
/phase               Phase 미터 + 행동 지침
/report              전체 바벨 리포트 (항상 실시간)
/accum [us|kr|TICKER...]  기관 매집 추적 — OBV·CMF·13F 매집 강도 랭킹 (기본: 보유+美+韓)
/sim [bull2|0~5]     시장 상태 시뮬레이션

── 포트폴리오 ────────────────────────────────────
/portfolio           보유현황 + 개별 종목 P&L + 총액
/rebalance           안전마진 + 종목 비중 진단 + DCA 조정
/history             성과 히스토리 (1d/7d/30d/90d)
/sgov                SGOV 실탄 현재/목표 비교

── DCA & 주문 ────────────────────────────────────
/dca                 오늘 DCA 배분 금액
/order               소수점 매수 주문서 (키움 즉시 입력)

── 종목 관리 ─────────────────────────────────────
/holding                           보유 종목 목록
/holding buy TICKER 주수 단가 [frac]  매수 기록 + 가격 갱신
/holding sell TICKER [주수]           매도 기록
/holding target [TICKER 비중% ...]    목표 비중 조회/설정
/holding dca [TICKER 비중% ...]       DCA 비중 조회/변경
/holding refresh                   전 종목 현재가 갱신
/holding dividend [금액 TICKER]    QQQI 배당 조회/기록  ← /dividend 통합
/holding apply                     파싱된 스냅샷 반영   ← /apply_snapshot 통합

── 세금 ──────────────────────────────────────────
/tax                               올해 실현손익 + 양도세 추산
/tax sim TICKER [수량] [단가]        매도 전 세금 시뮬레이션
/tax sell TICKER 수량 매수단가 매도단가  매도 기록
/tax history                       전체 매도 기록
/tax delete N                      N번 기록 삭제
/tax import apply                  파싱된 매도내역 일괄 반영

── AI 상담 & 알림 ────────────────────────────────
/ask 질문                           AI 포트폴리오 상담
/alert add TICKER 가격 buy|sell [메모]  가격 알림 등록
/alert list                        알림 목록
/alert remove ID                   알림 삭제
※ 진입(enter) 신호 발생 시 목표가(sell)·손절가(buy) 알림 자동 등록
  → 발동 시 signal_outcomes.json에 R-multiple 기록 + 짝 알림 자동 제거

📎 PDF·이미지 전송 → 자동 파싱 → /holding apply 또는 /tax import apply

── 읽기전용 게스트 (STOCK_BOT_GUEST_IDS) ─────────
/market              시황 브리핑 — 국면·낙폭·RSI·VIX·F&G (사실형, 처방 없음)
/indicators TICKER   종목 기술적 지표 — RSI·이동평균·모멘텀·52주 위치
/myadd TICKER 주수 평단가   내 보유 종목 추가 (user_id 스코프 store)
/myremove TICKER     내 보유 종목 삭제
/myportfolio         내 포트폴리오 평가 — 평가액·손익·수익률 (본인 데이터, 처방 없음)
/help                게스트 도움말
※ 게스트는 위 6개만 허용 — 주문·신호·종목관리·세금·AI상담 전면 차단 (법적 안전)
※ 게스트 포트폴리오는 본인 chat_id 네임스페이스에 격리 (소유자 portfolio_snapshot과 분리)
```

> `/dividend` → `/holding dividend` 통합, `/apply_snapshot` → `/holding apply` 통합  
> 기존 명령어는 하위 호환으로 유지

## 역할 (telegram_bot)

| 역할 | chat_id | 권한 |
|------|---------|------|
| owner | `STOCK_BOT_CHAT_ID` | 전체 (주문·신호·종목관리·세금·AI상담·첨부) |
| guest | `STOCK_BOT_GUEST_IDS` (쉼표구분) | 읽기전용 — `/market` `/indicators` `/myadd` `/myremove` `/myportfolio` `/help` (`_GUEST_COMMANDS`) |
| 차단 | 그 외 | "권한 없음" |

- 보안 경계: `_command_allowed(role, cmd)` (순수 함수) — 게스트는 처방형/주문 명령 전면 차단.
- 게스트 출력은 `bot/guest_report.py`(시황·지표) + `bot/guest_portfolio.py`(본인 포트폴리오) — **사실형 데이터·지표·본인평가만, 처방(매매신호·목표가·DCA·레버리지) 금지.** "서술 OK, 지시 금지" 원칙.
- 게스트 포트폴리오는 store 문서 `guest_holdings` 를 게스트 chat_id(user_id)에 저장 — 소유자 데이터와 격리.
- 첨부·일반텍스트(스냅샷 파싱)는 포트폴리오 수정 → owner 전용.

## 환경변수

| 변수 | 필수 | 기본값 |
|------|------|--------|
| `STOCK_BOT_TOKEN` | ✅ | — |
| `STOCK_BOT_CHAT_ID` | — | `5771238245` |
| `STOCK_BOT_GUEST_IDS` | — | — (쉼표구분 읽기전용 게스트 chat_id) |
| `KIWOOM_API_KEY` | — | — (openapi.kiwoom.com 발급) |
| `KIWOOM_API_SECRET` | — | — |
| `SYNC_TOKEN` | — | — (portfolio_sync_server 인증) |
| `SYNC_PORT` | — | `8765` |
| `SAVE_TICKER_API_BASE` | — | `https://saveticker.com/api` |
| `INVESTMENT_REPORT_MAX_NASDAQ_SCAN` | — | `100` |
| `INVESTMENT_REPORT_MAX_KOSPI_SCAN` | — | `30` |
| `INVESTMENT_REPORT_ARCA_PAGES` | — | `1` |
| `STOCK_COLLECTOR_ARCA_PAGES` | — | `2` |
| `STOCK_REPORT_PROJECT_DIR` | — | `/home/ubuntu/projects/stock-report` |
| `BARBELL_MAX_DCA_MULT` | — | `5.0` (DCA 배율 절대 상한 — F&G·ML 증폭 폭주 차단) |
| `BARBELL_DCA_VOL_CAP` | — | `0.40` (QQQ 연변동성 초과 시 DCA 배율 비례 축소) |
| `BARBELL_LEV_HALT_DD` | — | `-55.0` (낙폭 이하 시 레버리지 증액 정지 — 전소 방어) |
| `BARBELL_PRICE_STALE_DAYS` | — | `4` (최신 종가 이보다 오래되면 stale → Phase 에스컬레이션 보류) |

## IB Phase

| Phase | 조건 | DCA배율 | 레버리지 |
|-------|------|---------|---------|
| 🫧 Bull-2 | RSI>75 + 1M>8% + VIX<15 | 0.5× | — |
| 🐂 Bull-1 | RSI>70 또는 1M>5% | 0.8× | — |
| 🟢 0 | 고점 -5% 이내 | 1.0× | — |
| 🟡 1 | -5%~-10% | 1.5× | — |
| 🟠 2 | -10%~-15% | 2.0× | QLD |
| 🔴 3 | -15%~-20% | 2.5× | QLD |
| 🚨 4 | -20%~-30% | 3.0× | QLD 70 + TQQQ 30 |
| 💥 5 | -30%+ | 5.0× | TQQQ (에스컬레이션 3회) |

## 출력 파일

```
~/reports/investment-report-{date}.md    — 전체 분석 (Markdown)
~/reports/investment-data-{date}.json    — 원본 데이터
~/reports/investment-summary-{date}.json — 정제 요약 (save_csv.py 입력)
~/reports/investment-summary-{date}.txt  — 모바일 요약 (텔레그램 직접 발송)
~/reports/investment-chart-{date}.png    — 포트폴리오 대시보드 (텔레그램 sendPhoto, report_charts.py)
~/.cache/barbell_state.json             — Phase 상태 (크론·봇 공유)
~/.cache/barbell_state.lock             — Phase 상태 쓰기 잠금
~/.cache/barbell_anchor.json            — 낙폭 고점 앵커 (Phase 드리프트 방지)
~/.local/state/stock-report/barbell_bot.pid  — 봇 PID (단일 인스턴스 잠금)
~/.local/share/stock-report/stock_report.db      — SQLite 통합 저장소 (user_id 스코프, WAL)
                                                   └ 컬렉션: tax_records · portfolio_history
                                                     · qqqi_dividends · signal_outcomes · price_alerts
                                                   └ 문서: dca_weights · target_weights · leverage_state
                                                     · barbell_state · barbell_anchor
                                                     · portfolio_snapshot (파일 권위 + store 그림자)
                                                   (레거시 JSON 자동 마이그레이션 + 파일 미러 — store.py)
~/.local/share/stock-report/            — 런타임 데이터 (pending, paper_track + 레거시 JSON 원본)
~/.local/share/stock-report/paper_track.json     — A/B 페이퍼 트레이딩 기록 (meta vs rule)
~/reports/ml-cache/leverage_best_params.json     — Optuna 최적 파라미터 (UPRO·vol targeting)
~/reports/ml-cache/entry_score_params.json       — 진입점수 가중치 (캘리브레이션 채택 시 생성)
~/reports/ml-cache/fundamental_scores.json       — 펀더멘털 점수 7일 캐시 (랭커 틸트용)
~/reports/ml-cache/fundamental_snapshots.jsonl   — 펀더멘털 주간 point-in-time 스냅샷
~/reports/ml-cache/institutional_snapshots.jsonl — 기관 매집 강도·13F 지분 주간 스냅샷 (델타 추적)
```

## 포트폴리오
MSFT, QQQI, ORCL, SAP, UNH, SGOV, NVDA, GOOGL, SPMO

보유 티커는 `portfolio_universe.load_portfolio_tickers()` 가 `portfolio_snapshot.json` 에서 파생하는 것이 단일 소스다.
- 리포트·뉴스 수집·ML 파이프라인에 보유 종목 목록을 **하드코딩 금지** — 반드시 위 함수 사용
- 전량 청산 시 `holding_manager.sell_holding()` 이 은퇴 티커를 자동 기록
- `tests/bot_smoke_test.py` (매일 09:00 KST)가 소스·런타임 설정에 남은 은퇴 티커 언급을 감사 → 발견 시 텔레그램 경보
- 의도적 언급(시장 유니버스 등)은 해당 줄에 `ticker-ok` 주석으로 감사 제외

## 안전 규칙
- `.env`, `portfolio_snapshot.json`, `leverage_state.json`, `price_alerts.json` 절대 커밋 금지
- 티커 표시 시 회사명 병기: `MSFT — Microsoft`
- 출력은 한국어 기본
- 텔레그램 메시지 4000자 초과 시 줄바꿈 기준 분할 (4096자 제한)
- `STOCK_BOT_CHAT_ID` 는 env var — 코드에 하드코딩 금지
- `KIWOOM_API_KEY` / `KIWOOM_API_SECRET` 절대 커밋 금지
- `portfolio_snapshot.json` writer 3종(`holding_manager._save`·`portfolio_sync_server`·`kiwoom_sync_rest`)은
  모두 `safe_io.atomic_write_json` + `safe_io.file_write_lock` 경유 — 직접 `json.dump`/in-place write 금지.
  (atomic rename 으로 torn read 방지 + 교차 프로세스 락으로 동시 쓰기 lost update 방지) → 이후 `store.shadow_doc` 비차단 동기화
- 레버리지/DCA 권고 안전장치: `barbell_strategy.leverage_dca_guard`(변동성 캡·절대 상한·낙폭 정지) +
  `fetch_qqq_data` stale 플래그(묵은 데이터 시 `run()`이 Phase 에스컬레이션 보류) — 튜닝은 `BARBELL_*` env var
- 크론 스케줄 단일 진실원: `deploy/crontab.stock-report` (변경 후 `crontab deploy/crontab.stock-report` 적용)
- 기록로그(tax/history/dividend/signal_outcomes/price_alerts)는 `store.py` 경유 — 직접 파일 R/W 금지
  (DB 경로 override: `STOCK_REPORT_DB` env var, 기본 `~/.local/share/stock-report/stock_report.db`)
- 설정 블롭(dca/target/leverage)은 store 권위 + 파일 미러(`store.save_doc`) — advisor 편집은
  `bot/stock_advisor._sync_editable_to_store()` 가 store로 reimport. 직접 `json.dump` 금지
- store 파일 미러는 `DEFAULT_USER` 만 기록 — 테스트 시 모듈 파일 경로 상수를 tmp로 리다이렉트할 것
  (라이브 `dca_weights.json` 등 덮어쓰기 방지)
