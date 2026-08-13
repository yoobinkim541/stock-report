# Chart Program Completion Audit

검증 시각: 2026-08-13 UTC

브랜치: `codex/chart-analysis-workbench`

기준: `docs/superpowers/specs/2026-08-08-chart-engine-expansion-design.md`

## Scope

목표는 TradingView UI의 복제가 아니라 유료 차트 워크플로의 실용 기능을 자체 데이터·모의투자·AI 패치 구조에서 제공하는 것이다. 네 개 Packet이 하나의 버전형 `ChartDocument`, 공유 조건 DSL, renderer adapter를 사용한다.

## Benchmark Matrix

| Capability | Status | Evidence |
| --- | --- | --- |
| Time-based charts | implemented | line, area, baseline, candle, hollow, HA, OHLC/high-low |
| Price-based charts | implemented | Renko, Kagi, Line Break, Range와 파라미터·synthetic provenance |
| Session/provenance | implemented | regular/extended/all, timezone, source, freshness, strict timeframe |
| Technical/custom studies | implemented | 검색 registry, bounded parameters, pane placement, Strategy Studio output |
| Koyfin-style series | implemented | price, peer, benchmark, portfolio, fundamental, analyst series와 정규화 |
| TrendSpider-style analysis | implemented | deterministic trend/pattern/MTFA/seasonality/relative-strength rail |
| Events | implemented | earnings, news, trades, alerts와 chart markers/filter contract |
| Shared conditions | implemented | multi-symbol/timeframe tree shared by alert, backtest, replay paper rules, AI diff |
| Export | implemented | chart document, analysis snapshot, source bars |
| Replay | implemented | look-ahead-safe cursor, shared clock, speed/step/live jump, branch persistence |
| Chart paper trading | implemented | market/limit/stop/bracket, OCO, partial exit, fees/slippage/leverage, editable lines |
| Strategy handoff | implemented | Strategy Studio result and condition packet to cursor-safe replay rules |
| Multi-chart | implemented | persisted 1~16 panels, 4x4 compact mode, active maximize |
| Synchronization | implemented | symbol, interval, period, crosshair, range, replay, compatible drawings |
| Persistence | implemented | autosave/save, versions, restore, templates, replay sessions, drawing state |
| Keyboard command palette | intentionally different | Streamlit focus 충돌을 피하고 compact visible controls를 유지; browser pan/zoom/drawing은 무 rerun |
| 5,000-bar p95 < 50 ms | intentionally different | Plotly p95 258.4 ms; renderer-neutral 문서가 Canvas 전환 경계로 남음 |
| DOM/order-flow | implemented where supported | KIS trade-size VAP, KR 10호가/US 1호가 depth, spread, imbalance, bounded history |
| True footprint/bid-ask delta | data-blocked | authoritative aggressor side가 없어 추정하지 않고 명시적으로 비활성화 |
| TPO market profile | intentionally different | 현재는 실제 체결량 기반 VAP를 우선; 임의 시간 분포를 order flow로 표시하지 않음 |

## Alternative Products Adopted

- TrendSpider: 하나의 명시적 조건 트리를 분석, 알림, 백테스트, 모의 규칙, AI 수정에 재사용한다.
- Koyfin: 가격뿐 아니라 벤치마크, 포트폴리오, 펀더멘털, 애널리스트 시리즈를 같은 워크플로에서 관리한다.
- FINVIZ Elite: 차트에서 이벤트·펀더멘털·비교·내보내기로 빠르게 이동한다.
- thinkorswim: 차트 주문선과 하단 주문·포지션·전략·이벤트·진단 터미널을 결합한다.

## Verification Chain

- Packet 1: `2026-08-08-chart-packet1-verification.md`
- Packet 2: `2026-08-09-chart-packet2-verification.md`
- Packet 3: `2026-08-09-chart-packet3-verification.md`
- Packet 4: `2026-08-13-chart-packet4-verification.md`
- 최종 관련 회귀: **476 passed**, 실패·skip 없음, 기존 Plotly nanoseconds 경고 1건
- Packet 4 브라우저: console/page/request 오류 0, desktop/mobile overflow false, Plotly DOM 2

## Residual Boundaries

- Plotly는 현재 drawing runtime과 호환되지만 5,000봉·9 trace의 50 ms 목표를 충족하지 못한다. Canvas 가격 surface 전환은 문서·도메인 로직을 바꾸지 않고 진행할 수 있다.
- 미국 무료 KIS 호가는 1단계이며, 국내 10호가와 같은 시장 깊이를 만들 수 없다.
- Replay broker는 단일 종목 long 중심이다. 숏 차입과 포트폴리오 교차증거금은 현재 목표 밖이다.
- 4x4 overview는 비교용 compact view다. 전체 연구·드로잉·주문 도구는 활성 패널 최대화에서 제공한다.
