# Intraday Market Data Enrichment

AI 콘솔은 장중 시장 질문을 받을 때 `agent_console.market_snapshot_store`의 한국 시장 미시구조 스냅샷을 읽습니다. 챗봇은 원문 API 응답을 직접 프롬프트에 넣지 않고, 아래 정규화된 필드만 사용합니다.

## Snapshot Contract

- `indices`: `kospi`, `kosdaq` 현재가와 등락률
- `investor_flow`: KOSPI/KOSDAQ 외국인, 기관, 개인 순매수
- `k200_futures`: KOSPI200 선물 현재가, 등락률, 베이시스, 거래량, 가능하면 외국인 선물 순매수
- `breadth`: 상승, 하락, 보합 종목 수
- `fx`: USD/KRW 환율
- `field_status`: 각 필드별 `ok`, `source`, `as_of`, `error`
- `ts`, `as_of`, `max_age_s`: 신선도 판단 기준

값이 없으면 추정해서 채우지 않습니다. 대신 `field_status`와 `unavailable`에 미연결/누락 사유를 남겨 AI 콘솔이 답변에서 데이터 공백을 드러내게 합니다.

## Collector

```bash
KR_MARKET_MICROSTRUCTURE_ENABLED=true .venv/bin/python crons/kr_microstructure_snapshot.py
```

권장 cron:

```cron
* * * * * cd /home/ubuntu/projects/stock-report && .venv/bin/python crons/kr_microstructure_snapshot.py >> /tmp/kr_microstructure_snapshot.log 2>&1
```

## Environment

- `KR_MARKET_MICROSTRUCTURE_ENABLED=true`: collector/healthcheck 활성화
- `KR_MARKET_MICROSTRUCTURE_CACHE=/home/ubuntu/.cache/kr_market_microstructure.json`: 파일 스냅샷 위치
- `KR_MARKET_MICROSTRUCTURE_STALE_S=120`: stale 기준
- `KR_MARKET_MICROSTRUCTURE_SOURCE_FILE=/path/to/broker_snapshot.json`: 증권사/KRX 브리지 산출물을 읽는 입력 파일
- `KR_MARKET_MICROSTRUCTURE_REQUIRED_FIELDS=indices,investor_flow,k200_futures,breadth,fx`: 헬스체크 필수 필드
- `KR_MARKET_MICROSTRUCTURE_KIWOOM_ENABLED=true`: Kiwoom 장중 투자자 수급 수집 활성화
- `KIWOOM_INVESTOR_FLOW_STEX_TP=3`: Kiwoom 수급 거래소 구분. 기본은 통합
- `KIWOOM_INVESTOR_FLOW_AMOUNT_UNIT_KRW=1000000`: Kiwoom 금액 필드를 KRW로 환산하는 배수
- `KR_MARKET_MICROSTRUCTURE_KIS_FUTURES_ENABLED=true`: KIS 국내선물옵션 현재가 수집 활성화
- `KIS_K200_FUTURES_CODE=A05608`: 지정하면 해당 선물코드를 조회. 비워두면 선물 전광판에서 거래량 최대 월물을 선택
- `REDIS_URL` 또는 `UPSTASH_REDIS_URL`: 있으면 Redis에 쓰고 파일 fallback도 같이 유지
- `AGENT_CONSOLE_TOSS_FX_ENABLED=true`: Toss API 환율 fallback 활성화
- `QUOTES_POLL_ENABLED=true`: US/KR REST quote poller 활성화
- `REALTIME_US_ENABLED=true`: KIS US WebSocket quote stream 활성화
- `ORDERFLOW_CAPTURE_ENABLED=true`: KIS WS의 개별 체결과 호가 스냅샷을 `~/reports/ml-data/orderflow/{세션 날짜}/{SYMBOL}.jsonl`에 저장
- `ORDERFLOW_BOOK_SAMPLE_SECS=1.0`: 심볼별 호가 보존 간격. 낮출수록 큐 변화는 촘촘하지만 저장량이 증가
- `ORDERFLOW_CAPTURE_MAX_BYTES=268435456`: 거래소 현지 세션 날짜별 캡처 상한. 상한 이후 이벤트는 시세 캐시에 영향 없이 드롭하고 상태에 유실 수 기록
- `ORDERFLOW_PENDING_MAX=100000`: 디스크 flush 사이 메모리 큐 상한. 장애 중 무제한 메모리 증가를 차단
- `ORDERFLOW_RETENTION_DAYS=14`: 세션 날짜 파티션 보존 기간. 기본 설정의 총 JSONL 상한은 약 3.5 GiB
- `REALTIME_REST_REDIS_KEY=quotes:rest`: REST quote cache Redis key
- `REALTIME_WS_REDIS_KEY=quotes:ws`: KIS WebSocket quote cache Redis key
- `REALTIME_REDIS_TTL_S=180`: quote cache Redis TTL

오더플로 계약은 거래소 시각과 앱 수신시각을 분리하고, KIS 개별 체결량을 우선 사용한다. 미국 장후장이 UTC 자정을 지나도 거래소 현지 세션 날짜에 보존한다. 국내 호가는 최대 10단계, 미국 무료 실시간 호가는 1단계다. 차트 reader는 최근 10,000건·최대 8MiB만 뒤에서 읽고 `truncated`와 캡처 유실 상태를 함께 표시한다. 쓰기 예외는 bounded 큐에서 재시도하며, 구독 심볼 외 이벤트는 저장하지 않는다. 리플레이 중에는 미래 정보 누출을 막기 위해 라이브 오더플로를 비활성화한다. 원천에 매수/매도 주도 체결 필드가 없으므로 풋프린트와 bid/ask delta는 OHLCV나 호가로 추정하지 않으며 UI에서 비활성 상태로 표시한다.

## Built-in Sources

`providers.kr_microstructure` can fill the snapshot without a separate bridge when credentials and feature flags are present:

- `indices`: Naver realtime index JSON for `KOSPI`, `KOSDAQ`, and `KPI200` as `kospi200`
- `breadth`: Naver mobile stock count JSON for KOSPI/KOSDAQ total, up, and down counts; unchanged is calculated as `total - up - down`
- `fx`: Toss API when `AGENT_CONSOLE_TOSS_FX_ENABLED=true` and credentials are present
- `investor_flow`: Kiwoom REST `ka10063` intraday investor trading data first, `ka10066` after-hours trading second, `ka10051` industry flow last-resort fallback. All are normalized to KOSPI/KOSDAQ foreign/institution/individual net KRW and tagged with `scope`/`basis`.
- `k200_futures`: KIS domestic futureoption display board selects the most active KOSPI200 futures contract, then `inquire-price` fills price, change percent, basis, and volume

Broker/KRX bridge data still takes precedence for fields it provides. The built-in KIS futures path currently covers price-side futures data; foreign futures net contracts still require a broker/KRX bridge field such as `foreign_net`.

For the investor-flow field, the snapshot prefers the market-wide intraday Kiwoom path because that is the closest match to the MTS "장중 투자자별 매매" screen. If that route is unavailable, the collector falls back to the after-hours market path and finally the sector-level path.

## US 24-Hour Tracking

`quotes_poller.us_trading_session()` treats US equities as sessioned, not simply open or closed:

- `overnight`: Sunday 20:00 ET through Friday 04:00 ET overnight windows, excluding Saturday and Friday after 20:00 ET
- `premarket`: 04:00-09:30 ET
- `regular`: 09:30-16:00 ET
- `afterhours`: 16:00-20:00 ET
- `closed`: Saturday, Sunday before 20:00 ET, and Friday after 20:00 ET

During every non-closed US session, `quotes_poller.py` polls US symbols and writes `session` metadata to `~/.cache/rest_quotes.json`. AI Console quote lines preserve that metadata so overnight prices are visibly separated from regular-session prices.

Overnight data remains source-dependent. The current built-in route uses the same read-only quote providers as the REST poller, so if the broker source does not return a valid overnight price the cache is not fabricated. If a dedicated Blue Ocean/IBKR overnight feed is added later, it should set `venue`, `spread_pct`, `liquidity_flag`, and `tradable` on each quote entry rather than replacing the session contract.

## Shared Redis Cache

When `REDIS_URL` or `UPSTASH_REDIS_URL` is present, market and quote writers mirror their JSON cache to Redis while keeping the local file warm:

- Korean microstructure: `market:kr:microstructure`
- KIS WebSocket quotes: `quotes:ws`
- REST quote poller: `quotes:rest`

Readers prefer Redis only when its heartbeat is fresh. If Redis is missing, unreachable, or stale, they fall back to the existing file cache. This keeps single-server operation simple while allowing Vercel functions, local daemons, healthchecks, and future workers to share the same 24-hour tracking state.

## Broker/KRX Bridge Shape

외부 수집기가 `KR_MARKET_MICROSTRUCTURE_SOURCE_FILE`에 아래 형태로 쓰면 collector가 그대로 정규화합니다.

```json
{
  "indices": {
    "kospi": {"price": 3310.2, "change_pct": 0.42, "source": "broker_api"},
    "kosdaq": {"price": 912.1, "change_pct": -0.2, "source": "broker_api"}
  },
  "investor_flow": {
    "kospi": {"foreign_net": 120000000000, "institution_net": -40000000000, "individual_net": -80000000000, "source": "krx"}
  },
  "k200_futures": {"price": 452.2, "change_pct": 0.31, "foreign_net": 1800, "source": "broker_api"},
  "breadth": {"advancers": 510, "decliners": 310, "unchanged": 74, "source": "krx"},
  "fx": {"usdkrw": {"rate": 1387.2, "change": -2.1, "source": "toss_api"}}
}
```

## Operating Rule

AI 콘솔이 “오늘 한국증시 어땠어”, “장중 수급 확인해줘” 같은 질문을 받으면 다음을 우선 확인합니다.

1. 스냅샷 `as_of`가 stale인지
2. KOSPI/KOSDAQ 방향
3. 외국인/기관 현물 수급
4. KOSPI200 선물과 외국인 선물 방향
5. 상승/하락 종목 수로 시장 폭 확인
6. USD/KRW로 환율 압력 확인

이 중 일부가 없으면 답변을 중단하지 않고, 가능한 필드를 근거로 답하되 빠진 필드를 명시합니다.
