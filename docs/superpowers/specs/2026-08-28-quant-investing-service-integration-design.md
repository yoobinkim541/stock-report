# 퀀트 투자 서비스 통합 설계

- 작성일: 2026-08-28
- 상태: 설계 검토 대기
- 범위: 국내 장중 단기매매 + 국내·미국 중기 포트폴리오
- 원칙: 공통 전략·백테스트 엔진, 실행 프로필별 데이터·체결·리스크 정책

## 1. 배경과 목표

현재 서비스는 `ml/strategy_studio`의 선언형 전략 명세와 규칙 기반 바 시뮬레이션,
`ml/backtest.py`의 기본 성과 계산, `ml/risk_model.py`의 포트폴리오 위험 계측,
`ml/models.py`의 LightGBM/sklearn 어댑터, KIS/KRX·Yahoo·뉴스·공시 수집기를 각각
보유하고 있다. 그러나 이 기능들이 하나의 실험 계약과 실행 원장으로 묶여 있지 않아
다음 문제가 있다.

- 전략 명세가 지표·규칙·고정 비중에 치우쳐 팩터·ML·뉴스·국면 모델을 조합하기 어렵다.
- `optimization`과 `walk_forward` 설정이 명세에 존재하지만 백테스트 실행 경로에서
  충분히 적용되지 않는다.
- 백테스트와 모의투자가 서로 다른 체결 가정을 사용할 수 있어 성과 차이의 원인을
  추적하기 어렵다.
- 현재 유니버스와 일부 외부 데이터는 과거 시점 구성·공개 시점이 보존되지 않아
  생존편향과 룩어헤드 위험이 있다.
- AI 전략 수정은 현재 키워드 휴리스틱 패치 중심이라 복합적인 전략 변경과 검증을
  안정적으로 표현하기 어렵다.

목표는 다음과 같다.

1. 규칙·팩터·ML·앙상블 전략을 하나의 선언형 명세로 표현한다.
2. 국내 1·5분봉 단기 프로필과 국내·미국 시간봉/일봉/주봉 프로필이 같은 엔진을
   공유하되 데이터와 체결 정책은 격리한다.
3. 거래비용, 스프레드, 지연, 부분체결, 거래량 제한, 갭을 백테스트와 모의투자에
   동일한 실행 이벤트 모델로 적용한다.
4. 모델 성과가 아니라 비용 차감 후 위험조정 성과와 검증 안정성을 기준으로 전략을
   승격한다.
5. AI가 자연어로 전략을 수정하고, 변경 diff·재검증·버전 이력을 남기도록 한다.

## 2. 연구에서 가져올 원칙

연구 결과는 복잡한 모델을 즉시 실거래의 중심에 두기보다, 기준선·비용·검증·불확실성
계층을 먼저 만드는 방향을 지지한다.

- 금융 시계열 foundation model은 저데이터 환경의 보조 사전정보가 될 수 있지만,
  랜덤워크 대비 개선 폭이 작고 모든 시장에서 일관된 알파를 보장하지 않는다.
  따라서 독립적인 매매 결정기가 아니라 후보 신호 또는 앙상블 구성요소로 사용한다.
  [Pretrained Time-Series Foundation Models for Financial Return Forecasting](https://arxiv.org/abs/2606.27100)
- 예측 후 최적화는 작은 예측 신호를 과도한 거래로 소진할 수 있으므로 거래비용을
  목적함수와 제약조건에 포함한다.
  [Machine Learning and the Implementable Efficient Frontier](https://academic.oup.com/rfs/advance-article/doi/10.1093/rfs/hhag022/8524346)
- 단일 워크포워드 결과만으로 백테스트 과적합을 판정하지 않는다. CPCV, DSR, PBO,
  비용·국면별 성과를 함께 기록한다.
  [Backtest overfitting comparison](https://www.sciencedirect.com/science/article/pii/S0950705124011110),
  [Deflated Sharpe Ratio](https://doi.org/10.2139/ssrn.2460551)
- RL은 종목 선정보다 시장가/지정가 선택과 분할체결 같은 실행 문제에 먼저 적용한다.
  현재 관련 결과는 시뮬레이터·사전 공개 연구의 성격이 강하므로 실계좌 기본 경로로
  사용하지 않는다.
  [Reinforcement Learning for Trade Execution with Market and Limit Orders](https://arxiv.org/abs/2507.06345)
- LLM은 수치 예측과 최종 주문 결정보다 뉴스·공시 구조화, 전략 패치 생성, 결과
  설명에 사용한다. 모든 생성 결과는 구조화 검증기를 거친다.
  [GPT-4 as Sell-Side Analysts](https://arxiv.org/abs/2412.01069)

## 3. 설계 원칙

### 3.1 동일한 전략, 다른 실행 프로필

전략의 의미는 `StrategySpec`으로 보존하고, 시장별 차이는 `data_profile`과
`execution_profile`로 분리한다.

- `kr_intraday`: KIS/KRX 1분·5분봉, 수급·호가·시장 breadth, 국내 세션과 가격제한폭
- `global_swing`: 국내·미국 1시간·4시간·일봉·주봉, FX·배당·분할·overnight gap
- `extended_us`: 미국 정규장·프리마켓·애프터마켓, 시간대별 유동성·스프레드 차등

### 3.2 자유도는 DSL과 검증된 플러그인으로 제공

사용자가 임의 Python을 입력해 실행하는 방식은 사용하지 않는다. 대신 지표·팩터·모델·
최적화기·체결기가 등록된 플러그인 레지스트리에서 선택되도록 한다. 이를 통해 높은
조합 자유도, 재현성, 버전 관리, 코드 실행 보안을 확보한다.

### 3.3 휴먼 리뷰 대신 자동 승격 게이트

일반적인 전략 실험에는 사람의 수동 검토를 요구하지 않는다. 자동 검증 게이트가
누수·최소 표본·비용 후 성과·MDD·turnover·국면 안정성을 판정한다. 다만 실거래
연결은 sandbox와 별도의 명시적 활성화 상태로 둔다.

## 4. 공통 데이터 계약

모든 시계열과 이벤트는 아래 메타데이터를 보존해야 한다.

### 4.1 가격·거래 데이터

```json
{
  "symbol": "005930.KS",
  "timestamp": "2026-08-28T10:35:00+09:00",
  "open": 0.0,
  "high": 0.0,
  "low": 0.0,
  "close": 0.0,
  "volume": 0,
  "source": "kis",
  "timeframe": "5m",
  "session": "regular",
  "adjustment": "raw",
  "received_at": "2026-08-28T10:35:02+09:00",
  "quality": "complete"
}
```

필수 규칙:

- `timestamp`는 거래소 이벤트 시각, `received_at`은 수집 시각으로 분리한다.
- 시간대와 정규장/extended session을 명시한다.
- 조정주가와 원주가를 혼용하지 않는다.
- 결측·중복·시간 역전·비정상 가격을 품질 상태로 기록한다.
- 동일 바의 재수집은 `(symbol, timeframe, timestamp, source)` 키로 멱등 처리한다.

### 4.2 뉴스·공시·재무 데이터

```json
{
  "event_id": "source:external-id",
  "symbol": "AAPL",
  "published_at": "2026-08-27T21:00:00Z",
  "available_at": "2026-08-27T21:03:12Z",
  "source": "saveticker",
  "event_type": "earnings",
  "payload_version": 1,
  "extraction_confidence": 0.86,
  "raw_ref": "raw/news/2026/08/..."
}
```

`published_at`과 실제 모델 사용 가능 시각인 `available_at`을 분리한다. 재무제표는
기간 종료일이 아니라 시장에 공개된 시점 이후에만 피처로 사용한다. 원문은 별도
raw 저장소에 보관하고, LLM 추출 결과에는 원문 참조와 추출 모델 버전을 붙인다.

### 4.3 유니버스

유니버스 구성은 현재 구성종목 목록을 그대로 과거에 투영하지 않는다. 최소한
`symbol`, `effective_from`, `effective_to`, `source`, `reason`을 저장하고, 과거
백테스트에는 해당 시점에 유효한 유니버스를 사용한다. point-in-time 구성 데이터가
없는 경우 결과에 생존편향 경고와 신뢰도 하락을 표시한다.

### 4.4 주문·체결 원장

```json
{
  "run_id": "run-...",
  "strategy_id": "spec-...",
  "strategy_version": 3,
  "symbol": "005930.KS",
  "decision_at": "...",
  "submitted_at": "...",
  "accepted_at": "...",
  "filled_at": "...",
  "side": "buy",
  "requested_qty": 100,
  "filled_qty": 80,
  "decision_price": 70000.0,
  "fill_price": 70025.0,
  "fee": 1000.0,
  "slippage": 25.0,
  "status": "partial",
  "reason": "factor_entry"
}
```

백테스트·모의투자·실거래 어댑터는 같은 원장 형태를 사용한다. 환경별 차이는
체결 어댑터가 담당한다.

## 5. StrategySpec 확장

기존 `indicators`, `rules`, `sizing`, `costs`는 하위 호환으로 유지한다. 신규 필드는
다음과 같다.

```json
{
  "name": "국내 단기 모멘텀 앙상블",
  "market": "kr",
  "timeframe": "5m",
  "base_symbol": "005930.KS",
  "data_profile": "kr_intraday",
  "universe": {
    "type": "screen",
    "definition": "stocks_in_play",
    "point_in_time": true
  },
  "features": [
    {"plugin": "momentum", "lookbacks": [5, 20, 60]},
    {"plugin": "volume_shock", "lookback": 20},
    {"plugin": "market_breadth", "source": "krx"}
  ],
  "signal": {
    "type": "ensemble",
    "members": [
      {"type": "rule", "ref": "vwap_reclaim"},
      {"type": "model", "ref": "lgbm_excess_return_v1"}
    ],
    "aggregation": "rank_weighted",
    "min_confidence": 0.55
  },
  "portfolio": {
    "optimizer": "cost_aware_risk_budget",
    "max_position_pct": 0.15,
    "max_gross_exposure": 1.0,
    "target_volatility": 0.20,
    "max_turnover": 0.30,
    "cash_symbol": "CASH"
  },
  "execution": {
    "profile": "kr_intraday",
    "latency_ms": 500,
    "max_participation_rate": 0.10,
    "partial_fill": true,
    "stop_policy": "atr_trailing",
    "time_stop_bars": 24
  },
  "validation": {
    "mode": "purged_walk_forward",
    "embargo_bars": 5,
    "min_trades": 100,
    "min_test_periods": 4,
    "benchmarks": ["buy_and_hold", "equal_weight", "rsi_baseline"],
    "metrics": ["net_cagr", "sharpe", "sortino", "max_drawdown", "turnover", "pbo", "dsr"]
  },
  "promotion": {
    "environment": "sandbox",
    "require_cost_adjusted_positive_excess": true,
    "max_drawdown_breach_action": "reject"
  }
}
```

`rules`는 결정론적 기준선과 설명 가능한 조건에 사용하고, `signal`은 rule/factor/model/
ensemble을 통일된 출력 계약으로 감싼다. 모든 신호는 최소한 `score`, `confidence`,
`as_of`, `feature_version`, `model_version`을 반환한다.

## 6. 신호·모델 계층

### 6.1 1단계 기준선

- momentum, value, quality, profitability, volatility, liquidity, seasonality
- RSI, EMA, Bollinger, VWAP, ATR 등 기존 지표
- Elastic Net, LightGBM, 기본 cross-sectional ranker
- buy-and-hold, equal-weight, 단순 RSI를 필수 benchmark로 사용

### 6.2 국면과 불확실성

변동성·상관관계·금리·달러·신용·VIX·시장 breadth를 이용해 국면을 분류하고,
신호마다 다음을 기록한다.

- 예측값과 예측 구간
- 데이터 신선도와 결측률
- 현재 국면과 학습 국면의 거리
- 모델 간 신호 불일치
- 신호가 유효하지 않을 때의 fallback

conformal 또는 quantile 방식은 처음부터 모든 전략에 강제하지 않고, ML 신호와
포트폴리오 최적화 입력에 선택적으로 연결한다.

### 6.3 고급 모델의 도입 순서

1. 기존 기준선과 LightGBM/Elastic Net의 순차 비교
2. 여러 모델의 rank ensemble과 국면별 가중치
3. foundation model을 저데이터·보조 피처로 연결
4. 실행 시뮬레이터가 충분히 검증된 뒤 RL 체결 정책을 shadow mode로 운영

모델 레지스트리는 `model_id`, 학습 데이터 범위, feature schema, 코드 커밋, seed,
성능, 비용 후 성능, 적용 가능 프로필을 저장한다.

## 7. 포트폴리오 최적화

기본 목적함수는 다음과 같이 정의한다.

```text
maximize expected_return
       - risk_aversion * portfolio_risk
       - transaction_cost
       - turnover_penalty
       - concentration_penalty
       - regime_mismatch_penalty
```

제약조건:

- 종목별 최대 비중
- gross/net exposure
- 섹터·팩터 노출
- 목표 변동성 또는 risk budget
- 최대 turnover
- 현금 최소 비중
- 레버리지 상한과 낙폭예산
- 장중 프로필의 거래량 참여율

초기 구현은 기존 `ml/optimization.py`의 deterministic grid fallback을 활용하고,
가능하면 convex solver 또는 Optuna를 선택적으로 연결한다. 결과에는 목표 비중,
실제 적용 비중, 비용 전후 비중 변화, 제약조건으로 잘린 원인을 함께 반환한다.

레버리지는 기본적으로 분석·검증 화면에서만 계산한다. 실거래 승격은 충분한 테스트
기간, 비용 후 성과, MDD 예산, 강제 축소·kill switch를 모두 만족한 경우에만 별도
실행 프로필로 허용한다.

## 8. 공통 실행·모의투자 엔진

### 8.1 바 기반 체결

최소 실행 모델:

- 시점 `t`의 결정은 `t+1`의 체결에만 적용
- 시가·고가·저가 범위 내에서 stop/limit 가능 여부 판정
- 갭 발생 시 stop 가격과 실제 체결 가격을 분리
- 스프레드, 수수료, 슬리피지, 지연 반영
- 바 거래량을 기준으로 partial fill과 참여율 제한
- 국내 가격제한폭 및 미국 정규장/extended session 정책 적용
- 주문 실패, 재시도, 취소, 부분체결을 원장에 기록

### 8.2 포지션 라이프사이클

전략은 `flat → entered → holding → trimmed → exited` 상태를 명시적으로 가진다.
반액 익절·반액 손절·시간 청산·트레일링 손절·전량 청산은 주문 이벤트로 표현하고,
단순히 종가에서 weight를 바꾸는 방식으로 숨기지 않는다.

### 8.3 국내 단기 프로필

- KIS/KRX 1분·5분봉과 수급·호가·market breadth 사용
- 장 시작·장 마감·동시호가를 별도 세션으로 구분
- 유동성 부족 시 신규 진입 차단
- 일정 시간 이상 데이터 sink가 정체되면 전략을 자동 pause

### 8.4 글로벌 중기 프로필

- 일봉·시간봉·주봉 지원
- 배당·분할·환율·overnight gap 반영
- 미국 extended-hours는 정규장과 다른 spread·participation 정책 적용
- 장기 신호와 단기 실행 신호를 별도 모델 버전으로 기록

## 9. 백테스트와 검증

백테스트 실행 결과는 단일 요약 수치가 아니라 다음 묶음으로 저장한다.

- 데이터 스냅샷과 유니버스 버전
- 전략 명세 hash와 코드 커밋
- train/validation/test 구간
- 비용·슬리피지·지연 설정
- equity, positions, orders, fills
- gross/net 성과
- CAGR, Sharpe, Sortino, Calmar, MDD, turnover, hit rate
- 비용 drag, exposure, concentration, capacity
- 국면별·종목군별·시간대별 성과
- DSR, PBO, seed stability, 경고 목록

검증 모드:

1. `single_pass`: 빠른 UI 미리보기 전용. 승격에 사용할 수 없다.
2. `walk_forward`: 순차 학습·검증·테스트.
3. `purged_walk_forward`: 라벨 겹침을 제거하고 embargo 적용.
4. `cpcv`: 여러 경로의 out-of-sample 조합으로 경로 의존성을 확인.

필수 실패 조건:

- 미래 시점 데이터 사용
- 가격·이벤트 timestamp 누락
- 비용 후 benchmark 초과 실패
- 최소 거래 수 또는 테스트 기간 미달
- 과도한 turnover·capacity 초과
- 특정 단일 국면에만 성과 집중
- MDD·레버리지 예산 초과

## 10. AI 전략 협업 UX

### 10.1 대화 흐름

```text
사용자 요청
  → 현재 StrategySpec·데이터 품질·최근 검증 결과 로드
  → LLM이 JSON Patch 생성
  → schema/보안/범위 검증
  → 변경 diff 표시
  → 빠른 preview
  → 필요 시 전체 검증 실행
  → 자동 승격 게이트 판정
  → sandbox 버전 저장
```

예시 요청:

> 거래 횟수를 줄이고 손실 국면에서 비중을 낮춰줘.

LLM은 `max_turnover`, `regime_filter`, `risk_budget` 같은 구조화된 필드만 변경한다.
변경 전후의 거래 수·비용·MDD·수익률·국면별 성과를 함께 보여준다. 자연어 설명과
수치 결과가 충돌하면 수치 결과를 우선한다.

### 10.2 UI 구성

- 전략 라이브러리: preset, 사용자 전략, 모델 버전, sandbox/live 상태
- 전략 편집기: JSON이 아닌 폼·블록·조건·팩터·모델 선택 UI
- 실험 패널: 기간·프로필·비용·검증 모드 선택
- 결과 패널: equity, drawdown, turnover, exposure, trade markers, benchmark
- 진단 패널: 데이터 품질, 누수 경고, 제약조건 충돌, 실패 원인
- 대화 패널: 질문, patch diff, 재검증 버튼, 버전 비교
- 전략 그래프: 전략 버전 → 모델 → 데이터 스냅샷 → 백테스트 → 모의 원장 연결

`dashboard/pages/research.py`와 전략 캔버스는 같은 결과 DTO를 사용하고, AI 콘솔은
동일한 API를 호출한다. UI마다 별도 백테스트 로직을 두지 않는다.

## 11. 저장·캐시·운영

초기 단계에서는 현재 저장소와 파일·SQLite 기반 원장을 우선 활용한다. Redis는 모든
기능의 전제조건으로 두지 않는다.

- 장기 raw·결과: 기존 파일/SQLite 경로와 버전 참조
- 반복 백테스트: 전략 hash + 데이터 snapshot + 비용 설정을 캐시 키로 사용
- 장중 hot state: 프로세스 메모리 또는 기존 저장소로 시작
- Redis 도입 조건: 여러 수집기·실행기 간 실시간 상태 공유, 재시작 후 stream 복구,
  초 단위 fan-out, 작업 큐가 실제 병목이 되는 시점

Redis를 도입하더라도 진실의 원천은 주문·체결 원장과 immutable raw 데이터로 두고,
Redis는 hot cache·stream·queue로만 사용한다.

### 11.1 운영 기본값과 credential-free smoke

첫 릴리스에서 지원하는 프로필은 다음 세 가지다.

| 프로필 | 입력·세션 | 신선도·체결 정책 |
|---|---|---|
| `kr_intraday` | KIS/KRX 1분·5분봉과 수급·호가·breadth, 국내 정규장 | stale bar, 불완전 bar 또는 quote source 장애 시 신규 진입 pause, 설정된 청산은 허용 |
| `global_swing` | 국내·미국 일봉/시간봉/주봉과 환율·기업행사·overnight gap | 정규장 중심의 중기 전략, 폐장 구간을 다음 가격으로 forward-fill하지 않음 |
| `extended_us` | 미국 extended-hours를 정규장과 분리한 세션 | 동일한 주문·체결 DTO를 사용하되 spread·참여율·세션 정책은 별도로 적용 |

검증 모드는 아래처럼 승격 가능 여부를 명시한다.

- `single_pass`: 빠른 UI preview 전용이며 promotion/activation evidence로 사용할 수 없다.
- `walk_forward`: 순차 학습·검증·테스트를 확인하는 연구 모드다. 단독 결과는 activation-safe가 아니다.
- `purged_walk_forward`: 라벨 overlap을 제거하고 embargo를 적용하는 기본 승격 후보 모드다.
- `cpcv`: 여러 OOS 경로를 진단하며, 실제 fold별 chronology와 provenance 증거가 모두
  있을 때만 승격 후보가 된다. 누락된 증거는 fail-closed 한다.

프로필 health가 `pause`이면 실행기는 `strategy_paused` diagnostic과 원장 이벤트를
남기고 신규 entry intent를 차단한다. `allow_exits_on_pause`가 켜진 경우 exit intent는
계속 처리하며, 저장된 snapshot에 기록한 source health와 timestamp로 동일한 결정을
paper replay에서 재현한다.

`sandbox`와 `paper`는 각각 shadow/pilot 성격의 비실거래 환경이다. 검증 게이트 통과는
live 연결의 충분조건이 아니며, live에는 명시적 활성화, 서버 capability, 완전한
data/model provenance, 비용 후 성과 증거와 kill switch가 필요하다. credential-free
로컬 개발은 아래 합성 smoke 명령으로 가능하고, 실제 market collector나 broker
credential을 호출하지 않는다.

```bash
./.venv/bin/pytest tests/test_strategy_end_to_end.py -q
```

Redis는 여러 collector/executor 간 실시간 상태 공유, 재시작 후 stream 복구, 초 단위
fan-out 또는 작업 큐가 실제 병목으로 측정될 때만 도입한다. 그 전까지는 프로세스
메모리와 기존 파일/SQLite를 사용하고, 도입 후에도 immutable raw와 주문·체결 원장은
진실의 원천으로 유지한다.

## 12. 단계별 구현 계획

### Phase 0: 계약과 계측

- StrategySpec 신규 필드와 버전 migration
- bar/event/order/fill 공통 DTO
- 데이터 품질·시점·유니버스 경고
- 전략·데이터·모델 hash 기록

완료 기준: 기존 RSI 전략이 변경 없이 실행되고, 모든 결과에 버전·데이터 시점이
남는다.

### Phase 1: 공통 전략 엔진

- rule/factor/model/ensemble signal adapter
- 다중 지표·다중 종목 공통 컨텍스트
- portfolio target과 execution order 분리
- 현재 engine의 `walk_forward`·`optimization` 연결

완료 기준: RSI, momentum, mean reversion, breakout, factor ranking을 동일한 API로
실행한다.

### Phase 2: 현실적인 체결·검증

- next-bar, gap, spread, slippage, latency, partial fill
- 국내 단기·글로벌 중기 execution profile
- purged walk-forward, CPCV, DSR/PBO 결과 DTO
- 백테스트·모의투자 공통 원장

완료 기준: 동일 전략을 백테스트와 모의투자에서 실행하고 체결 이벤트 차이를
비교할 수 있다.

### Phase 3: ML·리스크·최적화

- feature registry와 model registry
- LightGBM/Elastic Net ranker
- regime·uncertainty layer
- cost-aware optimizer, risk budget, turnover·capacity 제약

완료 기준: gross 성과가 아니라 비용 후 성과와 위험기여를 기준으로 전략을 비교한다.

### Phase 4: AI 협업과 UI

- `_heuristic_patch()`를 구조화 LLM patch 생성기로 확장
- patch schema·diff·자동 검증
- 전략 버전 그래프와 결과 비교
- research·strategy canvas·AI console 공통 API

완료 기준: 자연어 요청에서 sandbox 전략 버전과 재현 가능한 검증 결과가 생성된다.

### Phase 5: 고급 모델과 자동화

- foundation model 보조 신호
- RL execution shadow mode
- champion/challenger 자동 평가
- 조건부 자동 승격과 kill switch

다음 항목은 초기 범위에서 제외한다.

- foundation model의 즉시 실거래 단독 사용
- RL의 직접 종목 선정·실계좌 주문
- 검증 결과 없는 자동 live 승격
- 임의 Python 전략 코드 실행

## 13. 위험과 trade-off

| 선택 | 이점 | 비용·위험 | 대응 |
|---|---|---|---|
| 공통 DSL | 재현성·UI 연결·보안 | 표현력 제한 가능 | 플러그인 레지스트리와 버전 확장 |
| ML 앙상블 | 비선형·다양한 신호 | 과적합·설명 난이도 | 기준선·ablation·seed 안정성 |
| 비용 인지 최적화 | turnover·실현비용 통제 | 기대수익을 과도하게 축소할 수 있음 | 비용 시나리오별 결과 표시 |
| 단기 데이터 확대 | 표본 축적·장중 분석 | 품질·지연·저장 비용 | source quality와 pause 정책 |
| RL 실행 | 주문 정책 최적화 가능 | 시뮬레이터와 실시장 괴리 | shadow mode와 제한된 pilot |
| 자동 승격 | 운영 속도 향상 | 잘못된 모델의 자동 확산 | sandbox/live 분리·kill switch |
| Redis 추가 | hot state와 fan-out 개선 | 운영 복잡도·이중화 | 병목 확인 후 선택 도입 |

## 14. 검토가 필요한 결정

구현 계획으로 넘어가기 전에 다음 세 가지를 확정한다.

1. 국내 단기 프로필의 첫 기준 시간봉을 `5m`으로 고정하고 `1m`은 데이터 품질이
   확인된 종목만 허용할지 여부
2. 첫 ML 모델을 cross-sectional LightGBM ranker로 시작할지, 기존 단일자산 모델을
   먼저 공통 adapter로 감쌀지 여부
3. 자동 승격 게이트 통과 후에도 live 연결은 명시적 활성화가 필요하다는 정책

권장 기본값은 `5m`, cross-sectional LightGBM ranker, sandbox 우선이다.
