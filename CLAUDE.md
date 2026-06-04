# Stock Report — Intelligence Barbell

## 아키텍처 핵심

```
deliver_investment_report.sh (크론 23:00 UTC)
  ├── investment_report.py   → ~/reports/investment-{report,data,summary}
  ├── barbell_strategy.py    → Phase 분류·알림 (STATE: ~/.cache/barbell_state.json)
  └── portfolio_tracker.py   → 히스토리 기록 (~/.local/share/stock-report/)

kiwoom_sync_rest.py (크론 23:35 UTC = 08:35 KST, 월~금)
  └── 키움 REST API kt00018 → portfolio_snapshot.json domestic 섹션 업데이트

telegram_bot.py (상시)
  ├── fetch_market()         → barbell_strategy 전체 조회 (5분 캐시)
  ├── Phase 5min 감시        → barbell_state.json 공유 (크론과 중복 방지)
  └── 가격알림 5min 체크

portfolio_sync_server.py (상시, port 8765)
  └── 외부 잔고 데이터 수신 → portfolio_snapshot.json 업데이트
```

## 파일 역할 (핵심만)

| 파일 | 역할 | 상태파일 |
|------|------|----------|
| `barbell_strategy.py` | Phase 분류, DCA·SGOV·레버리지 계산, 리포트 | `~/.cache/barbell_state.json` |
| `telegram_bot.py` | 봇 메인 루프, 명령어 라우터, 첨부파일 처리 | `telegram_bot_state.json` 제거됨 |
| `holding_manager.py` | 포트폴리오 CRUD + DCA/목표비중 파일 | `portfolio_snapshot.json`, `dca_weights.json`, `target_weights.json` |
| `tax_tracker.py` | 실현손익 기록·조회·세금 계산 | `~/.local/share/stock-report/tax_records.json` |
| `portfolio_tracker.py` | 일일 히스토리 + 배당 기록 | `~/.local/share/stock-report/` |
| `attachment_parser.py` | PDF/이미지 OCR 파싱, pending 파일 관리 | `pending_snapshot.json`, `pending_sells.json` |
| `price_alerts.py` | 알림 CRUD + check_alerts() | `price_alerts.json` |
| `order_generator.py` | Phase 기반 소수점 매수 주문서 생성 | — |
| `source_collector.py` | 뉴스 JSONL 캐시 수집·다이제스트 | `~/reports/source-cache/*.jsonl` |
| `stock_advisor.py` | Hermes CLI로 AI 상담 프롬프트 실행 | — |
| `kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 (크론 08:35 KST) | — |
| `portfolio_sync_server.py` | 외부 잔고 수신 Flask 서버 (port 8765, Bearer 인증) | — |
| `bot_healthcheck.py` | 봇·서버 상태 자동 점검 (크론 30분, 문제 시만 알림) | `/tmp/healthcheck_last_alert.json` |

## 텔레그램 봇 명령어

```
── 시장 현황 ─────────────────────────────────────
/status              Phase + 핵심 수치 + 1M모멘텀 + 수익률 (5분 캐시)
/summary             한 줄 빠른 현황 — Phase·QQQ·총액·F&G
/phase               Phase 미터 + 행동 지침
/report              전체 바벨 리포트 (항상 실시간)
/sim [bull2|0~5]     시장 상태 시뮬레이션

── 포트폴리오 ────────────────────────────────────
/portfolio           보유현황 + 총액
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
~/.local/share/stock-report/            — 런타임 데이터 (tax, history, dividend, pending)
```

## 포트폴리오
MSFT, QQQI, ORCL, NOW, CRM, SAP, UNH, SGOV, CPNG, NVDA, GOOGL, SPMO

## 안전 규칙
- `.env`, `portfolio_snapshot.json`, `leverage_state.json`, `price_alerts.json` 절대 커밋 금지
- 티커 표시 시 회사명 병기: `MSFT — Microsoft`
- 출력은 한국어 기본
- 텔레그램 메시지 4000자 초과 시 분할 (4096자 제한)
- `STOCK_BOT_CHAT_ID` 는 env var — 코드에 하드코딩 금지
- `KIWOOM_API_KEY` / `KIWOOM_API_SECRET` 절대 커밋 금지
