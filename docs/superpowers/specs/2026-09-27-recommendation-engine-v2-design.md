# 추천 엔진 v2 설계

## 1. 목적

현재 종목 추천은 `ml.entry_analyzer`, `reports.investment_report`, 미국·한국 모의투자 정책이 서로 다른 점수와 원장을 사용한다. 수집 데이터는 원본 보존과 분류는 되어 있지만 추천용 특징량으로 일관되게 변환되지 않고, 추천 생성 때 대용량 JSONL을 다시 훑거나 종목별 가격 조회를 반복한다.

이번 작업의 목표는 다음 네 가지다.

1. 추천 표면이 달라도 같은 point-in-time 데이터 계약과 평가 기준을 사용한다.
2. 뉴스·거시·시장 스냅샷·예측시장 데이터를 추천 목적에 맞게 분리한다.
3. 점수, 표본 불확실성, 데이터 품질, 포트폴리오 집중 위험을 함께 보여준다.
4. 원본 캐시는 유지하면서 추천 조회는 증분 인덱스와 배치 특징량으로 빠르게 만든다.

실거래 주문과 기존 모의투자 주문 규칙은 이 설계의 직접 변경 대상이 아니다. 새 추천은 먼저 shadow 모드로 기존 결과와 비교하고, 검증 관문을 통과한 경우에만 표시·모의투자 정책에 연결한다.

## 2. 확인된 현재 문제

- 진입 후보 원장 9,380건에 `20`과 `20d`가 같은 평가를 별도 행으로 저장하고 있다. 20일 결과는 5,559건씩 중복된다.
- 진입 결과에는 `fwd_ret` 1,178건, `fwd_excess` 1,422건의 비유한 값이 있다. 현재 평균·상관 계산이 `NaN`으로 오염될 수 있다.
- 20d 진입 후보는 163건이며 성공률은 46.6%다. 점수 0.80 이상 구간의 성공률은 37.6%로 점수의 확률 해석이 성립하지 않는다.
- 저장된 GBDT 모델의 학습 종료일은 2025-04-14이고 모델 파일 생성일은 2026-06-27이다. OOS IC는 -0.010, 상위 10% 초과수익은 -0.99%다.
- `ml.data_sources.fetch_news_features`는 현재 JSONL 파싱을 구현하지 않은 placeholder다.
- `~/reports`는 약 16GB, `source-cache`는 약 11GB다. 최근 캐시에는 예측시장·시장 스냅샷 데이터가 뉴스 데이터보다 훨씬 많다.
- 뉴스 라벨은 20,032건이며 중복 ID가 121건 있다. 라벨 생성 지연은 중앙값 0.6시간, 90분위 2.85시간이다.
- SaveTicker는 현재 HTTP 403으로 차단되어 있다. 차단 상태를 정상적인 빈 뉴스로 취급하면 추천 신뢰도가 과대평가된다.

## 3. 범위와 비범위

### 포함

- 추천 결정·성과 원장의 canonical horizon 및 비유한 수치 정규화
- 원본 JSONL을 보존하는 증분 SQLite 추천 인덱스
- 소스 가족별 뉴스·거시·시장·예측시장 특징량
- 유사 과거 표본 수축, 점수 보정, 데이터 품질·신선도 표시
- 선택 후보와 비선택 후보를 함께 기록하는 shadow feedback
- 기존 GBDT 챔피언/챌린저 관문과 추천 결과 연결
- 배치 가격·특징량 계산 및 warm-cache 성능 검증

### 제외

- 새로운 LLM 프롬프트·World Memory·위키 UI
- 실제 증권계좌 주문 실행
- SaveTicker 차단 우회 또는 인증정보 변경
- 원본 JSONL 삭제·재작성
- 처음부터 새 GBDT 모델을 실전 채택하는 것

## 4. 제안 아키텍처

```text
raw source JSONL
      │
      ├─ source indexer (incremental, SQLite)
      │      ├─ event_index
      │      └─ ticker_day_features
      │
      ├─ price/macro batch snapshot
      │
      └─ point-in-time feature assembler
                   │
        ┌──────────┴──────────┐
        │                     │
  existing surface score   news/macro shadow axes
        │                     │
        └──────────┬──────────┘
                   │
       calibration + risk/concentration gate
                   │
       RecommendationSnapshot
                   │
       selected + shadow candidate outcomes
```

추천 엔진은 기존 세 표면의 내부 계산을 한 번에 합치지 않는다. 각 표면의 원점수와 목적은 유지하고, 공통 래퍼가 결과를 정규화한다.

- `entry_analyzer`: 단기·스윙 진입 후보와 유사 과거 분포
- `investment_report`: 펀더멘털·기술·뉴스 기반 스캔 후보
- `us/kr policy`: 모의 포트폴리오 편입·증액·감액 후보

공통 래퍼는 세 결과를 억지로 하나의 점수로 평균내지 않고 `surface`, `horizon`, `market`, `model_version`을 보존한다. 비교가 필요한 경우에만 calibrated alpha와 위험 패널티를 사용해 최종 순위를 만든다.

## 5. 데이터 계약

### 5.1 RecommendationSnapshot

모든 추천 결과는 다음 필드를 갖는다.

```text
id, decision_ts, asof_date, surface, market, ticker, horizon
raw_score, calibrated_probability, expected_excess
signal, rank, selection_status
sample_size, sample_quality, uncertainty_low, uncertainty_high
freshness_seconds, source_coverage, data_quality, missing_fields
risk_score, concentration_penalty, total_rank_score
model_version, parameter_version, feature_version
reasons[], evidence_refs[]
```

`selection_status`는 `selected`, `shadow`, `filtered`, `invalid` 중 하나다. 추천 화면은 `selected`만 노출할 수 있지만, 학습 원장은 `shadow`와 `filtered`도 저장해야 선택편향을 줄일 수 있다.

### 5.2 Horizon 규칙

과거 별칭은 읽을 때 다음 canonical 값으로 변환한다.

```text
20, 20d -> 20d
60, 60d -> 60d
30m, 1h, 4h, 1d, 3d, 5d -> 그대로 유지
```

같은 `base_decision_id + canonical_horizon`은 하나의 outcome만 유효하다. 기존 중복 행은 삭제하지 않고 읽기 단계에서 canonical key로 dedupe하며, 정리 리포트에 중복 수를 기록한다.

### 5.3 수치 정규화

`NaN`, `+inf`, `-inf`는 append 전에 `null`로 변환한다. `success`, `path_result`, 원본 가격 등 평가에 필수인 값이 없으면 outcome 상태를 `invalid`로 저장하고 평균·학습 표본에서 제외한다. 원본 레코드는 감사용으로 유지한다.

## 6. 증분 소스 인덱스

인덱스 파일은 `~/reports/ml-cache/recommendation_index.sqlite3`로 한다. Python 표준 라이브러리 `sqlite3`만 사용해 신규 의존성을 추가하지 않는다.

### 테이블

- `ingest_checkpoint(path PRIMARY KEY, inode, byte_offset, file_size, mtime, indexed_at)`
- `event_index(event_id PRIMARY KEY, content_id, source, source_family, kind, trust, topic, tickers_json, published_at, collected_at, body_len, body_available, wiki_eligible, raw_path, raw_line, content_hash)`
- `ticker_day_features(ticker, day, news_count, article_count, community_count, source_diversity, trust_score, directional_score, freshness_seconds, body_ratio, feature_version, PRIMARY KEY(ticker, day, feature_version))`

원문 본문은 SQLite에 복제하지 않는다. `raw_path`와 `raw_line`으로 기존 JSONL 원문을 다시 열 수 있게 하고, 콘텐츠 해시로 중복·교차 소스 재전파를 처리한다. append-only 일별 파일은 checkpoint 이후 바이트만 읽는다. 파일이 교체되거나 offset이 축소되면 해당 파일만 안전하게 재색인한다.

### 소스 분리

초기 분류는 현재 `reports.source_collector.SOURCE_CLASSIFICATION`을 사용한다.

- 뉴스: SaveTicker, Arca 뉴스/분석, Telegram 기사·분석
- 커뮤니티: Arca 일반글, Telegram 커뮤니티 신호
- 거시: FRED, World Government Bonds, 경제 캘린더
- 시장: Yahoo Finance 가격·시장 스냅샷
- 예측시장: Polymarket, Kalshi

뉴스 추천 특징량에는 뉴스·커뮤니티만 직접 사용한다. 거시·시장·예측시장은 별도 축으로 저장하고, 동일 이벤트를 여러 소스가 반복한 경우 `source_diversity`는 증가시키되 이벤트 수를 단순 합산하지 않는다.

신뢰도 prior는 `A=0.90`, `B=0.75`, `C=0.55`, `D=0.35`로 시작한다. 이는 초기 품질 표시와 shadow 특징량에만 사용하며, 뉴스 축의 실전 가중치는 검증 관문 전까지 0이다. 소스별 성과 가중치는 다음 단계의 시간순 OOS 평가 결과로만 조정한다.

## 7. 특징량과 점수

### 뉴스·증거 특징량

티커·기준일별로 다음을 계산한다.

- `news_count_1d`, `news_count_3d`, `news_count_7d`
- `article_count`, `community_count`, `source_diversity`
- `trust_weighted_direction`, `event_type_mix`
- `body_available_ratio`, `median_freshness_seconds`
- point-in-time LLM label의 방향·강도 감쇠합

라벨은 `published_at <= decision_ts`이고 `labeled_at <= decision_ts`인 경우만 사용한다. 라벨 생성이 늦거나 소스가 차단되어도 0점으로 위장하지 않고 `missing_fields`와 `data_quality`를 낮춘다.

### 표본 수축

유사 과거 성공률은 기존 비율을 그대로 사용하지 않고 Beta(2,2) prior를 적용한다.

```text
shrunk_win_prob = (wins + 2) / (samples + 4)
```

20개 미만 표본은 `low`, 20~49개는 `medium`, 50개 이상은 `high`로 표시한다. 구간 하·상한은 표본 수를 반영한 보수적 신뢰구간으로 계산한다.

### 점수 보정

각 surface의 raw score는 5개 quantile bin을 사용하는 시간순 학습 구간에서 `positive excess` 확률로 보정한다. 한 bin의 표본이 20개 미만이면 인접 bin과 합쳐 최소 표본을 확보하고, 전체 유효 표본이 100개 미만이면 보정기를 만들지 않는다. 보정기는 학습 구간에서만 fit하고 다음 walk-forward 구간에서 평가한다. OOS Brier score가 기준선보다 높거나 ECE가 기준선보다 0.02 이상 악화되면 raw score를 유지한다.

최종 순위는 다음 요소를 별도로 보존한다.

```text
total_rank_score = calibrated_alpha
                  - risk_penalty
                  - concentration_penalty
```

여기서 뉴스 축은 검증 전 `0`이며, 위험 패널티에는 변동성·MDD·레버리지·데이터 부족만 포함한다. 종목 선택 횟수 제한을 새로 두지 않고, top-N 표시 단계에서만 동일 섹터·상관군 과집중을 감점한다.

## 8. 피드백 루프와 채택 관문

추천 때 선택 후보뿐 아니라 후보군 전체에 다음 메타데이터를 저장한다.

- `selection_rank`, `selection_status`, `filter_reason`
- raw/calibrated score, feature quality, evidence refs
- model/parameter/feature version
- 예상 보유 기간과 horizon

성과 백필은 canonical horizon별로 한 번만 생성한다. 평가는 gross return, net return, benchmark excess, MDD, stop/target 경로, 비용·슬리피지를 모두 분리한다.

GDBT 또는 뉴스 축은 다음 조건을 모두 만족할 때만 champion 후보가 된다.

1. 최소 3개 purged walk-forward fold가 존재한다.
2. 평균 OOS IC가 기준선보다 `0.01` 이상 높다.
3. 상위 10%의 순 초과수익이 거래비용보다 높다.
4. MDD가 기준선보다 악화되지 않거나 악화 폭이 2%p 이내다.
5. OOS Brier score가 기준선 이하이고 ECE 악화가 0.02 미만이다.
6. 데이터 품질 낮음·소스 차단 구간을 제외한 별도 검증에서도 방향이 유지된다.

조건을 통과하지 못하면 `shadow`로만 저장하고 기존 규칙 기반 결과를 유지한다.

## 9. 성능 목표

- warm-cache에서 100개 후보의 추천 특징량 조회 p95: 300ms 이하
- 추천 생성 중 전체 `source-cache/events-*.jsonl` 재스캔: 0회
- 동일 실행에서 동일 티커의 가격·기술지표 계산: 1회
- 신규 10,000 이벤트 증분 색인: 10초 이하를 목표로 하며 초과 시 health metric 기록
- 인덱스 오류·잠금·손상 시 기존 tail 조회와 기존 점수로 graceful fallback

## 10. 오류 처리와 관측성

인덱스 상태에 `last_indexed_at`, `last_source_file`, `last_offset`, `lag_seconds`, `error_count`, `fallback_count`를 저장한다. 추천 결과에는 `data_quality=degraded`를 명시하고, 차단된 소스는 빈 신호가 아니라 `unavailable`로 표시한다.

데이터 계약 위반, canonical 중복, 비유한 수치, 라벨 미래시점 사용, 인덱스 lag를 주간 검증 리포트에 포함한다. 이 지표가 기준을 넘으면 새 모델 채택을 자동 보류한다.

## 11. 단계적 출시

### 단계 1: 원장 정합성

Canonical horizon, 비유한 수치 정규화, 중복 리포트와 회귀 테스트를 먼저 적용한다. 기존 JSONL은 보존한다.

### 단계 2: 증분 인덱스와 뉴스 특징량

SQLite 인덱서와 `fetch_news_features`를 구현하고, 기존 추천에는 연결하지 않은 채 shadow feature를 생성한다. 기존 source collector의 raw archive·분류·health 기록을 재사용한다.

### 단계 3: 공통 추천 스냅샷과 보정

세 추천 surface를 공통 스냅샷으로 감싸고 calibration, uncertainty, data quality를 함께 기록한다. 리포트에는 기존 추천과 v2 shadow 순위를 나란히 기록한다.

### 단계 4: 성능 최적화와 채택 검증

배치 가격 조회, 공통 특징량 memoization, 후보 전체 shadow outcome을 적용한다. 최소 한 번의 주간 재학습과 세 개 이상의 walk-forward 결과가 쌓인 뒤에만 v2를 명시적으로 활성화한다.

## 12. 테스트 전략

- 단위: horizon canonicalization, finite normalization, source classification, point-in-time label filtering, Beta shrinkage, score calibration
- 저장소: SQLite checkpoint 재시작, offset 축소·파일 교체, duplicate content hash, raw reference 복원
- 통합: source JSONL → index → ticker-day features → RecommendationSnapshot
- 회귀: 기존 entry/report/mock 결과의 필드·정렬·fallback 보존
- 성능: 100개 후보 warm-cache p95, 10,000개 증분 이벤트 색인 시간, 전체 JSONL 재스캔 금지
- 검증: purged walk-forward, 비용·슬리피지 반영, champion/challenger 채택 보류 조건

성공 기준은 “새 점수가 더 높다”가 아니다. 정규화된 평가 뷰의 중복·비유한 값이 0이고, 추천 생성 시간이 안정화되며, OOS에서 기준선 대비 개선된 경우에만 추천 품질 개선으로 간주한다. 원본 append-only 원장에 남아 있는 과거 중복·결측은 삭제하지 않고 감사 지표로 계속 표시한다.

## 13. 트레이드오프

- SQLite 인덱스가 추가되어 저장 구조가 하나 늘어나지만, 원본 JSONL을 재작성하지 않고 추천 조회 비용을 크게 줄일 수 있다.
- shadow 후보와 증거 메타데이터를 더 저장하므로 ml-data가 증가하지만 선택편향과 사후 원인 분석이 좋아진다.
- 뉴스 축을 즉시 실전에 넣지 않으므로 단기적으로 추천 변화가 작을 수 있다. 대신 현재 SaveTicker 차단·라벨 지연·소스 품질 문제로 인한 허위 개선을 막는다.
- 공통 래퍼를 도입해 초기 코드량은 늘지만, 한 surface만 고쳐 다른 surface가 그대로인 문제를 줄인다.
