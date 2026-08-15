# Institution Hub Cross-Screening & LLM 분석 — Design

## Goal

관심종목 페이지의 "기관투자자 허브"(2026-07-31 설계로 만든 것) 상단에 다음을 추가한다:

1. 기관별 보유 종목 비중 도넛 차트
2. 여러 13F 기관이 동시에 신규편입/비중증가/비중감소한 종목 스크리닝
3. 하원의원(STOCK Act 공시)이 최근 90일 많이 매수·매도한 종목 스크리닝
4. 위 스크리닝 결과를 LLM이 "왜 이런 흐름일 수 있는지" 해설(버튼 클릭 시에만)

기존 기관허브(카드+비교표+LLM 공통패턴 요약)는 그대로 두고 그 위에 얹는다.

## Constraints Found During Brainstorming

- **풋옵션 데이터 없음**: 하원 공시 원본(23,891건)을 확인한 결과 `asset_type`이 전부
  "Stock"이고 옵션은 자유텍스트에 극히 드물게(1건) 섞여 나옴 — 구조화 필드 없음.
  **풋옵션 리스트업은 이번 스코프에서 제외**(사용자 확인, 2026-08-15).
- **기관허브가 이미 지연로딩 중**: "느리다" 불만 때문에 토글 뒤에 숨겨져 있음
  (dashboard/pages/watchlist.py `_watch_show_hub`). 이번 기능도 그 토글 안에
  들어가야 하며, 별도 지연로딩을 또 추가하지 않는다(사용자 확인: 접근법 A).
- **`_normalize_top_holdings`는 상위 10개로 절단**: 교차기관 스크리닝은 전체
  보유내역이 필요 — 정규화된 스냅샷이 아니라 `thirteenf.latest_holdings()` 원본을
  직접 써야 한다.
- **`allocation_donut()`이 이미 존재**([dashboard/charts.py:353](../../../dashboard/charts.py)) —
  포트폴리오 홈 화면이 쓰는 것과 같은 함수를 재사용, 새 차트 함수 불필요.

## Scope Decisions (사용자 확인, 2026-08-15)

| 항목 | 결정 |
|---|---|
| 풋옵션 데이터 | 제외 |
| 로딩 위치 | 기존 기관허브 토글 안 (별도 토글/버튼 없음) |
| 대상 기관 | 13F 실데이터 10개 전부 (berkshire, bridgewater, scion, citadel, duquesne, pershing_square, point72, third_point, tudor, nps) |
| 정치인 거래 집계 기간 | 최근 90일 |
| 아키텍처 | 토글 열릴 때 즉시 계산 (캐시로 반복 열람은 빠름, 캐시 만료 직후 첫 로드는 10~30초 가능) |

## Design

### 1. 데이터 계층

**`reports/institution_watch.py`**

```python
def _raw_holdings_with_prior(institution_key: str) -> tuple[list[dict], list[dict]] | None:
    """thirteenf.latest_holdings 현재+직전분기 원본(절단 없음). 실패 시 None."""

_SCREEN_DELTA_THRESHOLD = 0.005   # 0.5%p — return_proxy 의 커버리지 임계치와 같은 계열 상수

def screen_position_changes(institution_keys: list[str]) -> dict:
    """{new_buys, increased, decreased} — 각 항목: {ticker, name, institutions:[...],
    count, avg_delta_pct}. count 내림차순 → |avg_delta_pct| 내림차순 정렬, 상위 10개."""
```

각 기관에 대해 현재/직전 보유를 CUSIP 매칭해 종목별 `delta_pct = weight_now - weight_prior`
계산(신규편입은 `weight_prior=0` 취급, 전량청산은 `weight_now=0`). 종목별로 기관들을
모아 `count`(몇 개 기관이 같은 방향으로 움직였나)와 평균 변화폭으로 랭킹.

**`providers/congress_trading.py`**

```python
def top_traded(days: int = 90, limit: int = 10) -> dict:
    """{bought, sold} — 각 항목: {ticker, member_count, members:[...], total_amount_mid}.
    transaction_date 가 오늘로부터 days 이내인 것만, member_count 내림차순."""
```

### 2. 도넛 차트

새 함수 불필요 — `charts.allocation_donut(holdings)`를 재사용한다. 기관 카드 렌더링 시
`snapshot["top_holdings"]`(상위10, 이미 있음) + `total_value_usd - sum(top10 value)`을
"기타"라는 합성 항목으로 추가해 `[{ticker, value, name}]` 형태로 변환 후 전달한다.
`institution_watch_summary()`의 `institutions` 딕셔너리에 `total_value_usd` 필드를
추가로 실어야 한다(현재는 snapshot 에만 있고 UI 로 전달되는 institutions 리스트엔 없음).

### 3. LLM "왜?" 분석

`reports/institution_watch.py::explain_screen(screen: dict, congress: dict) -> dict`
— 기존 `build_common_moves_analysis`/`_common_moves_fallback` 과 같은 패턴(동일 LLM
헬퍼 `_try_llm_prompt`, 동일 JSON 프롬프트 스타일, 동일 fallback 원칙: LLM 실패 시
추측 없이 사실 나열). `with_llm_summary` 와 별개의 새 버튼으로 게이팅(자동 호출 금지).

### 4. 화면 배치 (`dashboard/pages/watchlist.py::_institution_hub_section`)

기존 멀티셀렉트 아래, 카드/비교표 **위**에 삽입:

```
### 🔄 여러 기관 공통 움직임 (직전 분기 대비)
[신규편입 / 비중증가 / 비중감소 — 표 3개, 각 행: 티커·종목명·기관수·평균변화폭]

### 🏛️ 정치인 매수·매도 상위 (최근 90일, 하원)
[많이 산 종목 / 많이 판 종목 — 표 2개]

### 🧠 왜 이런 움직임일까? (LLM 분석)
[버튼 게이팅]
```

기존 카드 루프(`_render_institution_cards`)는 각 카드에 도넛을 추가하되 구조는 유지,
비교 테이블은 그대로 아래.

### 5. 캐싱

`dashboard/cached.py`:
- `institution_screener()` → `views.institution_screener()` → `reports.institution_watch.screen_position_changes(ALL_13F_KEYS)`. ttl=3600(1h) — 13F 는 분기 갱신이라 넉넉히.
- `congress_top_traded(days=90)` → `views.congress_top_traded(days)` → `providers.congress_trading.top_traded(days)`. ttl=3600.
- LLM 설명은 버튼 클릭시에만 `cached.institution_screen_explain(...)` 별도 캐시(institution_watch 의 with_llm_summary 캐시 분리 패턴과 동일).

### 6. 에러 처리

기존 관례 그대로: 개별 기관 fetch 실패는 조용히 건너뛰고 나머지로 계속(`screen_position_changes`
는 부분 실패에 강건), 전체 실패 시 빈 `{"new_buys": [], "increased": [], "decreased": []}`
반환 후 UI 는 "표시할 데이터 없음" 캡션. LLM 실패는 위 3번의 fallback.

### 7. 테스트

- `screen_position_changes`/`top_traded`: 순수 로직, fixture 로 무네트워크 단위테스트
  (분류 임계치, 신규/증가/감소 판정, 기관수 랭킹, 90일 윈도 필터).
- `views`/`cached` 래퍼: 위임 + graceful 실패 (기존 패턴).
- `dashboard/pages/watchlist.py`: AppTest 스모크 — 신규 섹션 렌더, LLM 버튼 게이팅
  (자동 호출 안 됨), 도넛이 카드마다 뜸.
- 구현 후 실제 네트워크로 라이브 스모크(SEC EDGAR + GitHub 소스) 확인 후 커밋.
- 최종적으로 master 머지 후 배포된 대시보드에서 직접 렌더 확인(watchdog 자동 재시작
  대기 또는 수동 확인)까지 완료해야 마무리.
