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
| `kis_stream.py` | KIS 실시간 시세 **읽기전용** WebSocket 상시 프로세스 — 실전 WS(`ops.koreainvestment.com:21000`) 하드락·체결(가격·거래량)/호가 → 캐시 coalesce flush. `REALTIME_ENABLED` 게이트·주문경로 0(grep 강제)·재접속 백오프·watchdog 재기동 + **틱→1분봉 sink**(`INTRADAY_BARS_ENABLED` 시 `providers/intraday_bars.BarAggregator` — 단기 모의 데이터층) | `~/.cache/kis_realtime_quotes.json` |
| `safe_io.py` | 멀티프로세스 안전 파일 I/O — atomic write + 교차 프로세스 쓰기 락(portfolio_snapshot writer 공용) | `<path>.lock` |
| `ticker_names.py` | 종목 티커↔회사명 **단일 진실원**(표시·검색 공용) + **매크로 자산 판별·단위**(`MACRO_UNITS`·`is_macro`·`macro_unit` — 환율 ₩·금 $/oz·금리 % · 대시보드 뷰 분기와 히어로 단위 공유) — 큐레이트 EN/KO/KR 시드(**US 대형주 ~70 확장: 버크셔·JPM·월마트·비자·J&J·코카콜라·인기 ETF 등 한글별칭 포함** → universe 118; **L: S&P500 전체(`sp500_seed` ~503) 병합 → universe ~543**; **매크로 자산 시드: 환율(KRW=X)·금(GC=F)·은(SI=F)·유가(CL=F)·비트코인(BTC-USD)·이더(ETH-USD)·미10년물(^TNX)·달러인덱스·VIX·지수 — 한글별칭 "금/비트코인/환율/유가" 검색**) + 역인덱스 + yfinance 디스크캐시(graceful). `display_name`(US 영문·.KS 한글)·`label`(`회사명 (티커)`·maxlen)·`resolve`(한/영/티커→티커)·`normalize_input`(자유입력→정규 티커\|None·티커형은 정확매칭만→부분매칭 오염 차단·시드밖 리터럴 통과)·`search`. `fmt.name`·대시보드 검색(accept_new_options)·리포트·PNG·노션 공용. 경량(lazy yfinance) | `~/reports/ml-cache/ticker_names.json` |
| `notify.py` | 텔레그램 발송 단일 진실원 — send_telegram(4096 분할·토큰 마스킹)·send_photo (봇 제외 전 모듈 공용) | — |
| `providers/market_data.py` | 시장 데이터 수집층 — fetch_qqq_data·rsi·vix·fear_greed·ma200·portfolio_value·환율·캐시·leverage_state (barbell 에서 분리, 재export 호환) | `~/.cache/barbell_anchor·last_prices.json` |
| `kiwoom_mock.py` | 키움 **모의투자** 어댑터 — 모의 도메인(`mockapi.kiwoom.com`) 하드락 + 토큰·잔고(kt00018)·주문(kt10000/kt10001). 실거래 경로 없음 | — |
| `kis_mock.py` | 한국투자증권(KIS) **해외주식 모의투자** 어댑터 — 모의 도메인(`openapivts.koreainvestment.com:29443`) 하드락 + 토큰 디스크영속·해외 잔고/현재가/주문(정수주·지정가·hashkey). CANO+ACNT_PRDT_CD 필수(미설정 fail-closed). 실거래 경로 없음 | `~/.cache/kis_mock_token.json` |
| `providers/earnings_data.py` | 어닝·컨센서스·밸류에이션 데이터층 — yfinance(US 전체 무료: 서프라이즈·포워드 컨센서스·★리비전 모멘텀·PER/PBR/PSR/ROE/EPS/배당·배당CAGR) / KR(.KS) 열화모드(밸류·배당만). 결측 graceful·12h 캐시 | `~/reports/ml-cache/earnings_*.json` |
| `providers/kr_market_data.py` | KR 생존편향제거 데이터층 — **marcap**(연도별 parquet, 1995~ 전종목 시점별 시총·OHLCV·상폐포함) + **FDR KRX-DELISTING**(상폐 라벨·사유). top_n_by_marcap·ohlcv_from_marcap·distress_delistings. **pykrx 는 이 서버서 불가(KRX 403)** | `~/reports/ml-cache/marcap/*.parquet` |
| `providers/index_membership.py` | 교차시장 시점별 멤버십 — 美 S&P500(fja05680, 1996~ 생존편향0)·KR(marcap 위임). members_asof·change_events·membership_intervals(생존편향제거 마스킹) | `~/reports/ml-cache/sp500_history.csv` |
| `providers/edgar.py` | SEC EDGAR 재무층 — companyfacts(상폐기업 재무 보존·무료) → fundamental_trends(매출YoY·순마진·부채추세, 무룩어헤드). 美 퇴출예측 피처원 | `~/reports/ml-cache/edgar/` |
| `providers/naver_kr.py` | KR 수급(외인/기관/개인 순매수)+KOSPI200 멤버십(Naver — pykrx 공백 복구, 서버서 동작). investor_flow_features·kospi200_members. **Naver HTML=EUC-KR** | — |
| `providers/news_labels.py` | **LLM 뉴스 구조화 라벨층** — 수집 뉴스 → {티커,유형,방향,강도} point-in-time JSONL(published/labeled 시각 보존·무룩어헤드) + `news_axis`(방향×강도 감쇠합 [0,1]). 환각 방어(입력 태그 밖 티커 폐기·enum 검증). LLM=**피처 생성기 한정**(선택/타이밍 위임 금지 — 재현불가 출력은 백테스트 불성립) | `~/reports/ml-data/news_llm_labels.jsonl` |
| `providers/kis_quote.py` | KIS **실계좌 시세 read-only** REST — 실전 도메인(`openapi.koreainvestment.com:9443`) 하드락·현재가·10단계 호가·거래량(**KR·美 모두 무료 실시간**). 주문 경로 0(grep 강제)·`REALTIME_ENABLED` 게이트·실전키 fail-closed | `~/.cache/kis_quote_token.json` |
| `providers/realtime_quotes.py` | 실시간 캐시 **읽기전용 클라이언트** = 폴백 단일 seam. get_price/orderbook/best/volume, 2단 신선도(heartbeat+심볼 ts). stale·비활성·없음 → None → 소비자 yfinance 폴백. 예외 무발 | `~/.cache/kis_realtime_quotes.json` |
| `providers/intraday_bars.py` | 단기 1분봉 데이터층 — kis_stream 틱→OHLCV 집계(누적 볼륨 차분·v_anom/v_partial)·JSONL bar store reader(5m 리샘플·yfinance 폴백)·분대별 거래량 프로파일·심볼 변환 단일 진실원(base_symbol/to_yf/market_of) | `~/reports/ml-data/intraday_bars/*.jsonl` |
| `providers/intraday_universe.py` | 단기 동적 유니버스 스캐너 ("stocks in play") — KR KIS 거래대금 순위(+필터: 거래대금·가격·보통주만·ETF/스팩 제외)·US 히트맵 스냅샷 재사용 → \|등락\| 상위 top-K. 히스테리시스(보유 유지)·실패 시 정적 `INTRADAY_UNIVERSE_*` 폴백. US 단기 레버리지 on 시 기초자산과 체결 ETF를 함께 watchlist 편입 | `~/.cache/intraday_universe.json` |

**bot/ (텔레그램 서브커맨드)**
| 파일 | 역할 |
|------|------|
| `bot/holding_commands.py` | /holding 서브커맨드 (buy·sell·target·dca·dividend·apply) |
| `bot/tax_commands.py` | /tax 서브커맨드 (sim·sell·history·delete·import) |
| `bot/attachment_parser.py` | PDF/이미지 OCR 파싱, pending 파일 관리 |
| `bot/price_alerts.py` | 알림 CRUD + check_alerts() |
| `bot/order_generator.py` | Phase 기반 소수점 매수 주문서 생성 |
| `bot/stock_advisor.py` | AI 상담 프롬프트 실행 — source_digest DATA블록 인젝션 방어 + **편집 사후 가드**(범위/구조 검증 위반 시 실행 전 스냅샷 롤백·경고 병기) |
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
| `reports/social_sentiment.py` | 텔레그램 장문 포스트 구조화(순수) — 유형 분류(레딧분석/속보/프리마켓)·레딧(WSB) 섹션 파서(이모지 헤딩+불릿·티커 추출·불용어 차단)·최신 분석 요약·다이제스트 한 줄. **표시·컨텍스트 전용**(판단 반영은 news 축 게이트 경유만) |

**crons/ (크론 진입점)**
| 파일 | 역할 | 주기 |
|------|------|------|
| `crons/daily_leverage_retrain.py` | LeverageModel 재학습 + 월요일 Optuna 재최적화 | 평일 22:15 UTC |
| `crons/daily_ranking.py` | ML 종목 랭킹 발송 | 평일 22:00 UTC |
| `crons/notion_sync.py` | Notion 대시보드 동기화 (리포트 23:00 이후) + 리포트 아카이빙 호출. **히어로 KPI 밴드**(Phase·QQQ낙폭·내포트·DCA 4열 컬러 콜아웃)·로컬 PNG 네이티브 임베드(파일 업로드 API)·리포트 섹션 구조화(h3/불릿)·**보유종목 DB upsert**·안전 스왑(append-then-delete·child_database 보존) | 평일 23:30 UTC |
| `crons/notion_archive.py` | 일일 리포트 → Notion 월(`26/06`)/주(`4주차`) 계층 페이지 누적 아카이빙 (멱등 upsert, 대시보드와 독립) | notion_sync 가 호출 |
| `crons/news_spike_detector.py` | 속보 수집 + 급증 감지 + 텔레그램 알림 (+ 실시간 시세 동반표시). **경계선(규칙 5~6점)만 LLM 2차 판정**(`NEWS_SPIKE_LLM_ENABLED` opt-in·회당 상한·실패 시 규칙 점수 유지) | 매 1분 |
| `scripts/kis_stream_watchdog.sh` | 실시간 시세 WS 상시 프로세스(kis_stream) 재기동 — `REALTIME_ENABLED=true` 시만 기동(opt-in·꺼지면 no-op) | 매 1분 |
| `crons/kiwoom_sync_rest.py` | 키움 REST API 국내주식 잔고 동기화 | 평일 23:35 UTC |
| `crons/sp500_heatmap_snapshot.py` | 대시보드 홈 S&P500 시장맵 스냅샷 적재(`_sp500_heatmap_live`→JSON) — 콜드로드 즉시화. 표시데이터 | 매 20분 |
| `crons/kiwoom_mock_track.py` | 국내주식 자동 페이퍼트레이딩 (키움 **모의투자** — 신호 기반 리밸런스·모의 도메인 하드락·편입/퇴출 근거 원장 적재). **회전율 억제**(무거래밴드+랭크 히스테리시스)·**거래비용 적립**(수수료+증권거래세→리포트 계기)·★가격 축 3종(mom12·hi52·lowvol) point-in-time 원장 수집·**분할매수/매도**(`KR_MOCK_TRANCHES` 회당 목표 1/N·`lib/tranche`)·**최소보유**(`KR_MOCK_MIN_HOLD_DAYS`) | 평일 00:30 UTC |
| `crons/intraday_mock_track.py` | **단기(1분봉) 모의 트레이딩 엔진** — 유니버스 갱신→orphan 수리→bar/호가 적재→일손실 halt→청산(우선: stop→target→timestop→collapse→EOD flat)→진입(축 5+3 점수+가드 8종). US 레버리지는 기초자산 신호(`QQQ/NVDA` 등)로 레버리지 ETF(`TQQQ/NVDL` 등)를 체결하고, 신규 위험은 남은 일손실 예산 안으로 자동 축소. 정규 진입선 아래는 `INTRADAY_EXPLORE_*`로 작은 리스크 탐색 진입을 허용(KR 기본 0.40·35%, US 0.48·50%)하고, 거래횟수·동시포지션·재진입 쿨다운 기본 제한은 0이라 손실예산이 실제 제한자. 같은 종목도 청산 후 다음 새 봉에서 즉시 재진입 가능. US 5bp 스프레드는 소프트 기준으로만 남겨 극단 호가만 하드 차단. **shadow 기본**(`INTRADAY_SHADOW_ONLY` — 가상체결만 원장)·청산 즉시 net-of-cost R 보상·trade_events→차트 ▲▼ 마커. `INTRADAY_MOCK_ENABLED` off 면 no-op | 매 1분 |
| `crons/intraday_mock_learn.py` | 단기 정책 주간 학습 + ★게이트 (축 상관 재적합·walk-forward OOS 채택 + 트레이드≥100·순R>0·PSR≥0.95·PBO<0.5 → GO/OBSERVE/NO-GO 정직 verdict — **집행 전환은 항상 수동**) | 토 02:30 UTC |
| `crons/kiwoom_mock_report.py` | 국내 모의 일일 현황 보고 (NAV·손익·편입/퇴출 사유·누적 vs KOSPI·MDD vs 지수) + `/paper kr` 공용 | 평일 06:40 UTC |
| `crons/kr_mock_learn.py` | KR 모의 정책 강화 — 보상 백필 + ★목적함수(아웃퍼폼·MDD≤지수) OOS 게이트 재학습 | 토 02:00 UTC |
| `crons/us_mock_track.py` | 미국주식 자동 페이퍼트레이딩 (KIS 해외 모의 — us_policy 선택 + 바벨 배분·정수주 리밸런스·`Ledger("us_mock")` 결정+근거 적재). ★가격 축 3종+PEAD 축 point-in-time 수집 + **레버리지 ETF 후보군**(지수/섹터 기본 `QLD,TQQQ,SQQQ,SOXL,SSO,SOXS` + 단일종목 기본 `NVDL,NVD,TSLL,AAPU,AMZU,GGLL,MSFU,METU,CONL,PLTU,MSTU`, 최대 2개·예산 35% 캡) + **Tier3 구조레버 QLD 슬리브**(`US_MOCK_LEV_SLEEVE` 시 게이트 GO shadow 신선하면 NAV×(reco−1) 2x ETF — 모의 한정 라이브 검증·원장 side '레버슬리브'·게이트 소멸 시 청산 방향) + **분할매수/매도**(`US_MOCK_TRANCHES`·`lib/tranche`) | 평일 15:00 UTC (미 개장 후) |
| `crons/us_mock_report.py` | 미국 모의 일일 현황 + **로직 평가 스코어카드**(NAV·vs QQQ·MDD·편입/퇴출 적중률·실현 IC) + `/paper us` 공용 | 평일 21:30 UTC |
| `crons/us_mock_learn.py` | US 모의 정책 강화 — 보상 백필(편입 초과·퇴출 회피) + ★목적함수 OOS 게이트·챔피언-챌린저 재학습 | 토 03:00 UTC |
| `crons/weekly_kr_ranker_retrain.py` | KR 전용 랭커(KOSPI 대비 초과수익) 주간 재학습 (Purged WF·OOS IC) | 토 03:30 UTC |
| `crons/longterm_adaptive_eval.py` | 장기 전략 ★목표(vs QQQ 아웃퍼폼·MDD≤지수) 라이브 스코어카드 + 악화 시 보수적 레버리지 축소 shadow 권고 | 토 04:00 UTC |
| `crons/leverage_structural_eval.py` | Tier3 구조적 레버리지 ★게이트 재검증 (`backtest/leverage_structural_backtest` SPY+QQQ × 그리드 낙폭예산·DSR·PBO) — GO 시 권고 레버리지 shadow (표시·수동, 자동집행 0) | 토 04:15 UTC |
| `crons/factor_premium_eval.py` | Tier4 팩터 프리미엄 틸트 ★게이트 재검증 (`backtest/factor_premium_backtest` 롱온리 ETF vs SPY DSR 다중검정·약세슬라이스) — GO 팩터만 shadow. **현재 NO-GO**(밸류·사이즈·퀄리티·최소변동 SPY 미돌파, 모멘텀=SPMO 기보유) | 토 04:45 UTC |
| `crons/income_compounding_eval.py` | Tier5 인컴 복리 재투자 ★게이트 재검증 (`backtest/income_compounding_backtest` 커버드콜 QYLD vs 총수익 QQQ 세전/세후·재투자vs비축) — GO 시 shadow. **현재 NO-GO**(인컴 엔진 세후 CAGR −12.9%p 열위·방어기능). 재투자>현금비축(+33%)은 항상참 규율 | 토 05:00 UTC |
| `crons/concentration_validated_eval.py` | Tier6 검증된 집중 ★게이트 재검증 (`backtest/concentration_validated_backtest` 무스킬 랜덤집중 vs 분산 MC·DSR 다중검정·무생존편향 섹터ETF) — GO 시 shadow. **현재 NO-GO**(무스킬 집중은 보상없는 위험·분산 이길확률 26%; 검증된 집중=구조레버리지뿐) | 토 05:15 UTC |
| `crons/advice_adaptive_eval.py` | 포트폴리오 advice 적응 평가 (paper_track A/B meta vs rule ★목적함수 → blend 신뢰도 shadow 권고) | 토 04:30 UTC |
| `crons/us_axes_eval.py` | US 선택정책 가격축 ★게이트 주간 재검증 (`backtest/us_policy_backtest` — S&P500 **시점 멤버십 마스킹**(fja05680) + yfinance 순비용 워크포워드 vs QQQ·**커버리지 강등**[상폐 가격 부재 시 GO→OBSERVE]) — `ADAPTIVE_US_AXES_ENABLED` 시 shadow→`us_policy.load_params` 모의 반영(공용 `ml/adaptive/axes_shadow`) | 토 05:45 UTC |
| `crons/kr_axes_eval.py` | KR 선택정책 가격축 ★게이트 주간 재검증 (`backtest/kr_policy_backtest` 25년 marcap 무생존편향·순비용 워크포워드 → DSR/PBO verdict + 트레일링 5년 권고 축 `current_recommendation` + **🛡️레짐 방어 오버레이**[강세=고가모멘텀·약세=저변동 전환 → MDD 방어 verdict·**수익 아님**·약세해 6/7 방어이나 초과 DSR 미달] + **💸비용·회전율 스윕**[월간 리밸 드래그 ~2.4%p/년·주기↓로 확실 회수·gross 비단조라 OOS 재검]) — `ADAPTIVE_KR_AXES_ENABLED` 시 shadow→`kr_policy.load_params` 가 **모의 선택에만** 반영(클램프·가격축 합 ≤50% 상한·21일 stale 무시). **현재 OBSERVE**(OOS +5.5%p/년·MDD≤지수·DSR 미달 — 엣지 단정 불가) | 토 05:30 UTC |
| `reports/source_collector.py` | 전체 소스 수집 (텔레그램 채널·FRED·국채·시장 스냅샷) → JSONL 캐시. **소스별 헬스 기록**(`source_health.json` — 소스 크래시 격리·수집 0건 공백 감지)·텔레그램 **본문(body ≤3000자) 수집**(직접 HTML — 레딧분석 등 장문 구조화·티커/테마는 본문 우선)·**포스트 유형 태그**(레딧분석/속보/프리마켓)·다이제스트에 레딧/WSB 심리 한 줄·텔레그램 **t.me/s 직접 HTML 폴백**(jina 장애/무 bold 채널)·**arca/WGB 직접 HTML 폴백**·FRED 재시도+**공식 API 폴백**(`FRED_API_KEY`)·오류 원인 헬스 기록(경보에 표시)·무성공 grace 6h(배포 직후 오탐 방지) | 매 30분 (:05/:35) |
| `crons/paper_track.py` | MetaAllocator vs Phase 규칙 A/B 페이퍼 트레이딩 (월요일 Sharpe 비교 발송) | 평일 22:50 UTC |
| `crons/fundamental_snapshot.py` | 펀더멘털 point-in-time 스냅샷 적재 (look-ahead 없는 학습 피처용) | 토 01:00 UTC |
| `crons/options_snapshot.py` | 옵션 지표 스냅샷 (ATM IV·풋콜비·스큐·기대변동폭) — 학습 피처 축적 | 평일 21:30 UTC |
| `crons/accumulate_daily.py` | **주식 모으기 자동 기록** — 등록 플랜(`lib/accumulation` store 문서: 종목·금액 ₩/\$·매일/매주/매월)을 미 마감 직후 그날 종가×확정 종가 환율로 소수점 계좌에 매수 **기록**(멱등 last_run·휴장 스킵·천원 단위·텔레그램 요약). **실계좌 주문 0** — 키움 주식모으기 실매수의 거울. 플랜 없으면 no-op | 평일 21:10 UTC |
| `crons/earnings_snapshot.py` | 어닝 컨센서스·★리비전 모멘텀·서프라이즈·밸류에이션 point-in-time 적재 (실적/주가반응 예측 학습데이터 — 무룩어헤드) | 평일 22:10 UTC |
| `crons/news_llm_snapshot.py` | **LLM 뉴스 구조화 라벨 적재** (`NEWS_LLM_LABELS_ENABLED` opt-in) — 티커태그·미라벨 이벤트만 배치 라벨(회당 30건 캡) → news 축 피처 원천. KR 00:30·US 15:00 모의 결정 직전 신선화 | 평일 00:05·14:05 UTC |
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
| `tests/intraday_smoke_test.py` | 단기 모의 파이프라인 연기 테스트 — bar 집계·축·가드·청산·사이징·엔진 사이클·학습 게이트·안전 grep (무네트워크 합성) | 평일 00:10 UTC |
| `tests/bot_healthcheck.py` | 봇·서버 상태 점검 (프로세스·PID·파일 신선도·store DB 무결성·**수집 소스 공백 경보**) | 매 30분 |

**ml/ (ML 모델)**
| 파일 | 역할 | 상태파일 |
|------|------|----------|
| `ml/sweet_spot.py` | AR(1) 합성 데이터 + 임계값 전략 그리드서치 | — |
| `ml/leverage_optimizer.py` | Optuna TPE 레버리지 파라미터 탐색 + Walk-Forward OOS | `~/reports/ml-cache/leverage_best_params.json` |
| `ml/adaptive/` | 적응형 학습 공유 프레임워크 — policy(클램프)·ledger(불변 원장)·reward(★목적함수)·learner(OOS게이트)·regime(최근성)·champion_challenger·**evolution(진화 텔레메트리 — 주간 학습 append-only 이력 + 라이브 스냅샷 IC·적중·누적엣지 → 정직 verdict; `/evolve`·대시보드 공용·순수)** | `~/reports/ml-cache/policy_*.json`·`~/reports/ml-data/{kr,us}_mock_learning.jsonl` |
| `ml/kr_ranker.py` | 한국주식 전용 ranker (KOSPI 대비 초과수익 예측, US ranker 재사용·KR캐시) | `~/reports/ml-cache/kr_ranker_model.pkl` |
| `ml/kr_policy.py` | KR 모의 선택 정책 점수 (KR ranker + 규칙 가중 + ★가격 축 3종 `price_axes`[mom12·hi52·lowvol — US 정책과 공유], Policy 클램프). **기본 가중 = `backtest/kr_policy_backtest` 25년 실증 반영**(2001~2026 marcap 무생존편향·순비용·워크포워드: OOS 연결 +5.5%p/년·MDD≤지수 — DSR 미달 OBSERVE 라 보수 배분·rev1(+1M) 축 열위 실증→축소. US 는 신규 축 가중 0=수집만·주간 학습 OOS 게이트 채택 대기) | `~/reports/ml-cache/policy_kr_mock.json`·`kr_policy_backtest.json` |
| `ml/regime_classifier.py` | 추세 vs 횡보 레짐 감지 (Kaufman ER·무룩어헤드·비대칭 전이, US=QQQ·KR=^KS11) — 리포트/`/status` **표시 전용, 배분 불변**. 백테스트 게이트가 US 횡보 틸트 NO-GO·KR 현금디리스크 조건부(비용반영 시 Sharpe중립) 판정 (`backtest/sideways_backtest.py`·`backtest/kr_sideways_backtest.py`) | — |
| `ml/risk_model.py` | 포트폴리오 리스크 계측 (Aladdin식, Tier1) — Ledoit-Wolf 공분산·위험기여(Euler)·유효분산(참여비)·QQQ/TLT 팩터베타 + **성장최적 레버리지 계기판**(Kelly밴드·낙폭예산 상한·파산확률). `/risk`·`/portfolio`·`/rebalance` 노출 — **표시 전용, 배분 불변**(실제 레버리지는 Tier3 게이트 후). USD북 한정 | — |
| `ml/validation.py` | 백테스트 검증 formalism (Tier2, López de Prado) — PSR·**Deflated Sharpe**(다중검정)·**PBO**(CSCV 과적합확률)·Purged/Embargoed CV + `validate_strategy`(벤치마크 초과PSR). `backtest/sideways_backtest`·`kr_sideways_backtest` verdict 에 배선 — **판정·표시 전용**. 공격 엔진(Tier3~6) 라이브 게이트의 통계 관문 | — |
| `ml/deletion_risk.py` | 부실 퇴출 사전예측 (marcap 파생 피처→P(부실퇴출); 실데이터 OOS AUC 0.743·M&A 제외). 회피 통합·★RL 대상 | — (학습셋 marcap 조립) |
| `ml/earnings_predictor.py` | 실적 서프라이즈 예측 G3 (P(beat); 서프라이즈 지속성·모멘텀·리비전 모멘텀 훅). 엣지 게이트 캐시 | `~/reports/ml-cache/earnings_predictor.pkl` |
| `ml/earnings_move_predictor.py` | 실적후 주가반응 예측 G4 (기대 변동폭+방향확률; 방향은 무엣지·정직). 엣지 게이트 캐시 | `~/reports/ml-cache/earnings_move_predictor.pkl` |
| `ml/intraday_axes.py` | 단기 판단 축·가드·청산·사이징·가상체결 (전부 순수) — ORB 돌파(과확장 페널티)·VWAP 반전·시간대 정규화 volspike·OFI(10단계 호가)·뉴스 이벤트 창(롱 전용)·레짐 승수(Kaufman ER) + 리스크 사이징(남은 일손실 예산 우선·1/3 가치캡)·best 호가 가상체결(+스프레드/2+1틱 페널티)·KRX 호가단위 표 | — |
| `ml/intraday_policy.py` | 단기 정책 (Policy kr_intraday/us_intraday) — 축 가중 클램프·결측 재정규화 score·θ_entry/exit·stop/target/timestop 파라미터 | `~/reports/ml-cache/policy_{kr,us}_intraday.json` |

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
> **출력 포맷** (단일 진실원 `fmt.py`): 전 명령이 공통 레이어 경유 — `pct/money/spct`(0·음수0·부호 버그 차단), 짧은 구분선, **HTML 리치텍스트**(`send_html`·parse_mode=HTML): 핵심 `<b>굵게</b>`, 표는 `<pre>등폭</pre>`, 긴 리포트(/report·/rebalance)는 `<blockquote expandable>접기</blockquote>`(`_send_collapsible`), `/history`는 스파크라인. **모바일 주의**: `━ ─` 는 ambiguous-width 2칸 → 공백 정렬 의존 금지(정렬 필요표는 pre). 크론 공유 빌더(paper·history·barbell report)는 `html=` 파라미터로 텔레그램만 굵게, 크론은 평문. 이미지: 일일 PNG(`report_charts.build_portfolio_dashboard`, 히어로 KPI 밴드) + 온디맨드 `/card`(`build_portfolio_card`, 봇이 `.venv` subprocess 렌더 — hermes venv 불변). **종목 표시는 전 화면 `회사명 (티커)` 병기** — `fmt.name(ticker[, label, maxlen])` → `ticker_names`(단일 소스). 좁은 등폭칸은 maxlen 절단.
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
| `US_MOCK_INCLUDE_LEVERAGE` / `US_MOCK_LEVERAGE_UNIVERSE` / `US_MOCK_INCLUDE_SINGLE_LEVERAGE` / `US_MOCK_SINGLE_LEVERAGE_UNIVERSE` / `US_MOCK_LEVERAGE_MAX_POS` / `US_MOCK_LEVERAGE_MAX_WEIGHT` | — | `true` / `QLD,TQQQ,SQQQ,SOXL,SSO,SOXS` / `true` / `NVDL,NVD,TSLL,AAPU,AMZU,GGLL,MSFU,METU,CONL,PLTU,MSTU` / `2` / `0.35` (US 모의 일반 선택 바스켓에 지수·섹터·개별주 레버리지/인버스 ETF 허용. 종목 수와 총 예산 캡으로 쏠림 제한. **모의 한정**) |
| `{KR,US}_MOCK_REBAL_BAND` / `{KR,US}_MOCK_EXIT_BUFFER` | — | `0.25` / `2` (회전율 억제 — 무거래 밴드[목표比 ±25% 벗어날 때만 조정]·랭크 히스테리시스[보유종목 top-N+2 안이면 유지]. 크론 주기 불변·잔챙이 churn 제거) |
| `{KR,US}_MOCK_TRANCHES` | — | `3` (분할매수·분할매도 — 회당 목표의 1/N 만 거래·N회에 평균 진입/청산[`lib/tranche.py`·상태없음: 포지션 크기가 진행도 인코딩·매 실행 남은 갭을 상한만큼 줄여 N회 수렴]. **분산 축소지 알파 아님**·모의 bps 비용 불변[총 거래대금 동일]. 기본 3=분할 활성·`1`=현행 일괄. min_hold[청산 지연]·rebal_band 와 독립 합성. **모의 한정**) |
| `KR_MOCK_STUB_FRAC` | — | `0.5` (min_hold 보호 예외 — 포지션 가치가 목표(budget/N)의 이 비율 **미만**인 스텁[트란치 빌드 중 이탈한 반쪽]은 청산 허용. 저비중 잔재 60일 자본잠식 방지) |
| `KR_MOCK_MIN_HOLD_DAYS` | — | `60` (편입 후 최소 보유일 — 미만이면 타깃이탈이어도 청산 보류. **`backtest/kr_policy_backtest` 비용 OOS 실증**: 슬로우 신호 과잉거래가 순수익 ~2.4%p 잠식·최소보유가 gross 보존하며 비용만 절감[반기>월간 64% 연도·cross-axis·gross 보존=ROBUST]. **기본 60=모의 활성**(OOS 권고값 반영·모의로 라이브 검증)·`0` 으로 되돌리면 현행 무제한 회전. **모의 한정**·꼬리위험(2023 등) 있어 실계좌는 모의 검증 후 수동) |
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
| `ADAPTIVE_KR_AXES_ENABLED` | — | `false` (KR 가격축 주간 재검증 권고를 shadow 기록 → 모의 선택 정책에 반영. off면 평가·텔레그램만. **모의 한정 — 실계좌 집행 0**) |
| `ADAPTIVE_US_AXES_ENABLED` | — | `false` (US 가격축 주간 재검증 권고 shadow → 모의 선택 반영. KR 보다 약한 검증[상폐 가격 부재·커버리지 강등] — 동일 안전장치) |
| `US_MOCK_LEV_SLEEVE` / `US_MOCK_LEV_SYMBOL` | — | `false` / `QLD` (Tier3 게이트 GO shadow 신선 시 US 모의 NAV×(reco−1) 을 2x ETF 슬리브로 — **모의 한정 라이브 검증**. 게이트 NO-GO/stale 시 청산 방향) |
| `SYNC_TOKEN` | — | — (portfolio_sync_server 인증) |
| `SYNC_PORT` | — | `8765` |
| `NOTION_TOKEN` | — | — (Notion 대시보드 동기화·아카이빙. 없으면 notion_sync 스킵) |
| `NOTION_ARCHIVE_ROOT_ID` | — | — (아카이브 루트 페이지 강제 지정. 미설정 시 대시보드 부모 아래 자동탐색·생성 후 `~/.cache` 캐시) |
| `NOTION_ARCHIVE_PARENT_ID` | — | — (루트를 만들 부모. 기본: 대시보드의 부모 페이지) |
| `NEWS_SPIKE_LLM_ENABLED` | — | `false` (속보 경계선[규칙 5~6점]만 LLM 2차 판정. off면 규칙 점수만 — 기존 동작 불변) |
| `NEWS_SPIKE_LLM_MODEL` / `NEWS_SPIKE_LLM_PROVIDER` | — | `gpt-5-mini` / `openai-codex` |
| `NEWS_SPIKE_LLM_MAX_PER_RUN` / `NEWS_SPIKE_LLM_TIMEOUT` | — | `3` / `20` (회당 LLM 판정 상한·초 — 매 1분 크론 비용 통제) |
| `NEWS_LLM_LABELS_ENABLED` | — | `false` (LLM 뉴스 구조화 라벨 크론 opt-in. off면 라벨 미적재 → news 축 미기록·score 재정규화) |
| `NEWS_LLM_LABELS_MODEL` / `NEWS_LLM_LABELS_PROVIDER` / `NEWS_LLM_LABELS_TIMEOUT` | — | `gpt-5-mini` / `openai-codex` / `90` |
| `NEWS_LLM_LABELS_MAX` | — | `30` (회당 라벨 배치 상한 — 일 2회 실행 비용 캡) |
| `INVESTMENT_REPORT_LLM_ENABLED` | — | `1` (일일 리포트 LLM overlay — fact guard 통과 시만 코멘트 추가·결과는 store `llm_overlay_log` 축적) |
| `SAVE_TICKER_API_BASE` | — | `https://saveticker.com/api` (뉴스 + 경제캘린더 `/calendar/events`) |
| `DART_API_KEY` | — | — (DART OpenAPI 키 — KR 공시. 없으면 대시보드 공시탭 graceful 안내) |
| `DASHBOARD_ENABLED` | — | `false` (퀀트 터미널 streamlit 워치독 기동 게이트. true 여야 상시구동·opt-in) |
| `DASHBOARD_PASSWORD` | — | — (대시보드 접근 비번. **미설정 시 fail-closed 전면 차단**) |
| `DASHBOARD_PORT` | — | `8501` (127.0.0.1 바인드 — 외부는 SSH 터널/reverse proxy) |
| (외부 접속) | — | cloudflared quick tunnel + **Vercel 현관 고정 주소** — `scripts/cloudflared_watchdog.sh` 가 `DASHBOARD_ENABLED=true` 시 터널 유지·URL 변경 시 `dashboard/landing/index.html` 갱신→master push→Vercel 자동 재배포(현관 주소 불변)+텔레그램 통지 |
| `INVESTMENT_REPORT_MAX_NASDAQ_SCAN` | — | `100` |
| `INVESTMENT_REPORT_MAX_KOSPI_SCAN` | — | `30` |
| `INVESTMENT_REPORT_ARCA_PAGES` | — | `1` |
| `STOCK_COLLECTOR_ARCA_PAGES` | — | `2` |
| `STOCK_COLLECTOR_TG_CHANNELS` | — | `yuzukinaok1,insidertracking` (뉴스 텔레그램 채널 — 죽은 채널 무배포 교체) |
| `FRED_API_KEY` | — | — (FRED 공식 API 폴백 키 — fredgraph.csv 차단 시 복구 경로. 무료 발급: fred.stlouisfed.org/docs/api/api_key.html) |
| `JINA_API_KEY` | — | — (r.jina.ai 인증 — 익명 레이트리밋 완화. arca/WGB 프록시 수집 안정화) |
| `STOCK_REPORT_PROJECT_DIR` | — | `/home/ubuntu/projects/stock-report` |
| `BARBELL_MAX_DCA_MULT` | — | `5.0` (DCA 배율 절대 상한 — F&G·ML 증폭 폭주 차단) |
| `BARBELL_DCA_VOL_CAP` | — | `0.40` (QQQ 연변동성 초과 시 DCA 배율 비례 축소) |
| `BARBELL_LEV_HALT_DD` | — | `-55.0` (낙폭 이하 시 레버리지 증액 정지 — 전소 방어) |
| `BARBELL_PRICE_STALE_DAYS` | — | `4` (최신 종가 이보다 오래되면 stale → Phase 에스컬레이션 보류) |
| `INTRADAY_BARS_ENABLED` | — | `false` (kis_stream 틱→1분봉 집계 sink — 단기 데이터 수집. **가장 먼저 켤 것** — volspike 프로파일 20세션 축적이 크리티컬 패스) |
| `INTRADAY_MOCK_ENABLED` | — | `false` (매 1분 단기 모의 엔진. off 면 크론 no-op) |
| `INTRADAY_SHADOW_ONLY` | — | `true` (가상체결만 원장 기록·모의 주문 0. **★게이트 GO 후에만 수동 false**) |
| `INTRADAY_MARKETS` | — | `kr,us` |
| `INTRADAY_SCAN_ENABLED` / `INTRADAY_SCAN_TOP_KR` / `INTRADAY_SCAN_TOP_US` | — | `true` / `5` / `4` (동적 유니버스 — 거래대금·등락 상위. 실패 시 `INTRADAY_UNIVERSE_*` 정적 폴백) |
| `INTRADAY_MIN_TURNOVER_KRW` | — | `30000000000` (KR 후보 거래대금 하한 300억 — 유동성·스프레드 방어) |
| `INTRADAY_UNIVERSE_KR` / `INTRADAY_UNIVERSE_US` | — | `005930,000660,373220,005380,035420` / `QQQ` (정적 폴백 유니버스) |
| `INTRADAY_SLEEVE_FRAC` / `INTRADAY_RISK_PER_TRADE` | — | `0.10` / `0.005` (모의 NAV 중 슬리브 비율·트레이드당 리스크 사이징) |
| `INTRADAY_EXPLORE_ENABLED` / `INTRADAY_EXPLORE_ENTRY_KR` / `INTRADAY_EXPLORE_RISK_MULT_KR` / `INTRADAY_EXPLORE_ENTRY_US` / `INTRADAY_EXPLORE_RISK_MULT_US` | — | `true` / `0.40` / `0.35` / `0.48` / `0.50` (정규 `theta_entry` 아래 탐색 진입. KR은 하루 1건 콜드스타트 방지를 위해 더 낮은 문턱·작은 리스크로 표본을 늘림. 글로벌 `INTRADAY_EXPLORE_ENTRY` / `INTRADAY_EXPLORE_RISK_MULT`로 일괄 override 가능) |
| `INTRADAY_LEVERAGE_ENABLED` / `INTRADAY_LEVERAGE_MAP` | — | `true` / `QQQ:TQQQ,NVDA:NVDL,TSLA:TSLL,AAPL:AAPU,AMZN:AMZU,GOOGL:GGLL,MSFT:MSFU,META:METU,COIN:CONL,PLTR:PLTU,MSTR:MSTU` (US 단기에서 기초자산 신호를 레버리지 ETF 가상체결로 매핑. 기초자산+ETF 모두 실시간 watchlist 편입) |
| `INTRADAY_MAX_TRADES_DAY` / `INTRADAY_MAX_CONCURRENT_POS` / `INTRADAY_COOLDOWN_MIN` / `INTRADAY_DAILY_LOSS_HALT` | — | `0` / `0` / `0` / `0.015` (`0`=일 거래횟수·동시 포지션·같은 종목 재진입 쿨다운 제한 없음. 실제 제한자는 남은 일손실 예산: 신규 진입 수량을 `일손실한도 + 당일손익 - 열린포지션 최악손실` 안으로 축소. 동시포지션은 `_KR`/`_US` override 가능) |
| `INTRADAY_STOP_FRICTION_MULT` | — | `3.0` (스탑폭 하한 = 왕복마찰/주 × 배수 — 첫 실트레이드서 마찰(수수료+슬리피지 1,887원)>스탑리스크(1,683원)로 -1R 스탑이 -2.1R 증폭된 문제 방어. 사이징·R 분모도 마찰 포함 → 스탑 도달 = 정확히 -1R) |
| `INTRADAY_MIN_NOTIONAL_KRW` / `INTRADAY_MIN_NOTIONAL_USD` | — | `100000` / `150` (진입 최소 명목금액 — 학습 신호 품질용. 슬리브 1/3 캡보다 훨씬 낮게 유지할 것) |
| `INTRADAY_MAX_SPREAD_BPS_KR` / `INTRADAY_MAX_SPREAD_BPS_US` | — | `25` / `5` (소프트 스프레드 기준 — 실제 스프레드는 마찰·수량 산식에 반영) |
| `INTRADAY_HARD_SPREAD_BPS_KR` / `INTRADAY_HARD_SPREAD_BPS_US` | — | `25` / `50` (진입 하드 차단선. US 는 5bp 초과만으로 막지 않고 극단 호가만 차단) |
| `INTRADAY_FLAT_BUFFER_MIN` / `INTRADAY_ENTRY_CUTOFF_MIN` / `INTRADAY_STALE_FLAT_MIN` | — | `15` / `30` / `10` (마감 前 강제청산 버퍼·진입 컷오프·bar 정체 시 전량청산) |

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
| `dashboard/app.py` | Streamlit 엔트리 — 테마 주입 → 인증 게이트 → 사이드바(**단일 검색 셀렉트박스**[보유+전체 유니버스·한/영/티커 타입어헤드·`ticker_names.search_label`·**`accept_new_options`로 목록 밖 임의 티커 직접입력**→`ticker_names.normalize_input` 정규화·못찾으면 warning]·신선도·새로고침·**보유 워치리스트**·**🧪 모의투자 레일**[KR/US NAV·누적% — `views.paper_glance` store EOD 스냅샷만·무네트워크·스냅샷 없으면 숨김·상세 버튼→모의투자 페이지 `_nav_to_paper` 플래그 switch]) → `st.navigation` 6페이지. 종목선택은 **위젯↔session_state 동기화(`_tsel_sync`)**로 홈 행클릭 등 외부변경 반영·리셋버그 없음(**정규화 가능한 대기 `_tsel`을 `_opts`에 조건부 편입**해 신규 티커가 첫 옵션으로 리셋되는 것 차단·garbage는 self-heal). 최상단 `sys.path.insert(루트)` 필수(streamlit run `sys.path[0]=스크립트dir` 함정) |
| `.streamlit/config.toml` | **Terminal Noir 테마** (TradingView/토스증권) — 다크 블루블랙·일렉트릭블루 액센트·틸그린/코랄레드 시맨틱·Pretendard(한글)+JetBrains Mono(등폭 수치) fontFaces·radius/border/chart 팔레트 |
| `dashboard/theme.py` | 테마 단일 진실원 — 팔레트 상수 + **순수 HTML/SVG 빌더**(ticker_hero·rating_gauge **상단 반원 속도계**[`_arc`/`_polar`·5존]·sparkline·watchlist·**econ_calendar_html 달력 그리드**, 테스트가능) + `apply_plotly_theme`(차트 다크 템플릿) + `inject_global_css`(streamlit lazy — import 시 미로드해 charts 순수성 유지) |
| `dashboard/pages/` | 멀티페이지(비활성 페이지 미실행=lazy) — `home`(글랜스: 포트 ticker-hero+**📊 시장지표**[공포탐욕지수·S&P500·나스닥 일/주봉 RSI]+**🌐 매크로 자산**[환율·금·비트코인·이더·은·유가·미10년물·달러인덱스 카드+30일 스파크+버튼→종목분석·`views.macro_assets`·`@st.fragment`]+**🗺️ S&P500 시장맵**[섹터 트리맵·시총 타일크기·당일 등락 색·타일 클릭→분석·`@st.fragment`·크론 스냅샷 즉시]+배분도넛+클릭 보유표→종목분석 자동이동+Phase+**🚦ML 게이트 신호등**[구조레버·KR/US축 한 줄]+오늘일정)·`portfolio`(리스크 KPI+위험기여/팩터β 막대+½Kelly밴드+**🏗️Tier3 게이트 상태**+도넛)·`ticker`(히어로 **⚡실시간가**+게이지 상단밴드[`@st.fragment run_every=8` 자동갱신]·**내 포지션**[평단·평가손익·주수]·**실시간 호가**[KR 10단계·US 가격]·가격차트 풀폭(**라인/캔들 토글**·**로그 스케일**·`@st.fragment`)+**평단선**+**하단 서브패널 4종**[거래량·RSI·MACD(12·26·9 히스토)·스토캐스틱(%K14·%D3)·최대 5패널]+**🖍️ 드로잉 도구바**[🧲 자석(끝점을 봉 OHLC 스냅)·─ 수평선·🔱 피보나치 되돌림·📏 측정(Δ가격·%·봉수·기간)·🗑 지우기 — `plotly_embed` iframe JS·서버 도형(평단·현재가선) 보호·**localStorage 영속화**(종목:봉:스케일 키·브라우저별)]+**십자선+OHLC 데이터창**[hover 리드아웃 시고저종·거래량·등락%]+**🟩 하이킨아시**(`charts.heikin_ashi` 평활 캔들·표시용 정직 캡션)+**🔔 가격 알림**[expander 등록/목록/삭제 — `bot/price_alerts` store 공용·발동은 봇 5분 루프가 텔레그램 발송·`_alerts_write_lock`(RLock+flock)으로 봇↔대시보드 교차 프로세스 lost update 차단]·상세 **`st.segmented_control`+`@st.fragment` 섹션**[활성만 네트워크·부차정보 expander]·**⚙️ 포지션 관리**[추가·적립(금액→소수점)·축소 → `holding_manager` 기록·실주문 아님])·`market`(**경제일정 달력 그리드**[theme.econ_calendar_html·3주·오늘 강조·중요도순 칩·목록 expander]+**🗣️ 레딧/WSB 심리 카드**[insidertracking 분석 포스트 구조화·주인공 티커·시장 심리 불릿·컨텍스트 전용]+**수집 뉴스 출처별 탭**[SaveTicker/텔레그램/아카/FRED/국채/스냅샷·중요도순=속보 규칙점수 재사용+LLM 라벨 📈/📉·강도 병기·24/48/72h]+종목 뉴스)·`paper`(**🧪 자동 모의투자** — KR/US segmented·계좌 KPI[NAV·누적 vs 지수·MDD 전략/지수]·NAV 곡선·보유·거래비용·**🏗️슬리브 배지**[US — Tier3 게이트 GO/미통과·목표 vs 보유 QLD]·로직평가[evolution verdict+적중률/IC]·**판단근거 원장표**[결정⋈결과·구분 필터(레버슬리브 포함)·**축 피처 토글**(mom12·hi52·lowvol·pead 수집 현황)·실현 순초과·⏳미성숙]. `views.paper_summary` 가 store 스냅샷+모의잔고 API[불가 시 EOD 폴백]+`Ledger` read-only 조립·표시전용 주문 0)·`research`(**섹션 셀렉터**[랭킹/백테스트/학습/**축 게이트**]·무거운 스크리너·백테스트는 **▶실행 버튼 게이트+fragment**[진입 시 자동계산 0]·**🧬 정책 학습 곡선**·**🚦 가격축 ★게이트**[KR/US verdict·순초과/MDD/DSR/PBO·권고 축·shadow 반영 상태·폴드 채택 이력 + **🛡️레짐 방어 오버레이**·**💸비용 스윕** expander — `views.axes_gate_summary` 로컬 JSON read-only]) |
| `dashboard/charts.py` | plotly 차트 빌더(순수 함수·단위테스트·theme 다크 템플릿 적용) — allocation_donut·price_line·**price_candle**(캔들 OHLC+MA+평단·라인/캔들 토글)·**price_chart**(멀티패널 TradingView풍 — 상단 MA·BB·일목·추세선·슈퍼트렌드 등 + **하단 서브패널 최대 4종**[거래량·RSI·**MACD**·**스토캐스틱**·행 동적배정]+**로그 스케일**[가격축만 type=log·서브패널 선형·`_log_fixup_price_shapes` 로 도형/주석 y→log10])·**market_treemap**(S&P500 섹터 시장맵·시총 크기·등락 색)·hbar·signed_bars·value_bullet·equity_curve·**nav_curve**(모의 NAV 시계열+인셉션 기준선) |
| `dashboard/plotly_embed.py` | 커스텀 plotly 임베드(`st.components.v1.html` iframe·순수 HTML 반환) — 팬/줌 시 보이는 구간 y축 부드러운 자동맞춤(TradingView식·rAF lerp·비용적응 스로틀) + **🖍️ 드로잉 도구**[🧲 자석=끝점 봉 OHLC 스냅·수평선·피보나치 되돌림·측정 박스·전체 지우기 — 서버 도형(평단·현재가선) 깊은복사 보존으로 보호] + ▲▼ 마커 클릭 인차트 상세. `price_bounds_json`([ms,low,high,vol,open,close] 6열)·`compare_bounds_json`(% 프레임)·`pannable_chart_html`(pct_mode·y_log 플래그). 템플릿=평문+`@@TOKEN@@` 치환(f-string 중괄호 함정 회피) |
| `dashboard/cached.py` | `st.cache_data` 래퍼(멀티페이지 공용·TTL 15~60분) — valuation/financials/.../risk_struct/ohlc |
| `dashboard/data.py` | 포트폴리오/Phase 상태 + 스케일 명시 포맷터(f_frac_pct vs f_pct·부호버그 차단). streamlit 미import → 테스트가능 |
| `dashboard/views.py` | 모듈별 provider 래퍼(graceful·provider 내부 import) — risk_summary(구조화)·screener·backtest 등 |
| `dashboard/accumulate.py` | 사이드바 💰 주식 모으기 — 레일(오늘 배분+🔁 자동 플랜 목록) + 다이얼로그(계획표=`bot/order_generator.build()` 공용 산식·천원 단위 재배분 `round_alloc_1000`·오늘 기록·💱 환전 타이밍 스트립·자동 플랜 금액/주기 **즉시 편집**[`lib/accumulation.update_plan` — last_run 보존]·신규 등록·비중 편집) |
| `providers/intrinsic.py` | DDM·RIM 내재가치 닫힌해 + r/g 밴드 (DDM은 고배당주만·payout<40% 플래그) |
| `providers/etf_compare.py` | ETF 비교·점수층 — **TR(Adj Close)/PR(Close) 분리**(`auto_adjust=False`; 현 차트 조정종가=TR 근사·QYLD 3y TR+46.9% vs PR+1.9%)·피어 지표(수익률 창[커버리지<60% None]·MDD·추적차=**대표 ETF TR 프록시**[^XNDX 불안정])·**점수 1~100**(전략별[index/커버드콜/배당] 가중 백분위·결측 재정규화·소그룹 shrink·데이터부족 None 정직). 피어 그룹=루트 `etf_meta.py` 큐레이트 시드(11그룹·레버리지 제외·KR 5종 검증). 그룹 12h 디스크 캐시(`etf_peer_*.json`)·점수는 읽기 시 순수 재계산. 종목분석 ETF 뷰(📈 TR vs PR·🏆 동종 비교표·게이지) + ⇄ 비교 팝오버(피어 원클릭·PR 토글[일봉·비교 모드 전용]) 소비 |
| `providers/econ_calendar.py` | 경제 일정 (saveticker `/calendar/events`·키불요·한글) |
| `providers/insider.py` | 내부자거래 (SEC Form 4·edgar 재사용·parse_form4 순수) + 최근 SEC 공시 |
| `providers/dart.py` | KR 공시 (DART OpenAPI·corpCode 매핑·`DART_API_KEY` 없으면 graceful) |

**6페이지(멀티페이지·plotly 차트화):** 🏠홈(포트 글랜스)·💼포트폴리오(리스크 시각화)·🔍종목 분석(가격차트+밸류/재무/기관/공시/실적 **섹션**)·🗓️시장·캘린더(경제+뉴스)·🧪모의투자(자동 페이퍼트레이딩 계좌+판단근거 원장)·🔬리서치(랭킹 스크리너+ML 백테스트+정책 학습). 검증: `tests/test_dashboard*.py` — data/views 순수로직 + **charts 단위** + **페이지 렌더 AppTest(반드시 비루트 cwd**·streamlit sys.path 함정 가드).

> **UX 모델(H-series):** 종목선택은 사이드바 **단일 검색 셀렉트박스**(한/영/티커·리셋버그 없음) 하나로 통일. 무거운 계산(종목분석 5섹션·리서치 스크리너/백테스트)은 **`@st.fragment`+`st.segmented_control`/버튼 게이트**로 **활성 것만 실행**(전체 리로드·스피너 연속 제거 — 부분 rerun). 다크 터미널 유지하되 간격·대비·컴포넌트 라운드 다듬고 **≤600px 반응형**(theme.py 미디어쿼리 — 배지·게이지·워치리스트 축소). **시각 검증 한계**: 서버 로컬호스트+게이트라 자동 스크린샷 불가 → AppTest(구조·무예외)+theme 단위로 회귀 차단, 최종 룩은 육안.
>
> **실시간·포지션(J-series):** `REALTIME_ENABLED=ON` 시 종목분석이 KIS 실시간 시세·호가를 오버레이(`cached.realtime_quote`→`kis_quote.get_snapshot` REST 온디맨드·`market_data._realtime_current` 캐시 seam·graceful yfinance 폴백). `data.holding_position`(평단)·`load_holdings` 실시간 오버레이(value/ret 재계산). **포지션 관리**는 `holding_manager.buy/sell_holding`(atomic+락·봇 `/holding` 과 동일·해외 general·**실계좌 주문 0**·기록 전용). **theme.py 함정**: 광역 `span{{font-family}}` override 가 Streamlit 머티리얼 아이콘 ligature 를 덮으므로 아이콘 폰트 복원 규칙 필수(안 하면 "keyboard_arrow_right" 텍스트 노출). 사이드바 종목선택은 `st.switch_page`로 종목분석 이동(switch 후 위젯 유실 방어=`_tsel not in _opts` 재동기화).
>
> **검색 확장(K-series):** 검색 유니버스가 큐레이트 시드 한정이라 버크셔·JPM·월마트 등이 안 나오던 문제 → (K1) `ticker_names` EN/KO 시드에 인기 US 대형주 ~70(한글별칭 포함) 추가로 universe 55→118, 한/영/티커 타입어헤드 즉시 지원. (K2) 시드 밖 롱테일은 `st.selectbox(accept_new_options=True)` + `ticker_names.normalize_input`(순수·무네트워크)로 임의 US 티커 직접입력 조회(RIVN·COIN 등). **함정**: accept_new_options 신규 티커가 기존 reconciliation `_tsel not in _opts`에 걸려 첫 옵션으로 리셋 → **정규화 가능한 대기 `_tsel`만 `_opts`에 조건부 편입**으로 해결(garbage는 미편입→self-heal). 종목분석 14개 데이터콜은 임의 티커에 이미 graceful(None/빈/에러딕트). **AppTest 한계**: 세션주입은 accept_new_options 신규옵션 등록을 안 거쳐(브라우저 타이핑 경로 시뮬 불가) → 첫 run에 '방금 입력' 상태를 심어 `_pending` 가드만 검증, 정규화는 `normalize_input` unit이 커버. 한국주식은 현행 top10 유지(미국 집중).
>
> **종목분석 강화(L-series):** "상위 500 다 검색·캔들·게이지·비보유 분석" 4요청 → (L1) **S&P500 전체(~503) 검색 유니버스** — 신규 `sp500_seed.py`(티커→영문명, `scripts/gen_sp500_seed.py` 로 생성: `index_membership` 현재구성 + yfinance shortName·재현) → `ticker_names` 병합(universe 118→~543). 큐레이트 EN/KO 우선(깔끔한 표시명·한글). 영문명·티커로 500 검색(한글은 인기주). (L2) **라인/캔들 토글** — `charts.price_candle`(go.Candlestick+MA+평단) + `ticker._price_chart` `@st.fragment` segmented_control(차트만 부분 rerun). (L3) **게이지 SVG 재작성** — 기존 하단 반원(sweep=0)이 viewBox 밖 잘려 왜곡 → `_arc`/`_polar` 로 5존을 **상단 반원**(좌 약세→우 강세) 개별 원호 타일 + score→니들 각도(cx=100·viewBox 200x126). (L4) **비보유 분석 = 게이트 아님**(코드상 `render`/`_detail_sections` 보유 무관 전 섹션 렌더·`if pos:`는 포지션밴드만) → 근본원인 디스커버빌리티, L1 드롭다운 500 노출로 해소. AppTest(배포 app.py + 비보유 COST 무예외) 확인. sp500 이름은 yfinance 일회 fetch 임베드(render 무네트워크·영구).
>
> **S&P500 시장 맵(M-series):** 홈 첫 화면에 Finviz 풍 **섹터 트리맵**(시총 타일크기·당일 등락 색). (M1) 신규 `sp500_meta.py`(`SECTOR`·`MARKET_CAP` 503·`SECTOR_KR` 11 — `scripts/gen_sp500_meta.py` yfinance `.info` 재현). (M2) `charts.market_treemap`(go.Treemap·섹터→종목·`branchvalues="total"`·색 적→흑→녹 `cmid=0 cmin/cmax=±3`) + `views.sp500_heatmap`(정적 시드 + 라이브 `yf.download(2d)` pct·graceful) + `cached.sp500_heatmap` 30분. (M3) `home._market_map` `@st.fragment` — `st.plotly_chart(on_select="rerun")` 타일 클릭→`normalize_input`→종목분석 `switch_page`. **데이터 전략**: 섹터·시총=정적 스냅샷(사이즈 상대비율), 등락률만 라이브. **콜드 로드 ~60초**(503 배치)→O-series 크론 스냅샷으로 즉시화. 표시·주문 0.
>
> **시장지표+성능(O-series):** 홈에 **공포·탐욕지수 + S&P500·나스닥 일/주봉 RSI** + 시장맵 즉시화. (O1) `views.market_indicators`(`market_data.fetch_fear_greed` + ^GSPC/^IXIC 일봉·주봉 `yf.download`→`data.rsi`)·`cached` 15분. (O2/O5) **반원 게이지**: `theme._gauge_svg`(범용·`_arc`/`_polar` 재사용·zones·니들·중앙값) → `fng_gauge_html`(CNN 풍 공포탐욕 게이지·전주 추세)·`index_rsi_gauges_html`(일봉/주봉 RSI 게이지 2개·과매수>70 적·과매도<30 녹) + `home._market_bar`(시장맵 위·경량 즉시렌더). (O3) **성능**: `sp500_heatmap` 스냅샷-우선(`~/reports/ml-cache/sp500_heatmap.json` <90분 → 파일읽기 즉시·미스 시 라이브 후 self-heal write) + `crons/sp500_heatmap_snapshot.py`(매 20분·`_sp500_heatmap_live` 추출) → 콜드 ~60초 → 즉시. 표시·주문 0.
>
> **차트 강화 + 매크로 자산(N-series):** TradingView 미구현 요소 보강 + 홈 매크로 자산. (N1) **🖍️ 드로잉 도구**(`plotly_embed` iframe JS 확장) — 🧲 **자석**(그리기/편집 시 선·박스 끝점을 `price_bounds_json` 6열[+open·close]의 최근접 봉 OHLC 에 스냅·기본 ON)·─ **수평선**(paper 전폭+가격 라벨)·🔱 **피보나치** 되돌림(0·23.6·38.2·50·61.8·78.6·100%+밴드)·📏 **측정**(박스=Δ가격·Δ%·봉수·기간)·🗑 **지우기**. **함정 방어**: 서버 도형(평단·현재가선)은 사용자 도형과 무명 구분 불가 → **깊은복사본 보존**(`baseShapes`)으로 지우기 복원·`idx<baseShapeCount` 가드로 자석이 평단선을 못 움직이게(런타임 node 스텁 테스트로 검증). (N2) **하단 서브패널 MACD·스토캐스틱** — `price_chart` 패널 행 **동적 배정**(거래량→RSI→MACD→스토·최대 5패널·2·3패널은 기존 비율 유지 회귀방어). (N3) **로그 스케일** — 가격축만 `type=log`·서브패널 선형·plotly 규약(로그축 shape/annotation y=log10)이라 `_log_fixup_price_shapes` 가 가격패널 도형·주석 y 를 일괄 log10 변환(트레이스는 raw·무관). **함정: `add_annotation(row/col 없이)`는 yref=None 으로 남고 plotly.js 가 첫 y축=가격으로 coerce** — 추세선 라벨(전 구성)·panes==1 콜아웃이 이 경로라 yref None 도 변환 대상(적대 리뷰 확정 버그·회귀 테스트 有)·embed yFit 도 `toY=log10`·비교(%) 모드선 자동 비활성. (N4) **🌐 매크로 자산**(`views.macro_assets`) — 환율·금·비트코인·이더·은·유가·미10년물·달러인덱스 `yf.download(1mo)` 배치→카드(가격·등락·30일 스파크)+버튼→종목분석. `ticker_names` 매크로 시드(한/영/티커 검색·`_history_cached` 로 차트도 지원)·하단 마퀴에 금·비트코인·미10년물 추가. **검증**: charts/theme/plotly_embed 단위 + AppTest(스텁 `macro_assets`) + node 런타임 스텁으로 드로잉 로직. 표시·주문 0. **(P-series 유료급 보강)**: (P1) **드로잉 영속화** — localStorage `tndraw:종목:봉:스케일(:ha)` 자동 저장/복원(디바운스 250ms·지우기=저장도 클리어·스케일별 분리 버킷=좌표계 혼선 차단·node 런타임이 저장→재로드 복원 검증). (P2) **십자선+OHLC 데이터창** — `price_chart` spikes(x 전패널·y 가격만)+iframe hover 리드아웃(bounds 6열 재사용·비교모드는 %만). (P3) **차트→가격 알림** — `data.ticker_alerts/add/remove` → `bot/price_alerts` 재사용(발동·발송은 봇 5분 루프). **대시보드=신규 writer 프로세스**라 `_alerts_write_lock`(RLock+`safe_io.file_write_lock`) 추가 — save_collection 통째 교체의 교차 프로세스 lost update 차단(락 없으면 실패하는 2프로세스 동시등록 테스트로 양방향 검증). (P4) **하이킨아시** — `charts.heikin_ashi` 순수 변환(재귀 HA시가·Volume 보존)·표시용 정직 캡션·`:ha` 별도 드로잉 버킷. **(Q-series 데이터 우위)**: (Q1) **이벤트 마커 오버레이** — 실적 E(서프라이즈 beat 초록/miss 빨강)·배당 D(일봉 Dividends 열)·뉴스 N(LLM 라벨 point-in-time·방향색) 봉 아래 원형 배지(`charts._add_event_markers` — 봉저가 1.5% 아래 비율 오프셋=로그축 일정) + **진입존 밴드**(`_add_entry_zones` — 🎯 섹션과 같은 합류존: 입력 조립 `_entry_level_inputs` 로 추출 공유). 📐 이벤트 pills(기본 실적·배당). TradingView 에 없는 전유 데이터. (Q2) **2h/4h 봉** — yf 미지원이라 `ohlc_tf` 가 1h 리샘플(같은 2년 한계 정직 표기). (Q3) **⚡자동 갱신** — 토글(fragment **밖** — 래퍼 전환 필요) → `_price_chart_live`(run_every=8). **live 는 클라이언트 패치**: 메인 차트 html 을 **바이트 안정**(서버 bake 생략 — srcdoc 변경=iframe 재마운트=그리던 드로잉 리셋+수 MB 재전송)으로 유지하고, <1KB 피더 컴포넌트가 `tnrt:티커` localStorage push → iframe `patchLast` 가 마지막 봉·현재가선(`tn-last` 명명)·bounds·리드아웃 in-place 갱신(storage 이벤트+2s 폴링·30s stale 가드·**plotly 6 bdata typed-array 디코드 필수** — `Array.from({dtype,bdata})` 는 빈 배열). HA·비교·구형 렌더러만 종전 bake(df.copy 후·int열 float 승격=pandas3 대비). (Q4) **뷰 위치 유지** — iframe 이 `tnview:` 키로 x창 저장(제스처 끝), **60초 신선 + vm(기간 라디오) 일치** 시만 복원 → 자동갱신·지표 변경은 보던 위치 유지·라디오 변경/새 세션은 기본창 (라디오 무력화 함정 회피). **⚠️tz 함정**: plotly 는 naive 날짜문자열을 UTC 로 합성하는데 `Date.parse` 는 로컬 해석 — 파싱-재직렬화 왕복은 KST 에서 뷰가 9h씩 밀림(자동갱신마다 누적) → 저장·복원 모두 **range 원문 문자열 무파싱 왕복**(적대 리뷰 확정·node 회귀 有).
>
> **성능 회귀 주의(R-series):** 십자선을 plotly `showspikes` 로 넣으면 **마우스무브마다 전 트레이스 재그리기** → 다중 패널에서 스터터(실사용 버벅임 신고). 십자선은 `plotly_embed` 의 **DOM 오버레이**(rAF 스로틀·transform·재그리기 0)로 구현하고 `charts.price_chart` 는 스파이크를 켜지 않는다(재도입 방지 테스트 有). 차트는 `_price_chart_frag`(@st.fragment) 로 감싸 **컨트롤 변경이 페이지 전체가 아닌 차트만 rerun**. **데이터 윈도잉**: 차트 직렬화는 `charts.view_window`(뷰 기간×5 팬버퍼+워밍업 250봉·floor 800·"전체"=전량) 경유 — max 전량(~11k봉) fig+bounds 직렬화는 토글마다 수 MB push+수초 블로킹("채널 쓰면 다운" 확정 원인·재도입 금지). **도형 이벤트는 guard 와 분리(drawGuard)**: 프로그램 relayout in-flight(guard) 중 도착한 도형완성이 드롭되면 자석·도구가 간헐 무시 — guard 는 **카운터**(boolean 대입 금지·`guard++`/`unguard()`), 도형 자기 메아리는 전용 `drawGuard` + 내용 기반 가드 2중(node 회귀 有). **reconnect 워치독 = 연속 3회 실패 디바운스 + AbortController 타임아웃** — 단일 blip reload 은 로그인 튕김(쿠키 세션 도입 후에도 불필요 리로드 억제).
>
> **🖍️ 드로잉 무한루프 = 탭 프리즈(실측 확정·플레이라이트 재현):** plotly 는 `{shapes:[...]}` relayout 의 자기 `plotly_relayout` 이벤트를 **비동기**(promise `.then` 이 `guard=false` 로 되돌린 *뒤*)로 emit → boolean `guard` 만으론 자기 메아리를 못 막아 `applyDraw`→메아리→`magnet` 재스냅→`applyDraw` **무한 루프**로 탭이 얼어붙었다(수평선·자석·측정 등 그리는 순간 mouse.up 이 150초 블록). **카운터 방식은 sync/async/수동 emit 에 드리프트라 금물** — 방어는 **내용 기반** 2중: (1) 새 draw 이벤트의 마지막 도형이 이미 `tool-*` 이면 자기 메아리로 보고 무시(named 도형·drift 0), (2) `snapShape` 는 좌표가 실제로 바뀔 때만 `applyDraw`(멱등 — 이미 스냅된 unnamed 도형 메아리는 재적용 0 → 루프 종결). node 하니스가 **비동기 메아리를 충실히 모델**(`test_drawing_no_infinite_relayout_loop` — 수정 전 CAP 초과로 실패·negative control 확인)+플레이라이트 실브라우저로 프리즈 소멸 확인. **⚠️ 개발 시 배포 트리(`/home/ubuntu/projects/stock-report`)와 워크트리 혼동 주의** — 수정은 워크트리에 하고 커밋, 배포는 머지로.
>
> **TV급 도구·쿠키 세션·LLM 연관(T-series):** ① **드로잉 도구 확장** — 기존 4종에 │수직선·✚크로스라인·↗레이·⤢연장선(기울기 외삽)·📝메모(window.prompt)·📈롱/📉숏 포지션(진입→목표 드래그=보상존+RR 1:1 손절존 자동·rect 편집 가능·%·라벨) 추가. 풀뷰(chart_full)는 `dock=True` 로 **좌측 세로 도구 독**(TradingView 배치·`#wrap.dock` CSS). ② **쿠키 세션**(auth.py) — 카드 앵커(?tk=)·F5·재기동 리로드=새 세션이라 비번 재요구되던 것 → 로그인 시 HMAC 서명 토큰 쿠키(30일·`issue_token/verify_token` 순수) 발급, `st.context.cookies` 자동 재인증. 키=sha256(비번+`~/.cache/dashboard_auth_salt`) — 비번 변경/salt 삭제=전 기기 로그아웃. 워치독 리로드도 무입력 복귀 + **health fetch 2.5s 타임아웃·연속 3회 실패부터 down**(단발 리셋 오탐 리로드 제거). ③ **가드 계층화**(plotly_embed) — guard boolean→**카운터**(겹친 relayout 의 .then 조기해제 창 제거) + 도형 이벤트 전용 `drawGuard` 분리(applyDraw/복원 메아리 1차 차단·내용 기반 방어는 2차·팬 애니 중 도형 이벤트 삼킴 해결). ④ **🤖 LLM 연관 종목**(`providers/llm_related.py`) — hermes chat(codex) 로 현 종목 연관/경쟁 3~5개+한줄 근거. **환각 방어 = normalize_input 화이트리스트**(미해석 티커 폐기)·relation enum·24h 디스크 캐시(`~/reports/ml-cache/llm_related/`)·버튼 게이트·정직 라벨(검증 안 된 참고용). env: `DASH_LLM_RELATED_ENABLED`(기본 1)·`_MODEL`·`_PROVIDER`·`_TIMEOUT`.
>
> **TV 갭 반영 2차(V-series — 유저 TV 스크린샷 대비 미구현분):** ① **드로잉 3종** — 📐 **회귀추세**(구간 박스 드래그=종가 OLS 중심선+±2σ 채널·변화율 라벨·로그축은 log 공간 회귀), ⚓ **고정 VWAP**(짧게 긋기=앵커 봉부터 (저+고+종)/3×거래량 누적 — 도형이 아닌 **트레이스**[hover 지원], 영속화는 `tndraw` 문서에 **앵커 ms 목록(vwaps)만** 저장→로드 시 bounds 재계산·지우기가 `clearVwaps`로 트레이스 정리), 📊 **고정범위 볼륨 프로필**(구간 박스=24 가격빈 히스토그램[고저 걸친 빈 균등 분배]+POC 라인). 비교(%) 모드는 VWAP·프로필 차단(4열 bounds 에 실가격·OHLC 없음)·파생 도형(tool-reg/vprof)은 자석 스냅 제외. ② **지표 6종** — 상단: 켈트너 채널(EMA20±2×ATR10)·KAMA(10·2·30·`kama_series` 순수)·샹들리에 엑시트(22·3×ATR 롱/숏 스탑) / 하단 서브패널: Aroon(25)·볼린저 %b·PVT — **패널 동적 배정 5→8행**(6+ 는 가격 0.40+서브 균등·높이 900+85/패널). ③ **펀더멘털 서브패널(W2 — TV img08)**: 하단 지표 '펀더멘털' = 분기 매출 바(**폭=간격 45% 명시** — 자동폭은 분기 전체를 채워 뭉툭)+순이익 라인(적자 분기 빨강 마커)+hover 순마진·`providers/earnings_data.quarterly_fundamentals`(yfinance 분기/연간 손익 → {date,revenue,net_income,margin}·12h 캐시·**실패는 미캐시**=재시도 허용·`_fund_rows` 순수 파서)·분기<4개면 연간 폴백·ETF/매크로는 손익 없음→패널 생략+정직 캡션(`charts.fmt_big` T/B/M 축약). ④ **미구현 백로그(정직)**: 평행채널(수동 — 📐회귀추세가 대부분 커버)·피치포크·갠·피보 확장/타임존·XABCD/엘리엇 패턴·순환선·차트 내 전략 백테스트(무엣지 원칙상 리서치 탭 유지).
>
> **분기 EPS 서브패널(W2):** 하단 지표 pill "분기 EPS" — `cached.valuation` history(24분기·기캐시·무추가 네트워크)로 실제 EPS 바(beat 초록/miss 빨강)+예상 마커+서프라이즈 hover 를 가격 차트 아래 정렬(TV 유료 '펀더멘털 오버레이' 대응). 비교(%)·매크로 차단·무데이터 시 패널 미생성(graceful).
>
> **⏪ 바 리플레이(W-series):** 도구바 ⏪ = 과거 시점으로 되감아 한 봉씩 재생(매매 연습 — TV 유료 대표 기능). 미래 봉은 `replay-curtain`(layer:above rect·plot bg)으로 가리고, **y맞춤(yFit)·최고/최저 콜아웃·OHLC 데이터창을 `replayCut` 으로 클램프**해 미래 고저·값 누출을 차단. 컨트롤 = ⏮10봉 뒤·▶/⏸ 재생(1×/3×/10×)·▶| 한 봉·슬라이더·✕ 종료(콜아웃·y 복원). 커튼은 일시 상태 — **영속화(tndraw) 제외**·지우기 후에도 유지·⚡자동갱신 리런 시 초기화(재진입). 리플레이 중 드로잉 가능(도형은 저장됨). node 런타임 회귀(커튼 생성/전진/저장 제외/종료) 有.
>
> **매크로 자산 전용 뷰(S-series):** 금·환율·비트코인·금리는 PER/재무/기관/공시/호가/진입존이 무의미 → `ticker_names.is_macro` 분기로 **`_macro_sections`**(성과 프로필[1주~1년·YTD·52주 위치·연변동성·200일 이격] + 자산 특화[환율=환전 타이밍·**내 포트 민감도**, 금리=10년 백분위, 금=금은비] + **🔗 연관 자산 90일 상관**[`views.macro_correlations`·`_MACRO_REL` 관계 서술]) 로 대체. 히어로 단위·게이지도 매크로화(가치평가 게이지 생략·2열). 홈 매크로 카드는 **카드 자체가 `?tk=` 앵커**(순수 HTML=콜백 불가 → `app.py` 가 쿼리파라미터 소비 후 `clear()`+기존 `_nav_to_ticker` 경로로 switch_page; 버튼 행 제거). **함정**: `series_profile` 의 기간 수익률은 **이력이 창을 못 덮으면 None**(20일치로 "1년 +10%" 라벨을 만들면 허위 — 테스트가 강제).

> **분석·기록 확장(D~G-series · 2026-07-07~08):** ① **ETF 비교층** — `etf_meta.py`(11그룹 큐레이트 시드) + `providers/etf_compare.py`(TR/PR·피어 지표·점수 1~100) → 종목분석 ETF 뷰·⇄ 비교 팝오버. ② **차트 UX** — `dashboard/plotly_embed.py`(커스텀 임베드: relayouting y-follow·rAF lerp·비용 적응 스로틀·WebGL·거래량 q98 캡) + `dashboard/trendlines.py`(자동 추세선·상승/하락 채널) + **풀뷰 페이지**(`pages/chart_full.py` — st.navigation 7페이지째·사이드바 숨김·전 컨트롤 유지). ③ **자동 모으기** — `lib/accumulation.py`(플랜 store 문서 CRUD·due 판정·천원 최대잔여 라운딩) + `crons/accumulate_daily.py`(21:10 UTC 확정 종가·종가환율 기록·**16:05 ET 이전 실행 시 진행 봉 오인 방지 가드**) + `dashboard/accumulate.py`(사이드바 관리·즉시 편집). ④ **거래 원장·undo** — `lib/trade_events.py`(store 컬렉션 trade_events) + `holding_manager.undo_trade`(**임의 기록** rollback+replay — tail 역산·평단 ±0.01 검증·후속 이벤트 avg 재기록) → 종목분석 ⚙️ 거래 이력에서 행 선택 취소. ⑤ **진입 레벨 가이드** — `data.entry_levels`(MA·BB·52주·추세선·채널·매물대·VWAP·일목 + 밸류 → 1.5% 합류존 클러스터) + 🔔 존 도달 알림(price_alerts 연동). ⑥ **포트폴리오 페이지 재작성** — TWR 진짜 수익률(`data.twr_series`·원장 순유입 차감)·환율 기여·자산군 노출(레버리지 분리)·인컴·🇰🇷 국내북 섹션. ⑦ **홈 시장지표 확장** — S&P500 밸류 하이브리드(`providers/market_valuation.py` — multpl.com 보고이익 PER 1871~ 백분위 + 상위100 시총가중 조화평균 fPER/성장/PEG) + 🌡️ **시장 온도계**(`data.market_temperature` 역발상 종합·일별 이력 store `market_temp_history` — accumulate_daily 가 적재) + 시장맵 3종(S&P500 기술 세부 카테고리·KOSPI200·러셀2000 근사). ⑧ **결과 영속** — 리서치 스크리너/백테스트 마지막 실행 디스크 저장(`screener_last.json`·`backtest_last.json`) → 재방문 즉시 표시 + 🔄 다시 실행. ⑨ **일일 백업** — `scripts/backup_stock_data.sh`(store DB·portfolio_snapshot·dca/target tar — 14일 보존·매일 20:40 UTC).

**구동:** 게이트 JSON 콜드스타트는 `bash scripts/bootstrap_gates.sh`(배포 직후 1회 — KR marcap 첫 다운로드 수 분). 대시보드는 `bash scripts/run_dashboard.sh` (수동) 또는 `scripts/dashboard_watchdog.sh`(크론 매 1분·`DASHBOARD_ENABLED=true` opt-in·streamlit health 재기동). **활성화 = `.env` 에 `DASHBOARD_PASSWORD`(필수·fail-closed) + `DASHBOARD_ENABLED=true`.** 127.0.0.1 바인드 → 외부는 SSH 터널(`ssh -L 8501:127.0.0.1:8501`), reverse proxy(caddy TLS+auth), 또는 **cloudflared quick tunnel + Vercel 현관**(`scripts/cloudflared_watchdog.sh` 크론 매 1분·`DASHBOARD_ENABLED` 게이트 — 터널 죽으면 재기동 → 새 URL 을 `dashboard/landing/index.html` 에 sed → master push → Vercel(stock-dashboard) 자동 재배포. **현관 주소는 고정**·터널만 뒤에서 추적·텔레그램 통지 병행. 접근은 `DASHBOARD_PASSWORD` fail-closed 게이트가 방어). 봇과 별개 프로세스 → 봇 재시작 무관.

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
                                                     · trade_events (거래 원장 — undo 원천) · market_temp_history
                                                   └ 문서: dca_weights · target_weights · leverage_state
                                                     · accumulation_plans (자동 모으기 플랜)
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
~/reports/ml-cache/ticker_names.json             — 종목 티커→회사명 yfinance 디스크캐시 (ticker_names, 30일 TTL·큐레이트 시드 미스분만·graceful)
~/reports/ml-cache/fundamental_scores.json       — 펀더멘털 점수 7일 캐시 (랭커 틸트용)
~/reports/ml-cache/fundamental_snapshots.jsonl   — 펀더멘털 주간 point-in-time 스냅샷
~/reports/ml-cache/institutional_snapshots.jsonl — 기관 매집 강도·13F 지분 주간 스냅샷 (델타 추적)
~/reports/ml-cache/earnings_snapshots.jsonl      — 어닝 컨센서스·리비전·서프라이즈·밸류 일별 point-in-time (실적/주가반응 예측 학습용)
~/reports/ml-data/news_llm_labels.jsonl          — LLM 뉴스 구조화 라벨 point-in-time (published/labeled 시각 보존 — news 축 원천, 불변 append-only)
~/reports/source-cache/source_health.json        — 수집 소스별 헬스 (last_run/last_count/last_success — 공백 감지·healthcheck 경보·대시보드 배너)
~/reports/ml-cache/earnings_*.json               — earnings_data 종목별 요약 12h 캐시
~/reports/ml-cache/earnings_predictor.pkl        — 실적 서프라이즈 G3 모델 (엣지 게이트 통과 시만 — earnings_model_retrain)
~/reports/ml-cache/earnings_move_predictor.pkl   — 실적후 주가반응 G4 모델 (엣지 게이트 통과 시만)
~/reports/ml-cache/marcap/marcap-YYYY.parquet    — KR 전종목 시점별 시총패널 (1995~, raw GitHub fetch+캐시; kr_market_data)
~/reports/ml-cache/sp500_history.csv             — 美 S&P500 시점별 구성 이력 (fja05680, index_membership)
~/reports/ml-cache/edgar/                         — SEC EDGAR companyfacts·CIK맵 캐시 (edgar)
~/reports/ml-cache/kr_ranker_model.pkl           — KR 전용 랭커 모델 (KOSPI 대비 초과수익, safe_unpickle)
~/reports/ml-cache/policy_kr_mock.json           — KR 모의 선택 정책 가중치 (learner 채택 시 갱신, 클램프)
~/reports/ml-cache/kr_policy_backtest.json       — KR 선택정책 25년 워크포워드 검증 결과 (verdict·권고·폴드 — kr_axes_eval 주간 갱신)
~/reports/ml-cache/kr_policy_axes_shadow.json    — KR 가격축 권고 shadow (ADAPTIVE_KR_AXES_ENABLED 시 기록 → kr_policy.load_params 모의 반영)
~/reports/ml-cache/us_policy_backtest.json       — US 선택정책 멤버십 마스킹 워크포워드 검증 (커버리지 포함 — us_axes_eval 주간 갱신·서버 전용)
~/reports/ml-cache/us_policy_axes_shadow.json    — US 가격축 권고 shadow (ADAPTIVE_US_AXES_ENABLED 시 기록 → us_policy.load_params 모의 반영)
~/reports/ml-data/kr_mock_decisions.jsonl        — KR 모의 편입/퇴출 결정+근거 (불변 append-only, 학습/감사 — 절대 삭제 금지)
~/reports/ml-data/us_mock_decisions.jsonl        — US 모의 편입/퇴출 결정+근거 (불변 append-only, point-in-time features — 절대 삭제 금지)
~/reports/ml-data/us_mock_outcomes.jsonl         — US 모의 결정 실현 보상(초과수익 vs QQQ·side-aware 정답) (불변 append-only)
~/reports/ml-data/us_mock_journal/YYYY-MM.md     — US 사람용 편입/퇴출 저널 (월별 누적)
~/reports/ml-data/us_mock_learning.jsonl         — US 주간 학습 진화 이력 (채택여부·챔피언/챌린저 OOS·순비용 스냅샷 — evolution.record_learning, 불변 append-only, /evolve·대시보드 학습곡선 원천)
~/reports/ml-cache/policy_us_mock.json           — US 모의 선택 정책 가중치 (learner OOS 게이트·챔피언-챌린저 채택 시 갱신, 클램프)
~/.cache/kis_mock_token.json                     — KIS 해외 모의 OAuth 토큰 디스크 영속 (발급 레이트리밋 회피)
~/reports/ml-cache/sp500_heatmap.json            — 대시보드 홈 S&P500 시장맵 스냅샷 (sp500_heatmap_snapshot 크론 20분·rows[티커·섹터·시총·등락%]; views.sp500_heatmap <90분 우선읽기 → 콜드로드 즉시화)
~/.cache/kis_realtime_quotes.json                — 실시간 시세 캐시 (kis_stream writer·realtime_quotes reader; symbol→{price,bid,ask,bids/asks,volume,ts,delayed}+__heartbeat__. safe_io atomic)
~/reports/ml-data/intraday_bars/YYYY-MM-DD.jsonl — 단기 1분봉 store (kis_stream 단일 writer·틱 집계 OHLCV — 백테스트 데이터 축적·불변 append-only)
~/.cache/intraday_universe.json                  — 단기 동적 유니버스 (스캐너 결과·히스테리시스 — kis_stream 워치리스트 편입)
~/.cache/intraday_mock_state.json                — 단기 엔진 상태 (오픈 포지션 유일 권위·카운터·halt·쿨다운 — 손상 시 orphan 수리로 원장 정합 복구)
~/reports/ml-data/{kr,us}_intraday_decisions.jsonl — 단기 결정 원장 (id=date:ticker:HHMMSS·축 피처 point-in-time — 절대 삭제 금지)
~/reports/ml-data/{kr,us}_intraday_outcomes.jsonl  — 단기 실현 보상 (청산 즉시 net-of-cost R=fwd_excess — 절대 삭제 금지)
~/reports/ml-data/{kr,us}_intraday_learning.jsonl  — 단기 주간 학습·게이트 이력 (evolution — /evolve·대시보드)
~/reports/ml-cache/policy_{kr,us}_intraday.json    — 단기 정책 가중 (learner OOS 채택 시 갱신·클램프)
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
- 티커 표시 시 회사명 병기: `NVIDIA (NVDA)` 형식 (한국 상장주 `.KS`만 한글명 `삼성전자 (005930.KS)`) — 단일 소스 `ticker_names.py`(resolver) + `fmt.name(ticker[, label, maxlen])`. 좁은 등폭칸은 `maxlen` 절단. 대시보드 검색은 한글명·영문명·티커 어느 것으로도 resolve
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
