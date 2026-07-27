# Intraday Market Data Enrichment

AI 콘솔은 장중 시장 질문을 받을 때 `agent_console.market_snapshot_store`의 한국 시장 미시구조 스냅샷을 읽습니다. 챗봇은 원문 API 응답을 직접 프롬프트에 넣지 않고, 아래 정규화된 필드만 사용합니다.

## Snapshot Contract

- `indices`: `kospi`, `kosdaq` 현재가와 등락률
- `investor_flow`: KOSPI/KOSDAQ 외국인, 기관, 개인 순매수
- `k200_futures`: KOSPI200 선물 현재가, 등락률, 외국인 선물 순매수
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
- `REDIS_URL` 또는 `UPSTASH_REDIS_URL`: 있으면 Redis에 쓰고 파일 fallback도 같이 유지
- `AGENT_CONSOLE_TOSS_FX_ENABLED=true`: Toss API 환율 fallback 활성화

## Built-in Public Fallbacks

`providers.kr_microstructure` can fill part of the snapshot without a broker bridge:

- `indices`: Naver realtime index JSON for `KOSPI`, `KOSDAQ`, and `KPI200` as `kospi200`
- `breadth`: Naver mobile stock count JSON for KOSPI/KOSDAQ total, up, and down counts; unchanged is calculated as `total - up - down`
- `fx`: Toss API when `AGENT_CONSOLE_TOSS_FX_ENABLED=true` and credentials are present

Broker/KRX bridge data still takes precedence for fields it provides. `investor_flow` and `k200_futures` remain unavailable unless a trusted bridge writes them, because the current public fallback does not provide reliable intraday values for those fields.

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
