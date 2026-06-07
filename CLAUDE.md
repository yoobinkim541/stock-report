# Stock Report — Intelligence Barbell

## 아키텍처 핵심

```
deliver_investment_report.sh (크론 23:00 UTC)
  ├── investment_report.py   → ~/reports/investment-{report,data,summary}
  ├── barbell_strategy.py    → Phase 분류·알림 (STATE: ~/.cache/barbell_state.json)
  └── portfolio_tracker.py   → 히스토리 기록 (~/.local/share/stock-report/)

kiwoom_sync_rest.py (크론 23:35 UTC = 08:35 KST, 월~금)
  └── 키움 REST API kt00018 → portfolio_snapshot.json domestic 섹션 업데이트

daily_leverage_retrain.py (크론 22:15 UTC, 평일)
  ├── LeverageModel 일일 재학습 → 진입 신호 발송
  └── (월요일만) Optuna 파라미터 재최적화 → ~/reports/ml-cache/leverage_best_params.json

telegram_bot.py (상시, fcntl 단일 인스턴스 잠금)
  ├── fetch_market()         → barbell_strategy 전체 조회 (5분 캐시, threading.Lock)
  ├── Phase 5min 감시        → barbell_state.json 공유 (크론과 중복 방지)
  ├── 가격알림 5min 체크
  ├── holding_commands.py    → /holding 서브커맨드 위임
  └── tax_commands.py        → /tax 서브커맨드 위임

portfolio_sync_server.py (상시, port 8765)
  └── 외부 잔고 데이터 수신 → portfolio_snapshot.json 업데이트

news_spike_detector.py (크론 매 1분)
  ├── fetch_saveticker + fetch_arca(1page) + fetch_telegram → JSONL 캐시 저장
  ├── 최근 10분 vs 이전 110분 테마/티커 빈도 비교
  ├── 3배 이상 + 최소 3건 → 텔레그램 스파이크 알림
  └── 쿨다운: ~/.cache/news_spike_state.json (테마/티커별 1시간)

크론 검증:
  bot_smoke_test.py   — 매일 00:00 UTC (09:00 KST), 25항목 실데이터 테스트
  bot_healthcheck.py  — 매 30분, 프로세스·서버·파일 상태 점검
```

## 파일 역할 (핵심만)

| 파일 | 역할 | 상태파일 |
|------|------|----------|
| `barbell_strategy.py` | Phase 분류, DCA·SGOV·레버리지 계산, 리포트 | `~/.cache/barbell_state.json` |
| `telegram_bot.py` | 봇 메인 루프, 명령어 라우터, fcntl 단일 인스턴스 | `~/.local/state/stock-report/barbell_bot.pid` |
| `holding_commands.py` | /holding 서브커맨드 (buy·sell·target·dca·dividend·apply) | — |
| `tax_commands.py` | /tax 서브커맨드 (sim·sell·history·delete·import) | — |
| `holding_manager.py` | 포트폴리오 CRUD + DCA/목표비중 파일 (atomic write) | `portfolio_snapshot.json`, `dca_weights.json`, `target_weights.json` |
| `tax_tracker.py` | 실현손익 기록·조회·세금 계산 | `~/.local/share/stock-report/tax_records.json` |
| `portfolio_tracker.py` | 일일 히스토리 + 배당 기록 | `~/.local/share/stock-report/` |
| `attachment_parser.py` | PDF/이미지 OCR 파싱, pending 파일 관리 | `pending_snapshot.json`, `pending_sells.json` |
| `price_alerts.py` | 알림 CRUD + check_alerts() | `price_alerts.json` |
| `order_generator.py` | Phase 기반 소수점 매수 주문서 생성 | — |
| `source_collector.py` | 뉴스 JSONL 캐시 수집·다이제스트 | `~/reports/source-cache/*.jsonl` |
| `news_spike_detector.py` | 1분 크론 — 뉴스 수집 + 급증 감지 + 텔레그램 알림 | `~/.cache/news_spike_state.json` |
| `stock_advisor.py` | AI 상담 프롬프트 실행 | — |
| `kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 (크론 08:35 KST) | — |
| `portfolio_sync_server.py` | 외부 잔고 수신 Flask 서버 (port 8765, Bearer 인증) | — |
| `bot_healthcheck.py` | 봇·서버 상태 자동 점검 (30분, 중복인스턴스·409·PID·파일 신선도) | `/tmp/healthcheck_last_alert.json` |
| `bot_smoke_test.py` | 기능 검증 연기 테스트 25항목 (매일 크론, 실패 시만 알림) | — |
| `ml_smoke_test.py` | ML 파이프라인 end-to-end 연기 테스트 58항목 — p3~p12 전체, 네트워크 불필요 (매일 크론) | — |
| `ml/sweet_spot.py` | AR(1) 합성 데이터 생성 + 임계값 전략 그리드서치 (`optimize_sweet_spot`) + 선택적 matplotlib 시각화 | — |
| `ml/leverage_optimizer.py` | Optuna TPE 레버리지 파라미터 스위트스팟 탐색 (`optimize_leverage`) + Walk-Forward OOS 검증 + 결과 저장/로드 | `~/reports/ml-cache/leverage_best_params.json` |

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
~/.local/state/stock-report/barbell_bot.pid  — 봇 PID (단일 인스턴스 잠금)
~/.local/share/stock-report/            — 런타임 데이터 (tax, history, dividend, pending)
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
