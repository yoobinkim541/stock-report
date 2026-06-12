# 게스트 온보딩 & 배포 가이드

> 읽기전용 게스트를 안전하게 추가하는 운영 절차. (기능 설계는
> `docs/guest-readonly-access.md` 참고.)

## 0. 전제 — 법적 확인 먼저

게스트에게 **시황·기술적 지표·본인 포트폴리오 평가**만 제공하고, 매매신호·목표가·
주문은 일절 제공하지 않는 범위 안에서만 운영한다 ("서술 OK, 지시 금지"). 유료화·
다수 모집·맞춤 매매조언으로 확장하기 전엔 **금융 전문 변호사/금감원 유권해석**을
받을 것. (자세한 규제 경계: `docs/guest-readonly-access.md` §법적 근거)

## 1. 게스트 chat_id 확인

게스트의 텔레그램 숫자 chat_id가 필요하다. 셋 중 하나:

1. **봇 로그로 확인 (가장 간단)**: 게스트에게 봇한테 아무 메시지나 보내게 한 뒤,
   봇 로그에서 차단 라인을 찾는다.
   ```
   WARNING 차단: chat_id 123456789
   ```
   이 숫자가 게스트의 chat_id.
2. **@userinfobot**: 게스트가 텔레그램에서 `@userinfobot` 에게 메시지 → 자기 ID 회신.
3. **getUpdates API**: `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서
   `message.chat.id` 확인.

## 2. 환경변수 등록

`.env` (또는 배포 환경)에서 `STOCK_BOT_GUEST_IDS` 에 **쉼표 구분**으로 추가:

```bash
STOCK_BOT_GUEST_IDS=123456789,987654321
```

- 미설정/빈 값이면 게스트 없음 — 기존(소유자 전용)과 100% 동일.
- 소유자 `STOCK_BOT_CHAT_ID` 와 겹치면 소유자 권한이 우선.

## 3. 봇 재시작 (env 반영)

`STOCK_BOT_GUEST_IDS` 는 **프로세스 시작 시점에 평가**되므로 봇 재시작이 필요하다.

```bash
# 단일 인스턴스 잠금(fcntl)이 있으므로 기존 프로세스 종료 후 재기동
scripts/watchdog* 또는 운영 방식에 맞게 telegram_bot 재시작
```

## 4. 동작 확인

게스트 계정으로:
- `/help` → 게스트 도움말 (6개 명령 + 면책) 표시되면 정상
- `/market` → 시황 브리핑 (처방 없음)
- `/order` 등 소유자 명령 → "🔒 이 명령은 소유자 전용입니다" 차단 확인

## 5. 게스트 안내 템플릿 (면책 포함)

게스트에게 처음 보낼 안내 예시:

```
안녕하세요 👋 읽기전용 계정으로 등록되었습니다.

사용 가능:
  /market            시황 (국면·낙폭·RSI·VIX·F&G)
  /indicators QQQ    종목 기술적 지표
  /myadd QQQ 10 500  내 보유 종목 추가 (티커 주수 평단가)
  /myportfolio       내 포트폴리오 평가 (평가액·손익)
  /help              도움말

ℹ️ 제공되는 정보는 참고용 시장 데이터·지표이며 매매 권유가 아닙니다.
   투자 판단과 책임은 본인에게 있습니다.
```

## 6. 운영 점검

- **데이터 격리**: 게스트 보유 종목은 store 문서 `guest_holdings` 의 본인 chat_id
  네임스페이스에만 저장 — 소유자/다른 게스트와 상호 비공개.
- **store 무결성**: `tests/bot_healthcheck.py` 의 `check_store_db` 가 30분마다
  DB 접근·`PRAGMA quick_check` 점검 → 손상 시 소유자에게 알림.
- **게스트 제거**: `STOCK_BOT_GUEST_IDS` 에서 해당 id 삭제 후 재시작. (저장된
  `guest_holdings` 문서는 store에 남으므로 필요 시 별도 정리.)

## 7. 게스트가 할 수 없는 것 (보안 경계)

`_command_allowed(role, cmd)` (telegram_bot) 가 차단 — 게스트는 아래 전부 불가:
- 주문/주문서(`/order`), DCA·리밸런싱·SGOV 처방(`/dca` `/rebalance` `/sgov`)
- 매매신호·레버리지·진입분석(`/leverage` `/entry` `/meta` `/intraday` `/report` `/status` 등)
- 종목관리·세금·AI상담(`/holding` `/tax` `/ask` `/alert`)
- 첨부 파일·일반 텍스트(스냅샷 파싱 = 포트폴리오 수정)
