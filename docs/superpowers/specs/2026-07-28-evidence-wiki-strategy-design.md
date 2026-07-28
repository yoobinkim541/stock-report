# Evidence Wiki Strategy Design

Date: 2026-07-28

## Purpose

AI 콘솔 답변 품질과 단기투자 실험 품질을 같은 근거층 위에서 개선한다. 현재 문제는 두 갈래다.

- AI 콘솔이 로컬 컨텍스트 부족을 답변 중단이나 규칙 기반 시장 템플릿으로 처리하는 경우가 있다.
- 데이트레이딩 실험이 장중 의사결정 표본, 비용, 슬리피지, 데이터 freshness, 리스크 붕괴 여부를 충분히 분리해 설명하지 못한다.

목표는 원문, 시세, 수급, 뉴스, 위키, 모의투자 로그를 공통 EvidenceCard로 정규화하고, 이 근거를 AI 콘솔과 전략 실험층이 함께 쓰게 만드는 것이다.

## References

- [wilsonfreitas/awesome-quant](https://github.com/wilsonfreitas/awesome-quant): quant 라이브러리와 검증 도구 후보를 찾는 지도 역할로 사용한다.
- [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading): 데이터 수집, feature engineering, walk-forward 검증, 비용/리스크 관리, live trading/MLOps 설계 철학을 참고한다.
- [cantaro86/Financial-Models-Numerical-Methods](https://github.com/cantaro86/Financial-Models-Numerical-Methods): 옵션, 변동성, 수치 모델 wiki 확장용 개념 참고로 사용한다. AGPL 코드 복사는 하지 않는다.
- [LechGrzelak/Computational-Finance-Course](https://github.com/LechGrzelak/Computational-Finance-Course): 확률 과정, 옵션 가격, Monte Carlo, volatility 모델의 교육용 레퍼런스로 사용한다.

## Architecture

흐름은 다음 다섯 층으로 나눈다.

```text
Raw Source -> Evidence Card -> Wiki Insight -> Strategy Experiment -> AI Console Answer
```

Raw Source는 Saveticker, Telegram, 가격/수급 데이터, broker/KRX 데이터, 기존 memory, 모의투자 로그처럼 원문이나 숫자가 들어오는 층이다. 이 층에서는 판단을 최소화하고 원본을 보존한다.

Evidence Card는 모든 원문과 숫자를 시간, 출처, 종목, 자산군, 이벤트 유형, freshness, 신뢰도, 시장 영향 축으로 정규화한 공통 단위다.

Wiki Insight는 Evidence Card를 묶어 "AI CAPEX", "KR 수급", "JPM 피어 비교", "단기투자 실패 패턴"처럼 갱신 가능한 지식 카드로 만든다.

Strategy Experiment는 같은 Evidence Card와 로그를 받아 walk-forward 검증, 비용/슬리피지 반영, champion-challenger 비교, RiskGovernor 판단을 수행한다.

AI Console은 질문 의도를 먼저 고정하고, 필요한 Evidence/Wiki/실시간 데이터를 조회한 뒤 답변한다. 컨텍스트 부족은 실패 조건이 아니라 조회 트리거다.

## EvidenceCard Model

초기 모델은 다음 필드를 기준으로 한다.

```text
id
source_type        # saveticker, telegram, price, filing, report, paper, mock_trade_log
source_name
source_url
captured_at
event_time
raw_text
raw_payload
symbols
markets            # US, KR, FX, rates, crypto
topics             # ai_capex, liquidity, kr_flow, ib_peer, short_term_failure
event_type         # earnings, macro, supply_demand, price_break, rumor, strategy_outcome
confidence         # source/parser 기준 신뢰도
freshness          # intraday, daily, stale
impact_axes        # liquidity, growth, credit, valuation, positioning, sentiment
summary
claims             # 원문에서 뽑은 검증 가능한 주장
```

`raw_text`와 `raw_payload`는 버리지 않는다. 요약이나 위키가 틀렸을 때 원문으로 되돌아갈 수 있어야 하고, AI 콘솔도 답변 근거로 원문을 열람할 수 있어야 한다.

커뮤니티, Telegram, rumor성 데이터는 `confidence`와 `event_type=rumor`로 분리한다. 가격, 수급, 공시, 실적자료와 교차검증되기 전에는 결론 근거로 과하게 쓰지 않는다.

## InsightCard Model

Wiki는 사람이 수동 작성하는 문서가 아니라 EvidenceCard를 묶어 자동 갱신되는 InsightCard로 본다.

```text
topic_key
title
current_view
supporting_evidence_ids
conflicting_evidence_ids
last_updated_at
staleness_policy
open_questions
answer_hints
```

업데이트 규칙은 세 단계다.

1. Append: 새 원문은 항상 EvidenceCard로 저장한다.
2. Cluster: 종목, 키워드, 이벤트 유형, 시간으로 관련 evidence를 묶는다.
3. Revise: 기존 InsightCard가 있으면 갱신하고, 없으면 새 topic 후보를 만든다.

자동 갱신은 삭제보다 보존을 우선한다. 오래된 관점은 덮어쓰기 전에 `conflicting_evidence_ids`나 stale 상태로 남긴다.

## AI Console Routing

AI 콘솔은 답변 전에 질문 문장 자체를 기준으로 intent를 고정한다. 현재 화면, memory, market context가 먼저 답변 형식을 결정하면 안 된다.

초기 intent는 다음과 같다.

```text
meta_debug
stock_compare
portfolio_review
market_analysis
technical_analysis
strategy_review
live_market_check
wiki_lookup
```

각 intent는 필요한 데이터 조회를 강제한다.

```text
stock_compare
-> ticker normalize
-> peer auto select
-> local wiki search
-> latest quote/fundamental/news fetch
-> comparison table

market_analysis
-> index/sector/FX/rates/credit/news
-> current session freshness check
-> risk regime only when asked for market view

strategy_review
-> mock trade logs
-> signal decisions
-> realized outcomes
-> costs/slippage
-> missing sample warning
-> improvement hypotheses
```

답변 중단 조건은 좁힌다.

- 종목명이 불명확하다.
- 필요한 도구가 없다.
- 최신 데이터 접근이 실패했다.
- 사용자가 특정 기준을 요구했는데 비교축이 빠져 있다.

그 외에는 기본 기준으로 먼저 진행한다. 예를 들어 "JP모건 다른 IB랑 비교해줘"는 JPM, GS, MS, BAC, C를 기본 peer로 두고 필요 시 UBS, DB, BCS를 추가한다.

템플릿은 intent별로 제한한다. `stock_compare`, `meta_debug`, `technical_analysis`에서는 "현재 시장 상황 인식", "RISK-ON", "시장 신호 점수" 템플릿이 나오면 실패로 본다. `market_analysis`에서는 허용하되 EvidenceCard와 freshness를 붙인다.

답변 하단의 맥락 표시는 실제 사용한 데이터 개수를 보여준다.

```text
맥락: 시장 events 40 / wiki 12 / 실시간 5 / 로그 18
엔진: LLM primary, rules fallback unused
수집: quote ok, news ok, broker unavailable
후처리: wiki update queued
```

## Strategy Experiment Layer

데이트레이딩 설계는 하루 한 번 판단이 아니라 장중 반복 루프다.

```text
observe -> decide -> execute/simulate -> label -> learn
```

의사결정 단위는 DecisionSnapshot이다.

```text
timestamp
symbol
session_phase       # premarket, open, midday, close, afterhours
features            # price, volume, breadth, flow, news, volatility
signals             # momentum, reversal, liquidity, news shock
decision            # long, short, flat, reduce, exit
position_context
expected_edge
risk_budget
cost_estimate
reason_codes
```

성과 라벨은 OutcomeLabel로 분리한다.

```text
decision_id
horizon             # 5m, 15m, 30m, 1d
realized_return
max_adverse_excursion
max_favorable_excursion
slippage
fees
stop_hit
take_profit_hit
quality_label
```

표본은 실거래를 늘리기보다 shadow decision으로 빠르게 쌓는다. 후보 전략들이 동시에 "이 순간이면 매수/매도/관망했을 것"을 기록하고, 5분/15분/30분 뒤 결과를 자동 라벨링한다.

검증 기본값은 다음과 같다.

- walk-forward split
- purged 또는 embargoed validation
- transaction cost와 slippage 반영
- position sizing cap
- daily loss limit
- signal freshness check
- champion-challenger 비교

RiskGovernor는 특정 전략이 연속 손실, 과도한 회전율, stale 데이터, 수급/시장폭 불일치, 변동성 급등 구간에서 계속 진입할 때 shadow-only 또는 size-down으로 내린다.

## Phase Plan

### Phase 1: Evidence/Wiki 기반층

Saveticker, Telegram, 기존 memory, 가격/수급, 모의투자 로그를 EvidenceCard로 정규화한다. 원문은 보존하고, 위키는 InsightCard로 자동 갱신한다.

성공 기준:

- "LLY는 1조 달러가 넘을텐데"에서 티커를 놓치지 않는다.
- "JP모건 다른 IB랑 비교해줘"에서 peer 비교와 최신 데이터 조회를 수행한다.
- "한국증시는 어땠어"에서 한국 시장 freshness와 근거 출처를 표시한다.
- "단기투자 실적이 안좋은 이유가 뭘까"에서 모의투자 로그, signal decision, outcome label을 우선 조회한다.

### Phase 2: AI 콘솔 라우터

intent별 필수 데이터 수집과 forbidden template 규칙을 테스트로 고정한다. LLM이 연결되어 있으면 답변 본문은 LLM 경로를 우선 사용하고, 규칙 기반은 fallback일 때만 표시한다.

성공 기준:

- market_analysis가 아닌 질문에서는 RISK-ON 템플릿이 나오지 않는다.
- 맥락 표시는 고정 숫자가 아니라 실제 사용 데이터 개수를 보여준다.
- quote/news/wiki/broker 조회 성공과 실패가 분리되어 보인다.

### Phase 3: 전략 실험층

DecisionSnapshot, OutcomeLabel, shadow decision, RiskGovernor를 도입한다. 실거래를 늘리기 전에 후보 전략 표본을 빠르게 쌓고 비용/슬리피지/손실 제한을 검증한다.

성공 기준:

- 오늘/지난주 모의투자 로그 분석에서 손실 원인이 reason code, feature snapshot, outcome label로 설명된다.
- 비용 반영 전후 성과 차이를 볼 수 있다.
- stale 데이터나 sink 정체 상태에서는 신규 진입이 제한된다.

## Testing

AI 콘솔 테스트:

- "JP모건 다른 IB랑 비교해줘"는 peer 비교표와 최신 데이터 조회 계획 또는 결과를 포함한다.
- "컨텍스트에 없으면?"은 검색/위키/실시간 데이터 조회로 보강한다.
- "내 포트 평가해줘"는 보유 비중, 손실, 리스크 노출을 우선한다.
- "왜 이렇게 답했어?"는 meta_debug로 분류하고 시장 템플릿을 사용하지 않는다.
- "기술적 분석만 해봐"는 뉴스/거시 템플릿을 제외한다.

Wiki 테스트:

- raw_text와 raw_payload가 보존된다.
- EvidenceCard clustering이 종목, topic, event_type, 시간 기준으로 동작한다.
- stale InsightCard가 fresh evidence 없이 강한 결론으로 쓰이지 않는다.
- rumor evidence는 가격/수급 교차검증 전에는 낮은 confidence로 남는다.

전략 테스트:

- shadow decision이 여러 horizon에 대해 OutcomeLabel을 만든다.
- transaction cost와 slippage 반영 후 성과 악화가 감지된다.
- stale data 상태에서는 RiskGovernor가 신규 진입을 차단하거나 shadow-only로 내린다.
- champion/challenger 비교가 같은 기간과 같은 비용 가정으로 수행된다.

헬스체크:

- 장중인데 1분봉 bar 파일이 없으면 sink 정체로 경고한다.
- broker/KRX unavailable과 Redis stale을 분리해서 표시한다.
- AI 콘솔 맥락 표시가 실제 데이터 조회 결과와 일치하는지 확인한다.

## Non-Goals

- 네 참고 repo의 코드를 직접 복사하지 않는다.
- 첫 구현에서 완전한 broker 실거래 자동매매를 만들지 않는다.
- human review를 wiki 갱신의 필수 단계로 두지 않는다. 대신 confidence, conflict, stale, source trail로 자동 검증 가능성을 높인다.
- 모든 quant 라이브러리를 한 번에 도입하지 않는다. 필요한 검증 축부터 작게 붙인다.

## Open Decisions Fixed For This Spec

- 1차 목표는 AI 답변 품질과 전략 실험 품질을 분리하지 않고 공통 EvidenceCard 기반으로 묶는 것이다.
- 구현 순서는 Evidence/Wiki 기반층, AI 콘솔 라우터, 전략 실험층이다.
- 표본 증가는 실거래 빈도 증가가 아니라 shadow decision 증가로 시작한다.
- 커뮤니티/Telegram 데이터는 수집하되 낮은 confidence와 rumor 유형으로 분리한다.
