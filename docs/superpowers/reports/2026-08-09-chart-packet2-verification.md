# Chart Packet 2 Verification

검증 시각: 2026-08-09 UTC

브랜치: `codex/chart-analysis-workbench`

대상: 리플레이 커서, 모의 브로커, 영속 세션, 차트 주문선, 하단 터미널, 전략 규칙 핸드오프

## Delivered Commits

| Commit | Scope |
| --- | --- |
| `c0d10a9` | 미래 봉을 차단하는 리플레이 커서와 순수 모의 브로커 |
| `7bbdc2b` | SQLite 세션·이벤트·멱등 요청과 CRUD API |
| `6ba6b24` | 워크스페이스 패널 공유 리플레이 시계 |
| `036b855` | 주문·손절·목표·체결 차트 오버레이와 가격 수정 |
| `5808173` | 리플레이/주문/포지션/전략/이벤트/진단 터미널 |
| `01a92f6` | Strategy Studio 및 조건 트리의 커서 안전 핸드오프 |

## Automated Tests

```bash
../../.venv/bin/pytest -q \
  tests/test_chart_replay.py tests/test_chart_replay_storage.py \
  tests/test_chart_replay_controller.py tests/test_chart_replay_ui.py \
  tests/test_chart_replay_rules.py tests/test_chart_document.py \
  tests/test_chart_workspace.py tests/test_chart_workspace_pages.py \
  tests/test_chart_workbench.py tests/test_dashboard_charts.py \
  tests/test_plotly_embed.py tests/test_strategy_studio.py \
  tests/test_strategy_studio_pages.py tests/test_agent_console_storage.py \
  tests/test_chart_alert_runner.py
```

결과: **224 passed in 11.16s**. 실패와 skip 없음.

추가 회귀 묶음:

- Agent Console API 및 저장소: **140 passed in 104.96s**
- 리플레이 UI·렌더러·저장소: **184 passed**, Plotly Timestamp 변환 경고 1건
- Streamlit 핵심 차트 AppTest 5개: **5 passed**

## Broker Safety Evidence

| Contract | Evidence |
| --- | --- |
| Look-ahead 차단 | 모든 전략 입력은 `cursor` 이하로 잘리고 시장가는 다음 적격 봉 시가에서만 체결됨 |
| 되감기 | 기존 세션을 덮지 않고 명시적 branch를 생성하며 parent와 revision을 기록함 |
| 동일 봉 손절/목표 충돌 | 이익을 가정하지 않고 보수적인 stop-first 정책 사용 |
| Bracket OCO | 부모 체결 후 자식 활성화, 한쪽 체결 시 형제 주문 원자 취소 |
| 부분 청산 | 잔여 수량보다 큰 청산과 음수 포지션을 차단하고 형제 주문을 잔량에 맞춤 |
| 비용·레버리지 | 수수료, 슬리피지, 최대 레버리지, 유지증거금을 세션 설정으로 명시 |
| 강제 청산 | 유지증거금 위반 시 이벤트와 함께 모의 포지션을 강제 청산 |
| 동시 수정 | SQLite `BEGIN IMMEDIATE`, optimistic revision, request fingerprint 멱등성 적용 |
| 규칙 재현성 | packet/version/cursor별 멱등 실행 및 판단 당시 condition trace 저장 |
| 실시간 분리 | 리플레이 규칙과 기존 실시간 알림을 별도 namespace로 유지 |

## Browser Verification

로컬 서버: `http://127.0.0.1:8527`, 격리 DB `/tmp/chart-replay-browser.sqlite3`

브라우저: Playwright Chromium, headless

뷰포트: desktop `1440x1100`, mobile `390x844`

| State | Result | Evidence |
| --- | --- | --- |
| Replay desktop | 6개 터미널 탭, NAV/현금/익스포저/MDD, 설정과 분석 레일 표시 | `assets/chart-packet2-replay-desktop.png` |
| Orders desktop | market/limit/stop, buy/sell, bracket, 수량·가격 입력과 주문 제출 표시 | `assets/chart-packet2-orders-desktop.png` |
| Replay mobile | 카드 단일 열, 스크롤 가능한 탭, 가로 페이지 넘침 없음 | `assets/chart-packet2-replay-mobile.png` |

검증값:

- `Replay`, `Orders`, `Positions`, `Strategy`, `Events`, `Diagnostics` 탭 모두 탐지
- desktop iframe 3개, `NAV` 및 `Diagnostics` 콘텐츠 탐지
- desktop/mobile 모두 `documentElement.scrollWidth <= innerWidth`
- 애플리케이션 page error 없음

직접 `/ticker`를 연 경우 Streamlit이 `/ticker/_stcore/health`와 `/ticker/_stcore/host-config`를 요청해 404를 남기지만 화면과 상호작용은 정상이다. Packet 1의 `/chart` 딥링크에서도 확인된 프레임워크 진단 요청이며 차트 데이터나 리플레이 API 실패가 아니다.

## UI And Workflow Coverage

- 종목 분석, 차트 풀뷰, 워크스페이스가 같은 세션·커서·오버레이 계약을 사용한다.
- 재생/일시정지/한 봉 이동/속도/라이브 점프와 마지막 커서를 저장한다.
- 주문 생성·취소, 부분 청산, 비용·레버리지 설정을 터미널에서 처리한다.
- 차트에서 서버가 만든 `replay-order:<id>` 주문선만 수정 가능하며 적용 전 가격 변경을 확인한다.
- 비교 모드에서는 절대 가격 주문선을 숨겨 정규화 수익률 축과 혼동하지 않는다.
- Strategy Studio 결과와 공유 조건 트리를 버전형 packet으로 연결하고 매 중간 봉을 평가한다.

## Known Trade-Offs

- 브로커는 현재 단일 종목, 롱 전용이다. 포트폴리오 교차증거금과 숏 차입 모델은 후속 패킷 범위다.
- 재생 UI는 Streamlit 1초 fragment 주기이며 거래소 tick 재생이 아니다.
- OHLCV 안에서 발생 순서를 알 수 없는 충돌은 항상 보수적으로 처리하므로 실제 체결보다 불리할 수 있다.
- Plotly/Streamlit 조합은 현재 드로잉 호환성을 유지하지만 대규모 봉과 다중 패널 성능의 상한이 있다.
- `st.components.v1.html`의 upstream 폐기 경고가 남아 있어 Canvas/WebGL 렌더러 전환 시 함께 제거해야 한다.
