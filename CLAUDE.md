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
├── dashboard/   # 퀀트 터미널 Streamlit 웹 대시보드 (프로젝트 .venv 구동)
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
| `kis_stream.py` | KIS 실시간 시세 **읽기전용** WebSocket 상시 프로세스 — 실전 WS(`ops.koreainvestment.com:21000`) 하드락·체결(가격·거래량)/호가 → 캐시 coalesce flush. `REALTIME_ENABLED` 게이트·주문경로 0(grep 강제)·재접속 백오프·watchdog 재기동 | `~/.cache/kis_realtime_quotes.json` |
| `safe_io.py` | 멀티프로세스 안전 파일 I/O — atomic write + 교차 프로세스 쓰기 락(portfolio_snapshot writer 공용) | `<path>.lock` |
| `notify.py` | 텔레그램 발송 단일 진실원 — send_telegram(4096 분할·토큰 마스킹)·send_photo (봇 제외 전 모듈 공용) | — |
| `providers/market_data.py` | 시장 데이터 수집층 — fetch_qqq_data·rsi·vix·fear_greed·ma200·portfolio_value·환율·캐시·leverage_state (barbell 에서 분리, 재export 호환) | `~/.cache/barbell_anchor·last_prices.json` |
| `kiwoom_mock.py` | 키움 **모의투자** 어댑터 — 모의 도메인(`mockapi.kiwoom.com`) 하드락 + 토큰·잔고(kt00018)·주문(kt10000/kt10001). 실거래 경로 없음 | — |
| `kis_mock.py` | 한국투자증권(KIS) **해외주식 모의투자** 어댑터 — 모의 도메인(`openapivts.koreainvestment.com:29443`) 하드락 + 토큰 디스크영속·해외 잔고/현재가/주문(정수주·지정가·hashkey). CANO+ACNT_PRDT_CD 필수(미설정 fail-closed). 실거래 경로 없음 | `~/.cache/kis_mock_token.json` |
| `providers/earnings_data.py` | 어닝·컨센서스·밸류에이션 데이터층 — yfinance(US 전체 무료: 서프라이즈·포워드 컨센서스·★리비전 모멘텀·PER/PBR/PSR/ROE/EPS/배당·배당CAGR) / KR(.KS) 열화모드(밸류·배당만). 결측 graceful·12h 캐시 | `~/reports/ml-cache/earnings_*.json` |
| `providers/kr_market_data.py` | KR 생존편향제거 데이터층 — **marcap**(연도별 parquet, 1995~ 전종목 시점별 시총·OHLCV·상폐포함) + **FDR KRX-DELISTING**(상폐 라벨·사유). top_n_by_marcap·ohlcv_from_marcap·distress_delistings. **pykrx 는 이 서버서 불가(KRX 403)** | `~/reports/ml-cache/marcap/*.parquet` |
| `providers/index_membership.py` | 교차시장 시점별 멤버십 — 美 S&P500(fja05680, 1996~ 생존편향0)·KR(marcap 위임). members_asof·change_events·membership_intervals(생존편향제거 마스킹) | `~/reports/ml-cache/sp500_history.csv` |
| `providers/edgar.py` | SEC EDGAR 재무층 — companyfacts(상폐기업 재무 보존·무료) → fundamental_trends(매출YoY·순마진·부채추세, 무룩어헤드). 美 퇴출예측 피처원 | `~/reports/ml-cache/edgar/` |
| `providers/naver_kr.py` | KR 수급(외인/기관/개인 순매수)+KOSPI200 멤버십(Naver — pykrx 공백 복구, 서버서 동작). investor_flow_features·kospi200_members. **Naver HTML=EUC-KR** | — |
| `providers/kis_quote.py` | KIS **실계좌 시세 read-only** REST — 실전 도메인(`openapi.koreainvestment.com:9443`) 하드락·현재가·10단계 호가·거래량(**KR·美 모두 무료 실시간**). 주문 경로 0(grep 강제)·`REALTIME_ENABLED` 게이트·실전키 fail-closed | `~/.cache/kis_quote_token.json` |
| `providers/realtime_quotes.py` | 실시간 캐시 **읽기전용 클라이언트** = 폴백 단일 seam. get_price/orderbook/best/volume, 2단 신선도(heartbeat+심볼 ts). stale·비활성·없음 → None → 소비자 yfinance 폴백. 예외 무발 | `~/.cache/kis_realtime_quotes.json` |

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
| `bot/evolve_command.py` | /evolve — 모의 자기개선 진화 렌더 (KR+US `evolution.evolution_summary` → verdict·순비용 IC·스파크 추세·채택 이력) — owner 전용·표시·무엣지면 정직 |

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
| `crons/notion_sync.py` | Notion 대시보드 동기화 (리포트 23:00 이후) + 리포트 아카이빙 호출. **히어로 KPI 밴드**(Phase·QQQ낙폭·내포트·DCA 4열 컬러 콜아웃)·로컬 PNG 네이티브 임베드(파일 업로드 API)·리포트 섹션 구조화(h3/불릿)·**보유종목 DB upsert**·안전 스왑(append-then-delete·child_database 보존) | 평일 23:30 UTC |
| `crons/notion_archive.py` | 일일 리포트 → Notion 월(`26/06`)/주(`4주차`) 계층 페이지 누적 아카이빙 (멱등 upsert, 대시보드와 독립) | notion_sync 가 호출 |
| `crons/news_spike_detector.py` | 속보 수집 + 급증 감지 + 텔레그램 알림 (+ 실시간 시세 동반표시) | 매 1분 |
| `scripts/kis_stream_watchdog.sh` | 실시간 시세 WS 상시 프로세스(kis_stream) 재기동 — `REALTIME_ENABLED=true` 시만 기동(opt-in·꺼지면 no-op) | 매 1분 |
| `crons/kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 | 평일 23:35 UTC |
| `crons/kiwoom_mock_track.py` | 국내주식 자동 페이퍼트레이딩 (키움 **모의투자** — 신호 기반 리밸런스·모의 도메인 하드락·편입/퇴출 근거 원장 적재). **회전율 억제**(무거래밴드+랭크 히스테리시스)·**거래비용 적립**(수수료+증권거래세→리포트 계기) | 평일 00:30 UTC |
| `crons/kiwoom_mock_report.py` | 국내 모의 일일 현황 보고 (NAV·손익·편입/퇴출 사유·누적 vs KOSPI·MDD vs 지수) + `/paper kr` 공용 | 평일 06:40 UTC |
| `crons/kr_mock_learn.py` | KR 모의 정책 강화 — 보상 백필 + ★목적함수(아웃퍼폼·MDD≤지수) OOS 게이트 재학습 | 토 02:00 UTC |
| `crons/us_mock_track.py` | 미국주식 자동 페이퍼트레이딩 (KIS 해외 모의 — us_policy 선택 + 바벨 배분·정수주 리밸런스·`Ledger("us_mock")` 결정+근거 적재) | 평일 15:00 UTC (미 개장 후) |
| `crons/us_mock_report.py` | 미국 모의 일일 현황 + **로직 평가 스코어카드**(NAV·vs QQQ·MDD·편입/퇴출 적중률·실현 IC) + `/paper us` 공용 | 평일 21:30 UTC |
| `crons/us_mock_learn.py` | US 모의 정책 강화 — 보상 백필(편입 초과·퇴출 회피) + ★목적함수 OOS 게이트·챔피언-챌린저 재학습 | 토 03:00 UTC |
| `crons/weekly_kr_ranker_retrain.py` | KR 전용 랭커(KOSPI 대비 초과수익) 주간 재학습 (Purged WF·OOS IC) | 토 03:30 UTC |
| `crons/longterm_adaptive_eval.py` | 장기 전략 ★목표(vs QQQ 아웃퍼폼·MDD≤지수) 라이브 스코어카드 + 악화 시 보수적 레버리지 축소 shadow 권고 | 토 04:00 UTC |
| `crons/leverage_structural_eval.py` | Tier3 구조적 레버리지 ★게이트 재검증 (`backtest/leverage_structural_backtest` SPY+QQQ × 그리드 낙폭예산·DSR·PBO) — GO 시 권고 레버리지 shadow (표시·수동, 자동집행 0) | 토 04:15 UTC |
| `crons/factor_premium_eval.py` | Tier4 팩터 프리미엄 틸트 ★게이트 재검증 (`backtest/factor_premium_backtest` 롱온리 ETF vs SPY DSR 다중검정·약세슬라이스) — GO 팩터만 shadow. **현재 NO-GO**(밸류·사이즈·퀄리티·최소변동 SPY 미돌파, 모멘텀=SPMO 기보유) | 토 04:45 UTC |
| `crons/income_compounding_eval.py` | Tier5 인컴 복리 재투자 ★게이트 재검증 (`backtest/income_compounding_backtest` 커버드콜 QYLD vs 총수익 QQQ 세전/세후·재투자vs비축) — GO 시 shadow. **현재 NO-GO**(인컴 엔진 세후 CAGR −12.9%p 열위·방어기능). 재투자>현금비축(+33%)은 항상참 규율 | 토 05:00 UTC |
| `crons/concentration_validated_eval.py` | Tier6 검증된 집중 ★게이트 재검증 (`backtest/concentration_validated_backtest` 무스킬 랜덤집중 vs 분산 MC·DSR 다중검정·무생존편향 섹터ETF) — GO 시 shadow. **현재 NO-GO**(무스킬 집중은 보상없는 위험·분산 이길확률 26%; 검증된 집중=구조레버리지뿐) | 토 05:15 UTC |
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
| `ml/adaptive/` | 적응형 학습 공유 프레임워크 — policy(클램프)·ledger(불변 원장)·reward(★목적함수)·learner(OOS게이트)·regime(최근성)·champion_challenger·**evolution(진화 텔레메트리 — 주간 학습 append-only 이력 + 라이브 스냅샷 IC·적중·누적엣지 → 정직 verdict; `/evolve`·대시보드 공용·순수)** | `~/reports/ml-cache/policy_*.json`·`~/reports/ml-data/{kr,us}_mock_learning.jsonl` |
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
/status              현황 — Phase·QQQ·총액·F&G + 핵심 수치 (신선도 한 줄 표기 · 구 /summary 통합)
/phase [sim [모드]]  Phase 미터 + 행동 지침 · `/phase sim` = 시장 시뮬(구 /sim 통합)
/report              전체 바벨 리포트 (항상 실시간)
/accum [us|kr|TICKER...]  기관 매집 추적 — OBV·CMF·13F 매집 강도 랭킹 (기본: 보유+美+韓)
/earnings [TICKER]   실적·밸류에이션 — PER·PBR·PSR·ROE·EPS·배당성장 + 서프라이즈·컨센서스·리비전·PEAD (정보형)

── 포트폴리오 ────────────────────────────────────
/portfolio           보유현황 + 개별 종목 P&L + 총액 (+ 리스크 1줄)
/rebalance [dca|sgov]  안전마진 + 비중 진단 + DCA 조정 · `dca`=오늘 DCA 배분 · `sgov`=SGOV 실탄 비교 (구 /dca·/sgov 통합)
/risk                포트폴리오 위험 분석 — 변동성·위험기여·유효분산·팩터노출 + 성장최적 레버리지(Kelly·낙폭예산) — owner 전용·표시
/history             성과 히스토리 (1d/7d/30d/90d)

── 주문 & 모의 ───────────────────────────────────
/order               소수점 매수 주문서 (키움 즉시 입력)
/card                포트폴리오 카드 이미지 — 배분 도넛·총액·종목별 비중/수익 (sendPhoto) — owner 전용
/paper [kr|us]       모의 페이퍼트레이딩 — `kr`=국내(NAV·vs KOSPI)·`us`=미국(NAV·vs QQQ·적중률·IC)·생략=둘 다 (구 /mock·/usmock 통합) — owner 전용
/evolve              모의 자기개선 "진화" — KR+US 정책 학습 verdict(콜드스타트/관찰/약한엣지/무엣지) + 순비용 IC·누적엣지·주별 OOS 추세·채택 이력 — owner 전용·표시(무엣지면 정직 공개)

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

── ML·신호 (정보·표시용 — 매매신호 아님) ─────────
/signals [rank|entry|intraday|lev|meta]  무엣지 신호 우산 (구 6개 통합)
   rank [retrain]                      NASDAQ100 LightGBM 종목 랭킹
   entry [포트|us50|kr|watch|TICKER]    진입 타점 분석
   intraday [1m|5m|15m] [kr|us100|TICKER]  단기봉 신호
   lev [retrain]                       레버리지 ETF 진입 분석 (QLD/TQQQ/SOXL/UPRO)
   meta                                ML 통합 포트폴리오 배분 (MetaAllocator)
※ 6티어 검증상 종목선택·장중타이밍 무엣지 → **정보·표시용**(출력 끝 정직 라벨). 검증 통과 공격은 구조적 레버리지(/risk·Tier3)뿐. (구 /mlreport 는 삭제 — cmd_mlreport 함수만 유닛 유지)

📎 PDF·이미지 전송 → 자동 파싱 → /holding apply 또는 /tax import apply

> **신선도 계약**: `/status`·`/phase`·`/portfolio`·`/rebalance`(dca·sgov 포함)·`/risk` 는 **실시간**(결정·평가 명령은 5분 캐시 우회 force-fresh) — 출력 끝 `🕒 기준시각·실시간/캐시` 표기. `/history` 는 일별(크론). `REALTIME_ENABLED` 시 QQQ·보유 해외종목은 KIS WS 실시간가 오버레이(`/holding` 은 ⚡ 표시).

── 읽기전용 게스트 (STOCK_BOT_GUEST_IDS) ─────────
/market              시황 브리핑 — 국면·낙폭·RSI·VIX·F&G (사실형, 처방 없음)
/indicators TICKER   종목 기술적 지표 — RSI·이동평균·모멘텀·52주 위치
/my [add|del]        내 포트폴리오 — 생략=평가(평가액·손익)·`add TICKER 주수 평단가`=추가·`del TICKER`=삭제 (구 /myadd·/myremove·/myportfolio 통합, user_id 스코프 store)
/help                게스트 도움말
※ 게스트는 위 4개만 허용 — 주문·신호·종목관리·세금·AI상담 전면 차단 (법적 안전)
※ 게스트 포트폴리오는 본인 chat_id 네임스페이스에 격리 (소유자 portfolio_snapshot과 분리)
```

> **메뉴 scope 분리** (setMyCommands): **소유자 채팅엔 소유자 메뉴(20), default·all_private_chats 엔 게스트 메뉴(4)** — `BotCommandScopeChat`(소유자)가 우선 적용돼 **소유자 메뉴엔 `/market`·`/my` 가 안 보임**(권한 아닌 표시만 분리 — 소유자는 입력 시 사용 가능). `/indicators` 는 종목 기술지표가 소유자에도 유용해 **소유자 메뉴에도 노출**(게스트 메뉴엔 그대로).
>
> **출력 포맷** (단일 진실원 `fmt.py`): 전 명령이 공통 레이어 경유 — `pct/money/spct`(0·음수0·부호 버그 차단), 짧은 구분선, **HTML 리치텍스트**(`send_html`·parse_mode=HTML): 핵심 `<b>굵게</b>`, 표는 `<pre>등폭</pre>`, 긴 리포트(/report·/rebalance)는 `<blockquote expandable>접기</blockquote>`(`_send_collapsible`), `/history`는 스파크라인. **모바일 주의**: `━ ─` 는 ambiguous-width 2칸 → 공백 정렬 의존 금지(정렬 필요표는 pre). 크론 공유 빌더(paper·history·barbell report)는 `html=` 파라미터로 텔레그램만 굵게, 크론은 평문. 이미지: 일일 PNG(`report_charts.build_portfolio_dashboard`, 히어로 KPI 밴드) + 온디맨드 `/card`(`build_portfolio_card`, 봇이 `.venv` subprocess 렌더 — hermes venv 불변).
>
> **하위호환 alias** (메뉴 비노출): `/summary`→`/status` · `/sim`→`/phase sim` · `/mock`→`/paper kr` · `/usmock`→`/paper us` · `/dca`→`/rebalance dca` · `/sgov`→`/rebalance sgov` · `/ranking·/entry·/intraday·/leverage·/meta`→`/signals …` · `/myadd·/myremove·/myportfolio`→`/my …` · `/dividend`→`/holding dividend` · `/apply_snapshot`→`/holding apply`

## 역할 (telegram_bot)

| 역할 | chat_id | 권한 |
|------|---------|------|
| owner | `STOCK_BOT_CHAT_ID` | 전체 (주문·신호·종목관리·세금·AI상담·첨부) |
| guest | `STOCK_BOT_GUEST_IDS` (쉼표구분) | 읽기전용 — `/market` `/indicators` `/my` `/help` (`_GUEST_COMMANDS`) |
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
| `KOREA_MOCK_ENABLED` | — | `false` (KIS 해외 모의 페이퍼트레이딩 루프 활성화. true 여야 주문 집행) |
| `KOREA_MOCK_API_KEY` / `KOREA_MOCK_API_SECRET` | — | — (없으면 `KOREA_API_KEY/SECRET` 재사용 — KIS 앱키) |
| `KOREA_MOCK_ACCOUNT_NO` | — | — (KIS 모의 계좌 `CANO-ACNT_PRDT_CD` 형식. **필수** — 미설정 시 잔고/주문 fail-closed) |
| `US_MOCK_UNIVERSE` / `US_MOCK_MAX_POS` / `US_MOCK_INVEST` / `KOREA_MOCK_SEED` | — | Nasdaq기본 / `5` / `0.9` / `100000` (US 모의 전략 파라미터·시드 USD) |
| `{KR,US}_MOCK_REBAL_BAND` / `{KR,US}_MOCK_EXIT_BUFFER` | — | `0.25` / `2` (회전율 억제 — 무거래 밴드[목표比 ±25% 벗어날 때만 조정]·랭크 히스테리시스[보유종목 top-N+2 안이면 유지]. 크론 주기 불변·잔챙이 churn 제거) |
| `{KR,US}_MOCK_BUY_BPS` / `{KR,US}_MOCK_SELL_BPS` | — | KR `2`/`20` · US `15`/`15` (거래비용 bps — 수수료+KR 증권거래세. `ml/adaptive/costs.py`. 리포트 누적비용·회전율 계기 + 보상 fwd_excess net-of-cost 차감) |
| `REALTIME_ENABLED` | — | `false` (KIS 실시간 시세 수신·소비 **마스터 게이트**. off면 stream 미기동·전 소비자 yfinance 폴백) |
| `REALTIME_US_ENABLED` | — | `false` (美 해외 실시간 스트림. **미국=무료 실시간 0분지연**·별도 신청 불필요(open-trading-api 확정). off면 美 미구독→yfinance) |
| `REALTIME_KR_MAX` / `REALTIME_US_MAX` / `REALTIME_FLUSH_SECS` | — | `10` / `10` / `1.0` (WS 워치리스트 시장별 캡·캐시 flush 주기. 41심볼/세션 제한 대응) |
| `REALTIME_STALE_S` / `REALTIME_HEARTBEAT_STALE_S` / `REALTIME_QUOTE_STALE_S` | — | `60` / `120` / `10` (소비자 신선도 임계 초 — 초과 시 yfinance/정적 폴백) |
| `REALTIME_FILLS_ENABLED` / `REALTIME_HTS_ID` | — | `false` / — (실계좌 체결통보 알림. HTS ID = 체결통보 tr_key. 실거래 체결 시 텔레그램 push — **포트폴리오 미수정**·수동 반영) |
| `ADAPTIVE_ENTRY_ENABLED` | — | `false` (해외 진입 임계값 적응 학습 shadow 를 라이브에 반영. off면 shadow만·라이브 불변) |
| `ADAPTIVE_LONGTERM_ENABLED` | — | `false` (장기 전략 악화 시 보수적 레버리지 축소 shadow 기록. off면 평가·권고만) |
| `ADAPTIVE_LEVERAGE_ENABLED` | — | `false` (Tier3 구조적 레버리지 GO 권고를 shadow 기록 → `/risk` 표시. off면 게이트 평가·텔레그램만. **자동집행은 항상 없음** — 실계좌 수동) |
| `ADAPTIVE_FACTOR_TILT_ENABLED` | — | `false` (Tier4 팩터 틸트 GO 시 보상 팩터 shadow 기록 → `/risk` 표시. 현재 게이트 NO-GO라 무기록. 자동집행 항상 없음) |
| `ADAPTIVE_INCOME_ENGINE_ENABLED` | — | `false` (Tier5 인컴 엔진 GO(세후 총수익 우위·희귀) 시 shadow → `/risk`. 현재 NO-GO. 자동집행 항상 없음) |
| `ADAPTIVE_CONCENTRATION_DISPLAY_ENABLED` | — | `false` (Tier6 집중 GO(무스킬 집중>분산·희귀) 시 shadow. 현재 NO-GO. 과집중 경고는 `/risk` 상시. 자동집행 항상 없음) |
| `TIER6_SEED` / `TIER6_MC_SAMPLES` | — | `6` / `500` (집중 게이트 몬테카를로 재현 seed·표본수) |
| `TIER3_RF_FALLBACK` / `TIER3_LETF_SPREAD` / `TIER3_LETF_EXPENSE` / `TIER3_BUDGET` | — | `0.03` / `0.005` / `0.009` / `0.50` (레버리지 게이트 비용·낙폭예산 가정) |
| `ADAPTIVE_ADVICE_ENABLED` | — | `false` (MetaAllocator A/B 우위 시 blend 신뢰도 shadow 기록. off면 평가·권고만) |
| `SYNC_TOKEN` | — | — (portfolio_sync_server 인증) |
| `SYNC_PORT` | — | `8765` |
| `NOTION_TOKEN` | — | — (Notion 대시보드 동기화·아카이빙. 없으면 notion_sync 스킵) |
| `NOTION_ARCHIVE_ROOT_ID` | — | — (아카이브 루트 페이지 강제 지정. 미설정 시 대시보드 부모 아래 자동탐색·생성 후 `~/.cache` 캐시) |
| `NOTION_ARCHIVE_PARENT_ID` | — | — (루트를 만들 부모. 기본: 대시보드의 부모 페이지) |
| `SAVE_TICKER_API_BASE` | — | `https://saveticker.com/api` (뉴스 + 경제캘린더 `/calendar/events`) |
| `DART_API_KEY` | — | — (DART OpenAPI 키 — KR 공시. 없으면 대시보드 공시탭 graceful 안내) |
| `DASHBOARD_ENABLED` | — | `false` (퀀트 터미널 streamlit 워치독 기동 게이트. true 여야 상시구동·opt-in) |
| `DASHBOARD_PASSWORD` | — | — (대시보드 접근 비번. **미설정 시 fail-closed 전면 차단**) |
| `DASHBOARD_PORT` | — | `8501` (127.0.0.1 바인드 — 외부는 SSH 터널/reverse proxy) |
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

## 퀀트 터미널 (웹 대시보드)

`dashboard/` — Streamlit 웹 대시보드. **봇과 달리 프로젝트 `.venv` 로 구동**(풀 ML 스택 보유 → sklearn/lightgbm/matplotlib subprocess 우회 불필요). 기존 `providers/`·`reports/`·`ml/` 함수를 그대로 재사용하는 **표시 레이어**(주문 집행 0·무엣지 라벨 유지).

| 파일 | 역할 |
|------|------|
| `dashboard/app.py` | Streamlit 엔트리 — 테마 주입 → 인증 게이트 → 사이드바(종목 퀵픽·신선도·새로고침·**보유 워치리스트**) → `st.navigation` 5페이지. 최상단 `sys.path.insert(루트)` 필수(streamlit run `sys.path[0]=스크립트dir` 함정) |
| `.streamlit/config.toml` | **Terminal Noir 테마** (TradingView/토스증권) — 다크 블루블랙·일렉트릭블루 액센트·틸그린/코랄레드 시맨틱·Pretendard(한글)+JetBrains Mono(등폭 수치) fontFaces·radius/border/chart 팔레트 |
| `dashboard/theme.py` | 테마 단일 진실원 — 팔레트 상수 + **순수 HTML/SVG 빌더**(ticker_hero·rating_gauge 반원속도계·sparkline·watchlist, 테스트가능) + `apply_plotly_theme`(차트 다크 템플릿) + `inject_global_css`(streamlit lazy — import 시 미로드해 charts 순수성 유지) |
| `dashboard/pages/` | 멀티페이지(비활성 페이지 미실행=lazy) — `home`(글랜스: 포트 ticker-hero+배분도넛+클릭 보유표→종목분석 자동이동+Phase+오늘일정)·`portfolio`(리스크 KPI+위험기여/팩터β 막대+½Kelly밴드+도넛)·`ticker`(심볼 히어로+**기술신호 게이지**+가격라인+MA·밸류밴드 불릿·서프라이즈 막대·기관·공시·실적)·`market`(경제캘린더+뉴스)·`research`(스크리너+백테스트 이퀴티+**🧬 정책 학습 곡선**: KR/US 모의 자기개선 verdict·순비용 IC·OOS 곡선·채택 이력) |
| `dashboard/charts.py` | plotly 차트 빌더(순수 함수·단위테스트·theme 다크 템플릿 적용) — allocation_donut·price_line·hbar·signed_bars·value_bullet·equity_curve |
| `dashboard/cached.py` | `st.cache_data` 래퍼(멀티페이지 공용·TTL 15~60분) — valuation/financials/.../risk_struct/ohlc |
| `dashboard/data.py` | 포트폴리오/Phase 상태 + 스케일 명시 포맷터(f_frac_pct vs f_pct·부호버그 차단). streamlit 미import → 테스트가능 |
| `dashboard/views.py` | 모듈별 provider 래퍼(graceful·provider 내부 import) — risk_summary(구조화)·screener·backtest 등 |
| `providers/intrinsic.py` | DDM·RIM 내재가치 닫힌해 + r/g 밴드 (DDM은 고배당주만·payout<40% 플래그) |
| `providers/econ_calendar.py` | 경제 일정 (saveticker `/calendar/events`·키불요·한글) |
| `providers/insider.py` | 내부자거래 (SEC Form 4·edgar 재사용·parse_form4 순수) + 최근 SEC 공시 |
| `providers/dart.py` | KR 공시 (DART OpenAPI·corpCode 매핑·`DART_API_KEY` 없으면 graceful) |

**5페이지(멀티페이지·plotly 차트화):** 🏠홈(포트 글랜스)·💼포트폴리오(리스크 시각화)·🔍종목 분석(가격차트+밸류/재무/기관/공시/실적 서브탭)·🗓️시장·캘린더(경제+뉴스)·🔬리서치(랭킹 스크리너+ML 백테스트). 검증: `tests/test_dashboard*.py` — data/views 순수로직 + **charts 단위** + **페이지 렌더 AppTest(반드시 비루트 cwd**·streamlit sys.path 함정 가드).

**구동:** `bash scripts/run_dashboard.sh` (수동) 또는 `scripts/dashboard_watchdog.sh`(크론 매 1분·`DASHBOARD_ENABLED=true` opt-in·streamlit health 재기동). **활성화 = `.env` 에 `DASHBOARD_PASSWORD`(필수·fail-closed) + `DASHBOARD_ENABLED=true`.** 127.0.0.1 바인드 → 외부는 SSH 터널(`ssh -L 8501:127.0.0.1:8501`) 또는 reverse proxy(caddy TLS+auth). 봇과 별개 프로세스 → 봇 재시작 무관.

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
~/.cache/notion_holdings_db.json        — Notion 보유종목 DB id 캐시 (notion_sync._sync_holdings_db — 1회 생성·일일 행 upsert)
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
~/reports/ml-cache/concentration_validated_shadow.json — Tier6 집중 GO (ADAPTIVE_CONCENTRATION_DISPLAY_ENABLED 시만; 현재 NO-GO라 미생성)
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
~/reports/ml-data/us_mock_decisions.jsonl        — US 모의 편입/퇴출 결정+근거 (불변 append-only, point-in-time features — 절대 삭제 금지)
~/reports/ml-data/us_mock_outcomes.jsonl         — US 모의 결정 실현 보상(초과수익 vs QQQ·side-aware 정답) (불변 append-only)
~/reports/ml-data/us_mock_journal/YYYY-MM.md     — US 사람용 편입/퇴출 저널 (월별 누적)
~/reports/ml-data/us_mock_learning.jsonl         — US 주간 학습 진화 이력 (채택여부·챔피언/챌린저 OOS·순비용 스냅샷 — evolution.record_learning, 불변 append-only, /evolve·대시보드 학습곡선 원천)
~/reports/ml-cache/policy_us_mock.json           — US 모의 선택 정책 가중치 (learner OOS 게이트·챔피언-챌린저 채택 시 갱신, 클램프)
~/.cache/kis_mock_token.json                     — KIS 해외 모의 OAuth 토큰 디스크 영속 (발급 레이트리밋 회피)
~/.cache/kis_realtime_quotes.json                — 실시간 시세 캐시 (kis_stream writer·realtime_quotes reader; symbol→{price,bid,ask,bids/asks,volume,ts,delayed}+__heartbeat__. safe_io atomic)
~/.cache/kis_quote_token.json                    — KIS 실전 시세 OAuth 토큰 디스크 영속 (모의 토큰과 별개·실전 앱키)
~/.cache/kis_fills.jsonl                          — 실계좌 체결통보 기록 (kis_stream, REALTIME_FILLS_ENABLED 시 — append-only·알림용·AES 복호화)
~/reports/ml-data/kr_mock_outcomes.jsonl         — KR 모의 결정 실현 보상(초과수익) (불변 append-only)
~/reports/ml-data/kr_mock_learning.jsonl         — KR 주간 학습 진화 이력 (채택여부·챔피언/챌린저 OOS·순비용 스냅샷 — evolution.record_learning, 불변 append-only, /evolve·대시보드 학습곡선 원천)
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
- **실시간 시세는 실계좌 키 read-only 전용** — `providers/kis_quote.py`·`kis_stream.py` 는 실전 도메인
  (`openapi…:9443`·`ops…:21000`)을 하드락하되 **주문 URL/TR/함수가 코드에 없다**(테스트가 소스 grep 으로 강제).
  실시간은 *가격 숫자*만 제공 → 알림·표시·페이퍼 지정가에만 쓰이고 실계좌 집행은 불변(없음). `REALTIME_ENABLED`
  off(기본)면 전 소비자가 yfinance 로 투명 폴백 — 실시간 장애가 기존 흐름을 깨지 않는다
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
