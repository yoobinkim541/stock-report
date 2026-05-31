# 📊 Stock Report — Intelligence Barbell v2.1

> 감정 없이, 규칙대로. QQQ Phase 기반 자동화 투자 시스템.

미국 주식 포트폴리오의 **매일 아침 리포트 자동 생성 + 텔레그램 발송**,  
그리고 시장 국면 변화 시 **즉시 알림**을 보내는 개인 투자 자동화 시스템입니다.

---

## 🏗 구조 한눈에 보기

```
매일 23:00 UTC (KST 08:00)
        │
        ▼
deliver_investment_report.sh
        │
        ├─► investment_report.py   →  포트폴리오 수익률 + 종목 분석 (Markdown 리포트)
        ├─► save_csv.py            →  CSV 내보내기
        └─► barbell_strategy.py   →  Phase 분류 + 시각화 리포트
                │
                └─► Telegram @Stock_botbot
                      ├── 매일: 투자 리포트 문서 전송
                      └── Phase 변화 시: 즉시 바벨 전략 알림
```

---

## ⚙️ 스크립트 목록

| 파일 | 역할 |
|------|------|
| `barbell_strategy.py` | Intelligence Barbell v2.1 — Phase 분류, SGOV/DCA/레버리지 계산, 시각화 리포트 |
| `investment_report.py` | 포트폴리오 수익률 + 펀더멘털 분석 Markdown 리포트 생성 |
| `fundamental_score.py` | 종목별 100점 펀더멘털 스코어링 (yfinance 기반) |
| `daily_signals.py` | 가격/거래량 기반 일일 신호 감지 |
| `market_report.py` | 시장 뉴스 리포트 (SaveTicker API + Arca Live) |
| `save_csv.py` | JSON 요약 → CSV 내보내기 |
| `deliver_investment_report.sh` | 전체 파이프라인 실행 + 텔레그램 발송 쉘 스크립트 |

---

## 🏋️ Intelligence Barbell v2.1

### 전략 개요

```
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│  상승장  │  강세장  │  중립    │  조정    │  크래시  │
│  SGOV↑   │  SGOV+   │  DCA     │ SGOV→QLD │  TQQQ    │
└──────────┴──────────┴──────────┴──────────┴──────────┘
```

QQQ 52주 고점 대비 낙폭을 기준으로 **8단계**를 구분하여,  
각 단계마다 DCA 배율, SGOV 목표 비중, 레버리지 ETF 투입 전략을 자동 결정합니다.

### Phase 테이블

| Phase | 조건 | DCA | SGOV | 레버리지 |
|-------|------|-----|------|---------|
| 🫧 Bull-2 | RSI>75 + 1M모멘텀>8% + VIX<15 | 0.5× (2만원) | 20% 비축 | — |
| 🐂 Bull-1 | RSI>70 또는 1M모멘텀>5% | 0.8× (3.2만원) | 12% 비축 | — |
| 🟢 Phase 0 | 고점 -5% 이내 (중립) | 1.0× (4만원) | 8% 유지 | — |
| 🟡 Phase 1 | -5% ~ -10% | 1.5× (6만원) | 유지 | — |
| 🟠 Phase 2 | -10% ~ -15% | 2.0× (8만원) | 30% → QLD | QLD |
| 🔴 Phase 3 | -15% ~ -20% | 2.5× (10만원) | +35% → QLD | QLD |
| 🚨 Phase 4 | -20% ~ -30% | 3.0× (12만원) | 전량 전환 | QLD 70 + TQQQ 30 |
| 💥 Phase 5 | -30%+ | 5.0× (20만원) | QQQI 20~30% | TQQQ 전면 |

### 바벨 구조

- **SGOV** (초단기 국채) — 하락장 대비 실탄. Phase 2+에서 QLD/TQQQ로 전환
- **QQQI** (나스닥100 고배당) — 월간 배당 현금흐름 엔진. Phase별로 재투자 방향 결정
- **QLD / TQQQ** — 조정/크래시 구간에서 단계적 투입하는 레버리지 ETF

### 리포트 샘플

```
🏋️ Intelligence Barbell v2.1
📅 2026-05-30 08:00 KST

📍 Phase  🟢 Phase 0 — 정상 모드
 B2    B1   [N0]   P1    P2    P3    P4    P5
 🫧    🐂   ◉🟢    🟡    🟠    🔴    🚨    💥
  QQQ 고점 대비  -1.00%   정상 DCA 유지. 변화 없음.

━━━ 💼 포트폴리오 ━━━
  총액  $8,124.50   (₩11,211,810)
  환율  1,380.0원/USD
  SGOV  $1,012.30   █░░░░░░░░░  12.5%  실탄
  QQQI  $2,035.40   ███████░░░  25.1%  배당엔진

━━━ 📈 QQQ 레이더 ━━━
  현재가  $  515.20   52주高 $527.50  低 $395.20
  낙폭      -2.33%   52주위치 ████████████ 96%
  ◄──────────────────────●►
  -30%                   0%
  RSI   58.0  ███████░░░░░  중립 ✅
  VIX   17.5  ████░░░░░░░░  정상 ✅
  200MA     +12.3%  ✅

━━━ 💸 DCA  40,000원  ($28.99 @ 1,380원)  [1.0x] ━━━
  NOW    ████████  8,000원  $5.8  (20%)
  ORCL   ████████  8,000원  $5.8  (20%)
  NVDA   ██████░░  6,000원  $4.3  (15%)
  MSFT   ██████░░  6,000원  $4.3  (15%)
  ...
```

---

## 🚀 설치 및 설정

### 요구사항

```
python3 (3.10+)
uv  (패키지 관리)
```

```bash
# 의존성 설치
uv pip install yfinance numpy requests beautifulsoup4 python-dotenv
```

### 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일에 다음을 입력합니다:

```env
STOCK_BOT_TOKEN=your_telegram_bot_token_here
```

> ⚠️ `.env` 파일은 절대 커밋하지 마세요. `.gitignore`에 포함되어 있습니다.

### 포트폴리오 설정

`portfolio_snapshot.json`을 본인의 보유 종목에 맞게 수정합니다.  
(스냅샷 형식은 `portfolio_snapshot.example.json` 참고)

---

## 🤖 양방향 텔레그램 봇

폰에서 언제든지 현재 상태를 조회할 수 있는 양방향 봇입니다.  
**입대 후에도 자동 알림 + 수동 조회가 모두 가능합니다.**

### 명령어

| 명령어 | 설명 |
|--------|------|
| `/status` | Phase + 핵심 수치 (빠른 조회, 5분 캐시) |
| `/phase` | Phase 미터 + 행동 지침 |
| `/portfolio` | 포트폴리오 실시간 현황 |
| `/dca` | 오늘 DCA 배분 금액 |
| `/sgov` | SGOV 실탄 현재/목표 비교 |
| `/report` | 전체 바벨 리포트 (항상 실시간) |
| `/alert add TICKER 가격 buy\|sell` | 가격 알림 등록 |
| `/alert list` | 활성 알림 목록 |
| `/alert remove ID` | 알림 삭제 |

### 봇 실행 (수동)

```bash
# 포그라운드 실행 (테스트용)
python3 telegram_bot.py

# 백그라운드 실행 (운영)
nohup python3 telegram_bot.py >> /tmp/barbell_bot.log 2>&1 &

# 로컬 테스트 (Telegram 미전송)
python3 telegram_bot.py --test
```

### 봇 상시 가동 (크론 watchdog)

봇이 예기치 않게 종료되면 1분 내 자동 재시작합니다.

```bash
# 크론 등록
crontab -e
```

```cron
# 봇 watchdog — 1분마다 실행 중인지 확인, 죽어 있으면 재시작
* * * * * /home/ubuntu/projects/stock-report/bot_watchdog.sh

# 서버 재부팅 시 봇 자동 시작
@reboot /home/ubuntu/projects/stock-report/bot_watchdog.sh
```

### 가격 알림 예시

```
/alert add CPNG 14.00 sell 손절
/alert add ORCL 260.00 sell 익절 목표
/alert add QQQ 430.00 buy Phase2 매수 기회
```

---

## 📖 사용법

### 바벨 전략 분석 (실시간)

```bash
# 실시간 분석 (Phase 변화 시 텔레그램 자동 발송)
python3 barbell_strategy.py

# 텔레그램 강제 발송
python3 barbell_strategy.py --send

# 시장 상태 시뮬레이션
python3 barbell_strategy.py --sim bull2   # 과열
python3 barbell_strategy.py --sim 1       # Phase 1 조정
python3 barbell_strategy.py --sim 3       # Phase 3 베어 진입
python3 barbell_strategy.py --sim 5       # 크래시

# QLD/TQQQ 매수 후 포지션 업데이트
python3 barbell_strategy.py --update-leverage QLD 5 78.50
python3 barbell_strategy.py --update-leverage TQQQ 3 45.20
```

### 일일 투자 리포트

```bash
# 전체 파이프라인 (리포트 생성 + 텔레그램 발송 + 바벨 분석)
bash deliver_investment_report.sh
```

### 개별 스크립트

```bash
python3 investment_report.py    # 포트폴리오 분석 리포트
python3 market_report.py        # 시장 뉴스 리포트
python3 fundamental_score.py    # 종목 펀더멘털 스코어
```

---

## ⏰ 크론 설정

```cron
# 매일 KST 08:00 (UTC 23:00) 월~금 자동 실행
0 23 * * 1-5 /path/to/deliver_investment_report.sh >> /tmp/stock_cron.log 2>&1
```

---

## 📁 파일 구조

```
stock-report/
├── barbell_strategy.py          # 핵심 전략 엔진 (Phase 분류 + 시각화 리포트)
├── telegram_bot.py              # 양방향 텔레그램 봇
├── price_alerts.py              # 가격 알림 관리 모듈
├── bot_watchdog.sh              # 봇 상시 가동 watchdog
├── investment_report.py         # 포트폴리오 분석
├── fundamental_score.py         # 펀더멘털 스코어링
├── daily_signals.py             # 일일 신호 감지
├── market_report.py             # 시장 뉴스
├── save_csv.py                  # CSV 내보내기
├── deliver_investment_report.sh # 크론 실행 스크립트
├── deliver_market_report.sh     # 시장 리포트 발송
├── CLAUDE.md                    # 프로젝트 컨텍스트 (Claude Code용)
├── .env                         # 🔒 비공개 — 텔레그램 토큰
├── portfolio_snapshot.json      # 🔒 비공개 — 보유 종목 스냅샷
├── leverage_state.json          # 🔒 비공개 — QLD/TQQQ 포지션
└── price_alerts.json            # 🔒 비공개 — 등록된 가격 알림

~/.cache/barbell_state.json      # Phase 상태 캐시 (중복 알림 방지)
~/reports/                       # 생성된 리포트 저장 디렉토리
/tmp/barbell_bot.log             # 봇 실행 로그
/tmp/barbell_bot.pid             # 봇 PID (watchdog용)
```

---

## 🤖 텔레그램 봇 설정

1. [@BotFather](https://t.me/BotFather)에서 새 봇 생성 → 토큰 발급
2. `.env`에 `STOCK_BOT_TOKEN` 입력
3. 봇에게 메시지를 먼저 보낸 후 `chat_id` 확인:
   ```
   https://api.telegram.org/bot{TOKEN}/getUpdates
   ```
4. `barbell_strategy.py`의 `TELEGRAM_CHAT_ID` 수정

---

## ⚠️ 주의사항

- 이 시스템은 **개인 투자 자동화 도구**입니다. 투자 결과에 대한 책임은 본인에게 있습니다.
- yfinance 무료 API 기반으로 실시간이 아닌 **전일 종가** 기준 데이터를 사용합니다.
- Phase 알림은 **매일 변화가 있을 때만** 발송됩니다 (중복 방지).

---

## 📦 의존성

| 패키지 | 용도 |
|--------|------|
| `yfinance` | 주가, RSI, MA 등 시장 데이터 |
| `numpy` | 수치 계산 |
| `requests` | 텔레그램 API, 외부 HTTP |
| `beautifulsoup4` | 뉴스 크롤링 |
| `python-dotenv` | 환경변수 로딩 |
