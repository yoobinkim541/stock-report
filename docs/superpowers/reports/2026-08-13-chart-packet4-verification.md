# Chart Packet 4 Verification

검증 시각: 2026-08-13 UTC

브랜치: `codex/chart-analysis-workbench`

대상: KIS 체결·호가 원천 보존, bounded reader, 오더플로 분석 레일

## Source Contract

- 국내 `H0STCNT0`: 거래소 시각, 현재가, 개별 체결량, 누적 거래량을 [KIS 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py)의 컬럼 순서로 파싱한다.
- 미국 `HDFSCNT0`: 거래일, 거래소 시각, 현재가, 개별 체결량, 누적 거래량을 [KIS 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions_ws.py)의 컬럼 순서로 파싱한다.
- 국내 호가는 최대 10단계, 미국 무료 실시간 호가는 1단계만 보존한다.
- 거래소 시각과 앱 수신시각은 별도 필드다. 개별 체결량이 없을 때만 누적 거래량 차이를 사용하며, 누적값 리셋은 음수 거래량으로 만들지 않는다.

## Delivered Capabilities

- `ORDERFLOW_CAPTURE_ENABLED`가 켜진 경우에만 거래소 현지 세션 날짜·심볼별 JSONL 파티션에 버전형 이벤트를 append한다.
- 세션별 256 MiB 기본 상한, 14일 보존, 100,000건 메모리 큐 상한, 최근 10,000건·최대 8 MiB tail reader로 비용을 제한한다.
- 체결과 호가 저장 실패는 기존 실시간 quote cache와 1분봉 sink에 전파되지 않는다.
- 구독 allowlist 밖의 파싱 결과는 폐기하며, 쓰기 예외는 큐에서 재시도하고 실제 드롭 수는 원자적 상태 파일에 공개한다.
- 종목 차트와 멀티차트 분석 레일에서 스프레드, 호가 불균형, 호가 깊이, 가격대별 체결량을 표시한다.
- 저장 윈도우가 잘렸으면 UI에 최근 건수, 읽은 바이트, `일부만 표시`를 명시한다.
- 리플레이 커서가 활성화되면 라이브 오더플로 loader를 차단해 미래 데이터 누출을 막는다.
- 원천 aggressor side가 없는 경우 풋프린트와 매수/매도 체결 델타를 추정하지 않고 비활성화한다.

## Automated Tests

```bash
../../.venv/bin/pytest -q \
  tests/test_chart_*.py tests/test_dashboard_charts.py \
  tests/test_dashboard_pages.py tests/test_plotly_embed.py \
  tests/test_plotly_embed_runtime.py tests/test_kis_stream.py \
  tests/test_orderflow_store.py
```

결과: **476 passed in 29.02s**, Plotly Timestamp 변환 경고 1건. 실패와 skip 없음.

네트워크가 차단된 샌드박스에서 홈 페이지의 yfinance 재시도가 30초 AppTest 제한을 넘긴 이력이 있어, 같은 페이지 묶음을 네트워크 허용 환경에서 별도 재검증했다.

```bash
../../.venv/bin/pytest -q tests/test_dashboard_pages.py::test_page_renders_without_exception
```

결과: **8 passed in 14.67s**.

## Browser Verification

로컬 서버: `http://127.0.0.1:8531`

브라우저: Playwright Chromium 1228, headless

뷰포트: desktop `1440x1000`, mobile `390x844`

| Check | Result |
| --- | --- |
| Console errors / page errors / failed requests | 0 / 0 / 0 |
| Order-flow section, storage window, capture-loss caption | visible |
| Plotly charts | 2 charts, 2 live Plotly DOM roots |
| iframe | 0 |
| Desktop horizontal overflow | false |
| Mobile horizontal overflow | false |

증거:

- `assets/chart-packet4-orderflow-desktop.png`
- `assets/chart-packet4-orderflow-mobile.png`

## Trade-Offs

- JSONL은 장애 복구와 원문 검사가 쉽지만 장기 분석 효율은 Parquet보다 낮다. 이번 Packet은 세션별 bounded 조회와 14일 보존에 집중한다.
- 1초 호가 샘플링은 저장량을 제한하지만 모든 큐 변화를 재현하지 못한다.
- KIS 무료 미국 호가는 1단계라 국내 10호가와 같은 깊이 분석을 제공할 수 없다.
- 진짜 footprint와 bid/ask delta는 authoritative aggressor side가 있는 공급자를 연결할 때만 활성화할 수 있다.

## Final Review Remediation

- 리플레이 분석 레일에서 현재 날짜의 라이브 오더플로를 읽던 시간 누출을 차단했다.
- 거래소 현지 세션 날짜 파티션과 14일 보존으로 UTC 자정 분절과 장기 디스크 누적을 제한했다.
- 구독 allowlist를 적용해 다중 레코드 파싱 글리치가 저장소를 오염시키지 못하게 했다.
- 쓰기 예외는 bounded 큐에서 재시도하고, 큐·용량 상한의 실제 유실은 세션별 상태로 공개한다.
- 문자열 직렬화를 반복 연결에서 `list + join`으로 바꿔 대량 batch 비용을 선형화했다.
- 빈 상태 문구를 미설정, 리플레이 격리, 공급자 오류, 현재 이벤트 없음으로 분리했다.
