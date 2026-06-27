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
| `kiwoom_mock.py` | 키움 **모의투자** 어댑터 — 모의 도메인(`mockapi.kiwoom.com`) 하드락 + 토큰·잔고(kt00018)·주문(kt10000/kt10001). 실거래 경로 없음 | — |
| `providers/earnings_data.py` | 어닝·컨센서스·밸류에이션 데이터층 — yfinance(US 전체 무료: 서프라이즈·포워드 컨센서스·★리비전 모멘텀·PER/PBR/PSR/ROE/EPS/배당·배당CAGR) / KR(.KS) 열화모드(밸류·배당만). 결측 graceful·12h 캐시 | `~/reports/ml-cache/earnings_*.json` |
| `providers/kr_market_data.py` | KR 생존편향제거 데이터층 — **marcap**(연도별 parquet, 1995~ 전종목 시점별 시총·OHLCV·상폐포함) + **FDR KRX-DELISTING**(상폐 라벨·사유). top_n_by_marcap·ohlcv_from_marcap·distress_delistings. **pykrx 는 이 서버서 불가(KRX 403)** | `~/reports/ml-cache/marcap/*.parquet` |
| `providers/index_membership.py` | 교차시장 시점별 멤버십 — 美 S&P500(fja05680, 1996~ 생존편향0)·KR(marcap 위임). members_asof·change_events·membership_intervals(생존편향제거 마스킹) | `~/reports/ml-cache/sp500_history.csv` |
| `providers/edgar.py` | SEC EDGAR 재무층 — companyfacts(상폐기업 재무 보존·무료) → fundamental_trends(매출YoY·순마진·부채추세, 무룩어헤드). 美 퇴출예측 피처원 | `~/reports/ml-cache/edgar/` |
| `providers/naver_kr.py` | KR 수급(외인/기관/개인 순매수)+KOSPI200 멤버십(Naver — pykrx 공백 복구, 서버서 동작). investor_flow_features·kospi200_members. **Naver HTML=EUC-KR** | — |

**bot/ (텔레그램 서브커맨드)**
| 파일 | 역할 |
|------|------|
| `bot/holding_commands.py` | /holding 서브커맨드 (buy·sell·target·dca·dividend·apply) |
| `bot/tax_commands.py` | /tax 서브커맨드 (sim·sell·history·delete·import) |
| `bot/attachment_parser.py` | PDF/이미지 OCR 파싱, pending 파일 관리 |
| `bot/price_alerts.py` | 알림 CRUD + check_alerts() |
| `bot/order_generator.py` | Phase 기반 소수점 매수 주문서 생성 |
| `bot/stock_advisor.py` | AI 상담 프롬프트 실행 |
| `bot/earnings_commands.py` | /earnings 서브커맨드 (실적 캘린더·밸류에이션·서프라이즈·컨센서스·PEAD) — owner 전용·정보형 |

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
| `reports/earnings_reaction.py` | 과거 실적후 주가반응(PEAD) — 반응1일·5/20일 드리프트·beat→상승 적중률·드리프트 지속성 (after-close 표준) |

**crons/ (크론 진입점)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `crons/daily_leverage_retrain.py` | LeverageModel 재학습 + 월요일 Optuna 재최적화 | 평일 22:15 UTC |
| `crons/daily_ranking.py` | ML 종목 랭킹 발송 | 평일 22:00 UTC |
| `crons/notion_sync.py` | Notion 대시보드 동기화 (리포트 23:00 이후) + 리포트 아카이빙 호출 | 평일 23:30 UTC |
| `crons/notion_archive.py` | 일일 리포트 → Notion 월(`26/06`)/주(`4주차`) 계층 페이지 누적 아카이빙 (멱등 upsert, 대시보드와 독립) | notion_sync 가 호출 |
| `crons/news_spike_detector.py` | 속보 수집 + 급증 감지 + 텔레그램 알림 | 매 1분 |
| `crons/kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 | 평일 23:35 UTC |
| `crons/kiwoom_mock_track.py` | 국내주식 자동 페이퍼트레이딩 (키움 **모의투자** — 신호 기반 리밸런스·모의 도메인 하드락·편입/퇴출 근거 원장 적재) | 평일 00:30 UTC |
| `crons/kiwoom_mock_report.py` | 국내 모의 일일 현황 보고 (NAV·손익·편입/퇴출 사유·누적 vs KOSPI·MDD vs 지수) + `/mock` 공용 | 평일 06:40 UTC |
| `crons/kr_mock_learn.py` | KR 모의 정책 강화 — 보상 백필 + ★목적함수(아웃퍼폼·MDD≤지수) OOS 게이트 재학습 | 토 02:00 UTC |
| `crons/weekly_kr_ranker_retrain.py` | KR 전용 랭커(KOSPI 대비 초과수익) 주간 재학습 (Purged WF·OOS IC) | 토 03:30 UTC |
| `crons/longterm_adaptive_eval.py` | 장기 전략 ★목표(vs QQQ 아웃퍼폼·MDD≤지수) 라이브 스코어카드 + 악화 시 보수적 레버리지 축소 shadow 권고 | 토 04:00 UTC |
| `crons/leverage_structural_eval.py` | Tier3 구조적 레버리지 ★게이트 재검증 (`backtest/leverage_structural_backtest` SPY+QQQ × 그리드 낙폭예산·DSR·PBO) — GO 시 권고 레버리지 shadow (표시·수동, 자동집행 0) | 토 04:15 UTC |
| `crons/factor_premium_eval.py` | Tier4 팩터 프리미엄 틸트 ★게이트 재검증 (`backtest/factor_premium_backtest` 롱온리 ETF vs SPY DSR 다중검정·약세슬라이스) — GO 팩터만 shadow. **현재 NO-GO**(밸류·사이즈·퀄리티·최소변동 SPY 미돌파, 모멘텀=SPMO 기보유) | 토 04:45 UTC |
| `crons/income_compounding_eval.py` | Tier5 인컴 복리 재투자 ★게이트 재검증 (`backtest/income_compounding_backtest` 커버드콜 QYLD vs 총수익 QQQ 세전/세후·재투자vs비축) — GO 시 shadow. **현재 NO-GO**(인컴 엔진 세후 CAGR −12.9%p 열위·방어기능). 재투자>현금비축(+33%)은 항상참 규율 | 토 05:00 UTC |
| `crons/advice_adaptive_eval.py` | 포트폴리오 advice 적응 평가 (paper_track A/B meta vs rule ★목적함수 → blend 신뢰도 shadow 권고) | 토 04:30 UTC |
| `reports/source_collector.py` | 전체 소스 수집 (텔레그램 채널·FRED·국채·시장 스냅샷) → JSONL 캐시 | 매 30분 (:05/:35) |
| `crons/paper_track.py` | MetaAllocator vs Phase 규칙 A/B 페이퍼 트레이딩 (월요일 Sharpe 비교 발송) | 평일 22:50 UTC |
| `crons/fundamental_snapshot.py` | 펀더멘털 point-in-time 스냅샷 적재 (look-ahead 없는 학습 피처용) | 토 01:00 UTC |
| `crons/options_snapshot.py` | 옵션 지표 스냅샷 (ATM IV·풋콜비·스큐·기대변동폭) — 학습 피처 축적 | 평일 21:30 UTC |
| `crons/earnings_snapshot.py` | 어닝 컨센서스·★리비전 모멘텀·서프라이즈·밸류에이션 point-in-time 적재 (실적/주가반응 예측 학습데이터 — 무룩어헤드) | 평일 22:10 UTC |
| `crons/naver_flow_snapshot.py` | KR 투자자 수급 + KOSPI200 멤버십 forward 스냅샷 (Naver — pykrx 공백 복구, 시점별 이력 축적) | 평일 07:30 UTC |
| `crons/earnings_model_retrain.py` | 어닝 예측(G3 서프라이즈·G4 주가반응) 주간 재학습 + 모델 캐시 (엣지 게이트: AUC>0.52·skill>0.02 시만 저장 → /earnings 라이브 예측 공급) | 토 03:50 UTC |
| `crons/institutional_snapshot.py` | 기관 매집 강도·13F 지분 주간 스냅샷 적재 (델타 추적용) + 상위 5 다이제스트 발송 | 토 01:30 UTC |
| `backtest/entry_calibration.py` | 진입점수 가중치·임계값 walk-forward 재추정 (OOS 개선 시만 자동 채택) | 매월 1일 14:00 UTC |
| `crons/entry_adaptive_learn.py` | 진입 임계값 라이브 outcome 적응 학습 (signal_outcomes→★목적함수 OOS 게이트→shadow; `ADAPTIVE_ENTRY_ENABLED` 시만 라이브) | 매월 1일 14:30 UTC |

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
| `ml/adaptive/` | 적응형 학습 공유 프레임워크 — policy(클램프)·ledger(불변 원장)·reward(★목적함수)·learner(OOS게이트)·regime(최근성)·champion_challenger | `~/reports/ml-cache/policy_*.json` |
| `ml/kr_ranker.py` | 한국주식 전용 ranker (KOSPI 대비 초과수익 예측, US ranker 재사용·KR캐시) | `~/reports/ml-cache/kr_ranker_model.pkl` |
| `ml/kr_policy.py` | KR 모의 선택 정책 점수 (KR ranker + 규칙 가중, Policy 클램프) | `~/reports/ml-cache/policy_kr_mock.json` |
| `ml/regime_classifier.py` | 추세 vs 횡보 레짐 감지 (Kaufman ER·무룩어헤드·비대칭 전이, US=QQQ·KR=^KS11) — 리포트/`/status` **표시 전용, 배분 불변**. 백테스트 게이트가 US 횡보 틸트 NO-GO·KR 현금디리스크 조건부(비용반영 시 Sharpe중립) 판정 (`backtest/sideways_backtest.py`·`backtest/kr_sideways_backtest.py`) | — |
| `ml/risk_model.py` | 포트폴리오 리스크 계측 (Aladdin식, Tier1) — Ledoit-Wolf 공분산·위험기여(Euler)·유효분산(참여비)·QQQ/TLT 팩터베타 + **성장최적 레버리지 계기판**(Kelly밴드·낙폭예산 상한·파산확률). `/risk`·`/portfolio`·`/rebalance` 노출 — **표시 전용, 배분 불변**(실제 레버리지는 Tier3 게이트 후). USD북 한정 | — |
| `ml/validation.py` | 백테스트 검증 formalism (Tier2, López de Prado) — PSR·**Deflated Sharpe**(다중검정)·**PBO**(CSCV 과적합확률)·Purged/Embargoed CV + `validate_strategy`(벤치마크 초과PSR). `backtest/sideways_backtest`·`kr_sideways_backtest` verdict 에 배선 — **판정·표시 전용**. 공격 엔진(Tier3~6) 라이브 게이트의 통계 관문 | — |
| `ml/deletion_risk.py` | 부실 퇴출 사전예측 (marcap 파생 피처→P(부실퇴출); 실데이터 OOS AUC 0.743·M&A 제외). 회피 통합·★RL 대상 | — (학습셋 marcap 조립) |
| `ml/earnings_predictor.py` | 실적 서프라이즈 예측 G3 (P(beat); 서프라이즈 지속성·모멘텀·리비전 모멘텀 훅). 엣지 게이트 캐시 | `~/reports/ml-cache/earnings_predictor.pkl` |
| `ml/earnings_move_predictor.py` | 실적후 주가반응 예측 G4 (기대 변동폭+방향확률; 방향은 무엣지·정직). 엣지 게이트 캐시 | `~/reports/ml-cache/earnings_move_predictor.pkl` |

## 텔레그램 봇 명령어

```
── 시장 현황 ─────────────────────────────────────
/status              Phase + 핵심 수치 + 1M모멘텀 + 수익률 (5분 캐시)
/summary             한 줄 빠른 현황 — Phase·QQQ·총액·F&G
/phase               Phase 미터 + 행동 지침
/report              전체 바벨 리포트 (항상 실시간)
/accum [us|kr|TICKER...]  기관 매집 추적 — OBV·CMF·13F 매집 강도 랭킹 (기본: 보유+美+韓)
/earnings [TICKER]   실적·밸류에이션 — PER·PBR·PSR·ROE·EPS·배당성장 + 서프라이즈·컨센서스·리비전·PEAD (정보형)
/sim [bull2|0~5]     시장 상태 시뮬레이션

── 포트폴리오 ────────────────────────────────────
/portfolio           보유현황 + 개별 종목 P&L + 총액 (+ 리스크 1줄)
/rebalance           안전마진 + 종목 비중 진단 + DCA 조정 (+ 달러 vs 리스크 비중)
/risk                포트폴리오 위험 분석 — 변동성·위험기여·유효분산·팩터노출 + 성장최적 레버리지(Kelly·낙폭예산) — owner 전용·표시
/history             성과 히스토리 (1d/7d/30d/90d)
/sgov                SGOV 실탄 현재/목표 비교

── DCA & 주문 ────────────────────────────────────
/dca                 오늘 DCA 배분 금액
/order               소수점 매수 주문서 (키움 즉시 입력)
/mock                국내 모의 페이퍼트레이딩 현황 (NAV·손익·편입/퇴출 사유·vs KOSPI·MDD) — owner 전용

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
| `KIWOOM_MOCK_ENABLED` | — | `false` (모의 페이퍼트레이딩 루프 활성화. true 여야 주문 집행) |
| `KIWOOM_MOCK_API_KEY` / `KIWOOM_MOCK_API_SECRET` | — | — (없으면 `KIWOOM_API_KEY/SECRET` 재사용 — 앱키는 계좌 공용) |
| `KIWOOM_MOCK_ACCOUNT_NO` | — | — (모의 계좌번호, 표시·로깅용) |
| `KR_MOCK_UNIVERSE` / `KR_MOCK_MAX_POS` / `KR_MOCK_INVEST` / `KIWOOM_MOCK_SEED` | — | `20` / `5` / `0.9` / `10000000` (모의 전략 파라미터) |
| `ADAPTIVE_ENTRY_ENABLED` | — | `false` (해외 진입 임계값 적응 학습 shadow 를 라이브에 반영. off면 shadow만·라이브 불변) |
| `ADAPTIVE_LONGTERM_ENABLED` | — | `false` (장기 전략 악화 시 보수적 레버리지 축소 shadow 기록. off면 평가·권고만) |
| `ADAPTIVE_LEVERAGE_ENABLED` | — | `false` (Tier3 구조적 레버리지 GO 권고를 shadow 기록 → `/risk` 표시. off면 게이트 평가·텔레그램만. **자동집행은 항상 없음** — 실계좌 수동) |
| `ADAPTIVE_FACTOR_TILT_ENABLED` | — | `false` (Tier4 팩터 틸트 GO 시 보상 팩터 shadow 기록 → `/risk` 표시. 현재 게이트 NO-GO라 무기록. 자동집행 항상 없음) |
| `ADAPTIVE_INCOME_ENGINE_ENABLED` | — | `false` (Tier5 인컴 엔진 GO(세후 총수익 우위·희귀) 시 shadow → `/risk`. 현재 NO-GO. 자동집행 항상 없음) |
| `TIER3_RF_FALLBACK` / `TIER3_LETF_SPREAD` / `TIER3_LETF_EXPENSE` / `TIER3_BUDGET` | — | `0.03` / `0.005` / `0.009` / `0.50` (레버리지 게이트 비용·낙폭예산 가정) |
| `ADAPTIVE_ADVICE_ENABLED` | — | `false` (MetaAllocator A/B 우위 시 blend 신뢰도 shadow 기록. off면 평가·권고만) |
| `SYNC_TOKEN` | — | — (portfolio_sync_server 인증) |
| `SYNC_PORT` | — | `8765` |
| `NOTION_TOKEN` | — | — (Notion 대시보드 동기화·아카이빙. 없으면 notion_sync 스킵) |
| `NOTION_ARCHIVE_ROOT_ID` | — | — (아카이브 루트 페이지 강제 지정. 미설정 시 대시보드 부모 아래 자동탐색·생성 후 `~/.cache` 캐시) |
| `NOTION_ARCHIVE_PARENT_ID` | — | — (루트를 만들 부모. 기본: 대시보드의 부모 페이지) |
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
~/.cache/notion_archive_root.json       — Notion 리포트 아카이브 루트 페이지 id 캐시 (notion_archive.py)
~/.local/state/stock-report/barbell_bot.pid  — 봇 PID (단일 인스턴스 잠금)
~/.local/share/stock-report/stock_report.db      — SQLite 통합 저장소 (user_id 스코프, WAL)
                                                   └ 컬렉션: tax_records · portfolio_history
                                                     · qqqi_dividends · signal_outcomes · price_alerts · kr_mock_history
                                                   └ 문서: dca_weights · target_weights · leverage_state
                                                     · barbell_state · barbell_anchor
                                                     · portfolio_snapshot (파일 권위 + store 그림자)
                                                   (레거시 JSON 자동 마이그레이션 + 파일 미러 — store.py)
~/.local/share/stock-report/            — 런타임 데이터 (pending, paper_track + 레거시 JSON 원본)
~/.local/share/stock-report/paper_track.json     — A/B 페이퍼 트레이딩 기록 (meta vs rule)
~/reports/ml-cache/leverage_best_params.json     — Optuna 최적 파라미터 (UPRO·vol targeting)
~/reports/ml-cache/entry_score_params.json       — 진입점수 가중치 (캘리브레이션 채택 시 생성)
~/reports/ml-cache/entry_score_params_adaptive.json — 진입 임계값 적응 shadow (ADAPTIVE_ENTRY_ENABLED 시만 라이브 반영)
~/reports/ml-cache/longterm_policy_shadow.json   — 장기 보수적 레버리지 축소 shadow (ADAPTIVE_LONGTERM_ENABLED 시만 기록)
~/reports/ml-cache/structural_leverage_shadow.json — Tier3 구조적 레버리지 GO 권고 (ADAPTIVE_LEVERAGE_ENABLED 시만; /risk 표시·수동집행)
~/reports/ml-cache/factor_tilt_shadow.json        — Tier4 팩터 틸트 GO 보상 팩터 (ADAPTIVE_FACTOR_TILT_ENABLED 시만; 현재 NO-GO라 미생성)
~/reports/ml-cache/income_engine_shadow.json      — Tier5 인컴 엔진 GO (ADAPTIVE_INCOME_ENGINE_ENABLED 시만; 현재 NO-GO라 미생성)
~/reports/ml-cache/advice_blend_shadow.json      — MetaAllocator blend 신뢰도 shadow (ADAPTIVE_ADVICE_ENABLED 시만 기록)
~/reports/ml-cache/fundamental_scores.json       — 펀더멘털 점수 7일 캐시 (랭커 틸트용)
~/reports/ml-cache/fundamental_snapshots.jsonl   — 펀더멘털 주간 point-in-time 스냅샷
~/reports/ml-cache/institutional_snapshots.jsonl — 기관 매집 강도·13F 지분 주간 스냅샷 (델타 추적)
~/reports/ml-cache/earnings_snapshots.jsonl      — 어닝 컨센서스·리비전·서프라이즈·밸류 일별 point-in-time (실적/주가반응 예측 학습용)
~/reports/ml-cache/earnings_*.json               — earnings_data 종목별 요약 12h 캐시
~/reports/ml-cache/earnings_predictor.pkl        — 실적 서프라이즈 G3 모델 (엣지 게이트 통과 시만 — earnings_model_retrain)
~/reports/ml-cache/earnings_move_predictor.pkl   — 실적후 주가반응 G4 모델 (엣지 게이트 통과 시만)
~/reports/ml-cache/marcap/marcap-YYYY.parquet    — KR 전종목 시점별 시총패널 (1995~, raw GitHub fetch+캐시; kr_market_data)
~/reports/ml-cache/sp500_history.csv             — 美 S&P500 시점별 구성 이력 (fja05680, index_membership)
~/reports/ml-cache/edgar/                         — SEC EDGAR companyfacts·CIK맵 캐시 (edgar)
~/reports/ml-cache/kr_ranker_model.pkl           — KR 전용 랭커 모델 (KOSPI 대비 초과수익, safe_unpickle)
~/reports/ml-cache/policy_kr_mock.json           — KR 모의 선택 정책 가중치 (learner 채택 시 갱신, 클램프)
~/reports/ml-data/kr_mock_decisions.jsonl        — KR 모의 편입/퇴출 결정+근거 (불변 append-only, 학습/감사 — 절대 삭제 금지)
~/reports/ml-data/kr_mock_outcomes.jsonl         — KR 모의 결정 실현 보상(초과수익) (불변 append-only)
~/reports/ml-data/kr_mock_journal/YYYY-MM.md     — 사람용 편입/퇴출 저널 (월별 누적)
~/reports/ml-data/kospi200_members.jsonl         — KOSPI200 시점별 멤버십 forward 스냅샷 (Naver, naver_flow_snapshot)
~/reports/ml-data/kr_flow_snapshots.jsonl        — KR 투자자 수급 일별 스냅샷 (외인/기관, Naver)
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
- **자동 주문 집행은 모의(paper)에 한함** — `kiwoom_mock.py` 는 `mockapi.kiwoom.com` 도메인을
  하드락(`_assert_mock_url`)하고 실거래(`api.kiwoom.com`) 경로를 코드에 두지 않는다. 봇의 실계좌
  자동매매는 여전히 없음(권고만). `crons/kiwoom_mock_track.py` 는 `KIWOOM_MOCK_ENABLED=true` 일 때만 동작
- `portfolio_snapshot.json` writer 3종(`holding_manager._save`·`portfolio_sync_server`·`kiwoom_sync_rest`)은
  모두 `safe_io.atomic_write_json` + `safe_io.file_write_lock` 경유 — 직접 `json.dump`/in-place write 금지.
  (atomic rename 으로 torn read 방지 + 교차 프로세스 락으로 동시 쓰기 lost update 방지) → 이후 `store.shadow_doc` 비차단 동기화
- 레버리지/DCA 권고 안전장치: `barbell_strategy.leverage_dca_guard`(변동성 캡·절대 상한·낙폭 정지) +
  `fetch_qqq_data` stale 플래그(묵은 데이터 시 `run()`이 Phase 에스컬레이션 보류) — 튜닝은 `BARBELL_*` env var
- 크론 스케줄 단일 진실원: `deploy/crontab.stock-report` (변경 후 `crontab deploy/crontab.stock-report` 적용)
- **의존성**: `requirements.txt` 가 작동 .venv 의 정확한 핀(Python 3.11). 재구축 = `uv venv && uv pip install -r requirements.txt`.
  새 의존성 설치 시 `uv pip freeze > requirements.txt` 로 갱신 필수(미기록 시 재구축에서 소실). pandas 는 <3 고정(pykrx 호환).
- **`reports/` gitignore 퀴크**: `.gitignore` 가 `reports/` 를 무시 → reports/ 소스 신규 파일은 `git add -f` 필요(기존 추적 파일은 유지). 출력 `~/reports/` 와 소스 `reports/` 가 같은 패턴에 걸리는 레거시 — 신규 reports 모듈 추가 시 주의.
- 기록로그(tax/history/dividend/signal_outcomes/price_alerts)는 `store.py` 경유 — 직접 파일 R/W 금지
  (DB 경로 override: `STOCK_REPORT_DB` env var, 기본 `~/.local/share/stock-report/stock_report.db`)
- 설정 블롭(dca/target/leverage)은 store 권위 + 파일 미러(`store.save_doc`) — advisor 편집은
  `bot/stock_advisor._sync_editable_to_store()` 가 store로 reimport. 직접 `json.dump` 금지
- store 파일 미러는 `DEFAULT_USER` 만 기록 — 테스트 시 모듈 파일 경로 상수를 tmp로 리다이렉트할 것
  (라이브 `dca_weights.json` 등 덮어쓰기 방지)
