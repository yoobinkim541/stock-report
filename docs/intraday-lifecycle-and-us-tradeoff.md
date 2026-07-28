# 단기투자 생명주기와 미국 단기투자 trade-off

작성일: 2026-07-28

## 참고한 설계 축

- awesome-quant: 백테스트, 리스크, 거래소 캘린더, 시장 데이터 소스 분리.
- machine-learning-for-trading: 데이터 수집 -> 피처 -> 검증 -> 실행의 단계 분리.
- Computational-Finance-Course / Financial-Models-Numerical-Methods: 경로 의존 손익, 리스크 시뮬레이션, 보수적 체결 가정.

## 단기투자 생명주기

기존 엔진은 `진입 -> 전량 청산`에 가까웠다. 새 구조는 포지션을 아래 상태로 관리한다.

1. 진입
2. 보유
3. `+1R` 반액 익절
4. `-0.5R` 반액 손절
5. 잔여 수량 관리
6. `+2R` 전액 익절
7. `-1R` 전액 손절
8. 신호 붕괴 / 시간 청산 / 장마감 청산

보수 원칙:

- 같은 1분봉에서 stop과 target이 모두 닿으면 stop을 먼저 인정한다.
- 반액 익절 후 잔여 수량의 stop은 진입가 근처로 올려 손익 비대칭을 만든다.
- 부분 청산은 당일 손익에는 즉시 반영하고, 학습용 outcome은 최종 청산 때 모든 leg를 합산해 1건으로 남긴다.

## 미국 단기투자가 적게 거래되는 이유

2026-07-28 점검 기준으로 미국 1분봉은 장중에는 쌓인다.

- 2026-07-24: US 1분봉 약 14,219 rows
- 2026-07-27: US 1분봉 약 14,751 rows
- 2026-07-28 07:xx UTC: US 16 rows. 미국 정규장 전이라 낮은 게 정상이다.

문제는 수집 부재 하나가 아니라 진입 게이트다.

- 실시간 quote freshness가 false면 진입 차단.
- 기초 신호 티커와 레버리지 실행 티커가 다를 때 실행 티커 bar/features가 없으면 차단.
- 확인봉, 스프레드, 손실예산, 최소 주문금액을 모두 통과해야 한다.
- 기존에는 이 차단 사유가 원장에 남지 않아 "수집 문제"와 "진입 게이트 문제"가 구분되지 않았다.

## 운영 방향

당분간 권장값은 `KR active / US shadow`다.

- KR: 표본 축적이 빠르고 수급/지수/상승하락 종목 수와 연결하기 쉽다.
- US: 레버리지 ETF와 대형주 유동성은 좋지만, 시간대, 프리/애프터장, KIS WS 구독, freshness, 실행 티커 매핑 변수가 많다.
- US는 진단 원장으로 차단 사유를 축적한 뒤, `stale_data`, `no_execution_axes`, `confirm_wait` 비중이 낮아질 때 active 승격을 검토한다.

## 구조적 레버리지

미국 모의투자의 구조적 레버리지 슬리브는 `off | shadow | paper` 모드로 둔다.

- 기본 `shadow`: 추천과 진단만 남기고 mock 주문/예산에는 반영하지 않는다.
- `paper`: 게이트가 `GO`이고 `reco_lev > 1`일 때 `(reco_lev - 1)` 비율만큼 2x ETF 슬리브를 모의 주문한다.
- 레거시 `US_MOCK_LEV_SLEEVE=true`는 `paper`와 동일하게 해석한다.

## 새 진단 데이터

`~/reports/ml-data/{kr,us}_intraday_diagnostics.jsonl`에 매 실행별 요약을 남긴다.

- `candidates`
- `entries`
- `skip_reasons.no_bar`
- `skip_reasons.no_features`
- `skip_reasons.no_execution_axes`
- `skip_reasons.stale_data`
- `skip_reasons.spread`
- `skip_reasons.confirm_wait`
- `skip_reasons.score_below_threshold`

이 값은 `lib.intraday_status`를 통해 리포트와 대화형 답변에서 바로 읽을 수 있다.
