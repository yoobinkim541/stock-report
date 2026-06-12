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
| `tax_tracker.py` | 실현손익 기록·조회·세금 계산 | `~/.local/share/stock-report/tax_records.json` |
| `portfolio_tracker.py` | 일일 히스토리 + 배당 기록 | `~/.local/share/stock-report/` |
| `portfolio_sync_server.py` | 외부 잔고 수신 Flask 서버 (port 8765, Bearer 인증) | — |

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
| `reports/daily_signals.py` | 일일 시장 신호 탐지 |
| `reports/save_csv.py` | 투자 요약 CSV 저장 |

**crons/ (크론 진입점)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `crons/daily_leverage_retrain.py` | LeverageModel 재학습 + 월요일 Optuna 재최적화 | 평일 22:15 UTC |
| `crons/daily_ranking.py` | ML 종목 랭킹 발송 | 평일 22:00 UTC |
| `crons/notion_sync.py` | Notion 대시보드 동기화 | 평일 22:30 UTC |
| `crons/news_spike_detector.py` | 속보 수집 + 급증 감지 + 텔레그램 알림 | 매 1분 |
| `crons/kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 | 평일 23:35 UTC |
| `reports/source_collector.py` | 전체 소스 수집 (텔레그램 채널·FRED·국채·시장 스냅샷) → JSONL 캐시 | 매 30분 (:05/:35) |
| `crons/paper_track.py` | MetaAllocator vs Phase 규칙 A/B 페이퍼 트레이딩 (월요일 Sharpe 비교 발송) | 평일 22:50 UTC |
| `crons/fundamental_snapshot.py` | 펀더멘털 point-in-time 스냅샷 적재 (look-ahead 없는 학습 피처용) | 토 01:00 UTC |
| `crons/options_snapshot.py` | 옵션 지표 스냅샷 (ATM IV·풋콜비·스큐·기대변동폭) — 학습 피처 축적 | 평일 21:30 UTC |
| `backtest/entry_calibration.py` | 진입점수 가중치·임계값 walk-forward 재추정 (OOS 개선 시만 자동 채택) | 매월 1일 14:00 UTC |

**tests/ (테스트·헬스체크)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `tests/bot_smoke_test.py` | 기능 검증 연기 테스트 25항목 (실패 시만 알림) | 평일 00:00 UTC |
| `tests/ml_smoke_test.py` | ML 파이프라인 end-to-end 58항목 (네트워크 불필요) | 평일 크론 |
| `tests/bot_healthcheck.py` | 봇·서버 상태 점검 (프로세스·PID·파일 신선도) | 매 30분 |

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
```

> `/dividend` → `/holding dividend` 통합, `/apply_snapshot` → `/holding apply` 통합  
> 기존 명령어는 하위 호환으로 유지

## 환경변수

| 변수 | 필수 | 기본값 |
|------|------|--------|
| `STOCK_BOT_TOKEN` | ✅ | — |
| `STOCK_BOT_CHAT_ID` | — | `5771238245` |
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
~/.cache/barbell_state.json             — Phase 상태 (크론·봇 공유)
~/.cache/barbell_state.lock             — Phase 상태 쓰기 잠금
~/.cache/barbell_anchor.json            — 낙폭 고점 앵커 (Phase 드리프트 방지)
~/.local/state/stock-report/barbell_bot.pid  — 봇 PID (단일 인스턴스 잠금)
~/.local/share/stock-report/stock_report.db      — SQLite 통합 저장소 (user_id 스코프, WAL)
                                                   └ 컬렉션: tax_records · portfolio_history
                                                     · qqqi_dividends · signal_outcomes
                                                   (레거시 JSON 자동 마이그레이션 — store.py)
~/.local/share/stock-report/            — 런타임 데이터 (pending, paper_track + 레거시 JSON 원본)
~/.local/share/stock-report/paper_track.json     — A/B 페이퍼 트레이딩 기록 (meta vs rule)
~/reports/ml-cache/leverage_best_params.json     — Optuna 최적 파라미터 (UPRO·vol targeting)
~/reports/ml-cache/entry_score_params.json       — 진입점수 가중치 (캘리브레이션 채택 시 생성)
~/reports/ml-cache/fundamental_scores.json       — 펀더멘털 점수 7일 캐시 (랭커 틸트용)
~/reports/ml-cache/fundamental_snapshots.jsonl   — 펀더멘털 주간 point-in-time 스냅샷
```

## 포트폴리오
MSFT, QQQI, ORCL, SAP, UNH, SGOV, NVDA, GOOGL, SPMO

## 안전 규칙
- `.env`, `portfolio_snapshot.json`, `leverage_state.json`, `price_alerts.json` 절대 커밋 금지
- 티커 표시 시 회사명 병기: `MSFT — Microsoft`
- 출력은 한국어 기본
- 텔레그램 메시지 4000자 초과 시 줄바꿈 기준 분할 (4096자 제한)
- `STOCK_BOT_CHAT_ID` 는 env var — 코드에 하드코딩 금지
- `KIWOOM_API_KEY` / `KIWOOM_API_SECRET` 절대 커밋 금지
- `holding_manager._save()` 는 atomic write (temp→rename) — 직접 `json.dump` 호출 금지
- 기록로그(tax/history/dividend/signal_outcomes)는 `store.py` 경유 — 직접 파일 R/W 금지
  (DB 경로 override: `STOCK_REPORT_DB` env var, 기본 `~/.local/share/stock-report/stock_report.db`)
