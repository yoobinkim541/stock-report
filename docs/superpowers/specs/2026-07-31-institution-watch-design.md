# Institution Watch Design

## Goal

관심종목 화면 안에 기관투자자 관찰 허브를 만든다. 버핏/버크셔만 보던 현재 구조를 확장해서, 레이 달리오, 마이클 버리, 국민연금, 드러켄밀러, 피터 틸 같은 유명 기관투자자·헤지펀드·연기금의 공개 포트폴리오를 한 화면에서 비교하고, LLM이 여러 기관에서 공통으로 보이는 움직임과 대응을 요약해 주며, 그 분석 결과를 위키 문서로 축적한다.

이 작업은 "기관 추적"과 "LLM 위키 축적"을 함께 만드는 설계다. 단순 카드 추가가 아니라, 공개 데이터의 가용성 차이를 그대로 드러내면서도 비교 가능한 공통 프레임을 만드는 것이 핵심이다.

## Current Findings

현재 코드에는 관련 조각이 이미 있다.

- `dashboard/pages/watchlist.py` 는 단순한 종목 목록만 보여준다. 기관 추적 허브는 없다.
- `lib/watchlist.py` 는 종목 관심목록 CRUD만 담당한다.
- `providers/thirteenf.py` 와 `reports/notable_investors_wiki.py` 는 버크셔 13F만 추적한다.
- `dashboard/views.py` 의 `institutional(ticker)` 는 종목 단위 기관/수급 정보를 이미 제공하지만, 기관투자자 여러 명을 비교하는 모델은 아니다.
- `reports/wiki_distillation.py` 는 source_digest 를 playbook/risk/concept 로 승격시키는 경로를 이미 갖고 있다.
- `dashboard/pages/ai_console.py` 와 `agent_console/wiki.py` 는 위키를 검색·편집·재사용하는 인터페이스를 이미 제공한다.

즉, 새 시스템은 완전히 새로 만들기보다, 기존에 있는

`watchlist -> institution snapshots -> LLM analysis -> wiki`

흐름을 한 번 더 일반화하면 된다.

## Design

### 1. 기관 레지스트리와 데이터 가용성 모델

기관별로 다른 데이터 소스를 하나의 레지스트리로 묶는다. 각 기관은 다음 속성을 가진다.

```text
key
display_name
category              # hedge_fund, family_office, pension, sovereign, asset_manager
primary_sources       # 13F, annual_report, public_letter, filing, press_release, manual
metric_capabilities   # holdings, concentration, cash_ratio, options_exposure, return, notes
refresh_policy
confidence
```

중요한 원칙은 "없는 지표를 채워 넣지 않는다"이다. 예를 들어 13F는 보유 종목과 비중은 강하지만 현금 비중은 직접 주지 않는다. 반대로 연기금이나 공공기관은 연차보고서에서 총수익률이나 자산배분이 나올 수 있지만 옵션 노출은 비어 있을 수 있다. 화면은 이 차이를 숨기지 않고 `available / proxy / unavailable` 로 표시한다.

이 레지스트리는 버크셔 전용 하드코딩을 걷어내는 대신, 최소한 아래 계열을 담을 수 있어야 한다.

- Berkshire Hathaway
- Bridgewater
- Scion Asset Management
- Duquesne Family Office
- Founders Fund
- National Pension Service

초기에는 13F 기반 기관이 가장 정확하고, 국민연금처럼 별도 공시 체계가 있는 기관은 보조 어댑터로 붙인다.

### 2. 기관 스냅샷과 비교 모델

각 기관은 같은 형태의 스냅샷으로 정규화한다.

```text
institution_key
as_of
source_kind
freshness
holdings_count
top_holdings
portfolio_concentration
cash_ratio
options_exposure
reported_return
return_proxy
evidence_refs
availability_flags
notes
```

비교 화면은 "같은 항목을 모두가 갖고 있다"는 가정이 아니라, "같은 프레임으로 놓고 각자 가능한 칸만 채운다"는 방식으로 작동한다.

비교값은 세 층으로 나눈다.

1. **보고 값**: 기관이 직접 공시한 수치
2. **프록시 값**: 공개 자료로부터 합리적으로 추정한 값
3. **미가용**: 근거가 부족한 경우

이 구조가 있어야 수익률, 현금 비중, 옵션 같은 민감한 항목을 무리해서 맞추지 않으면서도 비교 UI를 유지할 수 있다.

### 3. 관심종목 화면의 기관 허브

`dashboard/pages/watchlist.py` 를 단순 종목 표에서 "기관 관찰 + 관심종목" 허브로 바꾼다.

권장 레이아웃은 다음과 같다.

- 상단: 기관 카드 그리드
  - 기관명
  - 카테고리
  - 최신 스냅샷 시점
  - 보유 종목 수
  - 현금/옵션/수익률 가용성
  - 전분기 대비 변화 요약
- 중단: 비교 테이블
  - 선택한 기관들을 나란히 놓고 holdings / concentration / cash / options / return / freshness 를 비교
  - 값이 없는 칸은 `—` 과 함께 이유를 노출
- 하단: LLM 공통 패턴 패널
  - "공통 매수", "공통 매도", "현금 확대", "헤지 강화", "방어 전환" 같은 패턴을 요약
  - 다수 기관 중 누가 같은 행동을 했고 누가 이탈했는지 함께 표시
- 보조 영역: 기존 관심종목 목록
  - 현재 종목 watchlist 는 그대로 유지
  - 기관 허브에서 나온 티커를 자동으로 관심종목 후보로 연결할 수 있다

이 화면은 `dashboard/pages/ai_console.py` 안에 같은 보고서를 재사용하는 탭을 둘 수도 있지만, 주 사용면은 `watchlist` 로 유지한다. 사용자는 관심종목 안에서 기관 비교와 시장 관심종목을 함께 볼 수 있어야 한다.

### 4. LLM 공통 움직임 분석

LLM은 각 기관 스냅샷을 읽고, 공통 패턴과 이탈 패턴을 설명하는 짧은 분석을 만든다.

입력은 다음을 포함한다.

- 기관별 스냅샷 요약
- 전분기 대비 변화
- 가용 지표의 품질 플래그
- 동일 섹터/동일 테마 보유의 중복 여부

출력은 다음처럼 제한한다.

```text
shared_moves
divergences
risk_readthrough
follow_up_questions
confidence
```

예시 분석 범주는 다음과 같다.

- 성장주 집중도 확대
- 현금 비중 증가
- 반도체/AI 쏠림
- 방어주로의 회전
- 풋 옵션 또는 헤지 강화
- 금융/에너지/중국 노출 축소

이 분석은 숫자를 조작하는 설명문이 아니라, 정규화된 스냅샷을 바탕으로 한 해석이어야 한다. 그래서 신호가 약하면 "공통 경향 없음" 이라고도 말할 수 있어야 한다.

### 5. 위키 축적

LLM 분석 결과는 위키에 축적한다.

권장 방식은 두 겹이다.

1. 기관별 snapshot digest
   - 기관 하나당 최신 스냅샷을 source_digest 로 저장
   - 제목 예: `기관투자자 위키: Berkshire Hathaway`
2. 교차기관 pattern digest
   - 여러 기관을 묶은 공통 패턴을 별도 source_digest 로 저장
   - 제목 예: `기관 공통 패턴: 현금 확대와 성장주 집중`

이 페이지들은 `reports/wiki_distillation.py` 의 대상이 되어, 반복적으로 검증되는 패턴이면 playbook/risk/concept 로 승격될 수 있다.

위키 페이지는 다음을 포함해야 한다.

- 기준 시점
- 사용한 기관 목록
- 가용 지표와 미가용 지표
- 핵심 관찰
- 공통 움직임
- 예외 기관
- 원문 근거

### 6. 데이터 freshness와 실패 정책

이 기능은 데이터 공백을 숨기지 않는 쪽이 중요하다.

- 새 필링이 없으면 "변화 없음" 으로 표시하고, 오래된 필링인지 함께 보여준다.
- 기관 간 비교에서 한 항목이 없으면 그 칸은 비우고 reason badge 를 붙인다.
- LLM 분석은 근거가 부족하면 보수적으로 축약한다.
- 위키에는 confidence 를 낮게 남기고, 장기적으로 패턴이 반복될 때만 playbook 으로 승격한다.

## Implementation Plan

1. 기관 레지스트리와 스냅샷 스키마를 정의한다.
2. `reports/notable_investors_wiki.py` 를 일반화하거나 새 기관 watch 모듈을 추가해 다기관 스냅샷을 만든다.
3. `dashboard/pages/watchlist.py` 를 기관 허브 + 기존 watchlist 의 복합 화면으로 확장한다.
4. LLM 공통 패턴 분석기를 추가하고, 결과를 위키 source_digest 로 저장한다.
5. `reports/wiki_distillation.py` 가 기관 패턴 digest 도 증류하도록 연결한다.
6. 테스트를 추가해 가용성 플래그, 비교 테이블, 위키 적재, 패턴 요약이 모두 안정적으로 동작하는지 확인한다.

## Success Criteria

- 관심종목 화면에서 버크셔 외 여러 기관을 볼 수 있다.
- 기관별로 holdings, concentration, cash, options, return 관련 가용성을 비교할 수 있다.
- LLM 이 공통 움직임과 이탈 패턴을 요약해 준다.
- 그 요약이 위키 문서로 축적된다.
- 비교 UI 가 없는 지표를 억지로 채워 넣지 않는다.
- 기존 종목 관심목록 기능은 깨지지 않는다.

## Non-Goals

- 모든 기관에 대해 실시간 완전한 포트폴리오를 보장하지 않는다.
- 비공개 브로커/프라임브로커 데이터는 새로 계약하지 않는다.
- 존재하지 않는 현금 비중이나 옵션 비중을 추정치처럼 보이게 만들지 않는다.
- 사람 수동 리뷰를 필수 단계로 두지 않는다.

## Risks

- 13F, 연차보고서, 공시의 시점이 달라서 같은 날짜처럼 보이지만 사실 다른 기간일 수 있다.
- 수익률과 현금 비중은 기관별 공개 수준이 달라 비교가 거칠어질 수 있다.
- LLM 분석이 그럴듯해 보여도 근거가 부족하면 과신 위험이 있다.
- 기관이 늘어날수록 데이터 품질 편차가 커지므로, availability badge 를 항상 보여줘야 한다.
