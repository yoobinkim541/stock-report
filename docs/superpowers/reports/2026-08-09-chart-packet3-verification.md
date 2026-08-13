# Chart Packet 3 Verification

검증 시각: 2026-08-09 UTC

브랜치: `codex/chart-analysis-workbench`

대상: 4x4 멀티차트, 링크 그룹, 심볼·봉·기간·십자선 동기화, 최대화, 고밀도 렌더링

## Reference Contract

TradingView 공식 문서는 멀티차트에서 symbol, crosshair, interval, time/date range 동기화와 선택 그룹을 제공하고, 드로잉은 같은 symbol에서만 동기화한다고 설명한다.

- [How to sync the charts of my layout?](https://www.tradingview.com/support/solutions/43000629992-how-to-sync-the-charts-of-my-layout/)
- [How to work in the multi-chart mode](https://www.tradingview.com/support/folders/43000578567/)
- [TradingView 4x4 chart grid](https://www.tradingview.com/support/solutions/43000728343-how-can-i-change-the-chart-view-grid/)

Packet 3은 이 계약을 현재 `ChartDocument`/Plotly/Streamlit 구조에 맞게 구현했다.

## Delivered Capabilities

| Capability | Result |
| --- | --- |
| Layouts | 기존 1/2/2x2/3+1/3x2에 3x3, 4x3, 4x4 추가 |
| Lossless resize | 축소 시 숨은 패널을 `parked_panels`에 보존하고 재확장 시 ID/문서/그룹 복원 |
| Typed mutation | 모든 패널 변경을 `mutate_panel()`로 검증하고 변경 trace 반환 |
| Link groups | 전체 또는 red/orange/yellow/green/blue/purple/gray 그룹별 전파 |
| Server sync | symbol, interval, period를 활성 정책과 그룹에 따라 실제 패널 문서에 반영 |
| Browser sync | crosshair와 visible timestamp range를 localStorage 채널로 즉시 전파 |
| Axis mapping | datetime 범위를 휴장 압축 category 축의 가장 가까운 봉 index로 매핑 |
| Loop guard | 원격 Plotly relayout 메아리를 재발행하지 않도록 nonce, stale, guard 적용 |
| Drawing scope | symbol/timeframe/scale이 같은 저장 키에서만 드로잉 공유 |
| Dense rendering | 7개 이상은 동일 높이 compact 차트; compare/하단지표/알림/도구바 로딩 생략 |
| Maximize | 선택 패널 하나만 전체 도구·지표·드로잉·리플레이와 함께 렌더 |
| Mobile | Streamlit 반응형 단일 열 전환, 페이지 가로 넘침 없음 |

## Automated Tests

```bash
../../.venv/bin/pytest -q \
  tests/test_chart_document.py tests/test_chart_transforms.py \
  tests/test_chart_data_policy.py tests/test_chart_series.py \
  tests/test_chart_studies.py tests/test_chart_conditions.py \
  tests/test_chart_workbench.py tests/test_dashboard_charts.py \
  tests/test_dashboard_pages.py tests/test_chart_workspace.py \
  tests/test_chart_workspace_pages.py tests/test_chart_alerts.py \
  tests/test_chart_alert_runner.py tests/test_chart_alert_worker.py \
  tests/test_plotly_embed.py tests/test_plotly_embed_runtime.py \
  tests/test_chart_replay.py tests/test_chart_replay_storage.py \
  tests/test_chart_replay_controller.py tests/test_chart_replay_ui.py \
  tests/test_chart_replay_rules.py tests/test_strategy_studio.py \
  tests/test_strategy_studio_pages.py tests/test_agent_console_storage.py
```

결과: **432 passed in 26.01s**, Plotly nanoseconds 변환 경고 1건. 실패와 skip 없음.

추가 TDD 증거:

- 4x4 16개 panel ID 생성과 축소·재확장 보존
- group-local/global/disabled 동기화와 document 일치
- invalid mutation 및 link group 거부
- 16개 Streamlit panel/link widget key 충돌 없음
- local range publish, category-axis remote apply, forced relayout echo 무발행
- loopback Agent Console URL이 브라우저에 노출되지 않음

## Browser Verification

로컬 서버: `http://127.0.0.1:8528`

브라우저: Playwright Chromium, headless

뷰포트: desktop `1920x1200`, mobile `390x844`

| State | Measurement | Evidence |
| --- | --- | --- |
| 4x4 desktop | panel 16, link group 16, chart iframe 16 + 공통 iframe 2 | `assets/chart-packet3-grid-desktop.png` |
| Maximized | chart iframe 1 + 공통 iframe 2 | `assets/chart-packet3-maximized-desktop.png` |
| Mobile | panel 16, single-column responsive flow | `assets/chart-packet3-grid-mobile.png` |
| Overflow | desktop/mobile 모두 `scrollWidth <= innerWidth` | Playwright assertion |
| Failed requests | 0 | Playwright `requestfailed` listener |

직접 `/chart`를 연 경우 Streamlit이 `/chart/_stcore/health`와 `/chart/_stcore/host-config`를 요청해 404 두 건을 남긴다. 화면·차트·동기화와 무관한 기존 deep-link 진단 요청이며 애플리케이션 page error는 없다.

## Security And Deployment Boundary

이전 구현은 `AGENT_CONSOLE_URL`이 없을 때 브라우저 iframe에 `http://127.0.0.1:8797`을 삽입했다. 원격 배포에서는 서버가 아니라 사용자 PC를 향하는 잘못된 요청이다.

수정 후에는 `AGENT_CONSOLE_PUBLIC_URL` 또는 loopback이 아닌 명시적 `AGENT_CONSOLE_URL`이 있을 때만 서버 드로잉 동기화를 켠다. 그 외에는 localStorage 영속화만 사용한다. 브라우저 재검증에서 Agent Console 연결 거부가 0건임을 확인했다.

## Known Trade-Offs

- 4x4 overview는 읽기·비교를 위한 compact 모드다. 드로잉 작성과 전체 지표 조작은 패널을 최대화해야 한다.
- 16개 Plotly 문서 직렬화는 Canvas/WebGL 네이티브 그리드보다 느리다. compare와 하단 연구 로딩을 제거했지만 첫 렌더 비용은 남는다.
- range sync는 timestamp 의미가 있는 차트에만 연결한다. Renko/Kagi/Line Break/Range 같은 sequence 차트에는 의도적으로 적용하지 않는다.
- 워크스페이스 그룹은 현재 하나의 group 값으로 symbol/interval/range를 함께 묶는다. TradingView Desktop처럼 항목별 서로 다른 그룹은 후속 확장점이다.
- `st.components.v1.html` upstream 폐기 경고는 남아 있으며 custom Plotly runtime을 보존하는 iframe 컴포넌트 전환이 필요하다.
