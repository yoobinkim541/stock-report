# 대시보드·차트·관심종목·LLM 위키 개선 명세 (SDD)

작성: 2026-10-23 · 기준 = 운영 서버 브랜치 `codex/hermes-wiki-recovery`(`dbf6344`) · 작업 브랜치 `feat/sdd-dashboard-chart-watchlist-wiki-live`

## 범위

운영 중인 두 화면을 기준으로 한다.

- Vercel 랜딩: `https://stock-report-bice.vercel.app/` (`src/app`, `/bridge` → Streamlit 게이트웨이)
- Streamlit 퀀트 터미널: `src/lib/gateway.ts`의 cloudflare 터널 주소 (`dashboard/app.py`, `dashboard/pages/*`)

FinanceAgentGUI는 참고 소스이며, 이 저장소의 구조·크론 경로·단일 진실원 모듈을 유지한 채 필요한 동작만 옮긴다. 파일 이동, 실계좌 주문 경로, 운영 데이터 변경, 검증 중 유료 LLM 호출은 범위 밖이다.

## 실측 근거 (2026-10-23, 운영 화면 읽기 전용)

| 영역 | 관찰 |
| --- | --- |
| 수집 → 위키 | AI 콘솔 파이프라인 헬스가 `saveticker: SaveTicker HTTP 403`, `economic_calendar: SaveTicker calendar HTTP 403`을 보고하는데 같은 화면의 "소스 공백"은 `0`이다. |
| 수집 원인 | `https://saveticker.com/api/*`는 Cloudflare 403(HTML)이다. 공개 호스트 `https://api.saveticker.com/api`는 `news/top-stories` 27건, `calendar/events` 55건을 200 JSON으로 반환한다. |
| 수집 함정 | 공개 호스트의 `news/list`는 페이지·크기 파라미터와 무관하게 `legacy-news-sunset-notice` 1건만 반환한다. 그대로 쓰면 공지가 뉴스로 수집된다. |
| 수집 은닉 | `fetch_saveticker_events()`는 경로별 실패를 `continue`로 삼켜 403과 정상 0건을 구분하지 않는다. |
| 위키 통계 | AI 위키 카드가 `페이지 4268 · 초안 338 · 검토 0`만 보여 안정(stable) 문서 수가 빠져 있다. 미검증 171·open Q 3326은 표시되지만 우선순위가 없다. |
| 위키 접근 | `AI 위키` 페이지는 `st.navigation`에 없고 AI 콘솔 탭/`switch_page`로만 열린다. |
| 차트 | `/chart`에서 왼쪽에 판독 불가한 빈 막대 열이 렌더된다. 콘솔에 Plotly `Too many auto-margin redraws` 경고가 난다. 신호 마커가 최근 구간에 겹쳐 가격을 가린다. |
| 관심종목 | 표시 전용이며 추가/삭제는 텔레그램 `/watch`만 가능하다. "가격은 최근 종가 기준(최대 5분 지연, 국내 티커는 미지원)". |
| 홈 | 미국 모의투자 카드가 `$100,000 · -60.24%`로 평가액과 수익률이 서로 모순된다. 사이드바 보유 종목 숫자에 단위가 없다. |

## 수용 기준

| ID | 동작 | 검증 |
| --- | --- | --- |
| SRC-01 | SaveTicker 뉴스·캘린더는 공개 API 호스트를 기본으로 사용하고 `SAVE_TICKER_API_BASE` 오버라이드를 유지한다. | 회귀 테스트 + 라이브 1회 수집 건수 |
| SRC-02 | sunset 공지 등 비기사 항목은 이벤트로 저장하지 않는다. | 공지 응답 픽스처 테스트 |
| SRC-03 | HTTP 오류·비JSON 응답은 정상 0건과 구분되어 소스 헬스에 실패로 남는다. | 403/HTML 픽스처 테스트 |
| WIKI-01 | 파이프라인 헬스의 공백 수가 실패 소스를 포함하고, 위키 카드는 상태별(초안·검토·안정·보관) 수를 모두 보인다. | 헬스 모델 테스트 + 화면 |
| WIKI-02 | `AI 위키`가 메인 내비게이션에서 직접 열린다. | 화면 |
| CHART-01 | 차트 풀뷰의 확인된 렌더 결함(빈 막대 열, auto-margin 재그리기 루프)을 원인에서 제거한다. | 재현 전/후 + 테스트 |
| CHART-02 | 신호 마커가 가격 캔들을 가리지 않도록 밀도를 제한한다(정보 손실 없이 요약). | 렌더 모델 테스트 + 화면 |
| CHART-03 | yfinance 최신 일봉의 OHLC 공백(NaN)이 캔들 SVG 를 깨뜨리지 않는다(표시 사본만 보정). | 픽스처 테스트 + 콘솔 |
| WATCH-01 | 국내 티커 현재가를 기존 관심종목 시세 경로(`market_data.batch_quote_change`, yfinance 배치)로 표시하고, 실패는 종목별로 구분 표시한다. KIS 실시간은 구독 종목에만 값이 있어 관심종목 소스로 쓰지 않는다(`dashboard/data.py::load_watchlist` 주석). | 제공자 실패 포함 테스트 |
| WATCH-02 | 대시보드에서 관심종목 추가/삭제가 가능하며 텔레그램 `/watch`와 같은 저장소·검증 규칙을 쓴다. | 저장소 왕복 테스트 |
| WATCH-03 | 가격 기준 시각을 표시하고 지연·실패를 정상값으로 위장하지 않는다. | 화면 + 테스트 |
| HOME-01 | 모의투자 카드의 평가액·수익률이 같은 기준에서 계산되어 모순되지 않는다. 사이드바 보유 숫자에 의미·단위를 붙인다. | 계산 테스트 + 화면 |
| SAFE-01 | 실계좌 주문 코드 부재 테스트, 기존 테스트 스위트, 크론 drift 검사가 유지된다. | pytest |

## 구현 경계

- 수집: `reports/source_collector.py`, `reports/market_report.py`, `reports/investment_report.py`, `providers/econ_calendar.py`의 기본 호스트만 공개 API로 바꾼다. 엔드포인트 계약은 FinanceAgentGUI `docs/news-feed.md`의 실측과 같다. robots 금지 경로 우회(브라우저 위장, 쿠키 재사용)는 하지 않는다.
- 위키·홈: `dashboard/pages/ai_console.py`, `dashboard/pages/ai_wiki.py`, `dashboard/app.py`, `dashboard/pages/home.py`와 해당 헬스 모델.
- 차트: `dashboard/pages/chart_full.py`, `dashboard/chart_*.py` 중 원인 파일만.
- 관심종목: `dashboard/pages/watchlist.py`와 기존 관심종목 저장소(`lib/`/`bot/watchlist`)를 재사용한다. 새 저장소를 만들지 않는다.

## 검증 절차

1. Linux 가상환경에서 관련 pytest와 전체 스위트를 실행한다(`fcntl` 의존으로 Windows 불가).
2. 공개 SaveTicker API로 수집 함수를 1회 실행해 건수를 확인한다(운영 데이터 디렉터리는 임시 경로로 격리).
3. 로컬 Streamlit을 격리된 데이터로 띄워 홈·관심종목·차트 풀뷰·AI 위키를 데스크톱/모바일에서 확인한다.
4. PR로 제출한다. 운영 배포(서버 반영·터널 재기동)는 사용자 확인 후 진행한다.

## 구현 결과 (2026-10-24)

| ID | 원인 → 조치 |
| --- | --- |
| SRC-01..03 | `saveticker.com/api` 는 Cloudflare 403. `providers/saveticker_api.py` 가 기본 호스트 `https://api.saveticker.com/api` 단일 진실원. `news/list` 는 종료 공지만 반환 → 공지 필터·빈 페이지에서 순회 중단, 보유종목 뉴스는 top-stories `$티커` 태그로 필터. 모든 경로 실패 시 `SourceProviderUnavailable(availability="error")`. 라이브 1회: 기사 17건(공지 0)·캘린더 58건. |
| WIKI-01 | `stale_sources()` 가 차단 소스를 의도적으로 제외 → `gap_sources`(stale+blocked+error) 를 따로 집계해 "소스 공백·차단"으로 표시. 위키 카드는 초안·검토·안정·보관 4상태(없으면 0). |
| WIKI-02 | `AI 위키` 를 `st.navigation` 에 등록(기존 `_wiki_page` 세션 키 재사용). |
| CHART-01 | 빈 막대 열 = 좌측 그리기 도구 독. 세로 flex + max-height 에서 버튼 기본 `flex-shrink:1` 로 25개+ 버튼이 몇 px 로 눌려 라벨이 잘림 → `flex:0 0 auto`. 로컬 재현에서 auto-margin 경고는 수정 후 재발하지 않음(수정 전 운영 화면에서만 관측). |
| CHART-02 | 체결 마커 13px+흰 테두리 → 9px·투명도 0.85, 두 렌더 경로 공용 `trade_marker_style`. y=체결가·이벤트별 클릭 편집 계약 유지. |
| CHART-03 | `ohlc_utils.repair_display_ohlc`: Close 있으면 Open=직전 Close·High/Low=Open/Close 최대·최소, Close 없으면 봉 제외. `cached.ohlc`·`chart_data_bundle` 표시 경로만 적용. |
| WATCH-01..03 | `lib.watchlist.normalize_ticker`(봇·대시보드 공통 검증, 국내 6자리 → `.KS`), 시세는 `.KS` 실패 시 `.KQ` 재조회, 표에 `₩/$`·일봉 날짜·출처·종목별 실패 사유. 페이지에서 추가/삭제. |
| HOME-01 | 레일 NAV 는 현 세대, 수익률은 전체 세대 누적이라 모순 → 같은 세대 수익률 표시, 전체 누적은 툴팁. 보유 레일 제목에 `평가액($) · 수익률`. |
