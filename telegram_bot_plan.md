# 텔레그램 양방향 봇 — 설계

## 목표
@Stock_botbot이 명령어를 받아서 Phase, 포트폴리오, 가격 알림 등을 응답하게 함

## 파일 구조

```
~/projects/stock-report/
├── telegram_bot.py          # 메인: 폴링 + 명령어 처리 (cron 2분마다 실행)
├── price_alerts.py           # 가격 알림 등록/조회/체크
├── price_alerts.json         # 알림 저장 파일
└── telegram_bot_state.json   # 봇 상태 (last_update_id 등)
```

## 명령어 목록

### /status — 전체 현황 요약
```
📊 전체 현황 (2026-05-30)

Phase: bull/bull_2 (🟢 강세 2단계)
MDD: -3.2%
DCA: 기본 (1.0x)
SGOV 목표: 35%

포트폴리오: 12종목
🟢 8 | ⚪ 4 | 🟡 0 | 🔴 0
평균 점수: 47.2/100

SPY: $756.48 (+0.25%)
```

### /phase — Phase 상세
```
🔵 Phase 상세 — bull/bull_2

시장: 강세 2단계 (가속)
SGOV 비중 목표: 35%
현재 SGOV: $4,200 (32%)
→ $390 추가 매수 필요

DCA 승수: 1.0x (기본)
SGOV DCA: $150 → QQQI/MSFT
```

### /portfolio — 포트폴리오 현황
```
📈 포트폴리오 현황

MSFT — Microsoft: 71점 B 🟢 분할매수
ORCL — Oracle: 61점 B 🟢 분할매수
NVDA — NVIDIA: 66점 B 🟢 분할매수
... (모든 12종목)
```

### /alert add <티커> <가격> <buy|sell>
```
/alert add CPNG 25000 sell
→ ✅ CPNG 25,000원 sell 알림 등록됨 (ID: 1)
```

### /alert list
```
📋 가격 알림 목록

1. CPNG sell @ 25,000원 (대기 중)
2. ORCL buy @ $180 (대기 중)
```

### /alert remove <id>
```
/alert remove 1
→ ✅ 알림 #1(CPNG sell @ 25,000원) 삭제됨
```

## 동작 방식

### 폴링 (cron 2분마다)
1. `telegram_bot.py` 실행
2. Telegram API에서 새 메시지 확인 (getUpdates)
3. 명령어 처리
4. 가격 알림 체크 (yfinance로 현재가 확인)
5. 조건 도달 시 텔레그램 전송
6. 상태 저장

### 읽어야 할 데이터
- Phase 상태: `barbell_strategy.py` → phase_state.json
- 포트폴리오: `investment_report.py` → investment-summary-{date}.json
- 종목 점수: investment-summary에서 추출
- SGOV/QQQI 데이터: yfinance 실시간 조회

## Price Alert 파일 형식 (price_alerts.json)
```json
[
  {
    "id": 1,
    "ticker": "CPNG",
    "target_price": 25000,
    "type": "sell",
    "currency": "KRW",
    "note": "",
    "created_at": "2026-05-30T15:00:00",
    "triggered": false,
    "triggered_at": null
  }
]
```

## 구현 규칙
- STOCK_BOT_TOKEN 사용 (.env에서 로드)
- CHAT_ID=5771238245 (명령어 보낸 사람만 처리)
- Phase 변화 감지는 기존 barbell_strategy.py 재사용
- yfinance로 실시간 가격 조회
- 모든 메시지는 한국어
