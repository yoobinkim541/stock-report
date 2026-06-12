# 읽기전용 게스트 접근 — 역할 게이팅 + 법적 안전 경계

> 목적: 본인 외 사용자에게 **자기 참고용 시황·기술적 지표 정보**만 제공하고,
> 주문·매매신호·맞춤 투자조언은 전면 차단한다. 한국 자본시장법상 규제 영역
> (투자자문업·투자일임업·유사투자자문업)에 들어가지 않도록 설계한 경계.

## 법적 근거 (요약 — 변호사 자문 아님)

| 구분 | 규제 | 회피 방법 |
|------|------|-----------|
| 남의 계좌 주문 대행 | 투자일임업 (등록) | **주문은 owner 본인만** — 게스트 주문 기능 0 |
| 특정인 맞춤 매매조언 | 투자자문업 (등록) | 게스트엔 **맞춤 신호·목표가·DCA 미제공** |
| 불특정 다수 유료 신호 | 유사투자자문업 (신고) | 게스트엔 **처방형 출력 자체가 없음** |

**핵심 원칙: 서술(descriptive)은 OK, 지시(prescriptive)는 금지.**
같은 데이터라도 "낙폭 -16%, RSI 38" (사실) 은 제공, "지금 사라 / 목표가 X" (지시) 는 금지.

## 역할 모델 (`telegram_bot.py`)

```
_role_for(chat_id):
    owner  ← STOCK_BOT_CHAT_ID        # 전체 권한
    guest  ← STOCK_BOT_GUEST_IDS      # 읽기전용 (쉼표구분)
    None   ← 그 외                     # 차단

_command_allowed(role, cmd):          # 보안 경계 (순수 함수, 테스트됨)
    owner → 전부 허용
    guest → _GUEST_COMMANDS 만 ({/help, /market, /indicators})
    None  → 전부 차단
```

게이팅 적용 지점:
- **명령**: `dispatch()` 가 `_command_allowed` 로 차단 → "소유자 전용" 안내.
- **첨부 파일**(스냅샷/매도내역 파싱 = 포트폴리오 수정): owner 전용.
- **일반 텍스트**(스냅샷 파싱): owner 전용.

## 게스트 출력 (`bot/guest_report.py`)

| 명령 | 내용 | 처방 포함? |
|------|------|-----------|
| `/market` | 국면(서술)·QQQ 낙폭·RSI·VIX·F&G·벤치마크 YTD | ❌ 없음 |
| `/indicators TICKER` | 현재가·RSI(14)·SMA20/50/200·1M·3M 모멘텀·52주 위치 | ❌ 없음 |
| `/myadd` · `/myremove` | 본인 보유 종목 입력/삭제 (그들 데이터) | ❌ 없음 |
| `/myportfolio` | 본인 포트폴리오 평가 — 평가액·손익·수익률 | ❌ 없음 |
| `/help` | 게스트 허용 명령 안내 | — |

- 모든 출력에 **면책 문구**(`DISCLAIMER`) 부착.
- Phase 번호·행동지침(DCA 배율·레버리지 전환)·목표가/손절가·매매신호·AI상담 **전부 제외**.

## 검증

`tests/role_gating_test.py` (네트워크 불필요, 16항목):
- 역할 해석, 게스트의 소유자 전용 16개 명령 차단, 허용 3개 명령,
  게스트 허용집합에 처방 명령 누출 없음, guest_report 출력에 처방형 표현 부재 + 면책 포함.

## 게스트 본인 포트폴리오 (`bot/guest_portfolio.py`)

게스트가 자기 보유 종목을 입력하고 본인 데이터의 평가를 본다 — 포트폴리오 트래커 수준.
- store 문서 `guest_holdings` 를 **게스트 chat_id(user_id) 네임스페이스**에 저장 →
  소유자 `portfolio_snapshot.json` 과 완전 격리 (파일 미러 없음, store-only).
- `/myadd TICKER 주수 평단가` (가중평단 누적) · `/myremove TICKER` · `/myportfolio`(yfinance 현재가로 평가액·손익·수익률).
- 처방(매매신호·목표가·DCA·리밸런싱) 없음 + 면책 문구.
- 검증: `store_smoke_test.py` 의 guest_portfolio CRUD·user_id 격리 항목.
