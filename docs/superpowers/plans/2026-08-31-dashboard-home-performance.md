# 대시보드 홈 로딩 최적화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 홈 첫 화면의 체감 응답 시간을 줄이고, 공통 사이드바·마퀴의 중복 호출을 제거하며, 상세 시장 위젯은 데이터가 필요할 때만 로드해 네트워크 지연이 전체 화면을 막지 않게 한다.

**Architecture:** 파일/로컬 snapshot 기반 데이터와 네트워크 기반 상세 데이터를 분리한다. `dashboard.cached`에 짧은 TTL의 공용 holdings 캐시와 snapshot-first market tape 경로를 둔다. 홈은 포트폴리오 핵심 KPI와 최근 브리핑을 먼저 그리고, 시장 지표·매크로·밸류 상세는 명시적인 on-demand 상태에서만 실행한다. 기존 `st.cache_data` TTL은 각 데이터의 freshness 요구에 맞게 유지한다.

**Tech Stack:** Python 3.11, Streamlit 1.58, pandas, Plotly, existing yfinance/KIS snapshot providers, pytest and Streamlit AppTest.

## Baseline Evidence

- 현재 `data.load_holdings()`는 실시간 overlay를 포함해 약 250ms가 걸리고 `dashboard/app.py`와 `dashboard/pages/home.py`에서 중복 호출된다.
- `market_indicators`, `macro_assets`, `market_tape`는 각각 yfinance 네트워크 배치 호출을 가진다.
- `sp500_heatmap`은 신선한 로컬 snapshot이면 빠르지만 snapshot이 없거나 stale이면 라이브 503종목 호출로 홈을 장시간 막을 수 있다.
- `sp500_valuation`은 provider 캐시 상태에 따라 무거운 집계를 수행할 수 있다.

## Global Constraints

- 홈에서 표시되는 기준 시각과 source/freshness를 숨기지 않는다.
- 네트워크 장애는 빈 화면이나 예외 대신 stale snapshot/부분 성공 안내로 흡수한다.
- 기존 홈의 클릭 이동, 국내 보유 표, 시장 맵 선택, 테마 토글을 깨지 않는다.
- cache TTL을 무작정 늘려 실시간 데이터 신선도를 희생하지 않는다.
- 모든 성능 변경은 AppTest 무예외와 순수 helper 단위 테스트를 함께 추가한다.

## Task 1: Add measured performance contracts

**Files:** `tests/test_dashboard_performance.py`, `tests/test_dashboard_pages.py`

- [ ] 홈 render를 네트워크 stub으로 실행하면서 `load_holdings`, `paper_glance`, market functions의 호출 횟수를 수집하는 fixture를 만든다.
- [ ] app entry와 home render에서 holdings가 중복 호출되는 현재 동작을 실패 기준으로 고정한다.
- [ ] 상세 시장 위젯을 로드하지 않은 첫 런이 heavy loader를 호출하지 않는 기준을 추가한다.
- [ ] 테스트는 절대적인 느린 시간값보다 호출 횟수·데이터 경계·렌더 성공 여부를 우선 검증한다.

## Task 2: Share cached holdings across app and pages

**Files:** `dashboard/cached.py`, `dashboard/app.py`, `dashboard/pages/home.py`, `dashboard/pages/portfolio.py`, `dashboard/pages/ticker.py`, `dashboard/pages/ai_console.py`, `tests/test_dashboard_performance.py`, `tests/test_dashboard_pages.py`

- [ ] `cached.holdings()`의 30초 TTL 계약과 테스트를 먼저 추가한다.
- [ ] app sidebar가 공용 holdings 결과를 사용하고 home이 같은 런에서 다시 provider를 부르지 않게 한다.
- [ ] 포트폴리오·종목·AI 콘솔의 보유 조회도 같은 helper를 사용하되, 테스트에서 `data.load_holdings` stub이 계속 주입되도록 한다.
- [ ] 실시간 overlay 실패 시 스냅샷 보유 데이터가 유지되는 기존 동작과 cache clear 동작을 검증한다.

## Task 3: Snapshot-first common market tape

**Files:** `dashboard/views.py`, `dashboard/cached.py`, `dashboard/app.py`, `tests/test_dashboard.py`, `tests/test_dashboard_performance.py`

- [ ] market tape snapshot의 경로, freshness, 최소 유효 행 수를 정의하고 신선 snapshot을 네트워크보다 먼저 읽는 테스트를 작성한다.
- [ ] live fetch 성공 시 atomic write로 snapshot을 갱신하고, 실패 시 마지막 정상 snapshot을 반환한다.
- [ ] timestamp/source를 tape payload에 보존하되 현재 HTML formatter와 호환되는 row shape를 유지한다.
- [ ] snapshot이 없을 때만 live fallback이 실행되는 호출 계약을 검증한다.

## Task 4: On-demand heavy home sections

**Files:** `dashboard/pages/home.py`, `tests/test_dashboard_pages.py`, `tests/test_dashboard_performance.py`

- [ ] 첫 홈 런에서 `market_indicators`, `macro_assets`, `sp500_valuation`이 실행되지 않고 핵심 KPI/브리핑/보유표/신선 snapshot 시장 맵이 렌더되는 실패 테스트를 추가한다.
- [ ] 세션 상태 기반의 명확한 상세 로드 command를 추가한다. 상세 데이터가 이미 세션에 있으면 재사용하고, 새로고침 시에만 캐시를 비운다.
- [ ] 상세 로드 후 시장 지표·매크로·밸류가 기존 UI와 동일하게 표시되고, 로드 전에는 오해를 부르는 `N/A` 값 대신 데이터 미로드 상태를 표시한다.
- [ ] Streamlit fragment와 페이지 rerun에서 로드 상태가 유지되는지 AppTest로 검증한다.

## Task 5: Avoid slow heatmap fallback on the first paint

**Files:** `dashboard/views.py`, `dashboard/pages/home.py`, `dashboard/cached.py`, `tests/test_dashboard.py`, `tests/test_dashboard_performance.py`

- [ ] stale/partial heatmap snapshot이 있을 때 라이브 503종목 fetch를 즉시 실행하지 않고 stale snapshot 또는 명시적 unavailable 상태를 반환하는 테스트를 작성한다.
- [ ] 운영용 snapshot refresh 경로와 사용자 요청 경로를 분리한다. 사용자 요청은 신선 snapshot을 우선하고, live fallback은 명시적인 새로고침/상세 요청에서만 허용한다.
- [ ] S&P500 최소 행 수 검증과 partial snapshot 방어를 유지한다.
- [ ] 기존 시장 맵 타일 클릭과 3개 시장 선택 회귀 테스트를 통과시킨다.

## Task 6: Verify end-to-end load behavior

**Files:** `tests/test_dashboard_performance.py`, `tests/test_dashboard_pages.py`, `docs/superpowers/specs/2026-08-31-recommendation-feedback-ux-design.md` (필요 시)

- [ ] cold/warm AppTest를 측정해 호출 수와 주요 단계 시간을 기록한다.
- [ ] 사이드바 새로고침, 홈 상세 로드, 다른 페이지 이동, 브라우저 세션 재진입에서 cache semantics를 검증한다.
- [ ] 관련 dashboard 테스트와 전체 테스트를 실행한다.
- [ ] 실제 배포 환경에서는 네트워크 성공/실패, snapshot age, 첫 렌더와 상세 로드 시간을 로그로 확인할 수 있게 한다.

## Definition of Done

- [ ] 홈 첫 화면이 네트워크 기반 상세 집계 때문에 멈추지 않는다.
- [ ] holdings와 market tape의 중복 호출이 제거되거나 공용 cache를 사용한다.
- [ ] 상세 데이터를 요청하면 기존 시장 지표·매크로·밸류 UI가 유지된다.
- [ ] stale/부분 snapshot이 신선 데이터처럼 표시되지 않고 기준 시각이 노출된다.
- [ ] AppTest와 관련 단위 테스트가 통과한다.

## Trade-offs

첫 화면을 빠르게 만들기 위해 상세 시장 위젯을 on-demand로 바꾸면 초기 정보량은 줄어든다. 대신 사용자가 첫 화면에서 핵심 포트폴리오 상태를 먼저 보고 필요할 때 최신 상세를 요청할 수 있다. snapshot-first는 네트워크 장애에 강하지만 잠시 stale할 수 있으므로 freshness와 source를 표시하고 사용자 새로고침 경로를 남긴다.
